#!/usr/bin/env python3
"""WebProxyLive - one-click local launcher (Flask edition).

Turn public files into live websites.

Run it with a single command::

    python app.py

It starts a Flask development server that behaves like the deployed Vercel
project:

* ``/`` and everything under ``/assets/`` are served from the ``public/`` folder.
* ``/<owner>.<repo>/`` and ``/<owner>.<repo>/<path>`` are proxied to GitHub.
* ``/drive/<folder-id>/`` and ``/drive/<folder-id>/<path>`` are proxied to a
  public Google Drive folder using Google's public endpoints, with **no API
  key, OAuth token, database or extra backend service**.
* ``/api/status`` (also reachable as ``/status``) reports what each backend can
  do and confirms that no credentials are required. It exposes no secrets.

Both proxies reuse the *exact* validation, fetching, MIME and caching logic that
Vercel runs in production (``api/github.py``, ``api/drive.py`` and
``api/status.py`` over the shared ``api/_shared.py`` engine), so what you see
locally is what you get deployed.

This file is a local development convenience only. The production deployment
still runs the serverless functions in ``api/``.

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
import json
import os
import re
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, Response, request, send_from_directory

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

ROOT = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.join(ROOT, "api")
GITHUB_FILE = os.path.join(API_DIR, "github.py")
DRIVE_FILE = os.path.join(API_DIR, "drive.py")
STATUS_FILE = os.path.join(API_DIR, "status.py")
PUBLIC_DIR = os.path.join(ROOT, "public")

DEFAULT_PORT = 8000
HOST = "127.0.0.1"

# The shared engine sits in api/ next to the two backends. Put that directory
# on the import path *before* loading them, because each backend does a plain
# ``import _shared`` at import time.
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)


# --------------------------------------------------------------------------- #
# Load the production backends so local behaviour matches deployment exactly
# --------------------------------------------------------------------------- #


def _load_module(module_name, file_path):
    """Import a serverless backend as a normal module without starting a server."""
    if not os.path.isfile(file_path):
        sys.stderr.write(
            "ERROR: could not find {rel} next to app.py.\n"
            "Make sure you run this file from inside the project folder.\n".format(
                rel=os.path.relpath(file_path, ROOT)
            )
        )
        raise SystemExit(1)

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit("ERROR: could not load {0}".format(file_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


import _shared  # noqa: E402  (API_DIR is prepared above)

github_backend = _load_module("webproxylive_github", GITHUB_FILE)
drive_backend = _load_module("webproxylive_drive", DRIVE_FILE)
status_backend = _load_module("webproxylive_status", STATUS_FILE)

app = Flask(__name__, static_folder=None)

#: The same catch-all shape used by the rewrite in vercel.json.
PROXY_RE = re.compile(
    r"^/([A-Za-z0-9-]+)\.([A-Za-z0-9_.-]+?)"
    r"(?:@([A-Za-z0-9._-]+))?(?:/(.*))?$"
)

#: The Google Drive shape used by the rewrite in vercel.json.
DRIVE_RE = re.compile(r"^/drive/([A-Za-z0-9_-]{10,128})(?:/(.*))?$")

#: Mirrors the aliases declared in vercel.json.
STATIC_ALIASES = {
    "index.html": "index.html",
    "favicon.ico": "assets/favicon.svg",
}

METHODS = ["GET", "HEAD", "OPTIONS"]


# --------------------------------------------------------------------------- #
# Response plumbing (converts a backend Outcome into a Flask Response)
# --------------------------------------------------------------------------- #


def _to_response(outcome, method="GET"):
    """Render a :class:`_shared.Outcome` as a Flask ``Response``.

    The outcome already carries the security headers, ``Content-Type``,
    ``Cache-Control`` and validators, so nothing is re-implemented here.
    """
    headers = dict(outcome.headers)
    body = b""
    if method != "HEAD" and outcome.status not in (204, 304):
        body = outcome.body
    return Response(body, status=outcome.status, headers=headers)


def _error(status, title, message, why=None, fix=None, method="GET"):
    """Return a Flask ``Response`` rendering the shared WebProxyLive error page."""
    return _to_response(
        _shared.html_outcome(status, title, message, why=why, fix=fix), method
    )


def _options_response():
    headers = dict(_shared.base_headers())
    headers["Allow"] = "GET, HEAD, OPTIONS"
    headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    headers["Access-Control-Allow-Headers"] = "Content-Type"
    headers["Cache-Control"] = "no-store"
    return Response(b"", status=204, headers=headers)


def _serve_outcome(outcome, method):
    """Apply conditional-request handling, then emit the response."""
    etag = outcome.headers.get("ETag")
    if outcome.status == 200 and etag:
        client_etag = request.headers.get("If-None-Match")
        if client_etag and client_etag.strip() == etag.strip():
            return _to_response(_shared.not_modified_outcome(dict(outcome.headers)), "GET")
    return _to_response(outcome, method)


# --------------------------------------------------------------------------- #
# Proxy logic (thin wrappers over the shared backend resolvers)
# --------------------------------------------------------------------------- #


def _proxy_github(owner, repo, branch, file_path, method="GET"):
    """Resolve a request against GitHub using the production backend."""
    outcome = github_backend.resolve(owner, repo, branch, file_path, method=method)
    return _serve_outcome(outcome, method)


def _proxy_drive(folder, file_path, method="GET", manifest=""):
    """Resolve a request against Google Drive using the production backend.

    No API key, OAuth token, database or extra backend is involved: the backend
    reads Google's public endpoints and reports honestly when Google refuses.
    """
    # Drive's public endpoints are requested with GET, so always fetch with GET
    # and simply omit the body for HEAD requests.
    outcome = drive_backend.resolve(folder, file_path, method="GET", manifest=manifest)
    return _serve_outcome(outcome, method)


# --------------------------------------------------------------------------- #
# Static landing page (mirrors the order of the production rewrite)
# --------------------------------------------------------------------------- #


@app.route("/", methods=METHODS)
def landing():
    if request.method == "OPTIONS":
        return _options_response()
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/index.html", methods=METHODS)
def landing_alias():
    """Mirror the ``/index.html -> /`` rewrite declared in vercel.json."""
    return landing()


@app.route("/assets/<path:filename>", methods=METHODS)
def assets(filename):
    if request.method == "OPTIONS":
        return _options_response()
    return send_from_directory(os.path.join(PUBLIC_DIR, "assets"), filename)


@app.route("/favicon.ico", methods=METHODS)
def favicon():
    if request.method == "OPTIONS":
        return _options_response()
    return send_from_directory(PUBLIC_DIR, STATIC_ALIASES["favicon.ico"])


@app.route("/api/github", methods=METHODS)
def api_github():
    if request.method == "OPTIONS":
        return _options_response()
    owner = _shared._clean(request.args.get("owner", ""))
    repo = _shared._clean(request.args.get("repo", ""))
    branch = _shared._clean(request.args.get("branch", ""))
    file_path = _shared._clean(request.args.get("file", ""))
    return _proxy_github(owner, repo, branch, file_path, request.method)


@app.route("/api/drive", methods=METHODS)
def api_drive():
    if request.method == "OPTIONS":
        return _options_response()
    folder = _shared._clean(request.args.get("folder", ""))
    file_path = _shared._clean(request.args.get("file", ""))
    manifest = _shared._clean(request.args.get("manifest", ""))
    return _proxy_drive(folder, file_path, request.method, manifest)


@app.route("/api/status", methods=METHODS)
@app.route("/status", methods=METHODS)
def api_status():
    """Mirror the diagnostics endpoint declared in vercel.json.

    The report never contains a credential, because the deployment never has
    one; it states plainly that no API key, OAuth, database or extra backend is
    required.
    """
    if request.method == "OPTIONS":
        return _options_response()
    try:
        payload = status_backend.report()
    except Exception:  # pragma: no cover - defensive
        return _error(
            500,
            "Diagnostics unavailable",
            "The diagnostics report could not be built.",
            method=request.method,
        )
    body = b"" if request.method == "HEAD" else json.dumps(
        payload, indent=2, sort_keys=True
    ).encode("utf-8")
    headers = dict(_shared.base_headers())
    headers["Content-Type"] = "application/json; charset=utf-8"
    headers["Cache-Control"] = "no-store"
    return Response(body, status=200, headers=headers)


@app.route("/<path:anything>", methods=METHODS)
def proxy(anything):  # noqa: ARG001 - the raw path is re-read below
    """Catch-all: handles ``/<owner>.<repo>/...`` and ``/drive/<folder>/...``."""
    if request.method == "OPTIONS":
        return _options_response()

    path = request.path

    drive_match = DRIVE_RE.match(path)
    if drive_match:
        return _proxy_drive(
            drive_match.group(1) or "",
            drive_match.group(2) or "",
            request.method,
            _shared._clean(request.args.get("manifest", "")),
        )

    match = PROXY_RE.match(path)
    if match:
        return _proxy_github(
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
        "That path is not a WebProxyLive page, a "
        + _shared.code_html("/<username>.<repository>/")
        + " GitHub URL, or a "
        + _shared.code_html("/drive/<folder-id>/")
        + " Google Drive URL.",
        why="The requested path did not match any route this service knows about.",
        fix="Return to the homepage and build a URL with the generator.",
        method=request.method,
    )


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="Local preview server for WebProxyLive (GitHub + Google Drive).",
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
    url = "http://%s:%d/" % (
        args.host if args.host != "0.0.0.0" else "127.0.0.1",
        args.port,
    )

    print(
        "\n"
        "  WebProxyLive - local preview (Flask)\n"
        "  -----------------------------------\n"
        "  Landing page : {url}\n"
        "  GitHub demo  : {url}octocat.Hello-World/\n"
        "  Drive demo   : {url}drive/<folder-id>/\n"
        "  Diagnostics  : {url}api/status\n"
        "  Drive mode   : no key needed - public Google endpoints only\n"
        "                 (no API key, OAuth, database or extra backend)\n"
        "  Backends     : api/github.py + api/drive.py + api/status.py\n"
        "                 (the same code Vercel runs)\n"
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
