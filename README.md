# WebProxyLive — turn public files into live websites

Serve any **public GitHub repository** or **public Google Drive folder** as a
live website on your own Vercel domain — no database, no accounts, no repository
registration, no external storage, no second backend, and **no API key**. The URL
itself carries everything the server needs. Both sources work with **zero
configuration**.

```
https://YOUR-DOMAIN.vercel.app/octocat.Hello-World/                 # GitHub
https://YOUR-DOMAIN.vercel.app/drive/1AbCdEfGhIjKlMnOpQrStUvWxYz/   # Google Drive
```

The first renders the root `index.html` of `https://github.com/octocat/Hello-World`.
The second renders the root `index.html` of a public Drive folder. Every asset
those pages reference (CSS, JavaScript, images, fonts) is served through the
**same Vercel origin**, so relative links keep working.

**No API key. No OAuth. No database. No external backend.** Google Drive support
is built on Google's own *public* endpoints, and when Google genuinely refuses to
expose a public folder without its API, the service says so honestly instead of
pretending it worked.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Folder structure](#2-folder-structure)
3. [URL formats](#3-url-formats)
4. [Exact Vercel deployment instructions](#4-exact-vercel-deployment-instructions)
5. [How GitHub file resolution works](#5-how-github-file-resolution-works)
6. [How Google Drive resolution works (no API key)](#6-how-google-drive-resolution-works-no-api-key)
7. [Routing design](#7-routing-design)
8. [Security protections](#8-security-protections)
9. [Caching configuration](#9-caching-configuration)
10. [Error handling](#10-error-handling)
11. [MIME types](#11-mime-types)
12. [Diagnostics endpoint](#12-diagnostics-endpoint)
13. [Limitations](#13-limitations)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. What it does

WebProxyLive is a stateless, read-only proxy for static files that are already
public somewhere else.

- **Two sources, one engine.** GitHub and Google Drive are resolved by the same
  shared module, so validation, headers, caching and error pages behave
  identically.
- **The URL is the configuration.** There is no database, no configuration file
  per site, and nothing to register.
- **No credentials of any kind.** No API key, no OAuth token, no service account,
  no database connection string. There is literally nothing secret to leak,
  because the deployment holds no secret.
- **Static only.** Browsers receive HTML, CSS, JavaScript, images, fonts and
  other plain files. Server-side code is never executed, and there is no
  runtime, build step or templating.
- **Safe by construction.** Upstream URLs are *built* from validated identifiers
  and a per-source host allow-list is enforced on every hop, so the service
  cannot be pointed at arbitrary hosts.
- **Honest about limits.** When a source (in practice, sometimes Google Drive)
  refuses anonymous access, the proxy returns a clear explanation naming the
  limitation and the fix — never a fake success.

What it is **not**: a CDN with global edge caching guarantees, a private-file
host, a server-side runtime, or a replacement for GitHub Pages / Drive's own
publishing. It is a lightweight, transparent way to preview and share a public
static folder.

---

## 2. Folder structure

```
.
├── app.py                    # Local Flask launcher: `python app.py`
├── api/
│   ├── _shared.py            # Shared engine: validation, fetch, MIME, errors
│   ├── github.py             # Vercel function: GitHub source router
│   ├── drive.py              # Vercel function: Google Drive source router (no key)
│   └── status.py             # Vercel function: diagnostics / capability report
├── public/
│   ├── index.html            # Landing page (independent of the proxies)
│   └── assets/
│       ├── style.css         # Landing page styles (dark + light themes)
│       ├── config.js         # Single branding / logo / nav reference
│       ├── demo.js           # Real demo files for the interactive editor
│       ├── app.js            # Landing page behaviour
│       └── favicon.svg       # Favicon
├── requirements.txt          # Production: no third-party dependencies
├── requirements-dev.txt      # Development only: Flask (for app.py)
├── vercel.json               # Rewrites, headers, function config
└── README.md
```

### Run it locally with one command

```bash
pip install -r requirements-dev.txt   # Flask, for the launcher only
python app.py
```

`app.py` is a **Flask** app. It opens a preview server, launches your browser
automatically, serves the landing page **and** emulates the deployed proxies by
importing and reusing the real logic from `api/github.py`, `api/drive.py` and
`api/status.py`, so behaviour matches production:

| URL | Result |
| --- | --- |
| `http://127.0.0.1:8000/` | Landing page |
| `http://127.0.0.1:8000/mdn.beginner-html-site-styled/` | A live GitHub repo rendered as a site |
| `http://127.0.0.1:8000/mdn.beginner-html-site-styled/styles/style.css` | Its CSS, correct `text/css` MIME |
| `http://127.0.0.1:8000/drive/FOLDER_ID/` | A public Drive folder rendered as a site |
| `http://127.0.0.1:8000/status` | Diagnostics: what is available and what is not |

Options:

```bash
python app.py --port 8080   # use a specific port
python app.py 8080          # the same thing, positionally
python app.py --no-open     # do not auto-open the browser
python app.py --debug       # Flask debug mode with auto-reload
```

`app.py` is **not** part of the Vercel deployment — Vercel installs only
`requirements.txt` (empty) and runs the functions in `api/` directly. Flask is
confined to `requirements-dev.txt` so it never inflates the serverless functions
or slows their cold start.

### Why the landing assets live in `public/assets/`

The GitHub proxy route is a catch-all:

```
/<username>.<repository>/<optional/path>
```

Any first path segment that contains a dot looks like a repository reference.
That means a file placed at the web root would collide with the proxy — e.g.
`/style.css` would be parsed as owner `style`, repo `css`, and wrongly forwarded
to GitHub. Putting the landing page's CSS/JS/favicon in `public/assets/` (no dot
in the segment) guarantees they are always served as normal static files and can
never be swallowed by the proxy route.

---

## 3. URL formats

| URL | Serves |
| --- | --- |
| `/` | The landing page |
| `/status` | Diagnostics / capability report |
| `/octocat.Hello-World/` | GitHub repository root → `index.html` |
| `/octocat.Hello-World/index.html` | A specific file |
| `/octocat.Hello-World/css/style.css` | A nested asset |
| `/octocat.Hello-World@gh-pages/` | Same repo, on the `gh-pages` branch |
| `/drive/FOLDER_ID/` | Drive folder root → `index.html`, or a directory listing |
| `/drive/FOLDER_ID/about.html` | A specific file in the folder |
| `/drive/FOLDER_ID/css/style.css` | A nested file in the folder |

For GitHub, the owner and repository are separated by a **dot**, and the whole
reference ends with a trailing slash. For Drive, the folder ID is the long token
in `https://drive.google.com/drive/folders/FOLDER_ID`.

A full Drive link can be pasted directly — the folder ID is extracted from it,
and stray punctuation from a chat message or spreadsheet cell is stripped:

```
https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing
                                              ^ the id  (…WxYz) is used
```

Branches whose name contains a slash (e.g. `feat/site`) cannot be expressed as a
single clean path segment, so use the explicit query form:

```
/api/github?owner=octocat&repo=Hello-World&branch=feat%2Fsite&file=
```

The landing page's URL generator chooses the correct form automatically.

Query strings are preserved on the URL you request; the proxy fetches the
underlying file and the browser keeps its own query string intact.

---

## 4. Exact Vercel deployment instructions

### Option A — Deploy with the Vercel CLI

```bash
# 1. From the project root:
npm i -g vercel        # install the CLI (once)

# 2. Log in
vercel login

# 3. Deploy a preview
vercel

# 4. Promote to production
vercel --prod
```

The CLI auto-detects the Python runtime from `requirements.txt` and the
file-based functions in `api/`.

### Option B — Deploy from GitHub

1. Push this folder to a GitHub repository.
2. Go to <https://vercel.com/new> and **Import** that repository.
3. Framework preset: **Other** (do not pick a framework).
4. Build command: leave empty. Output directory: `public`.
5. Click **Deploy**.

### Verify the deployment

```bash
# Landing page
curl -I https://YOUR-DOMAIN.vercel.app/

# Diagnostics
curl -s https://YOUR-DOMAIN.vercel.app/status

# GitHub proxy: repository root -> index.html
curl -I https://YOUR-DOMAIN.vercel.app/octocat.Hello-World/

# GitHub proxy: nested asset, check the Content-Type
curl -I https://YOUR-DOMAIN.vercel.app/octocat.Hello-World/css/style.css

# Drive proxy: folder root
curl -I https://YOUR-DOMAIN.vercel.app/drive/FOLDER_ID/
```

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `GITHUB_TOKEN` | no | Sends an `Authorization` header to raise GitHub's anonymous rate limit. GitHub hosting works without it. |

**There is no required environment variable.** In particular there is **no
`GOOGLE_DRIVE_API_KEY`** (it was removed), no OAuth client, no service account
and no database URL. Google Drive support runs entirely on Google's public
endpoints, so the deployment holds no credential and nothing sensitive can leak
through a misconfigured variable, a log line or a client bundle.

If you set `GITHUB_TOKEN`, set it under **Project → Settings → Environment
Variables** and redeploy. It is read only by the backend and is **never** shipped
to the browser — the frontend bundle contains no secret of any kind.

---

## 5. How GitHub file resolution works

The backend **does not** call the GitHub REST API for normal asset requests.
It fetches from GitHub's raw content host and lets GitHub resolve the default
branch for us:

```
https://raw.githubusercontent.com/<owner>/<repo>/HEAD/<path>
```

The `HEAD` ref is a symbolic ref that GitHub resolves to the repository's
**default branch** (whatever it is named — `main`, `master`, or anything else).
This means:

- **No API call** is spent resolving the default branch.
- **No API rate limit** is consumed for ordinary file requests.
- The same single code path works for every repository.

Resolution steps:

1. `vercel.json` rewrites the public URL into a call to the function with the
   parsed `owner`, `repo`, `branch` and `file` values.
2. The handler validates every component (see [Security](#8-security-protections)).
3. If the request targets a directory (the path is empty or ends in `/`),
   candidates are tried in order: `index.html`, then `index.htm`.
4. If the request targets a clean URL without a file extension (e.g. `/about`),
   the file is tried first, then `/about/index.html`.
5. The resolved file is fetched from the raw host and returned through the
   Vercel domain with correct headers, so relative `<link>`, `<script>`, `<img>`
   and `url(...)` references resolve back to the same origin.

Because assets are re-requested through `/{owner}.{repo}/...`, a stylesheet
reference such as `<link rel="stylesheet" href="css/style.css">` in `index.html`
becomes `/octocat.Hello-World/css/style.css` in the browser and is served by this
same function.

---

## 6. How Google Drive resolution works (no API key)

Drive has no raw-content host equivalent to GitHub's, so resolution is built from
the **public endpoints Google exposes to any anonymous browser**. A **folder ID
is never treated as a downloadable file ID**, and no endpoint is ever invented.

The resolver tries four layers in order and stops at the first that can serve a
real answer:

| Layer | Mechanism | What it provides |
| --- | --- | --- |
| **1 — Direct public file** | `drive.google.com/uc?export=download&id=<fileId>` | Public file bytes, including the `confirm`/`uuid` token handshake for larger files |
| **2 — Public folder discovery** | `drive.google.com/embeddedfolderview?id=<folderId>#list` | Google's own public folder listing, parsed for child names, ids, folders and file types |
| **3 — Direct asset fallback** | `drive.google.com/thumbnail?id=<id>&sz=w2000` | Public images/assets when the download endpoint defers to a viewer page |
| **4 — Honest failure** | branded HTML page | A clear explanation of why Google refused, and how to fix sharing |

Every upstream request targets `drive.google.com` only. The host is checked
against an allow-list on every hop, and URLs are **constructed** from validated
ids — the client can never supply a destination.

**Layer 2 — public folder discovery.** For each folder the resolver will descend
into, it requests Google's own embed view:

```
GET https://drive.google.com/embeddedfolderview?id=<FOLDER_ID>#list
```

The HTML is parsed for entries with a regular expression that keys on Google's
stable markers (`flip-entry` blocks carrying an `entry-<id>` identifier, an entry
title, and a `folders/<id>` link for subfolders). No API key, cookie or session
is involved — this is the same public view a browser gets for a link-shared
folder. If the response carries no recognisable entries but the page contains
"no items" markers, the folder is treated as **empty** rather than broken.

**Per-segment resolution.** The requested path is split into segments. For each
segment, the **currently resolved folder** is listed and the child is looked up
there:

1. An exact, case-sensitive name match wins.
2. Otherwise a case-insensitive match is used.
3. If several entries tie, the choice is deterministic: a real folder entry is
   preferred, then the lexicographically smallest id.

A child's id is never taken from the URL, so there is no string prefix to escape —
the request can only ever move **downward** from the selected folder.

**Layer 1/3 — direct file retrieval.** When a segment resolves to a file, its
bytes are fetched from:

```
GET https://drive.google.com/uc?export=download&id=<fileId>
```

For files large enough that Google interposes a virus-scan page, the response is
detected (by content type, by the `confirm`/`uuid` form field and by known
warning text), the token is extracted, and the request is repeated with
`id`, `confirm` and `uuid`. If the content endpoint returns a view-only page
instead, the resolver falls back to Layer 3:

```
GET https://drive.google.com/thumbnail?id=<fileId>&sz=w2000
```

**Resource keys are preserved.** When Google exposes a `resourceKey` for a
link-restricted item, it is kept on the entry and echoed on follow-up requests as
`X-Goog-Drive-Resource-Keys: <fileId>/<key>`, so files that require one still
resolve.

**Nested folders work.** `/drive/FOLDER_ID/css/style.css` resolves `css` inside
the root, then `style.css` inside `css` — one public listing per level, each
cached independently.

**Directory browser.** If `/drive/FOLDER_ID/` has no `index.html`, the proxy does
not invent a 404. It renders a professional directory listing of the folder with
subfolders, file names, type labels and clickable links (human-readable sizes are
shown when Google exposes them). This makes a public folder browsable even when
it was never intended as a website.

**Manifest fallback (a second zero-credential path).** Sometimes Google declines
to render an `embeddedfolderview` for a folder even though its files are public.
For exactly that case — and without adding any key, database or backend — you can
supply the file ids you already know, either inline or as a link to a public JSON
file:

```
/drive/FOLDER_ID/?manifest=<fileId>                       # one file, used for the whole path
/drive/FOLDER_ID/css/style.css?manifest=css/style.css:<fileId>
/drive/FOLDER_ID/?manifest=index.html:<id>;css/style.css:<id2>
/drive/FOLDER_ID/?manifest=<public-Drive-JSON-file-id>
```

JSON form:

```json
{ "index.html": "1AbCdEf…", "css/style.css": "1GhIjKl…" }
```

Manifest ids are validated (`^[A-Za-z0-9_-]{10,128}$`), so a URL-shaped value is
rejected with `400` and **never fetched** — a manifest cannot be used to reach an
arbitrary host. Invalid JSON is a `400`, and a manifest with no usable entries is
a `400` rather than a silent empty page.

**Caching.** Successful folder listings are cached in process memory with a short
TTL (a single page load can need several listings). Errors are **never** cached,
so a fix on the Drive side is picked up immediately. Nothing is persisted and no
credential is ever cached — there is none.

**Google Workspace documents** (Docs, Sheets, Slides, Forms) are not static
assets. They are reported as an unsupported file (`415`) instead of being
mis-served as HTML.

**No interstitial guessing beyond the documented handshake.** The `confirm`/`uuid`
flow is Google's own public mechanism, not a heuristic. If a response cannot be
recognised as file bytes, it is *not* streamed as if it were the asset.

### The honest limitation (please read)

Google does **not** provide unrestricted, filesystem-style, credential-free
enumeration of arbitrary public folders in every situation. The
`embeddedfolderview` endpoint is public and keyless, but it is a *view*, so it can
return a sign-in page, a "no preview" page, or nothing at all for some folders —
particularly very large folders, folders of certain file types, or folders Google
has flagged. Request C's rule is respected here: **the proxy does not fake it.**
When Layer 2 cannot enumerate a folder and Layer 1/3 cannot serve the requested
file, Layer 4 returns a branded page that states exactly that, and explains the
remedy — set the item to *Anyone with the link → Viewer*, or supply the file ids
explicitly with `?manifest=`.

Resolution steps:

1. The folder ID is validated (`^[A-Za-z0-9_-]{10,128}$`); a pasted full Drive
   link is reduced to its id and stray punctuation is stripped.
2. If a manifest maps the requested path, that id is served directly (Layers 1/3).
3. If the path is empty or ends in `/`, `index.html` then `index.htm` are tried.
4. Otherwise each segment is resolved against its parent folder by parsing the
   public listing, and the final entry must be a file.
5. The matched file is retrieved from the public content/thumbnail endpoint and
   streamed through the Vercel origin with the correct content type.

---

## 7. Routing design

`vercel.json` uses rewrites only; there is **no per-repository or per-folder
route**. Vercel path-parameter rewrites are used, and the more specific rules
are listed first because order matters — a request that matches an earlier rule
never reaches a later one:

```jsonc
{
  "outputDirectory": "public",
  "rewrites": [
    { "source": "/index.html",  "destination": "/" },
    { "source": "/favicon.ico", "destination": "/assets/favicon.svg" },

    // Diagnostics
    { "source": "/status", "destination": "/api/status" },

    // Google Drive: /drive/<folder-id>/<path>
    { "source": "/drive/:folder",        "destination": "/api/drive?folder=:folder" },
    { "source": "/drive/:folder/:file*", "destination": "/api/drive?folder=:folder&file=:file" },
    { "source": "/api/drive/:folder/:file*", "destination": "/api/drive?folder=:folder&file=:file" },

    // GitHub: /<owner>.<repo>[@branch]/<path>
    { "source": "/:owner.:repo",
      "destination": "/api/github?owner=:owner&repo=:repo" },
    { "source": "/:owner.:repo@:branch/:file*",
      "destination": "/api/github?owner=:owner&repo=:repo&branch=:branch&file=:file" },
    { "source": "/:owner.:repo/:file*",
      "destination": "/api/github?owner=:owner&repo=:repo&file=:file" }
  ]
}
```

How the parameters map:

| Parameter | Meaning | Example |
| --- | --- | --- |
| `:owner` | GitHub username | `octocat` |
| `:repo` | repository name | `Hello-World` |
| `:branch` | optional branch after `@` | `gh-pages` |
| `:file*` | optional zero-or-more-segment path | `css/style.css` |
| `:folder` | Drive folder ID | `1AbCdEf…` |

Notes:

- The separate `:owner.:repo` and `:owner.:repo/:file*` rules express “with or
  without a path”; Vercel matches the most specific applicable entry.
- Matching happens on the raw path, so values are passed explicitly as a query
  string. This avoids relying on how the runtime exposes path segments and makes
  every value easy to validate in the function.
- The backend re-checks everything (`vercel.json` is convenience routing, not the
  security boundary). Even a crafted URL that reaches a function is rejected by
  the validators in `api/_shared.py`.
- Requests without a dot in the first segment (`/assets/style.css`, `/`, `/status`)
  do not match the repository rules, so they fall through to normal static file
  serving or the diagnostics function.

Both proxies also accept a direct path form (`/api/github/<owner>.<repo>/<path>`
and `/api/drive/<folder-id>/<path>`) as a fallback, so they remain usable even if
reached without the rewrite layer.

### Shared module packaging

`api/_shared.py` holds the validation, fetch, MIME and error-page logic. Each
function adds `api/` to `sys.path` and imports it, and `vercel.json` ships it
alongside every function:

```jsonc
"functions": {
  "api/github.py": { "includeFiles": "api/_shared.py" },
  "api/drive.py":  { "includeFiles": ["api/_shared.py", "api/status.py"] },
  "api/status.py": { "includeFiles": ["api/_shared.py", "api/drive.py"] }
}
```

---

## 8. Security protections

The service is public and read-only, so validation is strict and the upstream
destination is never taken from user input directly.

**Input validation**

- GitHub owner must match `^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$` and cannot
  contain `--`.
- Repository must match `^[A-Za-z0-9._-]{1,100}$`, cannot be `.` or `..`, cannot
  end in `.git`, and cannot be a dot-prefixed traversal-like name.
- Branch is validated segment-by-segment (`[A-Za-z0-9._-]` per segment).
- Drive folder IDs must match `^[A-Za-z0-9_-]{10,128}$`.
- Manifest ids must match the same id pattern, so a manifest can never smuggle in
  a hostname or URL.
- Control characters, null bytes and surrounding whitespace are stripped or
  rejected before anything is used.

**Path traversal**

- Both `/` and `\` separators are normalised, then the path is split into
  segments.
- `..`, `.`, empty segments, backslashes, null bytes, control characters,
  over-long segments (> 255) and overly deep paths (> 40 segments) are rejected.
- Percent-encoded forms are decoded **before** validation, so `%2e%2e` cannot
  slip through.
- Drive resolution is per-segment against the previously resolved parent, so
  there is no string prefix to escape.

**SSRF / open redirect**

- Upstream URLs are **constructed**, never supplied by the client.
- A host allow-list is enforced for each source (GitHub raw hosts, Drive hosts),
  including suffix matching for regional/`usercontent` variants.
- A custom redirect handler re-validates the host on every hop, so a redirect
  cannot move the fetch to an unlisted host.
- Only HTTPS destinations are constructed.
- There is no "fetch this arbitrary URL" parameter anywhere in the API. The
  `?manifest=` parameter accepts **file ids only** — a URL-shaped value is
  rejected with `400` before any request is made.

**No stored credentials**

- The codebase contains no API key, OAuth token, service account or database
  credential, and reads none from the environment.
- The result is that there is no secret to leak via logs, error pages or the
  client bundle.

**Response hygiene**

- A fixed set of security headers is applied to responses (see below).
- Upstream `Set-Cookie` and hop-by-hop headers are dropped.
- Error pages never include stack traces or internal paths.

**Headers applied to responses**

| Header | Value |
| --- | --- |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `SAMEORIGIN` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | minimal, feature-restricting |
| `X-WebProxyLive` | `static-source-proxy` |
| `Cache-Control` | per-type, with `s-maxage` and `stale-while-revalidate` |
| `ETag` / `Last-Modified` | when the upstream provides them |

**Size limits**

- Individual files above **25 MiB** are refused with a clear message rather than
  streamed.
- Upstream requests time out after **15 seconds**.
- Parsed Drive listings are bounded (entry count and byte size) so a hostile or
  oversized response cannot exhaust memory.

---

## 9. Caching configuration

Immutable content types (CSS, JavaScript, images, fonts, media) receive a long
`s-maxage` with `stale-while-revalidate`, so repeat asset requests are served
from the edge without touching the source. HTML is cached briefly so edits
appear quickly. When the upstream provides an `ETag` or `Last-Modified`, the
proxy forwards it and answers `If-None-Match` / `If-Modified-Since` with a
`304 Not Modified` and no body.

Drive folder listings are cached in memory for a short TTL, which matters because
a single page load can require several listing lookups. Each folder is cached
independently, so a root listing is reused while a subfolder is listed once on
demand. Errors are never cached, and neither is anything secret — because the
deployment holds no credential to cache. The cache is per serverless instance and
disappears with it; nothing is persisted.

---

## 10. Error handling

Errors are rendered as branded HTML pages that match the landing page, and they
explain the problem in plain language:

- the status code,
- a short, human explanation,
- **why** it happened,
- **how to fix it**.

Drive failures are deliberately separated, so a visitor is never left guessing
between “wrong URL” and “Google refused”:

| Case | Response |
| --- | --- |
| Invalid Drive folder ID | `400` — the reference is not a folder id |
| Folder not publicly accessible | `403` — set sharing to *Anyone with the link → Viewer* |
| Folder cannot be enumerated | `403` — Google returned no public listing; explains the API limitation and the `?manifest=` alternative |
| File not found in folder | `404` — names the missing path |
| Nested path not found | `404` — names the segment that failed |
| Empty folder | a rendered empty-folder page, not a crash |
| Unsupported Drive resource | `415` — Google Docs/Sheets/Slides are not static files |
| Download blocked / quota | `403` / `429` — with the reason |
| Drive request timed out / unavailable | `504` / `502` | 
| Invalid or unusable manifest | `400` — names the problem |

The canonical Layer-4 message reads:

> **Google Drive resource unavailable** — This folder may not be publicly
> accessible, or Google may require authenticated/API access to enumerate its
> contents. Make sure the Drive folder is set to *Anyone with the link → Viewer*.

No stack traces, no internal paths, no upstream HTML is ever reflected back to
the visitor. `GET`, `HEAD` and `OPTIONS` are handled; `HEAD` returns the same
headers with no body, and `OPTIONS` returns the allowed methods.

Common cases include: `400` (malformed reference), `404` (repository, folder or
`index.html` missing), `403` (not public / Drive sign-in required), `429` (shared
GitHub anonymous rate limit), `413` (file too large), and `502` (upstream
unavailable or timed out).

---

## 11. MIME types

Content types are resolved by file extension (with a safe `application/octet-stream`
default) so browsers render CSS as `text/css`, JavaScript as `text/javascript`,
SVG as `image/svg+xml`, and so on. A correct content type is what stops modules
from being blocked and stylesheets from being ignored.

The proxy handles, among others: `html`, `htm`, `css`, `js`/`mjs`, `json`, `svg`,
`png`, `jpg`/`jpeg`, `webp`, `gif`, `ico`, `txt`, `md`, `xml`, `pdf`, `woff`,
`woff2`, `ttf`, `otf`, `eot`, `mp3`, `mp4`, `webm`, `wasm` and `map`.

---

## 12. Diagnostics endpoint

`/status` (and `/api/status`) returns a small JSON report so an operator can see
what the deployment can and cannot do — without ever exposing a secret (there are
none to expose):

```json
{
  "service": "WebProxyLive",
  "serverless": true,
  "storage": "none (stateless; in-memory cache only)",
  "requirements": {
    "api_key_required": false,
    "oauth_required": false,
    "database_required": false,
    "external_backend_required": false
  },
  "checks": {
    "github": "available",
    "google_drive": "no-key mode (limited by Google's public endpoints)"
  }
}
```

It answers `GET`, `HEAD` and `OPTIONS`. The Drive capabilities are also reported
programmatically by `api/drive.py`, which returns
`mode: "no-key"` and `folder_enumeration: "best-effort"` so the limitation is
stated in machine-readable form rather than hidden.

---

## 13. Limitations

- **Public sources only.** Private repositories and private Drive folders are not
  supported, by design — there are no user credentials and no accounts.
- **Drive folder enumeration is best-effort and keyless.** Google does not
  guarantee anonymous, filesystem-style listing of every public folder. When it
  refuses, the proxy says so plainly (Layer 4) and points you at `?manifest=`.
  This limitation is a property of Google's public endpoints, not a shortcut in
  this implementation.
- **A Drive folder big enough to be "view-only" may not be enumerable.** For
  those, supply file ids with `?manifest=`.
- **Static files only.** No PHP, no Python, no Node, no server-side includes.
- **No build step.** Files are served exactly as stored; there is no transpiling
  or bundling.
- **Absolute paths break.** A site that links to `/style.css` (domain root) will
  not resolve, because it is no longer at the domain root. Use relative paths.
- **25 MiB per file.** Larger files are refused with a clear error before any
  bytes are streamed.
- **Anonymous GitHub rate limits apply** to the shared egress IP. Setting
  `GITHUB_TOKEN` raises the ceiling.
- **No guaranteed uptime SLA.** These are serverless functions subject to Vercel
  and upstream availability.
- **Serverless execution limits apply.** Drive resolution makes a small, bounded
  number of upstream requests per file so it stays inside Vercel's time limits;
  there is no long-running process and no background job.

---

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `400` on a GitHub URL | Missing dot, or an invalid owner/repo/branch | Use `/owner.repository/`, not `/owner/repository/` |
| `404` repository not found | Repository is private or misspelled | Make it public, or check the name and case |
| `404` index missing | No `index.html` / `index.htm` at that folder | Add `index.html`, or request the file by name |
| `403` on a Drive folder | The folder is not shared publicly | Set General access to “Anyone with the link” (Viewer) |
| `403` “Google Drive resource unavailable” | Google returned no public listing for the folder | Share as *Anyone with the link → Viewer*; if it still refuses, use `?manifest=` with the file ids |
| `404` on a Drive file | The file is not a child of the resolved folder | Check the folder structure and file name, including case |
| No `index.html`, so a listing appears | The folder has no `index.html` | That is intended — the directory browser is shown. Add `index.html` for a site |
| Empty folder page | The folder really is empty | Upload files, or check you used the right folder id |
| `400` on a manifest | Invalid JSON, or no usable `path: id` entries | Send `{"index.html":"<id>"}` or `index.html:<id>`, with real ids |
| Manifest id rejected | The value looks like a URL | Manifest accepts **file ids only**, never URLs |
| `415` on a Drive file | The file is a Google Doc/Sheet/Slide | Export it to a real `.html`/`.pdf` file and re-upload |
| `429` from GitHub | Shared anonymous rate limit | Wait and retry, or set `GITHUB_TOKEN` |
| `429` from Drive | Google rate-limited the shared egress IP | Retry shortly; the listing cache reduces the number of calls |
| `504` / `502` | Upstream unavailable or timed out | Retry shortly |
| CSS/JS not loading | Absolute path in the page, or wrong file path | Use relative links such as `<link href="style.css">` |
| Images not loading | Case mismatch or unencoded spaces | Match the exact file name and case |
| `413` | File larger than 25 MiB | Keep site assets small |
| “Works locally, not here” | `.gitignore`d files or missing assets | Everything must be committed / uploaded |

---

## License

No license file is included yet. Treat this project as unlicensed until one is
added.
