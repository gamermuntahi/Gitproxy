#!/usr/bin/env python3
"""One-click local launcher for the GitHub Website Proxy.

Run it with a single command::

    python app.py

It starts a local web server that behaves like the deployed Vercel project:

* ``/`` and everything under ``/assets/`` are served from the ``public/`` folder.
* ``/<owner>.<repo>/`` and ``/<owner>.<repo>/<path>`` are proxied to GitHub by
  reusing the exact validation, fetching, MIME and caching logic from
  ``api/github.py`` — the same code Vercel runs in production.

Nothing here is used in production; this file exists purely so the project can
be previewed offline with zero installation. It uses only the Python standard
library, so ``python app.py`` works on a clean machine.

Usage
-----
    python app.py              # serve on the first free port from 8000, open browser
    python app.py 8080         # force a specific port
    python app.py 8080 --no-open   # do not launch a browser automatically
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

ROOT = os.path.dirname(os.path.abspath(__file__))
API_FILE = os.path.join(ROOT, "api", "github.py")
PUBLIC_DIR = os.path.join(ROOT, "public")
PUBLIC_REAL = os.path.realpath(PUBLIC_DIR)

DEFAULT_PORT = 8000
HOST = "127.0.0.1"


# --------------------------------------------------------------------------- #
# Load the production backend so local behaviour matches deployment exactly
# --------------------------------------------------------------------------- #


def _load_backend():
    """Import ``api/github.py`` as a normal module without running the server."""
    if not os.path.isfile(API_FILE):
        sys.stderr.write(
            "ERROR: could not find api/github.py next to app.py.\n"
            "Make sure you run this file from inside the project folder.\n"
        )
        raise SystemExit(1)

    spec = importlib.util.spec_from_file_location("github_proxy_backend", API_FILE)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit("ERROR: could not load api/github.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = _load_backend()


# --------------------------------------------------------------------------- #
# Static assets (the landing page)
# --------------------------------------------------------------------------- #

STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

#: Mirrors the aliases declared in vercel.json.
STATIC_ALIASES = {
    "/index.html": "index.html",
    "/favicon.ico": "assets/favicon.svg",
}

#: The same catch-all shape used by the rewrite in vercel.json.
PROXY_RE = re.compile(
    r"^/([A-Za-z0-9-]+)\.([A-Za-z0-9_.-]+?)"
    r"(?:@([A-Za-z0-9._-]+))?(?:/(.*))?$"
)


def _static_file_for(path):
    """Return a safe absolute path inside ``public/`` or ``None``."""
    cleaned = unquote(path)
    if cleaned in STATIC_ALIASES:
        cleaned = "/" + STATIC_ALIASES[cleaned]
    if cleaned == "/":
        cleaned = "/index.html"

    if ".." in cleaned.split("/"):
        return None

    relative = cleaned.lstrip("/")
    candidate = os.path.realpath(os.path.join(PUBLIC_DIR, relative))

    # Directory traversal / symlink escape guard.
    if candidate != PUBLIC_REAL and not candidate.startswith(PUBLIC_REAL + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def _security_headers():
    return {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Access-Control-Allow-Origin": "*",
    }


# --------------------------------------------------------------------------- #
# Proxy logic (a faithful local mirror of handler._serve)
# --------------------------------------------------------------------------- #


def _error(status, title, message):
    body = backend.error_page(status, title, message).encode("utf-8")
    headers = _security_headers()
    headers["Content-Type"] = "text/html; charset=utf-8"
    headers["Cache-Control"] = "no-store"
    return status, body, headers


def _proxy(owner, repo, branch, file_path, method):
    """Resolve a request against GitHub, returning ``(status, body, headers)``."""
    if not owner or not repo:
        return _error(
            400,
            "Invalid repository URL",
            "Use the format "
            + backend.code_html("/<username>.<repository>/")
            + ", for example "
            + backend.code_html("/octocat.Hello-World/")
            + ".",
        )

    if not backend.valid_owner(owner) or not backend.valid_repo(repo):
        return _error(
            400,
            "Invalid GitHub repository",
            "That does not look like a valid GitHub username and repository name.",
        )

    use_branch = bool(branch)
    if use_branch and not backend.valid_branch(branch):
        return _error(400, "Invalid branch name", "The branch name is not valid.")
    ref = branch if use_branch else "HEAD"

    try:
        segments = backend.normalise_path(file_path)
    except ValueError as exc:
        return _error(400, "Invalid file path", backend.html.escape(str(exc)))

    directory_request = not segments or (file_path or "").endswith("/")

    candidates = []
    if directory_request:
        for name in backend.INDEX_CANDIDATES:
            candidates.append(segments + [name])
    else:
        candidates.append(segments)
        # Clean-URL support: /about -> /about/index.html
        if "." not in segments[-1]:
            candidates.append(segments + ["index.html"])

    result = None
    chosen = None
    for candidate in candidates:
        result = backend.fetch(owner, repo, ref, candidate, method=method)
        if result.status == 200:
            chosen = candidate
            break
        if result.status in (0, 403, 429, 413):
            break

    if result is None:  # pragma: no cover - defensive
        return _error(500, "Unexpected error", "The server could not process the request.")

    if result.status == 200:
        served_path = "/".join(chosen or segments) or "index.html"
        content_type = backend.content_type_for(served_path)
        headers = _security_headers()
        headers["Content-Type"] = content_type
        headers["Cache-Control"] = backend.cache_control_for(content_type)

        upstream_etag = result.headers.get("etag")
        upstream_last_modified = result.headers.get("last-modified")
        if upstream_etag:
            headers["ETag"] = upstream_etag
        if upstream_last_modified:
            headers["Last-Modified"] = upstream_last_modified

        return 200, result.body, headers

    if result.status == 404:
        return _error(
            404,
            "Repository or file not found",
            "The repository "
            + backend.code_html(f"{owner}/{repo}")
            + " is either missing, private, or the requested file "
            + backend.code_html(file_path or "index.html")
            + " does not exist. The default branch is used automatically.",
        )

    if result.status in (403, 429):
        return _error(
            403,
            "GitHub is rate limiting this service",
            "GitHub temporarily refused the request. Please wait a moment and try again.",
        )

    if result.status == 413:
        return _error(
            413,
            "File too large",
            "That file exceeds the maximum size this proxy will serve.",
        )

    if result.status == 400:
        return _error(400, "Invalid request", "The request could not be processed.")

    return _error(
        502,
        "GitHub is unavailable",
        "The service could not reach GitHub. Please try again shortly.",
    )


# --------------------------------------------------------------------------- #
# Request handler
# --------------------------------------------------------------------------- #


class LocalDevHandler(BaseHTTPRequestHandler):
    server_version = "GitHubWebsiteProxyLocal/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def handle(self):
        """Same as the base implementation, minus the noisy disconnect trace.

        Browsers routinely drop idle keep-alive connections, which makes the
        stdlib server print a full traceback. That is harmless but alarming in
        a one-click preview, so it is swallowed here.
        """
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    # -- verbs ------------------------------------------------------------- #

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_OPTIONS(self):
        self._flush(
            204,
            b"",
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Max-Age": "86400",
            },
        )

    # -- dispatch ---------------------------------------------------------- #

    def _handle(self, method):
        try:
            parsed = urlparse(self.path)
            path = parsed.path

            # Static landing page first, so /assets/* is never proxied.
            static = _static_file_for(path)
            if static is not None:
                self._send_static(static, method)
                return

            # Explicit API form: /api/github?owner=..&repo=..&branch=..&file=..
            if path.rstrip("/") == "/api/github":
                query = parse_qs(parsed.query, keep_blank_values=True)

                def first(name):
                    values = query.get(name)
                    return backend._clean(values[0]) if values else ""

                status, body, headers = _proxy(
                    first("owner"),
                    first("repo"),
                    first("branch"),
                    first("file"),
                    method,
                )
                self._flush(status, body, headers)
                return

            # Pretty proxy form: /<owner>.<repo>[@branch][/<path>]
            match = PROXY_RE.match(path)
            if match:
                owner = match.group(1) or ""
                repo = match.group(2) or ""
                branch = match.group(3) or ""
                file_path = match.group(4) or ""
                status, body, headers = _proxy(owner, repo, branch, file_path, method)
                self._flush(status, body, headers)
                return

            # Unknown path with no extension -> hint how the service works.
            self._flush(*_error(
                404,
                "Page not found",
                "That path is neither a landing page asset nor a "
                + backend.code_html("/<username>.<repository>/")
                + " repository URL.",
            ))

        except Exception as exc:  # pragma: no cover - last resort
            self._flush(*_error(
                500,
                "Unexpected server error",
                backend.html.escape(str(exc)) or "Something went wrong.",
            ))

    # -- writers ----------------------------------------------------------- #

    def _send_static(self, absolute_path, method):
        try:
            with open(absolute_path, "rb") as handle:
                body = handle.read()
        except OSError as exc:
            self._flush(*_error(500, "Cannot read file", backend.html.escape(str(exc))))
            return

        ext = os.path.splitext(absolute_path)[1].lower()
        content_type = STATIC_MIME.get(ext, "application/octet-stream")

        headers = _security_headers()
        headers["Content-Type"] = content_type
        # Always fresh locally so edits appear immediately on refresh.
        headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

        self._flush(200, body, headers, method)

    def _flush(self, status, body, headers, method="GET"):
        self.send_response(status)
        for key, value in headers.items():
            safe = "".join(ch for ch in str(value) if ch not in "\r\n")
            self.send_header(key, safe)
        self.send_header("Content-Length", str(len(body) if body else 0))
        self.end_headers()
        if method != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def _parse_args(argv):
    port = DEFAULT_PORT
    open_browser = True
    for arg in argv[1:]:
        if arg in ("--no-open", "-n"):
            open_browser = False
        elif arg.isdigit():
            port = int(arg)
        elif arg in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
    return port, open_browser


def _start_server(preferred_port):
    for offset in range(0, 25):
        candidate = preferred_port + offset
        try:
            return ThreadingHTTPServer((HOST, candidate), LocalDevHandler)
        except OSError:
            continue
    sys.stderr.write(
        "ERROR: no free port found between %d and %d.\n"
        % (preferred_port, preferred_port + 24)
    )
    raise SystemExit(1)


def main(argv=None):
    argv = argv or sys.argv
    preferred_port, open_browser = _parse_args(argv)
    httpd = _start_server(preferred_port)
    port = httpd.server_address[1]
    url = "http://%s:%d/" % (HOST, port)

    banner = (
        "\n"
        "  GitHub Website Proxy - local preview\n"
        "  ------------------------------------\n"
        "  Landing page : {url}\n"
        "  Example repo : {url}octocat.Hello-World/\n"
        "  Backend      : api/github.py (the same code Vercel runs)\n"
        "  Stop         : press Ctrl+C\n"
    ).format(url=url)
    print(banner)

    if open_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.6), webbrowser.open(url)),
            daemon=True,
        ).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped. Bye.\n")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
