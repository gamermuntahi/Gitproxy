"""WebProxyLive - shared engine for every proxy backend.

This module holds the logic that is common to *all* proxies:

* strict input validation helpers (owner / repo / branch / path segments),
* a MIME type table and cache-control policy,
* an SSRF-safe HTTP fetcher with a per-source host allow-list and
  redirect re-validation,
* the branded ``WebProxyLive`` error page,
* a ``BaseHTTPRequestHandler`` subclass that both the GitHub and the Google
  Drive entrypoints build on.

The GitHub logic below is deliberately *preserved* from the original
``api/github.py`` implementation: the same regexes, size limits, index
candidates and status handling are used, only reorganised so that a second
source (Google Drive) can reuse them without duplicating code and without
weakening any security guarantee.

Nothing in here ever connects to a host that is not on an explicit allow-list,
and no part of a destination URL ever comes directly from user input: the URL
is always *constructed* from individually validated identifiers.
"""

from __future__ import annotations

import html
import os
import re
from http.server import BaseHTTPRequestHandler
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote, unquote, urlparse

# --------------------------------------------------------------------------- #
# Branding / service identity
# --------------------------------------------------------------------------- #

SERVICE_NAME = "WebProxyLive"
SERVICE_TAGLINE = "Static websites, served from where you already keep your files."
SERVICE_TITLE = SERVICE_NAME + " \u00b7 Turn public files into live websites"

# --------------------------------------------------------------------------- #
# Shared limits
# --------------------------------------------------------------------------- #

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MiB per asset
MAX_PATH_SEGMENTS = 40  # deeper trees are almost certainly bogus
MAX_SEGMENT_LENGTH = 255
UPSTREAM_TIMEOUT = 15  # seconds
USER_AGENT = "webproxylive/1.0 (+https://vercel.app)"

#: Tried, in order, when a directory (or a repository / folder root) is asked for.
INDEX_CANDIDATES = ("index.html", "index.htm")

#: No API call is ever required for the default branch: the ``HEAD`` symbolic
#: ref lets the upstream host resolve it for us.
DEFAULT_REF = "HEAD"

# --------------------------------------------------------------------------- #
# Identifier patterns
# --------------------------------------------------------------------------- #

OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")

#: Google Drive folder / file identifiers are URL-safe base64-ish tokens.
DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")

# --------------------------------------------------------------------------- #
# Upstream allow-lists (anti-SSRF)
# --------------------------------------------------------------------------- #

#: The GitHub raw content host. Kept here so every module agrees on it.
GITHUB_RAW_HOST = "raw.githubusercontent.com"
GITHUB_RAW_BASE = "https://" + GITHUB_RAW_HOST

#: Allow-list for GitHub. Any sub-domain of the suffixes also counts.
GITHUB_ALLOWED_HOSTS = frozenset(
    {
        "raw.githubusercontent.com",
        "github.com",
        "objects.githubusercontent.com",
        "codeload.github.com",
        "media.githubusercontent.com",
    }
)
GITHUB_ALLOWED_HOST_SUFFIXES = (".githubusercontent.com", ".github.com")

#: Allow-list for Google Drive.
DRIVE_ALLOWED_HOSTS = frozenset(
    {
        "drive.google.com",
        "www.googleapis.com",
        "docs.google.com",
        "lh3.googleusercontent.com",
    }
)
DRIVE_ALLOWED_HOST_SUFFIXES = (".googleusercontent.com", ".google.com")

# --------------------------------------------------------------------------- #
# MIME types
# --------------------------------------------------------------------------- #

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".xhtml": "application/xhtml+xml; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".cjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".rss": "application/rss+xml; charset=utf-8",
    ".atom": "application/atom+xml; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".eot": "application/vnd.ms-fontobject",
    ".pdf": "application/pdf",
    ".wasm": "application/wasm",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

#: Text-ish types that should never be cached aggressively.
_SHORT_CACHE = {"text/html", "application/xhtml+xml", "application/xml"}

#: Headers attached to every response produced by this service.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Access-Control-Allow-Origin": "*",
    "X-Content-Type-Options-Proxy": "webproxylive",
}


def base_headers():
    """Return a fresh copy of the standard response headers."""
    headers = dict(SECURITY_HEADERS)
    headers["X-WebProxyLive"] = "static-source-proxy"
    return headers


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _clean(value):
    """Normalise a possibly URL-encoded, possibly blank query value."""
    if value is None:
        return ""
    value = value.strip()
    # Vercel substitutes capture groups literally; decode defensively.
    try:
        value = unquote(value)
    except Exception:
        pass
    # Never allow control characters / header injection primitives.
    return "".join(ch for ch in value if ch == "\t" or ord(ch) >= 32)


def valid_owner(owner):
    return bool(OWNER_RE.match(owner)) and "--" not in owner


def valid_repo(repo):
    if not REPO_RE.match(repo):
        return False
    if repo in (".", ".."):
        return False
    if repo.endswith(".git"):
        return False
    if repo.startswith(".") and repo.count(".") > 1:
        # allow ".github" style repos but reject "..foo"
        return False
    return True


def valid_branch(branch):
    """A branch/tag may contain slashes but must be a clean relative path."""
    if not branch or len(branch) > 255:
        return False
    if branch.startswith("/") or branch.endswith("/"):
        return False
    if "//" in branch or ".." in branch or "\\" in branch:
        return False
    for segment in branch.split("/"):
        if not BRANCH_SEGMENT_RE.match(segment):
            return False
        if segment.startswith("."):
            return False
    return True


def valid_drive_id(value):
    """A Google Drive folder or file identifier."""
    return bool(value) and bool(DRIVE_ID_RE.match(value))


def normalise_path(raw_path):
    """Return a safe list of path segments, or raise ``ValueError``.

    Rejects traversal, NUL bytes, backslashes and absolute paths. This is the
    single gate every user supplied path must pass, for both sources.
    """
    if raw_path is None:
        return []
    try:
        raw_path = unquote(raw_path)
    except Exception:
        pass

    raw_path = raw_path.replace("\x00", "")
    if "\\" in raw_path:
        raise ValueError("Backslashes are not allowed in paths")

    segments = []
    for segment in raw_path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ValueError("Path traversal is not allowed")
        if len(segment) > MAX_SEGMENT_LENGTH:
            raise ValueError("Path segment is too long")
        segments.append(segment)

    if len(segments) > MAX_PATH_SEGMENTS:
        raise ValueError("Path is too deep")
    return segments


def resolve_candidates(segments, file_path):
    """Return the ordered list of candidate paths for a request.

    * a directory (or root) request tries ``index.html`` then ``index.htm``;
    * a clean URL such as ``/about`` also tries ``about/index.html``;
    * an explicit file is requested as-is.

    Returns ``(candidates, directory_request)``.
    """
    directory_request = not segments or (file_path or "").endswith("/")

    candidates = []
    if directory_request:
        for name in INDEX_CANDIDATES:
            candidates.append(segments + [name])
    else:
        candidates.append(segments)
        # Clean-URL support: /about -> /about/index.html
        if "." not in segments[-1]:
            candidates.append(segments + ["index.html"])
    return candidates, directory_request


# --------------------------------------------------------------------------- #
# SSRF-safe HTTP fetching
# --------------------------------------------------------------------------- #


def _host_allowed(host, allowed_hosts, allowed_suffixes):
    if not host:
        return False
    host = host.lower().rstrip(".")
    if host in allowed_hosts:
        return True
    return any(host.endswith(suffix) for suffix in allowed_suffixes)


def url_allowed(url, allowed_hosts, allowed_suffixes):
    """Return True only for https:// URLs pointing at an allowed host."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    if parsed.username or parsed.password:  # reject userinfo tricks
        return False
    if parsed.port not in (None, 443):
        return False
    return _host_allowed(parsed.hostname, allowed_hosts, allowed_suffixes)


#: Openers are cached per allow-list so we do not rebuild one on every request.
_OPENER_CACHE = {}


def _build_safe_handler(allowed_hosts, allowed_suffixes):
    class _SafeRedirectHandler(urlrequest.HTTPRedirectHandler):
        """Follow redirects only while they stay on an allowed host."""

        max_redirections = 5

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not url_allowed(newurl, allowed_hosts, allowed_suffixes):
                raise urlerror.HTTPError(
                    newurl,
                    code,
                    "Blocked redirect to a host outside the allow-list",
                    headers,
                    fp,
                )
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return _SafeRedirectHandler


def opener_for(allowed_hosts, allowed_suffixes):
    key = (tuple(sorted(allowed_hosts)), tuple(sorted(allowed_suffixes)))
    opener = _OPENER_CACHE.get(key)
    if opener is None:
        opener = urlrequest.build_opener(
            _build_safe_handler(allowed_hosts, allowed_suffixes)
        )
        _OPENER_CACHE[key] = opener
    return opener


# Response headers that must never be forwarded from an upstream server. The
# write path below only ever cherry-picks validators, so this is defence in
# depth: it keeps any future pass-through from leaking cookies, redirects or
# hop-by-hop connection state to the browser.
BLOCKED_RESPONSE_HEADERS = frozenset(
    {
        # Never forward cookies or redirects from an upstream server.
        "set-cookie",
        "set-cookie2",
        "location",
        # Hop-by-hop headers (RFC 7230 section 6.1).
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        # Do not leak upstream server fingerprints.
        "server",
        "x-powered-by",
        "via",
        "x-served-by",
        "x-aspnet-version",
        "x-aspnetmvc-version",
        "x-generator",
    }
)


def sanitise_headers(headers):
    """Lower-case a header mapping and drop anything that could be injected.

    Rejects malformed names, strips CR/LF from values (header injection) and
    removes headers on :data:`BLOCKED_RESPONSE_HEADERS` entirely.
    """
    clean = {}
    for key, value in (headers or {}).items():
        if key is None:
            continue
        key = str(key).strip()
        if not key or any(ch in key for ch in "\r\n:"):
            continue
        key = key.lower()
        if key in BLOCKED_RESPONSE_HEADERS:
            continue
        clean[key] = "".join(ch for ch in str(value) if ch not in "\r\n")
    return clean


def merge_headers(target, extra):
    """Merge sanitised ``extra`` into ``target``, overriding canonically.

    :func:`sanitise_headers` lower-cases names, so a plain ``dict.update`` would
    leave a second, lower-cased ``content-type`` beside the canonical one. This
    removes any existing entry that matches case-insensitively and keeps the
    caller's own spelling, so an override really does override.
    """
    approved = sanitise_headers(extra)
    for original in (extra or {}).keys():
        if original is None:
            continue
        name = str(original).strip()
        lowered = name.lower()
        if lowered not in approved:
            continue
        for existing in [key for key in target if key.lower() == lowered]:
            del target[existing]
        target[name] = approved[lowered]
    return target


class UpstreamResult:
    """The outcome of an upstream fetch, HTTP failures included."""

    __slots__ = ("status", "headers", "body", "error")

    def __init__(self, status, headers=None, body=b"", error=None):
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.error = error


#: The only request header *names* a caller may contribute to an upstream
#: request. Everything here is either protocol metadata or a Google Drive
#: resource key, and every value is still sanitised before use. Keeping this an
#: allow-list (rather than a deny-list) means a caller can never smuggle a
#: header into the upstream request, and no request header ever originates in
#: user input.
ALLOWED_REQUEST_HEADERS = frozenset(
    {
        "x-goog-drive-resource-keys",
        "range",
        "if-none-match",
        "if-modified-since",
        "accept",
    }
)


def _safe_request_headers(extra_headers):
    """Filter caller supplied request headers down to the safe allow-list."""
    clean = {}
    for key, value in (extra_headers or {}).items():
        if key is None or value is None:
            continue
        key = str(key).strip().lower()
        if key not in ALLOWED_REQUEST_HEADERS:
            continue
        # Drop CR/LF (header injection) and cap the length of a single value.
        value = "".join(ch for ch in str(value) if ch not in "\r\n")[:1024].strip()
        if value:
            clean[key] = value
    return clean


def fetch_url(
    url,
    method="GET",
    allowed_hosts=(),
    allowed_suffixes=(),
    token=None,
    extra_headers=None,
):
    """Fetch a single resource, safely.

    ``url`` must already be constructed from validated components. The request
    never raises for HTTP level failures; the status is reported back so the
    caller can map it to a user-friendly response.

    ``extra_headers`` are filtered through :data:`ALLOWED_REQUEST_HEADERS`; it
    exists so the Drive backend can attach a resource key, not so callers can
    relay arbitrary client headers upstream.
    """
    if not url_allowed(url, set(allowed_hosts), tuple(allowed_suffixes)):
        return UpstreamResult(400, error="Constructed URL is not allowed")

    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    request_headers.update(_safe_request_headers(extra_headers))
    if token:
        request_headers["Authorization"] = "Bearer " + "".join(
            ch for ch in token if ch not in "\r\n"
        )

    request = urlrequest.Request(
        url,
        headers=request_headers,
        method="HEAD" if method == "HEAD" else "GET",
    )

    opener = opener_for(allowed_hosts, allowed_suffixes)

    try:
        response = opener.open(request, timeout=UPSTREAM_TIMEOUT)
    except urlerror.HTTPError as exc:
        headers = sanitise_headers(exc.headers)
        return UpstreamResult(exc.code, headers=headers, error="HTTP error")
    except urlerror.URLError as exc:
        return UpstreamResult(0, error=str(exc.reason))
    except Exception as exc:  # pragma: no cover - defensive
        return UpstreamResult(0, error=str(exc))

    headers = sanitise_headers(response.headers)

    if method == "HEAD":
        response.close()
        return UpstreamResult(response.status, headers=headers)

    length = headers.get("content-length")
    try:
        if length is not None and int(length) > MAX_FILE_BYTES:
            response.close()
            return UpstreamResult(413, headers=headers, error="File is too large")
    except ValueError:
        pass

    body = response.read(MAX_FILE_BYTES + 1)
    response.close()
    if len(body) > MAX_FILE_BYTES:
        return UpstreamResult(413, headers=headers, error="File is too large")

    return UpstreamResult(response.status, headers=headers, body=body)


# --------------------------------------------------------------------------- #
# Response helpers
# --------------------------------------------------------------------------- #


def content_type_for(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _MIME:
        return _MIME[ext]
    if not ext:
        # A literal extension-less file (README, LICENSE, Makefile, ...) is
        # served as plain text rather than HTML. Directory requests are always
        # resolved to an index candidate, so real pages still get text/html.
        # Defaulting to text/html here would let arbitrary source text render
        # as markup.
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def cache_control_for(content_type):
    base = content_type.split(";")[0].strip().lower()
    if base in _SHORT_CACHE:
        return "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
    return "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800"


def quote_path_segments(segments):
    return "/".join(quote(segment, safe="") for segment in segments)


def build_github_url(base, owner, repo, ref, segments):
    """Build a GitHub raw URL from already validated components."""
    encoded_ref = quote(ref, safe="/")
    encoded_path = quote_path_segments(segments)
    return base + "/" + owner + "/" + repo + "/" + encoded_ref + "/" + encoded_path


def code_html(text):
    """Return ``text`` wrapped in a code element, HTML-escaped safely."""
    return "<code>" + html.escape(str(text)) + "</code>"


class Outcome:
    """A fully resolved HTTP response, ready to be written by any frontend.

    ``headers`` already contains the security headers, ``Content-Type``,
    ``Cache-Control`` and any validators, so both the Vercel handler and the
    local Flask launcher can emit it without re-implementing the policy.
    """

    __slots__ = ("status", "body", "headers")

    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}


def html_outcome(status, title, message, why=None, fix=None):
    """Return an :class:`Outcome` rendering the branded error page."""
    headers = base_headers()
    headers["Content-Type"] = "text/html; charset=utf-8"
    headers["Cache-Control"] = "no-store"
    body = error_page(status, title, message, why=why, fix=fix).encode("utf-8")
    return Outcome(status, body, headers)


def file_outcome(result, served_path, extra_headers=None):
    """Return an :class:`Outcome` for a successful (200) upstream file."""
    content_type = content_type_for(served_path)
    headers = base_headers()
    headers["Content-Type"] = content_type
    headers["Cache-Control"] = cache_control_for(content_type)
    if extra_headers:
        merge_headers(headers, extra_headers)

    upstream_etag = result.headers.get("etag")
    upstream_last_modified = result.headers.get("last-modified")
    if upstream_etag:
        headers["ETag"] = upstream_etag
    if upstream_last_modified:
        headers["Last-Modified"] = upstream_last_modified

    return Outcome(200, result.body, headers)


def not_modified_outcome(headers):
    return Outcome(304, b"", headers)


# --------------------------------------------------------------------------- #
# Branded error page
# --------------------------------------------------------------------------- #

# Tokens are replaced with ``str.replace`` so that the CSS braces below need no
# escaping at all.
_ERROR_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__STATUS__ &middot; __SERVICE__</title>
<meta name="description" content="__STATUS__ error from __SERVICE__.">
<meta name="robots" content="noindex">
<style>
  :root {
    color-scheme: dark light;
    --bg: #05080f;
    --panel: rgba(15, 23, 42, .72);
    --border: rgba(148, 163, 184, .22);
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --accent-2: #818cf8;
    --accent-3: #a78bfa;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f6f8fc;
      --panel: rgba(255, 255, 255, .86);
      --border: rgba(15, 23, 42, .12);
      --text: #0f172a;
      --muted: #52607a;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background:
      radial-gradient(1100px 520px at 12% -8%, rgba(56, 189, 248, .14), transparent 60%),
      radial-gradient(900px 480px at 100% 0%, rgba(167, 139, 250, .14), transparent 62%),
      var(--bg);
    color: var(--text); padding: 24px;
  }
  .card {
    max-width: 620px; width: 100%; background: var(--panel);
    border: 1px solid var(--border); border-radius: 18px;
    padding: 40px 34px; box-shadow: 0 26px 70px rgba(2, 6, 23, .42);
    backdrop-filter: blur(14px);
  }
  .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 26px;
    font-weight: 700; letter-spacing: -.01em; }
  .dot { width: 12px; height: 12px; border-radius: 4px;
    background: linear-gradient(140deg, var(--accent), var(--accent-3)); }
  .code { font-size: 3.2rem; font-weight: 800; letter-spacing: -.03em;
    background: linear-gradient(90deg, var(--accent), var(--accent-2), var(--accent-3));
    -webkit-background-clip: text; background-clip: text; color: transparent;
    line-height: 1; }
  h1 { font-size: 1.3rem; margin: 12px 0 12px; letter-spacing: -.01em; }
  p { color: var(--muted); line-height: 1.65; margin: 0 0 18px; }
  code { background: rgba(148, 163, 184, .16); padding: 2px 6px; border-radius: 6px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em;
    color: var(--text); word-break: break-word; }
  .details { display: grid; gap: 12px; margin: 22px 0 4px; }
  .detail { border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px;
    background: rgba(148, 163, 184, .06); }
  .detail .k { display: block; font-size: .72rem; letter-spacing: .09em;
    text-transform: uppercase; color: var(--muted); margin-bottom: 6px; font-weight: 700; }
  .detail p:last-child { margin-bottom: 0; }
  .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 26px; }
  a.btn, button.btn { display: inline-block; padding: 11px 20px; border-radius: 11px;
    text-decoration: none; font-weight: 600; font-size: .92rem; border: 1px solid transparent;
    cursor: pointer; font-family: inherit; }
  .btn-primary { color: #05101f; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
  .btn-primary:hover { filter: brightness(1.08); }
  .btn-ghost { color: var(--text); background: transparent; border-color: var(--border); }
  .btn-ghost:hover { background: rgba(148, 163, 184, .12); }
  .foot { margin: 22px 0 0; font-size: .8rem; color: var(--muted); }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
</head>
<body>
  <main class="card">
    <div class="brand"><span class="dot" aria-hidden="true"></span>__SERVICE__</div>
    <div class="code">__STATUS__</div>
    <h1>__TITLE__</h1>
    <p>__MESSAGE__</p>
    __DETAILS__
    <div class="actions">
      <button class="btn btn-primary" type="button" onclick="location.reload()">Try again</button>
      <a class="btn btn-ghost" href="/">Return home</a>
      <button class="btn btn-ghost" type="button" id="copyUrl">Copy URL</button>
    </div>
    <p class="foot">__FOOT__</p>
  </main>
  <script>
    (function () {
      var btn = document.getElementById('copyUrl');
      if (!btn) return;
      btn.addEventListener('click', function () {
        var text = location.href;
        var done = function () { btn.textContent = 'Copied'; };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, done);
        } else {
          var input = document.createElement('textarea');
          input.value = text; document.body.appendChild(input); input.select();
          try { document.execCommand('copy'); } catch (e) {}
          document.body.removeChild(input); done();
        }
      });
    })();
  </script>
</body>
</html>
"""


def _detail(label, value):
    if not value:
        return ""
    return (
        '<div class="detail"><span class="k">'
        + html.escape(label)
        + "</span><p>"
        + value
        + "</p></div>"
    )


def error_page(status, title, message, why=None, fix=None, foot=None):
    """Build the user facing error page.

    ``message``, ``why`` and ``fix`` may contain safe, already-escaped HTML
    (use :func:`code_html` for anything user supplied). No stack trace, no
    internal detail ever appears here.
    """
    details = _detail("Why this happened", why) + _detail("How to fix it", fix)
    body = _ERROR_TEMPLATE
    replacements = {
        "__STATUS__": html.escape(str(status)),
        "__SERVICE__": html.escape(SERVICE_NAME),
        "__TITLE__": html.escape(str(title)),
        "__MESSAGE__": message,
        "__DETAILS__": details,
        "__FOOT__": html.escape(
            foot or ("Served by " + SERVICE_NAME + ". Nothing was logged or stored.")
        ),
    }
    for token, value in replacements.items():
        body = body.replace(token, value)
    return body


# --------------------------------------------------------------------------- #
# Request handler base
# --------------------------------------------------------------------------- #


class ProxyHandler(BaseHTTPRequestHandler):
    """Common request plumbing for every WebProxyLive source proxy.

    Subclasses implement :meth:`_serve`, which must resolve the request and
    then call :meth:`_send_upstream` (or :meth:`_respond_error`).
    """

    server_version = "WebProxyLive/1.0"
    protocol_version = "HTTP/1.1"

    #: Overridden by each proxy (e.g. "GitHub", "Google Drive").
    source_label = "source"

    # -- plumbing ---------------------------------------------------------- #

    def log_message(self, fmt, *args):  # keep Vercel logs tidy
        return

    # -- HTTP verbs -------------------------------------------------------- #

    def do_GET(self):
        self._serve("GET")

    def do_HEAD(self):
        self._serve("HEAD")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- core -------------------------------------------------------------- #

    def _respond(self, status, body, headers, method):
        self.send_response(status)
        for key, value in headers.items():
            # Defensive: never emit a blocked header, and never emit a value
            # containing CR/LF.
            if str(key).lower() in BLOCKED_RESPONSE_HEADERS:
                continue
            safe = "".join(ch for ch in str(value) if ch not in "\r\n")
            self.send_header(key, safe)
        self.send_header("Content-Length", str(len(body) if body else 0))
        self.end_headers()
        if method != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _respond_error(self, status, title, message, method, why=None, fix=None):
        body = error_page(status, title, message, why=why, fix=fix).encode("utf-8")
        headers = base_headers()
        headers.update(
            {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-store",
            }
        )
        self._respond(status, body, headers, method)

    def _send_upstream(
        self,
        result,
        served_path,
        method,
        not_found_title,
        not_found_message,
        unavailable_title,
        unavailable_message,
        rate_limit_title,
        rate_limit_message,
        why=None,
        fix=None,
        extra_headers=None,
    ):
        """Map an upstream result to a response. Shared by all sources."""
        if result.status == 200:
            content_type = content_type_for(served_path)
            headers = base_headers()
            headers["Content-Type"] = content_type
            headers["Cache-Control"] = cache_control_for(content_type)
            if extra_headers:
                merge_headers(headers, extra_headers)

            upstream_etag = result.headers.get("etag")
            upstream_last_modified = result.headers.get("last-modified")
            if upstream_etag:
                headers["ETag"] = upstream_etag
            if upstream_last_modified:
                headers["Last-Modified"] = upstream_last_modified

            if upstream_etag:
                client_etag = self.headers.get("If-None-Match")
                if client_etag and client_etag.strip() == upstream_etag.strip():
                    self._respond(304, b"", headers, method)
                    return

            self._respond(200, result.body, headers, method)
            return

        if result.status == 404:
            self._respond_error(
                404, not_found_title, not_found_message, method, why=why, fix=fix
            )
            return

        if result.status in (403, 429):
            headers = base_headers()
            headers["Content-Type"] = "text/html; charset=utf-8"
            headers["Cache-Control"] = "no-store"
            retry_after = result.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                headers["Retry-After"] = retry_after
            body = error_page(
                result.status,
                rate_limit_title,
                rate_limit_message,
                why=why,
                fix=fix,
            ).encode("utf-8")
            self._respond(result.status, body, headers, method)
            return

        if result.status == 413:
            self._respond_error(
                413,
                "File too large",
                "That file exceeds the "
                + code_html("25 MiB")
                + " limit this proxy will serve.",
                method,
                why="Very large single files are not streamed through the proxy, "
                "so downloads stay fast and predictable.",
                fix="Host the large asset somewhere it can be linked directly, and "
                "reference it with an absolute URL from your page.",
            )
            return

        if result.status == 400:
            self._respond_error(
                400,
                "Invalid request",
                "The request could not be processed.",
                method,
                why="One of the supplied values did not pass validation.",
                fix="Return home and rebuild the URL with the generator.",
            )
            return

        # 0 / 5xx / anything else -> upstream problem.
        self._respond_error(
            502,
            unavailable_title,
            unavailable_message,
            method,
            why=why,
            fix=fix,
        )

    def _serve(self, method):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError
