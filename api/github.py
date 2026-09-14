"""Vercel serverless backend for the GitHub Website Proxy service.

This module powers a *stateless* service that serves the contents of public
GitHub repositories through a Vercel domain, so a repository can behave like a
normal static website.

Request shape
-------------
``vercel.json`` rewrites user facing URLs into a call to this function::

    /<owner>.<repo>/                 ->  /api/github?owner=<owner>&repo=<repo>&branch=&file=
    /<owner>.<repo>/<path>           ->  /api/github?owner=<owner>&repo=<repo>&branch=&file=<path>
    /<owner>.<repo>@<branch>/<path>  ->  /api/github?owner=<owner>&repo=<repo>&branch=<branch>&file=<path>

File resolution
---------------
Files are fetched from GitHub's raw content host using the ``HEAD`` ref, which
GitHub resolves to the repository's *default* branch. This means the default
branch is detected automatically **without** a single GitHub API call::

    https://raw.githubusercontent.com/<owner>/<repo>/HEAD/<path>

Security model
--------------
Every component of the destination URL is derived from *validated* GitHub
identifiers. The function can only ever connect to a small allow-list of
GitHub owned hosts, and it re-validates every redirect hop. Users can never
supply an arbitrary destination URL, so SSRF, protocol injection and
path traversal are structurally impossible.
"""

from __future__ import annotations

import html
import os
import re
from http.server import BaseHTTPRequestHandler
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, quote, unquote, urlparse

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

RAW_HOST = "raw.githubusercontent.com"
RAW_BASE = "https://" + RAW_HOST

#: Anti-SSRF allow-list. The function may only ever talk to these hosts.
ALLOWED_HOSTS = frozenset(
    {
        "raw.githubusercontent.com",
        "github.com",
        "objects.githubusercontent.com",
        "codeload.github.com",
        "media.githubusercontent.com",
    }
)
#: Any sub-domain of these suffixes is also considered GitHub owned.
ALLOWED_HOST_SUFFIXES = (".githubusercontent.com", ".github.com")

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MiB per asset
MAX_PATH_SEGMENTS = 40  # deeper trees are almost certainly bogus
MAX_SEGMENT_LENGTH = 255
UPSTREAM_TIMEOUT = 15  # seconds
USER_AGENT = "vercel-github-website-proxy/1.0"

#: Tried, in order, when a directory (or the repository root) is requested.
INDEX_CANDIDATES = ("index.html", "index.htm")

OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")

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

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Access-Control-Allow-Origin": "*",
    "X-GitHub-Proxy": "vercel-github-website-proxy",
}


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


def normalise_path(raw_path):
    """Return a safe list of path segments, or raise ``ValueError``.

    Rejects traversal, NUL bytes, backslashes and absolute paths.
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


def build_raw_url(owner, repo, ref, segments):
    """Build a ``raw.githubusercontent.com`` URL from validated components."""
    encoded_ref = quote(ref, safe="/")
    encoded_path = "/".join(quote(segment, safe="") for segment in segments)
    return f"{RAW_BASE}/{owner}/{repo}/{encoded_ref}/{encoded_path}"


# --------------------------------------------------------------------------- #
# SSRF-safe HTTP fetching
# --------------------------------------------------------------------------- #


def _host_allowed(host):
    if not host:
        return False
    host = host.lower().rstrip(".")
    if host in ALLOWED_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES)


def url_allowed(url):
    """Return True only for https:// URLs pointing at GitHub owned hosts."""
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
    return _host_allowed(parsed.hostname)


class _SafeRedirectHandler(urlrequest.HTTPRedirectHandler):
    """Follow redirects only while they stay on GitHub owned hosts."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not url_allowed(newurl):
            raise urlerror.HTTPError(
                newurl, code, "Blocked redirect to a non-GitHub host", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urlrequest.build_opener(_SafeRedirectHandler)


class UpstreamResult:
    __slots__ = ("status", "headers", "body", "error")

    def __init__(self, status, headers=None, body=b"", error=None):
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.error = error


def fetch(owner, repo, ref, segments, method="GET"):
    """Fetch a single file from GitHub's raw host.

    ``ref`` is either a validated branch name or ``"HEAD"`` for the default
    branch. Never raises for HTTP level failures; the status is reported back
    so the caller can map it to a user-friendly response.
    """
    url = build_raw_url(owner, repo, ref, segments)
    if not url_allowed(url):
        return UpstreamResult(400, error="Constructed URL is not allowed")

    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }

    # Optional: a token raises GitHub's rate limit. It is never required and the
    # service works fully without it (raw content is not rate limited as
    # aggressively as the REST API). Only used if explicitly configured.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request_headers["Authorization"] = "Bearer " + "".join(
            ch for ch in token if ch not in "\r\n"
        )

    request = urlrequest.Request(
        url,
        headers=request_headers,
        method="HEAD" if method == "HEAD" else "GET",
    )

    try:
        response = _OPENER.open(request, timeout=UPSTREAM_TIMEOUT)
    except urlerror.HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return UpstreamResult(exc.code, headers=headers, error="HTTP error")
    except urlerror.URLError as exc:
        return UpstreamResult(0, error=str(exc.reason))
    except Exception as exc:  # pragma: no cover - defensive
        return UpstreamResult(0, error=str(exc))

    headers = {k.lower(): v for k, v in response.headers.items()}

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
        # resolved to an index.html candidate, so real pages still get
        # text/html. Defaulting to text/html here would let arbitrary
        # repository text render as markup.
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def cache_control_for(content_type):
    base = content_type.split(";")[0].strip().lower()
    if base in _SHORT_CACHE:
        return "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
    return "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800"


def code_html(text):
    """Return ``text`` wrapped in a code element, HTML-escaped safely."""
    return "<code>" + html.escape(str(text)) + "</code>"


# CSS braces are doubled so that ``str.format`` leaves them intact.
_ERROR_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{status} \u00b7 GitHub Website Proxy</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: linear-gradient(160deg, #0f172a 0%, #1e293b 55%, #0b1220 100%);
    color: #e2e8f0; padding: 24px; text-align: center;
  }}
  .card {{
    max-width: 560px; width: 100%; background: rgba(15, 23, 42, .72);
    border: 1px solid rgba(148, 163, 184, .22); border-radius: 16px;
    padding: 40px 32px; box-shadow: 0 24px 64px rgba(0, 0, 0, .45);
  }}
  .code {{ font-size: 3rem; font-weight: 800; letter-spacing: -.02em;
    background: linear-gradient(90deg, #38bdf8, #a78bfa); -webkit-background-clip: text;
    background-clip: text; color: transparent; }}
  h1 {{ font-size: 1.25rem; margin: 8px 0 12px; }}
  p {{ color: #94a3b8; line-height: 1.6; margin: 0 0 20px; }}
  code {{ background: rgba(148,163,184,.15); padding: 2px 6px; border-radius: 6px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; color: #e2e8f0; }}
  a.btn {{ display: inline-block; padding: 11px 22px; border-radius: 10px; text-decoration: none;
    font-weight: 600; color: #0f172a; background: linear-gradient(90deg, #38bdf8, #818cf8); }}
  a.btn:hover {{ filter: brightness(1.08); }}
</style>
</head>
<body>
  <main class="card">
    <div class="code">{status}</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <a class="btn" href="/">Go to the homepage</a>
  </main>
</body>
</html>
"""


def error_page(status, title, message):
    """Build the user facing error page. ``message`` may contain safe HTML."""
    return _ERROR_TEMPLATE.format(
        status=status, title=html.escape(str(title)), message=message
    )


# --------------------------------------------------------------------------- #
# Request handler
# --------------------------------------------------------------------------- #


class handler(BaseHTTPRequestHandler):
    """Vercel entrypoint: file-based Python function handler."""

    server_version = "GitHubWebsiteProxy/1.0"
    protocol_version = "HTTP/1.1"

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
            # Defensive: never emit a header value containing CR/LF.
            safe = "".join(ch for ch in str(value) if ch not in "\r\n")
            self.send_header(key, safe)
        self.send_header("Content-Length", str(len(body) if body else 0))
        self.end_headers()
        if method != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _respond_error(self, status, title, message, method):
        body = error_page(status, title, message).encode("utf-8")
        headers = dict(_SECURITY_HEADERS)
        headers.update(
            {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-store",
            }
        )
        self._respond(status, body, headers, method)

    def _parse_target(self):
        """Return (owner, repo, branch, file_path) from the request."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)

        def first(name):
            values = query.get(name)
            return _clean(values[0]) if values else ""

        owner = first("owner")
        repo = first("repo")
        branch = first("branch")
        file_path = first("file")

        # Fallback: allow direct /api/github/<owner>.<repo>/<path> access so the
        # function is still usable even if reached without the rewrite layer.
        if not owner or not repo:
            cleaned = _clean(unquote(parsed.path))
            segments = [s for s in cleaned.split("/") if s]
            if segments and segments[0] == "api":
                segments = segments[1:]
            if segments and segments[0] == "github":
                segments = segments[1:]
            if segments:
                head = segments[0]
                tail = "/".join(segments[1:])
                br = ""
                if "@" in head:
                    head, _, br = head.partition("@")
                if "." in head:
                    owner, _, repo = head.partition(".")
                    branch = br
                    if tail:
                        file_path = tail

        return owner, repo, branch, file_path

    def _serve(self, method):
        try:
            owner, repo, branch, file_path = self._parse_target()

            if not owner or not repo:
                self._respond_error(
                    400,
                    "Invalid repository URL",
                    "Use the format "
                    + code_html("/<username>.<repository>/")
                    + ", for example "
                    + code_html("/octocat.Hello-World/")
                    + ".",
                    method,
                )
                return

            if not valid_owner(owner) or not valid_repo(repo):
                self._respond_error(
                    400,
                    "Invalid GitHub repository",
                    "That does not look like a valid GitHub username and repository name.",
                    method,
                )
                return

            use_branch = bool(branch)
            if use_branch and not valid_branch(branch):
                self._respond_error(
                    400, "Invalid branch name", "The branch name is not valid.", method
                )
                return
            ref = branch if use_branch else "HEAD"

            try:
                segments = normalise_path(file_path)
            except ValueError as exc:
                self._respond_error(400, "Invalid file path", html.escape(str(exc)), method)
                return

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

            result = None
            chosen = None
            for candidate in candidates:
                result = fetch(owner, repo, ref, candidate, method=method)
                if result.status == 200:
                    chosen = candidate
                    break
                if result.status in (0, 403, 429, 413):  # do not retry these
                    break

            if result is None:  # pragma: no cover - defensive
                self._respond_error(
                    500,
                    "Unexpected error",
                    "The server could not process the request.",
                    method,
                )
                return

            if result.status == 200:
                served_path = "/".join(chosen or segments) or "index.html"
                content_type = content_type_for(served_path)
                headers = dict(_SECURITY_HEADERS)
                headers["Content-Type"] = content_type
                headers["Cache-Control"] = cache_control_for(content_type)

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
                    404,
                    "Repository or file not found",
                    "The repository "
                    + code_html(f"{owner}/{repo}")
                    + " is either missing, private, or the requested file "
                    + code_html(file_path or "index.html")
                    + " does not exist. The default branch is used automatically.",
                    method,
                )
                return

            if result.status in (403, 429):
                retry_after = result.headers.get("retry-after")
                headers = dict(_SECURITY_HEADERS)
                headers["Content-Type"] = "text/html; charset=utf-8"
                headers["Cache-Control"] = "no-store"
                if retry_after and retry_after.isdigit():
                    headers["Retry-After"] = retry_after
                body = error_page(
                    403,
                    "GitHub is rate limiting this service",
                    "GitHub temporarily refused the request. Please wait a moment and try again.",
                ).encode("utf-8")
                self._respond(403 if result.status == 403 else 429, body, headers, method)
                return

            if result.status == 413:
                self._respond_error(
                    413,
                    "File too large",
                    "That file exceeds the maximum size this proxy will serve.",
                    method,
                )
                return

            if result.status == 400:
                self._respond_error(
                    400, "Invalid request", "The request could not be processed.", method
                )
                return

            # 0 / 5xx / anything else -> upstream problem.
            self._respond_error(
                502,
                "GitHub is unavailable",
                "The service could not reach GitHub. Please try again shortly.",
                method,
            )

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
