"""WebProxyLive - diagnostics endpoint (Vercel serverless entrypoint).

``GET /api/status`` returns a small, machine-readable report describing what
this deployment can do and which credentials it does *not* need. It exists so
users can self-diagnose without reading source code.

    {
      "service": "WebProxyLive",
      "requirements": {
        "api_key_required": false,
        "oauth_required": false,
        "database_required": false,
        "external_backend_required": false
      },
      "checks": {
        "github": {...},
        "google_drive": {...}
      }
    }

Nothing secret is ever included: there are no credentials to report, no
environment values are echoed back, and no upstream request is made (so the
endpoint is fast and cannot be used to probe third parties).
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

_API_DIR = os.path.dirname(os.path.abspath(__file__))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import _shared  # noqa: E402  (path is prepared above)


def _github_capability():
    return {
        "available": True,
        "mode": "public-repository",
        "api_key_required": False,
        "hosts": sorted(_shared.GITHUB_ALLOWED_HOSTS),
        "url_formats": [
            "/<owner>.<repository>/",
            "/<owner>.<repository>/<path>",
            "/<owner>.<repository>@<branch>/<path>",
        ],
        "notes": (
            "Public GitHub repositories are read directly from "
            "raw.githubusercontent.com. No token, account or configuration is "
            "needed."
        ),
    }


def _drive_capability():
    try:
        report = _load_drive().capabilities()
    except Exception:  # pragma: no cover - defensive
        report = {
            "mode": "no-key",
            "api_key_required": False,
            "oauth_required": False,
            "database_required": False,
            "external_backend_required": False,
            "folder_enumeration": "unavailable",
            "notes": "The Drive backend could not be loaded.",
        }
    report["available"] = True
    report["hosts"] = sorted(_shared.DRIVE_ALLOWED_HOSTS)
    report["url_formats"] = [
        "/drive/<folder-id>/",
        "/drive/<folder-id>/<path>",
        "/drive/<file-id>",
        "/drive/<folder-id>/?manifest=<file-id|inline-json>",
    ]
    return report


def _load_drive():
    """Return the Drive backend module, reusing an already loaded copy.

    The same ``drive.py`` is imported under different names depending on the
    host: Vercel imports it as the function entrypoint, while the Flask launcher
    loads it as ``webproxylive_drive``. Either way the module is stateless, so
    reusing whichever copy is already in ``sys.modules`` avoids a duplicate
    import and keeps the diagnostics report consistent.
    """
    for name in ("drive", "webproxylive_drive"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "capabilities"):
            return module
    import drive

    return drive


def report():
    """Build the diagnostics document."""
    return {
        "service": _shared.SERVICE_NAME,
        "tagline": _shared.SERVICE_TAGLINE,
        "serverless": True,
        "storage": "none (stateless; in-memory cache only)",
        "requirements": {
            "api_key_required": False,
            "oauth_required": False,
            "database_required": False,
            "external_backend_required": False,
        },
        "limits": {
            "max_file_bytes": _shared.MAX_FILE_BYTES,
            "max_path_segments": _shared.MAX_PATH_SEGMENTS,
            "max_segment_length": _shared.MAX_SEGMENT_LENGTH,
            "upstream_timeout_seconds": _shared.UPSTREAM_TIMEOUT,
        },
        "checks": {
            "github": _github_capability(),
            "google_drive": _drive_capability(),
        },
        "notes": (
            "Google Drive does not provide unrestricted public "
            "filesystem-style folder enumeration without its API in every "
            "situation, so the no-key Drive mode handles refusal gracefully and "
            "explains it instead of faking success."
        ),
    }


class handler(BaseHTTPRequestHandler):
    """Vercel entrypoint: file-based Python function handler."""

    server_version = _shared.SERVICE_NAME
    sys_version = ""

    def log_message(self, *args):  # noqa: ARG002 - keep serverless logs quiet
        return

    def _send_json(self, status, payload):
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for name, value in _shared.base_headers().items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path not in ("/api/status", "/status", "/api/status.json"):
            self._send_json(
                404,
                {
                    "service": _shared.SERVICE_NAME,
                    "error": "not_found",
                    "message": "The diagnostics endpoint lives at /api/status.",
                },
            )
            return
        try:
            payload = report()
        except Exception:  # pragma: no cover - last resort
            self._send_json(
                500,
                {
                    "service": _shared.SERVICE_NAME,
                    "error": "internal_error",
                    "message": "The diagnostics report could not be built.",
                },
            )
            return
        self._send_json(200, payload)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._handle("GET")

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._handle("HEAD")

    def do_OPTIONS(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        for name, value in _shared.base_headers().items():
            self.send_header(name, value)
        self.end_headers()
