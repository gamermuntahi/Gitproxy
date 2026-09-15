"""WebProxyLive - GitHub source proxy (Vercel serverless entrypoint).

This module serves the contents of *public* GitHub repositories through a
Vercel domain, so a repository can behave like a normal static website. It is
the GitHub half of WebProxyLive; the Google Drive half lives in
``api/drive.py``, and both share the engine in ``api/_shared.py``.

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
supply an arbitrary destination URL, so SSRF, protocol injection and path
traversal are structurally impossible.
"""

from __future__ import annotations

import html
import os
import sys
from urllib.parse import parse_qs, urlparse

# The shared engine sits next to this file. Put that directory on the import
# path so the module loads both as a Vercel function and when the local Flask
# launcher (app.py) imports *this* file directly with importlib.
_API_DIR = os.path.dirname(os.path.abspath(__file__))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import _shared  # noqa: E402  (path is prepared above)
from _shared import (  # noqa: E402
    DEFAULT_REF,
    GITHUB_ALLOWED_HOSTS,
    GITHUB_ALLOWED_HOST_SUFFIXES,
    GITHUB_RAW_BASE,
    ProxyHandler,
    build_github_url,
    code_html,
    fetch_url,
    file_outcome,
    html_outcome,
    normalise_path,
    resolve_candidates,
    valid_branch,
    valid_owner,
    valid_repo,
)

#: Backwards-compatible aliases (the original module exposed these names).
RAW_HOST = _shared.GITHUB_RAW_HOST
RAW_BASE = GITHUB_RAW_BASE
ALLOWED_HOSTS = GITHUB_ALLOWED_HOSTS
ALLOWED_HOST_SUFFIXES = GITHUB_ALLOWED_HOST_SUFFIXES
INDEX_CANDIDATES = _shared.INDEX_CANDIDATES


def build_raw_url(owner, repo, ref, segments):
    """Build a ``raw.githubusercontent.com`` URL from validated components."""
    return build_github_url(RAW_BASE, owner, repo, ref, segments)


def fetch(owner, repo, ref, segments, method="GET"):
    """Fetch a single file from GitHub's raw host (SSRF-safe, allow-listed).

    ``ref`` is either a validated branch name or ``"HEAD"`` for the default
    branch. Never raises for HTTP level failures; the status is reported back
    so the caller can map it to a user-friendly response.
    """
    url = build_raw_url(owner, repo, ref, segments)
    return fetch_url(
        url,
        method=method,
        allowed_hosts=ALLOWED_HOSTS,
        allowed_suffixes=ALLOWED_HOST_SUFFIXES,
        # Optional: a token raises GitHub's rate limit. It is never required and
        # the service works fully without it (raw content is not rate limited as
        # aggressively as the REST API). Only used if explicitly configured.
        token=os.environ.get("GITHUB_TOKEN"),
    )


# --------------------------------------------------------------------------- #
# Resolution (shared by the Vercel handler and the Flask launcher)
# --------------------------------------------------------------------------- #


def resolve(owner, repo, branch, file_path, method="GET"):
    """Resolve a GitHub request into a :class:`_shared.Outcome`."""
    owner, repo = _shared._clean(owner), _shared._clean(repo)
    branch, file_path = _shared._clean(branch), _shared._clean(file_path)

    if not owner or not repo:
        return html_outcome(
            400,
            "Invalid repository URL",
            "Use the format "
            + code_html("/<username>.<repository>/")
            + ", for example "
            + code_html("/octocat.Hello-World/")
            + ".",
            why="The URL did not contain both a username and a repository name.",
            fix="Enter a repository as "
            + code_html("owner/repo")
            + " (or paste a full GitHub URL) into the generator on the homepage.",
        )

    if not valid_owner(owner) or not valid_repo(repo):
        return html_outcome(
            400,
            "Invalid GitHub repository",
            "That does not look like a valid GitHub username and repository name.",
            why="Usernames are 1-39 letters, digits or single hyphens. Repository "
            "names may contain letters, digits, dots, hyphens and underscores.",
            fix="Double-check the spelling on github.com and try again.",
        )

    use_branch = bool(branch)
    if use_branch and not valid_branch(branch):
        return html_outcome(
            400,
            "Invalid branch name",
            "The branch name is not valid.",
            why="A branch must be a clean relative path: no leading or trailing "
            "slash, no " + code_html("..") + " and no backslashes.",
            fix="Leave the branch field empty to use the default branch.",
        )
    ref = branch if use_branch else DEFAULT_REF

    try:
        segments = normalise_path(file_path)
    except ValueError as exc:
        return html_outcome(
            400,
            "Invalid file path",
            html.escape(str(exc)),
            why="The path contained a segment that is never valid in a URL.",
            fix="Link to a real file inside the repository instead.",
        )

    candidates, _directory_request = resolve_candidates(segments, file_path)

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
        return html_outcome(
            500,
            "Unexpected error",
            "The server could not process the request.",
        )

    if result.status == 200:
        served_path = "/".join(chosen or segments) or "index.html"
        return file_outcome(result, served_path)

    requested = file_path or "index.html"

    if result.status == 404:
        return html_outcome(
            404,
            "Repository or file not found",
            "The repository "
            + code_html(f"{owner}/{repo}")
            + " is either missing, private, or the requested file "
            + code_html(requested)
            + " does not exist. The default branch is used automatically.",
            why="GitHub returned 404 for the constructed raw URL. The usual "
            "causes are a typo in the owner or repository, a private repository, "
            "or a missing " + code_html("index.html") + ".",
            fix="Confirm the repository is public, that the file exists, and that "
            + code_html("index.html")
            + " sits in the folder you opened.",
        )

    if result.status in (403, 429):
        return html_outcome(
            result.status,
            "GitHub is rate limiting this service",
            "GitHub temporarily refused the request. Please wait a moment and try "
            "again.",
            why="GitHub applies rate limits to anonymous requests. The limit is "
            "shared, so a busy moment can briefly block new files.",
            fix="Wait a short while and reload. Setting an optional "
            + code_html("GITHUB_TOKEN")
            + " environment variable raises the limit.",
        )

    if result.status == 413:
        return html_outcome(
            413,
            "File too large",
            "That file exceeds the maximum size this proxy will serve.",
            why="Files above " + code_html("25 MiB") + " are not proxied.",
            fix="Reference the large file directly instead of through the proxy.",
        )

    if result.status == 400:
        return html_outcome(
            400, "Invalid request", "The request could not be processed."
        )

    # 0 / 5xx / anything else -> upstream problem.
    return html_outcome(
        502,
        "GitHub is unavailable",
        "The service could not reach GitHub. Please try again shortly.",
        why="The upstream request failed or timed out.",
        fix="Retry in a few seconds. If it keeps failing, GitHub may be having an "
        "incident.",
    )


# --------------------------------------------------------------------------- #
# Vercel entrypoint
# --------------------------------------------------------------------------- #


class handler(ProxyHandler):
    """Vercel entrypoint: file-based Python function handler."""

    source_label = "GitHub"

    def _parse_target(self):
        """Return (owner, repo, branch, file_path) from the request."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)

        def first(name):
            values = query.get(name)
            return _shared._clean(values[0]) if values else ""

        owner = first("owner")
        repo = first("repo")
        branch = first("branch")
        file_path = first("file")

        # Fallback: allow direct /api/github/<owner>.<repo>/<path> access so the
        # function is still usable even if reached without the rewrite layer.
        if not owner or not repo:
            cleaned = _shared._clean(parsed.path)
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
            outcome = resolve(owner, repo, branch, file_path, method=method)

            # Conditional requests need the client headers, so they are handled
            # here rather than inside resolve().
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
