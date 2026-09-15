#!/usr/bin/env python3
"""One-click local launcher for the GitHub Website Proxy (Flask edition).

Run it with a single command::

    python app.py

It starts a Flask development server that behaves like the deployed Vercel
project:

* ``/`` and everything under ``/assets/`` are served from the ``public/`` folder.
* ``/<owner>.<repo>/`` and ``/<owner>.<repo>/<path>`` are proxied to GitHub by
  reusing the exact validation, fetching, MIME and caching logic from
  ``api/github.py`` — the same code Vercel runs in production.

This file is a local development convenience only. The production deployment
still runs ``api/github.py`` as a Vercel serverless function.

Installing Flask
----------------
Flask is the only third-party dependency and is used *only* by this launcher::

    pip install flask

Usage
-----
    python app.py                 # serve on port 8000 and open the browser
    python app.py --port 8080     # force a specific port
    python app.py --no-open       # do not launch a browser automatically
    python app.py --debug         # enable Flask debug mode / auto-reload
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import threading
import webbrowser

from flask import Flask, Response, request, send_from_directory

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

ROOT = os.path.dirname(os.path.abspath(__file__))
API_FILE = os.path.join(ROOT, "api", "github.py")
PUBLIC_DIR = os.path.join(ROOT, "public")

DEFAULT_PORT = 8000
HOST = "127.0.0.1"


# --------------------------------------------------------------------------- #
# Load the production backend so local behaviour matches deployment exactly
# --------------------------------------------------------------------------- #


def _load_backend():
    """Import ``api/github.py`` as a normal module without starting a server."""
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

app = Flask(__name__, static_folder=None)

#: The same catch-all shape used by the rewrite in vercel.json.
PROXY_RE = re.compile(
    r"^/([A-Za-z0-9-]+)\.([A-Za-z0-9_.-]+?)"
    r"(?:@([A-Za-z0-9._-]+))?(?:/(.*))?$"
)

#: Mirrors the aliases declared in vercel.json.
STATIC_ALIASES = {
    "index.html": "index.html",
    "favicon.ico": "assets/favicon.svg",
}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Access-Control-Allow-Origin": "*",
}


# --------------------------------------------------------------------------- #
# Proxy logic (a faithful local mirror of handler._serve)
# --------------------------------------------------------------------------- #


def _error(status, title, message):
    """Return a Flask ``Response`` rendering the shared error page."""
    body = backend.error_page(status, title, message)
    headers = dict(SECURITY_HEADERS)
    headers["Content-Type"] = "text/html; charset=utf-8"
    headers["Cache-Control"] = "no-store"
    return Response(body, status=status, headers=headers)


def _proxy(owner, repo, branch, file_path, method="GET"):
    """Resolve a request against GitHub, returning a Flask ``Response``."""
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
        headers = dict(SECURITY_HEADERS)
        headers["Content-Type"] = content_type
        headers["Cache-Control"] = backend.cache_control_for(content_type)

        upstream_etag = result.headers.get("etag")
        upstream_last_modified = result.headers.get("last-modified")
        if upstream_etag:
            headers["ETag"] = upstream_etag
        if upstream_last_modified:
            headers["Last-Modified"] = upstream_last_modified

        body = b"" if method == "HEAD" else result.body
        return Response(body, status=200, headers=headers)

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
# Static landing page (mirrors the order of the production rewrite)
# --------------------------------------------------------------------------- #


@app.route("/")
def landing():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(PUBLIC_DIR, "assets"), filename)


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(PUBLIC_DIR, STATIC_ALIASES["favicon.ico"])


@app.route("/api/github")
def api_github():
    owner = backend._clean(request.args.get("owner", ""))
    repo = backend._clean(request.args.get("repo", ""))
    branch = backend._clean(request.args.get("branch", ""))
    file_path = backend._clean(request.args.get("file", ""))
    return _proxy(owner, repo, branch, file_path, request.method)


@app.route("/<path:anything>")
def proxy(anything):  # noqa: ARG001 - the raw path is re-read below
    """Catch-all: handles the pretty ``/<owner>.<repo>/...`` proxy form."""
    path = request.path
    match = PROXY_RE.match(path)
    if match:
        return _proxy(
            match.group(1) or "",
            match.group(2) or "",
            match.group(3) or "",
            match.group(4) or "",
            request.method,
        )

    # Try a nested static file first (e.g. /assets/css/x.css), then give a hint.
    relative = path.lstrip("/")
    candidate = os.path.realpath(os.path.join(PUBLIC_DIR, relative))
    public_real = os.path.realpath(PUBLIC_DIR)
    if candidate.startswith(public_real + os.sep) and os.path.isfile(candidate):
        return send_from_directory(PUBLIC_DIR, relative)

    return _error(
        404,
        "Page not found",
        "That path is neither a landing page asset nor a "
        + backend.code_html("/<username>.<repository>/")
        + " repository URL.",
    )


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="Local preview server for the GitHub Website Proxy.",
    )
    parser.add_argument(
        "port_positional",
        nargs="?",
        type=int,
        default=None,
        help="port to listen on (shorthand for --port)",
    )
    parser.add_argument("--port", type=int, default=None, help="port to listen on")
    parser.add_argument(
        "--host", default=HOST, help="interface to bind (default: %(default)s)"
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="do not open a web browser automatically",
    )
    parser.add_argument(
        "--debug", action="store_true", help="enable Flask debug mode / auto-reload"
    )
    args = parser.parse_args(argv)
    if args.port is None:
        args.port = args.port_positional if args.port_positional else DEFAULT_PORT
    return args


def main(argv=None):
    args = _parse_args(argv)
    url = "http://%s:%d/" % (args.host if args.host != "0.0.0.0" else "127.0.0.1", args.port)

    print(
        "\n"
        "  GitHub Website Proxy - local preview (Flask)\n"
        "  --------------------------------------------\n"
        "  Landing page : {url}\n"
        "  Example repo : {url}octocat.Hello-World/\n"
        "  Backend      : api/github.py (the same code Vercel runs)\n"
        "  Stop         : press Ctrl+C\n".format(url=url)
    )

    if not args.no_open and not args.debug:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    # use_reloader is bound to --debug so a plain run stays on a single process.
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=args.debug,
        threaded=True,
    )


if __name__ == "__main__":
    main()
