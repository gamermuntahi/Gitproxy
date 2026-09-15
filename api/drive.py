"""WebProxyLive - Google Drive source proxy (Vercel serverless entrypoint).

This module serves a *public* Google Drive folder or file as a static website:

    /drive/<folder-id>/                 ->  index.html / index.htm at the root
    /drive/<folder-id>/<path>           ->  a file or nested folder inside it
    /drive/<file-id>                    ->  a single public file
    /drive/<folder-id>?manifest=<...>   ->  caller supplied id map (no key)

No API key, OAuth token, database or extra backend is used anywhere.

Why this is layered
-------------------
A Google Drive folder id is **not** a downloadable file id, and Google does not
publish an anonymous "list this folder" REST endpoint. What Google *does* expose
publicly is:

* ``drive.google.com/embeddedfolderview?id=<id>`` - the folder view Google's own
  embed widget uses. For a link-shared folder it returns the folder's items as
  plain HTML, with each item's id, name and type. This is Layer 2.
* ``drive.google.com/uc?export=download&id=<id>`` - the classic public download
  endpoint for one file id. This is Layer 1.
* ``drive.google.com/thumbnail?id=<id>`` - a public image/asset proxy that works
  for some files the download endpoint refuses. This is Layer 3.

So the resolver tries, in order:

1. **Layer 1 - direct public endpoints.** When the id in the URL is a file id
   (or the requested path resolves to one, including ids supplied by a
   manifest), fetch it straight from Google's public download endpoint.
2. **Layer 2 - public folder discovery.** Read Google's public folder-view HTML
   for the folder, resolve each path segment against the real item list, and
   descend into subfolders the same way. No credentials, just the same public
   response a browser embedding the folder would get.
3. **Layer 3 - direct file fallback.** If a folder cannot be enumerated, but the
   id or the last path segment is itself a public file, serve that file
   directly instead of giving up.
4. **Layer 4 - graceful, honest failure.** If Google genuinely refuses to expose
   the folder's contents without its API, say exactly that and explain the
   public sharing fix. Nothing is faked and no error is dressed up as success.

A folder that is public but has no ``index.html`` renders a real directory
browser, so a folder of files is still usable as a listing.

Security model
--------------
* The folder id and every path segment pass the same strict validation as the
  GitHub proxy (``..``, backslashes, NUL bytes, control characters, over-long
  and over-deep paths are all rejected before any network call).
* Every upstream URL is **constructed** from an individually validated id on a
  Google-owned host; no user-supplied URL is ever fetched, so SSRF, localhost
  and arbitrary-domain access are impossible.
* Each path segment is only ever matched against the listing of the folder
  reached so far, so a request can never escape the selected folder.
* Only real, static files are proxied. Google Workspace items (Docs, Sheets,
  Slides, ...) are reported as unsupported instead of being dressed up as HTML.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urlparse

# Put the api/ directory on the import path so this module loads both as a
# Vercel function and when the local Flask launcher imports it directly.
_API_DIR = os.path.dirname(os.path.abspath(__file__))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import _shared  # noqa: E402  (path is prepared above)
from _shared import (  # noqa: E402
    DRIVE_ALLOWED_HOSTS,
    DRIVE_ALLOWED_HOST_SUFFIXES,
    INDEX_CANDIDATES,
    ProxyHandler,
    code_html,
    content_type_for,
    fetch_url,
    file_outcome,
    html_outcome,
    normalise_path,
    valid_drive_id,
)

# --------------------------------------------------------------------------- #
# Google's public (credential-free) endpoints
# --------------------------------------------------------------------------- #

#: The folder view Google's embed widget itself uses. Public and keyless.
DRIVE_FOLDER_VIEW_URL = "https://drive.google.com/embeddedfolderview"

#: The classic public download endpoint for a single file id.
DRIVE_UC_URL = "https://drive.google.com/uc"

#: Public thumbnail/asset proxy - a useful Layer 3 fallback for images.
DRIVE_THUMBNAIL_URL = "https://drive.google.com/thumbnail"

#: The human-facing file page, used only to learn a file's name.
DRIVE_FILE_VIEW_URL = "https://drive.google.com/file/d"

#: A Drive "path" costs one public request per level, so the depth is capped
#: well below the generic path limit to stay inside a serverless time budget.
MAX_DRIVE_DEPTH = 16

FOLDER_MIME = "application/vnd.google-apps.folder"

#: Google Workspace documents look like files but have no static bytes. They are
#: detected so the user gets a precise explanation instead of a broken download.
WORKSPACE_MIMES = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
        "application/vnd.google-apps.drawing",
        "application/vnd.google-apps.form",
        "application/vnd.google-apps.script",
        "application/vnd.google-apps.site",
        "application/vnd.google-apps.jam",
        "application/vnd.google-apps.map",
        "application/vnd.google-apps.fusiontable",
    }
)

#: Google's MIME vocabulary mapped onto the Content-Type we actually send, so a
#: ``.js`` file is served as JavaScript and never mistaken for an HTML document.
GOOGLE_MIME_MAP = {
    "text/html": "text/html; charset=utf-8",
    "application/xhtml+xml": "application/xhtml+xml; charset=utf-8",
    "text/css": "text/css; charset=utf-8",
    "text/javascript": "text/javascript; charset=utf-8",
    "application/javascript": "text/javascript; charset=utf-8",
    "application/x-javascript": "text/javascript; charset=utf-8",
    "application/ecmascript": "text/javascript; charset=utf-8",
    "text/plain": "text/plain; charset=utf-8",
    "text/markdown": "text/plain; charset=utf-8",
    "text/csv": "text/csv; charset=utf-8",
    "application/json": "application/json; charset=utf-8",
    "application/manifest+json": "application/manifest+json; charset=utf-8",
    "application/xml": "application/xml; charset=utf-8",
    "text/xml": "application/xml; charset=utf-8",
    "application/rss+xml": "application/rss+xml; charset=utf-8",
    "application/atom+xml": "application/atom+xml; charset=utf-8",
    "image/svg+xml": "image/svg+xml",
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
    "image/avif": "image/avif",
    "image/bmp": "image/bmp",
    "image/tiff": "image/tiff",
    "image/x-icon": "image/x-icon",
    "image/vnd.microsoft.icon": "image/x-icon",
    "font/woff": "font/woff",
    "font/woff2": "font/woff2",
    "font/ttf": "font/ttf",
    "font/otf": "font/otf",
    "application/font-woff": "font/woff",
    "application/x-font-woff": "font/woff",
    "application/x-font-ttf": "font/ttf",
    "application/vnd.ms-fontobject": "application/vnd.ms-fontobject",
    "application/pdf": "application/pdf",
    "application/wasm": "application/wasm",
    "application/zip": "application/zip",
    "application/gzip": "application/gzip",
    "audio/mpeg": "audio/mpeg",
    "audio/ogg": "audio/ogg",
    "audio/wav": "audio/wav",
    "audio/mp4": "audio/mp4",
    "video/mp4": "video/mp4",
    "video/webm": "video/webm",
    "video/quicktime": "video/quicktime",
}

#: Folder listings change rarely; cache them in memory for a warm instance only.
#: Errors are never cached, and nothing here is persisted anywhere.
LISTING_TTL = 300  # seconds
MAX_LISTING_ENTRIES = 5000
MAX_CACHE_ENTRIES = 512

#: Bounds for the (possibly large) folder-view HTML and for caller manifests.
MAX_EMBED_BYTES = 3 * 1024 * 1024
MAX_MANIFEST_ENTRIES = 5000

#: ``resourceKey`` values are short opaque tokens; anything else is discarded.
RESOURCE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")

_locks = threading.Lock()
_listing_cache = {}


# --------------------------------------------------------------------------- #
# Capabilities (used by the diagnostics endpoint; never contains secrets)
# --------------------------------------------------------------------------- #


def capabilities():
    """Describe what this backend can honestly do, with no credentials."""
    return {
        "mode": "no-key",
        "api_key_required": False,
        "oauth_required": False,
        "database_required": False,
        "external_backend_required": False,
        "direct_file": True,
        "folder_enumeration": "best-effort",
        "directory_browser": True,
        "manifest": True,
        "notes": (
            "Public Drive files are fetched directly from Google's public "
            "download endpoint. Public folder contents are read from Google's "
            "public folder-view response; Google does not always expose a "
            "folder's file list that way to anonymous callers, in which case "
            "the request fails with an explanation rather than faking success."
        ),
    }


# --------------------------------------------------------------------------- #
# Folder identifier handling
# --------------------------------------------------------------------------- #


def extract_folder_id(raw):
    """Accept a bare Drive id or any common Google Drive folder/file URL."""
    value = _shared._clean(raw)
    if not value:
        return ""

    # A full URL: pull the id out of the path or the query string.
    if "drive.google.com" in value or "docs.google.com" in value or "://" in value:
        try:
            parsed = urlparse(value)
        except Exception:
            return ""
        match = re.search(r"/folders/([A-Za-z0-9_-]{10,128})", parsed.path)
        if not match:
            match = re.search(r"/file/d/([A-Za-z0-9_-]{10,128})", parsed.path)
        if not match:
            # Docs, Sheets, Slides and friends use /<type>/d/<id>/edit.
            match = re.search(r"/d/([A-Za-z0-9_-]{10,128})", parsed.path)
        if match:
            return match.group(1)
        query = parse_qs(parsed.query)
        for key in ("id", "folderId"):
            if query.get(key):
                return _shared._clean(query[key][0])
        return ""

    # A bare id, possibly wrapped in quotes or with trailing punctuation left
    # over from a chat message or a spreadsheet cell.
    return value.strip().strip("<>\"',;").strip()


# --------------------------------------------------------------------------- #
# Tiny in-memory cache for folder listings
# --------------------------------------------------------------------------- #


def _cache_get(key):
    with _locks:
        hit = _listing_cache.get(key)
    if not hit:
        return None
    expires_at, value = hit
    if expires_at < time.time():
        return None
    return value


def _cache_set(key, value):
    with _locks:
        _listing_cache[key] = (time.time() + LISTING_TTL, value)
        # Keep the cache from growing without bound on a long-lived instance.
        if len(_listing_cache) > MAX_CACHE_ENTRIES:
            oldest = min(_listing_cache.items(), key=lambda item: item[1][0])[0]
            _listing_cache.pop(oldest, None)


# --------------------------------------------------------------------------- #
# Branded error tuples
#
# Each helper returns ``(status, title, message, why, fix)`` so callers can do
# ``html_outcome(*error)``. Raw Google error text is never shown to the user: it
# can be confusing, and echoing upstream payloads is a habit worth avoiding.
# --------------------------------------------------------------------------- #


def _invalid_folder(raw):
    return (
        400,
        "Invalid Google Drive id",
        "That does not look like a Google Drive folder or file id.",
        "An id is the long token in "
        + code_html("https://drive.google.com/drive/folders/<id>")
        + ". "
        + (code_html(raw) if raw else "No id was supplied."),
        "Open the item in Google Drive, copy its link, and paste it into the "
        "Drive tab of the generator.",
    )


def _invalid_path(message):
    return (
        400,
        "Invalid file path",
        html.escape(str(message)),
        "The path contained a segment that is never valid in a URL.",
        "Link to a real file inside the folder instead.",
    )


def _too_deep():
    return (
        400,
        "Path is too deep",
        "Drive paths are resolved one public request per level, so "
        + code_html(str(MAX_DRIVE_DEPTH))
        + " levels is the limit.",
        "Deeply nested paths are almost always a mistake.",
        "Link to the file using a path inside the folder's first levels.",
    )


def _enumeration_blocked(detail=""):
    return (
        403,
        "Google Drive resource unavailable",
        "This folder may not be publicly accessible, or Google may require "
        "authenticated/API access to enumerate its contents.",
        "WebProxyLive reads a public Drive folder through Google's public "
        "folder-view response and needs no API key, OAuth, database or extra "
        "backend. Google does not always expose a folder's file list that way "
        "to anonymous callers."
        + (" " + detail if detail else ""),
        "Make sure the Drive folder is set to "
        + code_html("Anyone with the link")
        + " \u2192 "
        + code_html("Viewer")
        + ", then reload. If it is already public, link a specific file with "
        + code_html("/drive/<folder-id>/<file-id>")
        + ", open a subfolder directly, or supply a "
        + code_html("?manifest=")
        + " of known file ids.",
    )


def _empty_folder():
    return (
        404,
        "This Google Drive folder is empty",
        "The folder is public and was read successfully, but it contains no "
        "files or subfolders.",
        "A Drive folder has no page of its own, so WebProxyLive needs at least "
        "one file (normally " + code_html("index.html") + ") to serve.",
        "Upload your website's files into the folder and reload.",
    )


def _no_such_file(display):
    return (
        404,
        "File not found in this Google Drive folder",
        "The folder is readable, but "
        + code_html(display or "index.html")
        + " is not inside it.",
        "Drive paths are resolved one segment at a time against the folder's "
        "real item list, so the name or its nesting did not match.",
        "Check the spelling and the folder nesting, and make sure the file sits "
        "in the folder you opened.",
    )


def _unsupported_workspace(name):
    return (
        415,
        "Google Workspace files cannot be served",
        code_html(name)
        + " is a Google Docs/Sheets/Slides item, not a static file.",
        "Those editors store their content in Google's own format, so there is "
        "no HTML/CSS/JS file to hand to a browser. WebProxyLive only serves real "
        "uploaded files.",
        "Export the document to HTML (File, Download, Web page) and upload the "
        "exported file to the folder, then refresh.",
    )


def _download_blocked(name):
    return (
        403,
        "Google blocked the public download",
        code_html(name or "That file")
        + " is in the folder, but Google refused to hand over its bytes.",
        "Drive serves link-shared files through its public download endpoint, "
        "and that endpoint occasionally answers with an interstitial page "
        "instead of the file.",
        "Open the file once in Google Drive and allow the download, then retry. "
        "Keeping a file small (a few megabytes) also avoids Drive's "
        "confirmation page.",
    )


def _rate_limited():
    return (
        429,
        "Google Drive is rate limiting this site",
        "Google temporarily refused more Drive requests from this deployment.",
        "Anonymous Drive access is rate limited per client.",
        "Wait a moment and reload.",
    )


def _timeout():
    return (
        504,
        "Google Drive request timed out",
        "Google did not answer the Drive request in time.",
        "Serverless functions have a hard time budget, and Drive occasionally "
        "responds slowly to anonymous requests.",
        "Reload in a moment. If it keeps timing out, the folder may be too "
        "large to enumerate this way.",
    )


def _unavailable(detail="upstream request failed"):
    return (
        502,
        "Google Drive is unavailable",
        "The service could not reach Google Drive. Please try again shortly.",
        detail + ".",
        "Retry in a few seconds.",
    )


def _bad_response():
    return (
        502,
        "Google Drive returned an unexpected response",
        "The Drive reply could not be read.",
        "An upstream reply was not the shape Google's public endpoints "
        "normally return.",
        "Retry in a few seconds.",
    )


def _too_large():
    return (
        413,
        "File too large",
        "That file exceeds the maximum size this proxy will serve.",
        "Files above " + code_html("25 MiB") + " are not proxied.",
        "Keep individual site assets smaller, or link the large file directly.",
    )


# --------------------------------------------------------------------------- #
# Public endpoint URLs (all constructed, never user supplied)
# --------------------------------------------------------------------------- #


def _embedded_url(folder_id):
    """Google's public folder-view response for one folder (Layer 2)."""
    return (
        DRIVE_FOLDER_VIEW_URL
        + "?"
        + urlencode({"id": folder_id}, quote_via=quote)
        + "#list"
    )


def _content_url(file_id, confirm="", uuid=""):
    """Google's public single-file download endpoint (Layer 1)."""
    params = {"export": "download", "id": file_id}
    if confirm:
        params["confirm"] = confirm
    if uuid:
        params["uuid"] = uuid
    return DRIVE_UC_URL + "?" + urlencode(params, quote_via=quote)


def _thumbnail_url(file_id):
    """Google's public thumbnail/asset proxy (Layer 3)."""
    return (
        DRIVE_THUMBNAIL_URL
        + "?"
        + urlencode({"id": file_id, "sz": "w2000"}, quote_via=quote)
    )


def _file_view_url(file_id):
    return DRIVE_FILE_VIEW_URL + "/" + quote(file_id, safe="") + "/view"


def _google_fetch(url, extra_headers=None):
    return fetch_url(
        url,
        allowed_hosts=DRIVE_ALLOWED_HOSTS,
        allowed_suffixes=DRIVE_ALLOWED_HOST_SUFFIXES,
        extra_headers=extra_headers,
    )


# --------------------------------------------------------------------------- #
# Parsing Google's public folder view
# --------------------------------------------------------------------------- #

_ENTRY_ID_RE = re.compile(r'id="entry-([A-Za-z0-9_-]{10,128})"')
_FLIP_TITLE_RE = re.compile(r'class="flip-entry-title"[^>]*>(.*?)<', re.S)
_IMG_ALT_RE = re.compile(r'<img[^>]*\balt="([^"]*)"', re.S)
_EMPTY_MARKERS = ("empty-folder-holder", "There are no items")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+(?:property|name)="(?:og:)?title"[^>]+content="([^"]*)"', re.I
)
_CONFIRM_RE = re.compile(r"[?&]confirm=([0-9A-Za-z_.\-]{1,64})")
_UUID_RE = re.compile(r"[?&]uuid=([0-9A-Za-z_.\-]{1,128})")
_HTML_TYPE_MARKERS = ("text/html", "application/xhtml")


def _clean_name(raw):
    """Return a safe display/match name, or an empty string if unusable."""
    name = "".join(ch for ch in str(raw or "") if ch == "\t" or ord(ch) >= 32)
    return name.strip()


def _workspace_mime_in(text):
    for mime in WORKSPACE_MIMES:
        if mime in text:
            return mime
    return ""


def _parse_embedded_listing(text, parent_id):
    """Parse Google's public folder-view HTML into entries.

    Returns ``None`` when the response is not a usable listing (so the caller
    can report an honest failure), a list (possibly empty) otherwise.
    """
    if "flip-entry" not in text:
        # No item markup at all: an empty folder is reported explicitly by
        # Google, anything else means we were not given a folder listing.
        if any(marker in text for marker in _EMPTY_MARKERS):
            return []
        return None

    matches = list(_ENTRY_ID_RE.finditer(text))
    if not matches:
        return None

    entries = []
    for index, match in enumerate(matches):
        if len(entries) >= MAX_LISTING_ENTRIES:
            break
        file_id = match.group(1)
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]

        name = ""
        title_match = _FLIP_TITLE_RE.search(block)
        if title_match:
            name = _clean_name(html.unescape(title_match.group(1)))
        if not name:
            alt_match = _IMG_ALT_RE.search(block)
            if alt_match:
                name = _clean_name(html.unescape(alt_match.group(1)))
        if not name:
            # A named entry we cannot read is not something to guess about.
            continue

        is_folder = ("/drive/folders/" + file_id) in block or FOLDER_MIME in block
        workspace_mime = "" if is_folder else _workspace_mime_in(block)
        entries.append(
            {
                "name": name,
                "id": file_id,
                "mime": workspace_mime,
                "is_folder": is_folder,
                "resource_key": "",
                "parent": parent_id,
                "workspace": bool(workspace_mime),
            }
        )

    if not entries:
        return None
    return entries


def _embedded_children(folder_id):
    """Return ``(entries, error_tuple)`` for a folder's public item list."""
    cache_key = "embed:" + folder_id
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached, None

    result = _google_fetch(_embedded_url(folder_id))

    if result.status == 0:
        return None, _timeout()
    if result.status in (401, 403):
        return None, _enumeration_blocked()
    if result.status == 404:
        return None, _enumeration_blocked(
            "Google answered 404 for the folder id, which means the id is "
            "wrong, the folder was deleted, or it is not shared publicly."
        )
    if result.status == 429:
        return None, _rate_limited()
    if result.status == 413:
        return None, _too_large()
    if result.status != 200:
        return None, _unavailable("Google returned HTTP " + str(result.status))

    text = result.body[:MAX_EMBED_BYTES].decode("utf-8", "replace")
    entries = _parse_embedded_listing(text, folder_id)
    if entries is None:
        return None, _enumeration_blocked(
            "Google's folder view did not include a readable file list."
        )

    _cache_set(cache_key, entries)
    return entries, None


# --------------------------------------------------------------------------- #
# Item name probing (Layer 1 / 3 only)
# --------------------------------------------------------------------------- #


def _probe_file_name(file_id):
    """Best-effort public file name via Google's file page. ``""`` if unknown."""
    result = _google_fetch(_file_view_url(file_id))
    if result.status != 200:
        return ""
    text = result.body[:262144].decode("utf-8", "replace")
    if _is_interstitial_text(text):
        return ""

    name = ""
    title_match = _TITLE_RE.search(text)
    if title_match:
        name = _clean_name(html.unescape(title_match.group(1)))
    if not name or name.lower() in ("google drive", "drive"):
        og_match = _OG_TITLE_RE.search(text)
        if og_match:
            name = _clean_name(html.unescape(og_match.group(1)))

    for suffix in (" - Google Drive", " - Google Docs", " - Google Sheets"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
    if not name or name.lower() in ("google drive", "drive"):
        return ""
    return name


def _is_interstitial_text(lowered_text):
    """True when Google answered with one of its own gate/interstitial pages."""
    if "google drive" not in lowered_text and "drive.google.com" not in lowered_text:
        return False
    markers = (
        "virus scan",
        "download anyway",
        "cannot be scanned",
        "can't scan this file",
        "you need access",
        "request access",
        "no longer available",
        "servicelogin",
        "sign in to continue",
    )
    return any(marker in lowered_text for marker in markers)


def _is_interstitial(result):
    content_type = (result.headers.get("content-type") or "").lower()
    if not any(marker in content_type for marker in _HTML_TYPE_MARKERS):
        return False
    return _is_interstitial_text(result.body[:131072].decode("utf-8", "replace").lower())


def _confirm_token(result):
    """Extract Drive's "download anyway" token from a confirmation page."""
    content_type = (result.headers.get("content-type") or "").lower()
    if not any(marker in content_type for marker in _HTML_TYPE_MARKERS):
        return ()
    text = result.body[:131072].decode("utf-8", "replace")
    confirm = _CONFIRM_RE.search(text)
    if not confirm:
        return ()
    uuid_match = _UUID_RE.search(text)
    return (confirm.group(1), uuid_match.group(1) if uuid_match else "")


# --------------------------------------------------------------------------- #
# Layer 1 / 3 - fetching one public file by id
# --------------------------------------------------------------------------- #


def _download_direct(entry):
    """Fetch a public file's bytes with no credentials.

    Returns ``(UpstreamResult, None)`` on success or ``(None, error_tuple)``.
    """
    headers = {}
    if entry.get("resource_key"):
        # Link-shared items may need their resource key echoed back.
        headers["X-Goog-Drive-Resource-Keys"] = (
            entry["id"] + "/" + entry["resource_key"]
        )

    result = _google_fetch(_content_url(entry["id"]), extra_headers=headers)

    if result.status == 200:
        token = _confirm_token(result)
        if token:
            # Drive answered with its "could not scan / download anyway" page.
            result = _google_fetch(
                _content_url(entry["id"], confirm=token[0], uuid=token[1]),
                extra_headers=headers,
            )

    if result.status in (403, 404):
        # The download endpoint refused. For images and other assets Drive
        # often still serves the bytes through its public thumbnail proxy.
        alternate = _google_fetch(_thumbnail_url(entry["id"]))
        if alternate.status == 200 and alternate.body and not _is_interstitial(
            alternate
        ):
            result = alternate

    if result.status != 200:
        return None, _content_error(entry, result)
    if _is_interstitial(result):
        return None, _download_blocked(entry["name"])
    return result, None


def _content_error(entry, result):
    """Map a failed public download onto a branded error tuple."""
    if result.status == 0:
        return _timeout()
    if result.status == 413:
        return _too_large()
    if entry.get("workspace"):
        return _unsupported_workspace(entry["name"])
    if result.status in (403, 404):
        if "." not in (entry["name"] or ""):
            # An extension-less item is usually a Workspace document.
            return _unsupported_workspace(entry["name"])
        if result.status == 403:
            return _download_blocked(entry["name"])
        return _no_such_file(entry["name"])
    if result.status in (401, 429):
        return _rate_limited()
    if result.status == 504:
        return _timeout()
    return _unavailable("Google returned HTTP " + str(result.status))


def _content_type_for(entry):
    """Pick the Content-Type from a known MIME type, else from the file name."""
    mapped = GOOGLE_MIME_MAP.get(entry.get("mime") or "")
    if mapped:
        return mapped
    # The extension table never guesses text/html for an unknown file, so a
    # mislabelled upstream type can never turn source code into markup.
    return content_type_for(entry["name"] or "")


def _serve_entry(entry):
    """Serve one resolved, non-folder entry; never guesses a folder is a file."""
    if entry.get("workspace"):
        return html_outcome(*_unsupported_workspace(entry["name"]))
    if not entry.get("name"):
        entry["name"] = _probe_file_name(entry["id"])
    result, error = _download_direct(entry)
    if result is None:
        return html_outcome(*error)
    return file_outcome(
        result,
        entry["name"] or "file",
        extra_headers={"Content-Type": _content_type_for(entry)},
    )


# --------------------------------------------------------------------------- #
# Layer 2 - resolving a path inside a public folder
# --------------------------------------------------------------------------- #


def _find_child(children, name, parent_id):
    """Return the child called ``name``, deterministically or not at all.

    An exact, case-sensitive match always wins. Otherwise a case-insensitive
    match is used, and ties are broken so the result never depends on the order
    Google happened to return: the entry whose recorded parent is the folder
    being searched is preferred, then the smallest id. Because every candidate
    comes from *this* folder's own listing, a file from another folder can never
    be resolved by accident.
    """
    matches = [child for child in children if child["name"] == name]
    if not matches:
        lowered = name.lower()
        matches = [child for child in children if child["name"].lower() == lowered]
    if not matches:
        return None
    matches.sort(
        key=lambda child: (0 if child.get("parent") == parent_id else 1, child["id"])
    )
    return matches[0]


def _follow_path(folder_id, segments, root_entries):
    """Walk ``segments`` down from ``folder_id`` using public folder views.

    Returns ``(entry, found, error_tuple)``: the final entry (a folder is
    returned as an entry too), whether the path existed at all, and a branded
    error tuple when Google itself failed.
    """
    current_id = folder_id
    children = root_entries
    if not segments:
        return (
            {
                "name": "",
                "id": folder_id,
                "mime": FOLDER_MIME,
                "is_folder": True,
                "resource_key": "",
                "parent": "",
                "workspace": False,
            },
            True,
            None,
        )

    for index, segment in enumerate(segments):
        found = _find_child(children, segment, current_id)
        if found is None:
            return None, False, None
        if index == len(segments) - 1:
            return found, True, None
        if not found["is_folder"]:
            # An intermediate segment resolved to a file: the path is bogus.
            return None, False, None
        children, error = _embedded_children(found["id"])
        if error:
            return None, False, error
        current_id = found["id"]
    return None, False, None


# --------------------------------------------------------------------------- #
# Caller supplied manifest (zero credentials)
# --------------------------------------------------------------------------- #


def _manifest_key(path):
    try:
        segments = normalise_path(path)
    except ValueError:
        return ""
    return "/".join(segments)


def _manifest_entry(table, key):
    item = table.get(key)
    if item is None:
        item = table.get(key.lower())
    if item is None:
        return None
    return {
        "name": item["name"] or (key.split("/")[-1] if key else "file"),
        "id": item["id"],
        "mime": item["mime"],
        "is_folder": False,
        "resource_key": item["resource_key"],
        "parent": "",
        "workspace": item["mime"] in WORKSPACE_MIMES,
    }


def _manifest_table(payload):
    """Turn a decoded manifest payload into a ``path -> item`` table."""
    table = {}
    entries = []

    if isinstance(payload, dict):
        if isinstance(payload.get("files"), list):
            entries = payload["files"]
        else:
            for path, value in payload.items():
                if isinstance(value, str):
                    entries.append({"path": path, "id": value})
                elif isinstance(value, dict):
                    record = dict(value)
                    record.setdefault("path", path)
                    entries.append(record)
    elif isinstance(payload, list):
        entries = payload

    for record in entries:
        if len(table) >= MAX_MANIFEST_ENTRIES * 2:
            break
        if not isinstance(record, dict):
            continue
        path = str(record.get("path") or record.get("name") or "")
        file_id = str(record.get("id") or "").strip()
        key = _manifest_key(path)
        if not key or not valid_drive_id(file_id):
            continue
        resource_key = str(record.get("resourceKey") or "").strip()
        if not RESOURCE_KEY_RE.match(resource_key):
            resource_key = ""
        item = {
            "name": _clean_name(record.get("name") or key.split("/")[-1]),
            "id": file_id,
            "mime": str(record.get("mime") or record.get("mimeType") or "")
            .strip()
            .lower(),
            "resource_key": resource_key,
        }
        table[key] = item
        table.setdefault(key.lower(), item)

    return table


def _manifest_pairs(text):
    """Parse a compact ``path:id;path2:id2`` manifest."""
    entries = []
    for chunk in re.split(r"[;\n]+", text):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        path, _, file_id = chunk.rpartition(":")
        entries.append({"path": path.strip(), "id": file_id.strip()})
    return entries


def _load_manifest(raw):
    """Load a caller manifest. Returns ``(table, error_tuple)``.

    Supported forms, all credential free:

    * inline JSON (``{"index.html": "<id>", ...}`` or a list of records),
    * a compact ``index.html:<id>;css/style.css:<id>`` string,
    * the id of a public Drive file whose contents are the JSON manifest.
    """
    value = _shared._clean(raw)
    if not value:
        return {}, None

    text = value
    if valid_drive_id(value):
        result = _google_fetch(_content_url(value))
        if result.status != 200 or _is_interstitial(result):
            return None, _enumeration_blocked(
                "The manifest file id could not be read as a public Drive file."
            )
        text = result.body[:MAX_EMBED_BYTES].decode("utf-8", "replace")

    stripped = text.strip()
    payload = None
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except Exception:
            return None, (
                400,
                "Invalid manifest",
                "The manifest was not valid JSON.",
                "A manifest maps each path in the site to a known Drive file id.",
                "Provide "
                + code_html('{"index.html": "<file-id>"}')
                + " or a " + code_html("path:<file-id>") + " list.",
            )
    else:
        payload = _manifest_pairs(stripped)

    table = _manifest_table(payload)
    if not table:
        return None, (
            400,
            "Manifest contained no usable entries",
            "None of the manifest entries had both a path and a valid Drive id.",
            "Each entry needs a site path and the 10+ character id of a public "
            "Drive file.",
            "Check the ids and rebuild the URL.",
        )
    return table, None


def _manifest_candidates(segments, directory_request):
    """The manifest keys a request should try, in order."""
    base = "/".join(segments)
    keys = []
    if directory_request:
        for name in INDEX_CANDIDATES:
            keys.append(base + "/" + name if base else name)
    else:
        keys.append(base)
        if not segments or "." not in segments[-1]:
            for name in INDEX_CANDIDATES:
                keys.append(base + "/" + name if base else name)
    return keys


# --------------------------------------------------------------------------- #
# Directory browser
# --------------------------------------------------------------------------- #


def _type_label(entry):
    if entry["is_folder"]:
        return "Folder"
    if entry.get("mime"):
        return entry["mime"]
    name = entry["name"]
    ext = os.path.splitext(name)[1].lower()
    return ext[1:].upper() + " file" if ext else "File"


def _directory_document(root_id, display, entries):
    """Render a professional listing for a public folder with no index page.

    ``root_id`` is the folder id from the request and ``display`` is the path
    that was requested inside it, so every link is built from the URL the visitor
    actually used and can be followed without re-deriving the folder chain.
    """
    prefix = "/drive/" + root_id + "/"
    if display:
        prefix += "/".join(quote(part, safe="") for part in display.split("/")) + "/"

    ordered = sorted(
        entries, key=lambda item: (not item["is_folder"], item["name"].lower())
    )

    rows = []
    for entry in ordered:
        href = prefix + quote(entry["name"], safe="")
        if entry["is_folder"]:
            href += "/"
        label = entry["name"] + ("/" if entry["is_folder"] else "")
        rows.append(
            "<tr>"
            '<td><a href="' + html.escape(href, quote=True) + '">'
            + html.escape(label)
            + "</a></td>"
            "<td>" + html.escape(_type_label(entry)) + "</td>"
            '<td class="dim">&mdash;</td>'
            "</tr>"
        )

    location = "/drive/" + root_id + ("/" + display if display else "") + "/"
    count = len(ordered)
    noun = "item" if count == 1 else "items"

    document = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Contents of __LOCATION__ &middot; __SERVICE__</title>
<meta name="robots" content="noindex">
<style>
  :root { color-scheme: dark light; --bg:#05080f; --panel:rgba(15,23,42,.72);
    --border:rgba(148,163,184,.22); --text:#e2e8f0; --muted:#94a3b8;
    --accent:#38bdf8; }
  @media (prefers-color-scheme: light) { :root { --bg:#f8fafc;
    --panel:rgba(255,255,255,.9); --border:rgba(15,23,42,.14);
    --text:#0f172a; --muted:#475569; --accent:#0284c7; } }
  * { box-sizing:border-box; }
  body { margin:0; padding:48px 20px; background:var(--bg); color:var(--text);
    font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  .box { max-width:820px; margin:0 auto; background:var(--panel);
    border:1px solid var(--border); border-radius:16px; padding:32px;
    box-shadow:0 24px 60px rgba(2,6,23,.35); }
  .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px;
    color:var(--accent); font-weight:700; margin:0 0 8px; }
  h1 { font-size:22px; margin:0 0 6px; word-break:break-word; }
  .meta { color:var(--muted); margin:0 0 22px; font-size:14px; }
  code { background:rgba(148,163,184,.16); padding:2px 6px; border-radius:6px;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:11px 10px; border-bottom:1px solid var(--border);
    font-size:14px; }
  th { color:var(--muted); font-weight:600; text-transform:uppercase;
    letter-spacing:.08em; font-size:11px; }
  td.dim { color:var(--muted); }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  .note { margin-top:24px; padding-top:18px; border-top:1px solid var(--border);
    color:var(--muted); font-size:13px; }
</style>
</head>
<body>
  <div class="box">
    <p class="eyebrow">Google Drive folder</p>
    <h1>Contents of <code>__LOCATION__</code></h1>
    <p class="meta">__COUNT__ __NOUN__ &middot; served without an API key, OAuth,
      database or extra backend.</p>
    <table>
      <thead><tr><th>Name</th><th>Type</th><th>Size</th></tr></thead>
      <tbody>
__ROWS__
      </tbody>
    </table>
    <p class="note">There is no <code>index.html</code> in this folder, so this
      directory listing is shown instead. Upload an <code>index.html</code> to
      serve it as a website, or open any file above directly. Sizes are not
      published by Google's public folder view, so they are shown as
      &ldquo;&mdash;&rdquo; when unknown.</p>
  </div>
</body>
</html>
"""
    body = (
        document.replace("__SERVICE__", html.escape(_shared.SERVICE_NAME))
        .replace("__LOCATION__", html.escape(location))
        .replace("__COUNT__", str(count))
        .replace("__NOUN__", noun)
        .replace("__ROWS__", "\n".join("        " + row for row in rows))
    )

    headers = _shared.base_headers()
    headers["Content-Type"] = "text/html; charset=utf-8"
    headers["Cache-Control"] = "no-store"
    return _shared.Outcome(200, body.encode("utf-8"), headers)


# --------------------------------------------------------------------------- #
# Resolution (shared by the Vercel handler and the Flask launcher)
# --------------------------------------------------------------------------- #


def _directory_target(root_id, root_entries, segments):
    """Resolve ``segments`` to a folder's own listing, or ``(None, None)``."""
    current_id = root_id
    children = root_entries
    for segment in segments:
        found = _find_child(children, segment, current_id)
        if found is None or not found["is_folder"]:
            return None, None
        nested, error = _embedded_children(found["id"])
        if error:
            return None, None
        current_id, children = found["id"], nested
    return current_id, children


def resolve(folder_id, file_path, method="GET", manifest=""):
    """Resolve a Google Drive request into a :class:`_shared.Outcome`."""
    raw_folder = _shared._clean(folder_id)
    folder_id = extract_folder_id(raw_folder)
    file_path = _shared._clean(file_path)

    if not folder_id or not valid_drive_id(folder_id):
        return html_outcome(*_invalid_folder(raw_folder))

    try:
        segments = normalise_path(file_path)
    except ValueError as exc:
        return html_outcome(*_invalid_path(exc))

    if len(segments) > MAX_DRIVE_DEPTH:
        return html_outcome(*_too_deep())

    display = "/".join(segments)
    directory_request = not segments or file_path.endswith("/")

    # --- Manifest (caller supplied ids; no key, no database) --------------- #
    if _shared._clean(manifest):
        table, manifest_error = _load_manifest(manifest)
        if manifest_error:
            return html_outcome(*manifest_error)
        if table:
            for key in _manifest_candidates(segments, directory_request):
                entry = _manifest_entry(table, key)
                if entry is not None:
                    return _serve_entry(entry)

    # --- Layer 2: public folder discovery (no credentials) ---------------- #
    entries, list_error = _embedded_children(folder_id)
    if list_error is None:
        # Which names to try, in order, and the segments that reach each one.
        plan = []
        if directory_request:
            for name in INDEX_CANDIDATES:
                plan.append(segments + [name])
        else:
            plan.append(segments)
            if not segments or "." not in segments[-1]:
                for name in INDEX_CANDIDATES:
                    plan.append(segments + [name])

        chosen = None
        for candidate in plan:
            entry, found, error = _follow_path(folder_id, candidate, entries)
            if error:
                return html_outcome(*error)
            # Only a real file can be served; a folder is not a page.
            if found and entry is not None and not entry["is_folder"]:
                chosen = entry
                break

        if chosen is not None:
            return _serve_entry(chosen)

        # Nothing servable: browse the folder, or explain precisely why not.
        directory_id, directory_entries = _directory_target(
            folder_id, entries, segments
        )
        if directory_id is not None:
            if not directory_entries:
                return html_outcome(*_empty_folder())
            # Build the listing from the requested path, not the resolved folder
            # id, so nested folders link back through their own URL.
            return _directory_document(folder_id, display, directory_entries)
        return html_outcome(*_no_such_file(display))

    # --- Layer 1 / 3: direct public file fallback -------------------------- #
    # The folder could not be enumerated. If the id (or a single path segment)
    # is itself a public file, serve that file directly instead of giving up.
    direct_id = ""
    if not segments:
        direct_id = folder_id
    elif len(segments) == 1 and valid_drive_id(segments[0]):
        direct_id = segments[0]

    if direct_id:
        probe = {
            "name": "",
            "id": direct_id,
            "mime": "",
            "is_folder": False,
            "resource_key": "",
            "parent": "",
            "workspace": False,
        }
        probe["name"] = _probe_file_name(direct_id)
        if probe["name"]:
            result, error = _download_direct(probe)
            if result is not None:
                return file_outcome(
                    result,
                    probe["name"],
                    extra_headers={"Content-Type": _content_type_for(probe)},
                )
            # A 415 here only means "the probed name has no extension": it is a
            # guess, not evidence of a Workspace document. Reporting it would
            # hide the real reason the id could not be served, so fall through
            # to the honest enumeration failure instead.
            if not error or error[0] != 415:
                return html_outcome(*error)

    # --- Layer 4: honest, branded failure --------------------------------- #
    return html_outcome(*list_error)


# --------------------------------------------------------------------------- #
# Vercel entrypoint
# --------------------------------------------------------------------------- #


class handler(ProxyHandler):
    """Vercel entrypoint: file-based Python function handler."""

    source_label = "Google Drive"

    def _parse_target(self):
        """Return (folder, file_path, manifest) from the request."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)

        def first(name):
            values = query.get(name)
            return _shared._clean(values[0]) if values else ""

        folder = first("folder")
        file_path = first("file")
        manifest = first("manifest")

        # Fallback: allow direct /api/drive/<folder>/<path> or /drive/<folder>/<path>.
        if not folder:
            cleaned = _shared._clean(parsed.path)
            segments = [s for s in cleaned.split("/") if s]
            if segments and segments[0] == "api":
                segments = segments[1:]
            if segments and segments[0] == "drive":
                segments = segments[1:]
            if segments:
                folder = segments[0]
                if len(segments) > 1:
                    file_path = "/".join(segments[1:])
                    if parsed.path.endswith("/"):
                        file_path += "/"

        return folder, file_path, manifest

    def _serve(self, method):
        try:
            folder, file_path, manifest = self._parse_target()
            # Public Google endpoints are requested with GET; for a HEAD request
            # the body is simply not written out by _respond().
            outcome = resolve(folder, file_path, method="GET", manifest=manifest)

            etag = outcome.headers.get("ETag")
            if outcome.status == 200 and etag:
                client_etag = self.headers.get("If-None-Match")
                if client_etag and client_etag.strip() == etag.strip():
                    self._respond(304, b"", dict(outcome.headers), method)
                    return

            self._respond(outcome.status, outcome.body, outcome.headers, method)

        except Exception:  # pragma: no cover - last resort
            try:
                self._respond_error(
                    500,
                    "Unexpected server error",
                    "Something went wrong while handling the request.",
                    method,
                )
            except Exception:
                pass
