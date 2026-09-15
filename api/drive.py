"""WebProxyLive - Google Drive source proxy (Vercel serverless entrypoint).

This module serves a *public* Google Drive folder as a static website:

    /drive/<folder-id>/                 ->  index.html / index.htm at the root
    /drive/<folder-id>/<path>           ->  a file or nested folder inside it

How a folder is read
--------------------
Google Drive has no HTTP directory listing. A public folder ("anyone with the
link") can be listed in one of two ways:

* **Without any key** (the default): the public ``embeddedfolderview`` page is
  parsed for entry links. This needs no API key and no account, which is the
  behaviour WebProxyLive promises.
* **With an optional key**: if ``DRIVE_API_KEY`` or ``GOOGLE_API_KEY`` is set in
  the environment, the Drive v3 REST API is used instead. It is more precise but
  is never required.

Files are then streamed through this domain using Drive's public download
endpoint, so relative ``css``/``js``/``img`` references keep working.

Security model
--------------
The folder id and every path segment are validated with the same strict rules
as the GitHub proxy. Each path segment is resolved *inside the selected
folder*, so a request can never escape it: a name is only ever looked up in the
listing of the folder that the previous, already-resolved segment pointed at.
No user supplied URL is ever fetched, so SSRF and path traversal are impossible.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlparse

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
    fetch_url,
    file_outcome,
    html_outcome,
    normalise_path,
    valid_drive_id,
)

# --------------------------------------------------------------------------- #
# Endpoints (all on Google owned hosts)
# --------------------------------------------------------------------------- #

DRIVE_LIST_URL = "https://drive.google.com/embeddedfolderview?id={id}#list"
DRIVE_DOWNLOAD_URL = "https://drive.google.com/uc?export=download&id={id}"
DRIVE_API_URL = "https://www.googleapis.com/drive/v3/files"

FOLDER_MIME = "application/vnd.google-apps.folder"

#: Folder listings change rarely; cache them in memory for a warm instance.
LISTING_TTL = 300  # seconds
MAX_LISTING_ENTRIES = 5000

#: Drive wraps large downloads in an HTML interstitial. We detect it by content
#: so the caller can return a real explanation instead of a broken page.
_INTERSTITIAL_MARKERS = (
    b"Virus scan warning",
    b"Google Drive - Quota exceeded",
    b"can't view or download this file at this time",
    b"confirm=",
)

_FOLDER_HREF_RE = re.compile(r"/drive/folders/([A-Za-z0-9_-]{10,128})")
_FILE_HREF_RE = re.compile(r"/file/d/([A-Za-z0-9_-]{10,128})")
_ENTRY_RE = re.compile(
    r'<a[^>]*class="flip-entry-title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

_locks = threading.Lock()
_listing_cache = {}


# --------------------------------------------------------------------------- #
# Folder identifier handling
# --------------------------------------------------------------------------- #


def extract_folder_id(raw):
    """Accept a bare folder id or any common Google Drive folder URL."""
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
        if match:
            return match.group(1)
        query = parse_qs(parsed.query)
        for key in ("id", "folderId"):
            if query.get(key):
                return _shared._clean(query[key][0])
        return ""

    # A bare id, possibly with trailing punctuation pasted from a chat message.
    return value.strip().strip("<>\"'")


# --------------------------------------------------------------------------- #
# Folder listing
# --------------------------------------------------------------------------- #


def _cache_get(key):
    with _locks:
        hit = _listing_cache.get(key)
    if not hit:
        return None
    expires_at, entries = hit
    if expires_at < time.time():
        return None
    return entries


def _cache_set(key, entries):
    with _locks:
        _listing_cache[key] = (time.time() + LISTING_TTL, entries)
        # Keep the cache from growing without bound on a long-lived instance.
        if len(_listing_cache) > 512:
            oldest = min(_listing_cache.items(), key=lambda item: item[1][0])[0]
            _listing_cache.pop(oldest, None)


def _unknown_folder_error(text):
    """Map an unrecognised listing page to a friendly error tuple."""
    lowered = (text or "").lower()
    if "you need permission" in lowered or "request access" in lowered:
        return (
            403,
            "This Drive folder is not public",
            "Google Drive refused to open the folder without permission.",
            "The folder is not shared publicly, so its files cannot be read anonymously.",
            "In Google Drive, right-click the folder, choose Share, then set General "
            "access to " + code_html("Anyone with the link") + ".",
        )
    if "not found" in lowered or "no longer exists" in lowered:
        return (
            404,
            "Drive folder not found",
            "That Drive folder could not be found.",
            "The folder id is wrong, or the folder was deleted.",
            "Re-copy the folder link from Google Drive and build the URL again.",
        )
    if "sign in" in lowered or "accounts.google.com" in lowered:
        return (
            403,
            "This Drive folder is not public",
            "Google asked for a sign-in, which means the folder is not shared publicly.",
            "Only folders shared with " + code_html("Anyone with the link")
            + " can be served without signing in.",
            "Change the folder's sharing settings, then try again.",
        )
    return None


def _parse_listing_html(text):
    """Return ``[(name, id, is_folder), ...]`` from an embedded folder view."""
    entries = []
    seen = set()
    for href, label in _ENTRY_RE.findall(text or ""):
        name = html.unescape(re.sub(r"<[^>]+>", "", label)).strip()
        if not name:
            continue
        folder_match = _FOLDER_HREF_RE.search(href)
        file_match = _FILE_HREF_RE.search(href)
        if folder_match:
            item_id, is_folder = folder_match.group(1), True
        elif file_match:
            item_id, is_folder = file_match.group(1), False
        else:
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        entries.append((name, item_id, is_folder))
        if len(entries) >= MAX_LISTING_ENTRIES:
            break
    return entries


def _list_via_html(folder_id):
    """Return ``(entries, error_tuple)`` using the key-free public listing."""
    url = DRIVE_LIST_URL.format(id=quote(folder_id, safe=""))
    result = fetch_url(
        url,
        method="GET",
        allowed_hosts=DRIVE_ALLOWED_HOSTS,
        allowed_suffixes=DRIVE_ALLOWED_HOST_SUFFIXES,
    )

    if result.status == 403:
        return None, (
            403,
            "This Drive folder is not public",
            "Google Drive would not list that folder.",
            "The folder is not shared with " + code_html("Anyone with the link") + ".",
            "Open the folder's Share settings and switch General access to "
            + code_html("Anyone with the link") + ".",
        )
    if result.status == 404:
        return None, (
            404,
            "Drive folder not found",
            "Google Drive could not find that folder.",
            "The folder id is wrong, or the folder was deleted.",
            "Re-copy the folder link and build the URL again.",
        )
    if result.status != 200:
        return None, (
            502,
            "Google Drive is unavailable",
            "The service could not reach Google Drive. Please try again shortly.",
            "The upstream request failed or timed out.",
            "Retry in a few seconds.",
        )

    text = result.body.decode("utf-8", "replace")
    entries = _parse_listing_html(text)
    if not entries:
        failure = _unknown_folder_error(text)
        if failure:
            return None, failure
    return entries, None


def _list_via_api(folder_id, api_key):
    """Return ``(entries, error_tuple)`` using the optional Drive v3 API."""
    query = quote("'" + folder_id + "' in parents and trashed = false", safe="")
    fields = quote("files(id,name,mimeType)", safe="(),")
    url = (
        DRIVE_API_URL
        + "?q="
        + query
        + "&fields="
        + fields
        + "&pageSize=1000&key="
        + quote(api_key, safe="")
    )
    result = fetch_url(
        url,
        method="GET",
        allowed_hosts=DRIVE_ALLOWED_HOSTS,
        allowed_suffixes=DRIVE_ALLOWED_HOST_SUFFIXES,
    )
    if result.status != 200:
        return None, (
            502,
            "Google Drive is unavailable",
            "The service could not reach Google Drive. Please try again shortly.",
            "The Drive API request failed or the API key was rejected.",
            "Retry shortly, or unset " + code_html("DRIVE_API_KEY")
            + " to use the key-free listing.",
        )

    try:
        payload = json.loads(result.body.decode("utf-8", "replace"))
    except Exception:
        return None, (
            502,
            "Google Drive is unavailable",
            "Google Drive returned an unexpected response.",
            "The Drive API response could not be read.",
            "Retry shortly.",
        )

    entries = []
    for item in payload.get("files", []):
        name = item.get("name") or ""
        item_id = item.get("id") or ""
        if not name or not item_id:
            continue
        entries.append((name, item_id, item.get("mimeType") == FOLDER_MIME))
        if len(entries) >= MAX_LISTING_ENTRIES:
            break
    return entries, None


def list_folder(folder_id):
    """Return ``(entries, error_tuple)`` for a folder id. Cached briefly."""
    cached = _cache_get(folder_id)
    if cached is not None:
        return cached, None

    api_key = os.environ.get("DRIVE_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        entries, error = _list_via_api(folder_id, api_key)
    else:
        entries, error = _list_via_html(folder_id)

    if error:
        return None, error
    _cache_set(folder_id, entries)
    return entries, None


def _find_entry(entries, name):
    """Look a name up in a listing, exact match first then case-insensitive."""
    for entry_name, entry_id, is_folder in entries:
        if entry_name == name:
            return entry_name, entry_id, is_folder
    lowered = name.lower()
    for entry_name, entry_id, is_folder in entries:
        if entry_name.lower() == lowered:
            return entry_name, entry_id, is_folder
    return None


def _resolve_folder(folder_id, segments):
    """Walk ``segments`` as folders. Returns ``(id, error_tuple)``.

    ``segments`` may be empty, in which case the starting folder is returned.
    Every segment is only ever looked up *inside* the folder reached so far,
    which is what makes escaping the selected folder impossible.
    """
    current = folder_id
    for segment in segments:
        entries, error = list_folder(current)
        if error:
            return None, error
        found = _find_entry(entries, segment)
        if not found or not found[2]:
            return None, None  # missing, or a file where a folder was expected
        current = found[1]
    return current, None


def _download(file_id):
    url = DRIVE_DOWNLOAD_URL.format(id=quote(file_id, safe=""))
    return fetch_url(
        url,
        method="GET",
        allowed_hosts=DRIVE_ALLOWED_HOSTS,
        allowed_suffixes=DRIVE_ALLOWED_HOST_SUFFIXES,
    )


def _looks_like_interstitial(result):
    if result.status != 200:
        return False
    head = bytes(result.body[:2048])
    return any(marker in head for marker in _INTERSTITIAL_MARKERS)


def _not_found(file_path):
    return html_outcome(
        404,
        "File not found in this Drive folder",
        "The folder is readable, but the requested file "
        + code_html(file_path or "index.html")
        + " is not inside it.",
        why="Drive paths are resolved one segment at a time, inside the selected "
        "folder. The name or its folder nesting did not match.",
        fix="Check the spelling and the folder nesting, and make sure "
        + code_html("index.html")
        + " sits directly in the folder you opened.",
    )


# --------------------------------------------------------------------------- #
# Resolution (shared by the Vercel handler and the Flask launcher)
# --------------------------------------------------------------------------- #


def resolve(folder_id, file_path, method="GET"):
    """Resolve a Google Drive request into a :class:`_shared.Outcome`."""
    raw_folder = folder_id
    folder_id = extract_folder_id(folder_id)
    file_path = _shared._clean(file_path)

    if not folder_id or not valid_drive_id(folder_id):
        return html_outcome(
            400,
            "Invalid Drive folder",
            "That does not look like a Google Drive folder id.",
            why="A folder id is the long token in "
            + code_html("https://drive.google.com/drive/folders/<id>")
            + ". "
            + (code_html(raw_folder) if raw_folder else "No folder was supplied."),
            fix="Open the folder in Google Drive, copy its link, and paste it into "
            "the Drive tab of the generator.",
        )

    try:
        segments = normalise_path(file_path)
    except ValueError as exc:
        return html_outcome(
            400,
            "Invalid file path",
            html.escape(str(exc)),
            why="The path contained a segment that is never valid in a URL.",
            fix="Link to a real file inside the folder instead.",
        )

    directory_request = not segments or file_path.endswith("/")

    # Work out which folder the requested name lives in, and what to try.
    candidates = []  # list of (folder_id, name)
    if directory_request:
        base_id, error = _resolve_folder(folder_id, segments)
        if error:
            return html_outcome(*error)
        if base_id is None:
            return _not_found(file_path)
        for name in INDEX_CANDIDATES:
            candidates.append((base_id, name))
    else:
        parent_id, error = _resolve_folder(folder_id, segments[:-1])
        if error:
            return html_outcome(*error)
        if parent_id is None:
            return _not_found(file_path)
        candidates.append((parent_id, segments[-1]))
        # Clean-URL support: /about -> /about/index.html
        if "." not in segments[-1]:
            sub_id, error = _resolve_folder(parent_id, [segments[-1]])
            if error:
                return html_outcome(*error)
            if sub_id is not None:
                for name in INDEX_CANDIDATES:
                    candidates.append((sub_id, name))

    chosen = None
    for candidate_folder, name in candidates:
        entries, error = list_folder(candidate_folder)
        if error:
            return html_outcome(*error)
        found = _find_entry(entries, name)
        if found and not found[2]:
            chosen = (found[0], found[1])
            break

    if chosen is None:
        return _not_found(file_path)

    served_name, file_id = chosen
    result = _download(file_id)

    if _looks_like_interstitial(result):
        return html_outcome(
            413,
            "This Drive file cannot be streamed",
            "Google Drive returned a download warning page instead of the file.",
            why="Drive does that for very large files, or files it wants to scan "
            "before download.",
            fix="Keep individual site assets under " + code_html("25 MiB")
            + ", or host large files elsewhere and link to them directly.",
        )

    if result.status == 200:
        return file_outcome(result, served_name)

    if result.status == 404:
        return _not_found(file_path)

    if result.status in (403, 429):
        return html_outcome(
            result.status,
            "Google Drive is refusing this request",
            "Drive temporarily refused to hand over the file.",
            why="The folder may not be public, or Google is limiting anonymous "
            "downloads from this network.",
            fix="Check that the folder is shared with "
            + code_html("Anyone with the link")
            + ", then wait a moment and reload.",
        )

    if result.status == 413:
        return html_outcome(
            413,
            "File too large",
            "That file exceeds the maximum size this proxy will serve.",
            why="Files above " + code_html("25 MiB") + " are not proxied.",
            fix="Reference the large file directly instead of through the proxy.",
        )

    return html_outcome(
        502,
        "Google Drive is unavailable",
        "The service could not reach Google Drive. Please try again shortly.",
        why="The upstream download failed or timed out.",
        fix="Retry in a few seconds.",
    )


# --------------------------------------------------------------------------- #
# Vercel entrypoint
# --------------------------------------------------------------------------- #


class handler(ProxyHandler):
    """Vercel entrypoint: file-based Python function handler."""

    source_label = "Google Drive"

    def _parse_target(self):
        """Return (folder, file_path) from the request."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)

        def first(name):
            values = query.get(name)
            return _shared._clean(values[0]) if values else ""

        folder = first("folder")
        file_path = first("file")

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

        return folder, file_path

    def _serve(self, method):
        try:
            folder, file_path = self._parse_target()
            # Drive's download endpoint is most reliable with GET, so always
            # fetch with GET and simply omit the body for HEAD requests.
            outcome = resolve(folder, file_path, method="GET")

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
