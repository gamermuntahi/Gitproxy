# WebProxyLive — turn public files into live websites

Serve any **public GitHub repository** or **public Google Drive folder** as a
live website on your own Vercel domain — no database, no accounts, no repository
registration, no external storage, no second backend, no required API keys. The
URL itself carries everything the server needs.

```
https://YOUR-DOMAIN.vercel.app/octocat.Hello-World/                 # GitHub
https://YOUR-DOMAIN.vercel.app/drive/1AbCdEfGhIjKlMnOpQrStUvWxYz/   # Google Drive
```

The first renders the root `index.html` of `https://github.com/octocat/Hello-World`.
The second renders the root `index.html` of a public Drive folder. Every asset
those pages reference (CSS, JavaScript, images, fonts) is served through the
**same Vercel origin**, so relative links keep working.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Folder structure](#2-folder-structure)
3. [URL formats](#3-url-formats)
4. [Deployment instructions](#4-exact-vercel-deployment-instructions)
5. [How GitHub file resolution works](#5-how-github-file-resolution-works)
6. [How Google Drive resolution works](#6-how-google-drive-resolution-works)
7. [Routing design](#7-routing-design)
8. [Security protections](#8-security-protections)
9. [Caching configuration](#9-caching-configuration)
10. [Error handling](#10-error-handling)
11. [MIME types](#11-mime-types)
12. [Limitations](#12-limitations)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. What it does

WebProxyLive is a stateless, read-only proxy for static files that are already
public somewhere else.

- **Two sources, one engine.** GitHub and Google Drive are resolved by the same
  shared module, so validation, headers, caching and error pages behave
  identically.
- **The URL is the configuration.** There is no database, no configuration file
  per site, and nothing to register.
- **Static only.** Browsers receive HTML, CSS, JavaScript, images, fonts and
  other plain files. Server-side code is never executed, and there is no
  runtime, build step or templating.
- **Safe by construction.** Upstream URLs are *built* from validated identifiers
  and a per-source host allow-list is enforced on every hop, so the service
  cannot be pointed at arbitrary hosts.

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
│   └── drive.py              # Vercel function: Google Drive source router
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
importing and reusing the real logic from `api/github.py` and `api/drive.py`, so
behaviour matches production:

| URL | Result |
| --- | --- |
| `http://127.0.0.1:8000/` | Landing page |
| `http://127.0.0.1:8000/mdn.beginner-html-site-styled/` | A live GitHub repo rendered as a site |
| `http://127.0.0.1:8000/mdn.beginner-html-site-styled/styles/style.css` | Its CSS, correct `text/css` MIME |
| `http://127.0.0.1:8000/drive/FOLDER_ID/` | A public Drive folder rendered as a site |

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
| `/octocat.Hello-World/` | GitHub repository root → `index.html` |
| `/octocat.Hello-World/index.html` | A specific file |
| `/octocat.Hello-World/css/style.css` | A nested asset |
| `/octocat.Hello-World@gh-pages/` | Same repo, on the `gh-pages` branch |
| `/drive/FOLDER_ID/` | Drive folder root → `index.html` or `index.htm` |
| `/drive/FOLDER_ID/about.html` | A specific file in the folder |
| `/drive/FOLDER_ID/css/style.css` | A nested file in the folder |

For GitHub, the owner and repository are separated by a **dot**, and the whole
reference ends with a trailing slash. For Drive, the folder ID is the long token
in `https://drive.google.com/drive/folders/FOLDER_ID`.

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

# GitHub proxy: repository root -> index.html
curl -I https://YOUR-DOMAIN.vercel.app/octocat.Hello-World/

# GitHub proxy: nested asset, check the Content-Type
curl -I https://YOUR-DOMAIN.vercel.app/octocat.Hello-World/css/style.css

# Drive proxy: folder root
curl -I https://YOUR-DOMAIN.vercel.app/drive/FOLDER_ID/
```

### Optional environment variables

None are required. Everything works anonymously for public sources. Two optional
variables exist to improve behaviour under load or for larger Drive folders:

| Variable | Required | Purpose |
| --- | --- | --- |
| `GITHUB_TOKEN` | no | Sends an `Authorization` header to raise GitHub's anonymous rate limit |
| `DRIVE_API_KEY` / `GOOGLE_API_KEY` | no | Switches Drive folder listing from the key-free HTML view to the Drive v3 API |

Set them under **Project → Settings → Environment Variables**, then redeploy.
They are read only by the backend; they are never exposed to the browser.

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

## 6. How Google Drive resolution works

Drive has no raw-content equivalent, so the backend resolves a folder by listing
it and then downloading the matched file.

**Key-free listing (default).** The backend reads Google's public
`embeddedfolderview` HTML for the folder and extracts the direct children
(name → file/folder ID). This needs no API key and no OAuth. Listings are cached
in memory with a short TTL to avoid repeated lookups while a page's assets load.

**Optional API listing.** If `DRIVE_API_KEY` (or `GOOGLE_API_KEY`) is set, the
Drive v3 REST API is used instead, which gives more precise and complete
listings. It is entirely optional.

**Per-segment resolution.** The requested path is split into segments, and each
segment is looked up **only inside the folder that was just resolved**. A child's
ID is never taken from the URL, so there is no way to walk out of the selected
folder — traversal is structurally impossible rather than merely filtered.

**Download interstitial detection.** When Google serves a virus-scan or quota
warning page instead of the file (large files, or files it wants to confirm),
the backend detects the marker page and returns a clear error rather than
streaming HTML as if it were the real asset.

Resolution steps:

1. The folder ID is validated (`^[A-Za-z0-9_-]{10,128}$`).
2. If the path is empty or ends in `/`, `index.html` then `index.htm` are tried.
3. Otherwise each segment is resolved against its parent folder, and the final
   entry must be a file.
4. The file is downloaded from the Drive `uc?export=download` endpoint and
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
- Requests without a dot in the first segment (`/assets/style.css`, `/`) do not
  match the repository rules, so they fall through to normal static file
  serving and the landing page is untouched.

Both functions also accept a direct path form (`/api/github/<owner>.<repo>/<path>`
and `/api/drive/<folder-id>/<path>`) as a fallback, so they remain usable even if
reached without the rewrite layer.

### Shared module packaging

`api/_shared.py` holds the validation, fetch, MIME and error-page logic. Each
function adds `api/` to `sys.path` and imports it, and `vercel.json` ships it
alongside both functions:

```jsonc
"functions": {
  "api/github.py": { "includeFiles": "api/_shared.py" },
  "api/drive.py":  { "includeFiles": "api/_shared.py" }
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
- There is no "fetch this arbitrary URL" parameter anywhere in the API.

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

---

## 9. Caching configuration

Immutable content types (CSS, JavaScript, images, fonts, media) receive a long
`s-maxage` with `stale-while-revalidate`, so repeat asset requests are served
from the edge without touching the source. HTML is cached briefly so edits
appear quickly. When the upstream provides an `ETag` or `Last-Modified`, the
proxy forwards it and answers `If-None-Match` / `If-Modified-Since` with a
`304 Not Modified` and no body.

Drive folder listings are cached in memory for a short TTL, which matters because
a single page load can require several listing lookups. The cache is per
serverless instance and disappears with it; nothing is persisted.

---

## 10. Error handling

Errors are rendered as branded HTML pages that match the landing page, and they
explain the problem in plain language:

- the status code,
- a short, human explanation,
- **why** it happened,
- **how to fix it**.

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

---

## 12. Limitations

- **Public sources only.** Private repositories and private Drive folders are not
  supported, by design — there are no credentials and no accounts.
- **Static files only.** No PHP, no Python, no Node, no server-side includes.
- **No build step.** Files are served exactly as stored; there is no transpiling
  or bundling.
- **Absolute paths break.** A site that links to `/style.css` (domain root) will
  not resolve, because it is no longer at the domain root. Use relative paths.
- **25 MiB per file.** Larger files are refused; large Drive files may also
  trigger Google's download interstitial.
- **Anonymous GitHub rate limits apply** to the shared egress IP. Setting
  `GITHUB_TOKEN` raises the ceiling.
- **Drive listing depends on public HTML** unless `DRIVE_API_KEY` is set, so very
  large or unusual folders may list less precisely.
- **No guaranteed uptime SLA.** These are serverless functions subject to Vercel
  and upstream availability.

---

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `400` on a GitHub URL | Missing dot, or an invalid owner/repo/branch | Use `/owner.repository/`, not `/owner/repository/` |
| `404` repository not found | Repository is private or misspelled | Make it public, or check the name and case |
| `404` index missing | No `index.html` / `index.htm` at that folder | Add `index.html`, or request the file by name |
| `404` on a Drive file | The file is not a direct child of the resolved folder | Check the folder structure and file name |
| `403` on a Drive folder | The folder is not shared publicly | Set General access to “Anyone with the link” (Viewer) |
| CSS/JS not loading | Absolute path in the page, or wrong file path | Use relative links such as `<link href="style.css">` |
| Images not loading | Case mismatch or unencoded spaces | Match the exact file name and case |
| `429` from GitHub | Shared anonymous rate limit | Wait and retry, or set `GITHUB_TOKEN` |
| `413` | File larger than 25 MiB | Keep site assets small |
| `502` | Upstream unavailable or timed out | Retry shortly |
| Works locally, not here | `.gitignore`d files or missing assets | Everything must be committed / uploaded |

---

## License

No license file is included yet. Treat this project as unlicensed until one is
added.
