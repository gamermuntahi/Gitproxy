# GitProxy — Vercel-only GitHub website hosting/proxy

Serve any **public GitHub repository** as a live website on your own Vercel
domain — no database, no accounts, no repository registration, no external
storage, no second backend. The URL itself carries everything the server needs.

Example:

```
https://YOUR-DOMAIN.vercel.app/gamermuntahi.MyWebsite/
```

renders the repository root (`index.html`) of
`https://github.com/gamermuntahi/MyWebsite`, and every asset that
`index.html` references is served through the **same Vercel origin**.

---

## Table of contents

1. [Folder structure](#1-folder-structure)
2. [Deployment instructions](#2-exact-vercel-deployment-instructions)
3. [Example URLs](#3-example-urls)
4. [How GitHub file resolution works](#4-how-github-file-resolution-works)
5. [Routing design](#5-routing-design)
6. [Security protections](#6-security-protections)
7. [Caching configuration](#7-caching-configuration)
8. [Error handling](#8-error-handling)
9. [MIME types](#9-mime-types)
10. [Known limitations](#10-known-limitations)

---

## 1. Folder structure

```
.
├── app.py                 # One-click LOCAL launcher: `python app.py`
├── api/
│   └── github.py          # Python serverless backend (GitHub fetch proxy)
├── public/
│   ├── index.html         # Landing page (fully independent of the proxy)
│   └── assets/
│       ├── style.css      # Landing page styles
│       ├── script.js      # Landing page JS (URL builder, copy, open)
│       └── favicon.svg    # Landing page favicon
├── requirements.txt       # No third-party dependencies (see note inside)
├── vercel.json            # Rewrites, headers, function config
└── README.md
```

### Run it locally with one command

```bash
python app.py
```

This opens a preview server and launches your browser automatically. It serves
the landing page **and** emulates the deployed proxy (it imports and reuses the
real logic from `api/github.py`, so behaviour matches production):

| URL | Result |
| --- | --- |
| `http://127.0.0.1:8000/` | Landing page |
| `http://127.0.0.1:8000/mdn.beginner-html-site-styled/` | A live GitHub repo rendered as a site |
| `http://127.0.0.1:8000/mdn.beginner-html-site-styled/styles/style.css` | Its CSS, correct `text/css` MIME |

Options:

```bash
python app.py 8080          # use a specific port
python app.py --no-open     # do not auto-open the browser
```

`app.py` uses only the Python standard library and is **not** part of the Vercel
deployment — Vercel runs `api/github.py` directly. If the first port is busy the
launcher automatically tries the next ones.

### Why the landing assets live in `public/assets/`

The proxy route is a catch-all:

```
/<username>.<repository>/<optional/path>
```

Any first path segment that contains a dot looks like `<username>.<repository>`.
That means a file placed at the web root would collide with the proxy — e.g.
`/style.css` would be parsed as owner `style`, repo `css`, and wrongly forwarded
to GitHub. Putting the landing page's CSS/JS/favicon in `public/assets/`
(no dot in the segment) guarantees they are always served as normal static
files and can never be swallowed by the proxy route.

---

## 2. Exact Vercel deployment instructions

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
file-based function in `api/`.

### Option B — Deploy from GitHub

1. Push this folder to a GitHub repository.
2. Go to <https://vercel.com/new> and **Import** that repository.
3. Framework preset: **Other** (do not pick a framework).
4. Build command: leave empty. Output directory: leave empty.
5. Click **Deploy**.

### Verify the deployment

```bash
# Landing page
curl -I https://YOUR-DOMAIN.vercel.app/

# Proxy: repository root -> index.html
curl -I https://YOUR-DOMAIN.vercel.app/gamermuntahi.MyWebsite/

# Proxy: nested asset, check the Content-Type
curl -I https://YOUR-DOMAIN.vercel.app/gamermuntahi.MyWebsite/css/style.css
```

### Optional environment variables

None are required. If GitHub ever rate-limits the shared egress during heavy
use you can add a token to raise the limit (the backend will use it
automatically if present):

| Variable        | Required | Purpose                                             |
| --------------- | -------- | --------------------------------------------------- |
| `GITHUB_TOKEN`  | no       | Sends an `Authorization` header to raise rate limits |

Set it under **Project → Settings → Environment Variables**, then redeploy.

---

## 3. Example URLs

| URL                                             | Serves                                            |
| ----------------------------------------------- | ------------------------------------------------- |
| `/`                                             | The landing page                                  |
| `/gamermuntahi.MyWebsite/`                      | Repository root → `index.html`                    |
| `/gamermuntahi.MyWebsite/index.html`            | `index.html`                                      |
| `/gamermuntahi.MyWebsite/about.html`            | `about.html`                                      |
| `/gamermuntahi.MyWebsite/css/style.css`         | `css/style.css`                                   |
| `/gamermuntahi.MyWebsite/js/app.js`             | `js/app.js`                                       |
| `/gamermuntahi.MyWebsite/images/logo.png`       | `images/logo.png`                                 |
| `/octocat.Hello-World/`                         | Any other repository, same pattern                |
| `/gamermuntahi.MyWebsite@dev/`                  | Same repo, on the `dev` branch                    |

Branches whose name contains a slash (e.g. `feat/site`) cannot be expressed as a
single clean path segment, so use the explicit API form for those:

```
/api/github?owner=gamermuntahi&repo=MyWebsite&branch=feat%2Fsite&file=
```

The landing page's URL builder chooses the correct form automatically.

Query strings are preserved on the URL you request; the proxy fetches the
underlying GitHub file and the browser keeps its own query string intact.

---

## 4. How GitHub file resolution works

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
2. The handler validates every component (see [Security](#6-security-protections)).
3. If the request targets a directory (the path is empty or ends in `/`),
   candidates are tried in order: `index.html`, then `index.htm`.
4. If the request targets a clean URL without a file extension (e.g. `/about`),
   the file is tried first, then `/about/index.html`.
5. The resolved file is fetched from the raw host and returned through the
   Vercel domain with correct headers, so relative `<link>`, `<script>`, `<img>`
   and `url(...)` references resolve back to the same origin.

Because assets are re-requested through `/{owner}.{repo}/...`, a stylesheet
reference such as `<link rel="stylesheet" href="css/style.css">` in
`index.html` becomes `/gamermuntahi.MyWebsite/css/style.css` in the browser and
is served by this same function.

---

## 5. Routing design

`vercel.json` uses a single catch-all rewrite; there is **no per-repository
route**. Vercel evaluates plain `rewrites` before the deployment's own files, so
order matters and the specific rules are listed first:

```jsonc
{
  "rewrites": [
    { "source": "/index.html", "destination": "/" },   // landing entry
    { "source": "/favicon.ico", "destination": "/assets/favicon.svg" },

    // catch-all proxy (regex with capture groups)
    {
      "source": "^/([A-Za-z0-9-]+)\\.([A-Za-z0-9_.-]+?)(?:@([A-Za-z0-9._-]+))?(?:/(.*))?$",
      "destination": "/api/github?owner=$1&repo=$2&branch=$3&file=$4"
    }
  ]
}
```

The regex captures four groups:

| Group | Meaning                          | Example                  |
| ----- | -------------------------------- | ------------------------ |
| `$1`  | GitHub username                  | `gamermuntahi`           |
| `$2`  | repository name                  | `MyWebsite`              |
| `$3`  | optional branch (`@branch`)      | `dev`                    |
| `$4`  | optional file path               | `css/style.css`          |

Notes:

- The branch group is intentionally restricted to `[A-Za-z0-9._-]` (no slashes).
  Allowing slashes made the group greedy and swallowed the file path.
- `$2` is non-greedy so that a dotted repository name still captures correctly.
- Groups are read as an explicit query string, which avoids relying on how the
  runtime exposes path segments, and makes the values easy to validate.
- Requests without a dot in the first segment (`/assets/style.css`, `/`) fall
  through to normal static file serving, so the landing page is untouched.

The function also accepts a direct path form (`/api/github/<owner>.<repo>/<path>`)
as a fallback, so it remains usable even if it is reached without the rewrite.

---

## 6. Security protections

The service is public, so validation is strict and the destination is never
taken from user input directly.

**Input validation**

- Username must match `^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$` and cannot contain
  `--`.
- Repository must match `^[A-Za-z0-9._-]{1,100}$`, and cannot be `.`/`..`,
  cannot end in `.git`, and cannot be a dot-prefixed traversal-ish name.
- Branch may contain `/` but is validated segment-by-segment, rejecting
  `..`, `//`, leading/trailing slashes, backslashes and dot-prefixed segments.
- The file path is normalised into segments; `.` segments are dropped and any
  `..` segment raises an error. Backslashes, NUL bytes, over-long segments and
  overly deep paths are rejected.

**SSRF / arbitrary URL fetching**

- A destination URL is **constructed** from validated identifiers only. A user
  can never supply `https://evil-site.com/file` as the target.
- The constructed URL is re-checked against an allow-list of GitHub-owned hosts
  (`raw.githubusercontent.com`, `github.com`, `*.githubusercontent.com`, …).
- Only `https` is allowed; `http`, userinfo (`user@host`), and non-443 ports are
  rejected.
- Every redirect hop is re-validated with a custom opener, so a GitHub redirect
  can never bounce the request to a non-GitHub host.

**Header injection & traversal**

- Control characters are stripped from all parsed values.
- Outgoing header values are scrubbed of CR/LF before being sent.
- `..` is rejected in both the file path and the branch, so the raw URL can
  never escape the intended repository path.

**Content handling**

- Unknown or extension-less files are sent as `text/plain` (never `text/html`)
  with `X-Content-Type-Options: nosniff`, so repository text cannot be rendered
  as executable markup in the browser.
- Only `GET`, `HEAD` and `OPTIONS` are implemented; all other methods receive a
  `501` from the standard library's handler.

---

## 7. Caching configuration

No external cache or database is used. Caching is done with plain HTTP headers
so Vercel's CDN can store responses at the edge:

| Asset type                        | `Cache-Control`                                                          |
| --------------------------------- | ------------------------------------------------------------------------ |
| HTML (`text/html`, XML)           | `public, max-age=60, s-maxage=300, stale-while-revalidate=600`           |
| Static assets (CSS/JS/images/fonts) | `public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800`  |
| Error pages                       | `no-store`                                                               |

Additional behaviour:

- GitHub's `ETag` and `Last-Modified` are forwarded, and a client sending
  `If-None-Match` receives a `304 Not Modified`, which avoids re-downloading
  unchanged files.
- The 60-second HTML / 24-hour asset split means edited pages refresh quickly
  while images, fonts and stylesheets stay cached at the edge for a long time.
- `vercel.json` additionally sets a cache header for `/assets/*`.

Content is served with `Access-Control-Allow-Origin: *` so proxied assets can be
consumed from other origins if needed.

---

## 8. Error handling

| Situation                                  | Status | Response                          |
| ------------------------------------------ | ------ | --------------------------------- |
| Invalid URL format / bad owner or repo     | `400`  | Friendly HTML error page          |
| Invalid branch or file path (e.g. `..`)    | `400`  | Friendly HTML error page          |
| Repository or file not found / repo private| `404`  | Friendly HTML error page          |
| GitHub rate limiting (`403` / `429`)       | `403`/`429` | Message with `Retry-After` if sent |
| File too large (> 25 MiB)                  | `413`  | Friendly HTML error page          |
| GitHub unreachable / unexpected upstream   | `502`  | Friendly HTML error page          |
| Unexpected server error                    | `500`  | Friendly HTML error page          |

A missing `index.html` at the repository root results in a `404` explaining that
no entry file exists.

---

## 9. MIME types

Correct `Content-Type` headers are returned per extension (never everything as
plain text):

| Type      | Header                                                              |
| --------- | ------------------------------------------------------------------- |
| HTML      | `text/html; charset=utf-8`                                          |
| CSS       | `text/css; charset=utf-8`                                           |
| JS        | `text/javascript; charset=utf-8`                                    |
| JSON      | `application/json; charset=utf-8`                                   |
| SVG       | `image/svg+xml`                                                     |
| PNG       | `image/png`                                                         |
| JPEG      | `image/jpeg`                                                        |
| WebP      | `image/webp`                                                        |
| ICO       | `image/x-icon`                                                      |
| WOFF      | `font/woff`                                                         |
| WOFF2     | `font/woff2`                                                        |
| TTF/OTF   | `font/ttf` / `font/otf`                                             |
| WebM/MP4  | `video/webm` / `video/mp4`                                          |
| Unknown   | `application/octet-stream`                                          |
| No ext.   | `text/plain; charset=utf-8`                                         |

Manifests (`.webmanifest`), XML/RSS/Atom, PDF, WASM, and audio formats are also
mapped — see `_MIME` in [`api/github.py`](api/github.py).

---

## 10. Known limitations

- **Public repositories only.** Private repositories require authentication and
  are intentionally out of scope.
- **Branches with slashes** (e.g. `feat/site`) cannot be expressed in a single
  clean path segment. Use the API form
  (`/api/github?owner=…&repo=…&branch=feat%2Fsite`) — the landing page does this
  automatically.
- **JavaScript modules and frameworks** that use root-absolute asset URLs
  (e.g. `/images/logo.png`) will resolve to this domain's root rather than the
  repository. Relative and nested paths (including `./`) work correctly.
- **25 MiB** per-file limit to stay within serverless response limits.
- GitHub's terms of service still apply; this service only mirrors publicly
  available content.
