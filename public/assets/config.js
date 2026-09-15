/* ==========================================================================
   WebProxyLive - central configuration
   --------------------------------------------------------------------------
   This is the SINGLE place to change branding, the logo and shared copy.
   Nothing else in the frontend hard-codes these values, so they can be
   updated in one edit.

   The logo is intentionally a temporary, publicly available placeholder image.
   Swap `logo.src` for a local file (for example "/assets/logo.svg") when a
   real logo is ready. If the remote image fails to load the header falls back
   to a text mark automatically, so nothing ever looks broken.
   ========================================================================== */

window.WPL_CONFIG = {
  name: "WebProxyLive",
  tagline: "Static websites, served from where you already keep your files.",
  headline: "Turn public files into live websites.",
  subhead:
    "Point WebProxyLive at a public GitHub repository or a public Google Drive " +
    "folder and get a real, shareable website URL in seconds. No database, " +
    "no accounts, no build step.",

  /* ----- Logo (single reference) ---------------------------------------- */
  logo: {
    // Temporary public placeholder image. It is one clearly identifiable
    // reference; replace it here in one edit when a final logo exists.
    src: "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/1f310.svg",
    alt: "WebProxyLive logo",
    // Shown if the remote image cannot be loaded (offline, blocked, 404).
    fallbackText: "WL"
  },

  /* ----- Primary navigation -------------------------------------------- */
  nav: [
    { href: "#home", label: "Home" },
    { href: "#how", label: "How It Works" },
    { href: "#github", label: "GitHub" },
    { href: "#drive", label: "Google Drive" },
    { href: "#docs", label: "Docs" },
    { href: "#faq", label: "FAQ" }
  ],

  /* ----- Footer links -------------------------------------------------- */
  footerLinks: [
    { href: "#github", label: "GitHub" },
    { href: "#docs", label: "Documentation" },
    { href: "#faq", label: "FAQ" },
    { href: "#security", label: "Security" },
    { href: "#troubleshooting", label: "Troubleshooting" }
  ],

  /* ----- Runtime guarantees (displayed as technical status, not uptime) */
  status: [
    { label: "Vercel Serverless", note: "Python functions, no servers to manage" },
    { label: "GitHub Support", note: "Public repositories and raw content" },
    { label: "Google Drive Support", note: "Public folders, no API key needed" },
    { label: "No API Key / OAuth", note: "Google's public endpoints only" },
    { label: "No Database", note: "The URL carries all the information" }
  ],

  /* ----- Examples used by the generators and cards --------------------- */
  githubExample: "octocat/Hello-World",
  driveExample: "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456",

  githubRepos: [
    { owner: "octocat", repo: "Hello-World", label: "Hello-World" },
    { owner: "gamermuntahi", repo: "MyWebsite", label: "MyWebsite" },
    { owner: "mdn", repo: "beginner-html-site-styled", label: "beginner-html-site-styled" }
  ],

  /* ----- Compatibility table ------------------------------------------- */
  supported: [
    { name: "index.html", kind: "ok", note: "Served automatically at the folder root" },
    { name: ".html", kind: "ok", note: "Static HTML pages" },
    { name: ".css", kind: "ok", note: "Stylesheets with correct MIME type" },
    { name: ".js", kind: "ok", note: "Client-side JavaScript" },
    { name: ".json", kind: "ok", note: "Static data files" },
    { name: ".svg .png .jpg .webp", kind: "ok", note: "Images, served with caching" },
    { name: ".woff2 .ttf .otf", kind: "ok", note: "Web fonts" },
    { name: ".md .txt .xml", kind: "ok", note: "Text, Markdown and feeds" },
    { name: ".mp4 .webm .mp3", kind: "ok", note: "Media, as long as it is under 25 MB" },
    { name: "PHP / Node / Python", kind: "no", note: "No server-side runtime is provided" },
    { name: "Databases, APIs", kind: "no", note: "Static files only - nothing executes" },
    { name: "Drive manifest", kind: "ok", note: "Supply known file IDs with ?manifest= - still no key" },
    { name: "Private sources", kind: "no", note: "Repositories and folders must be public" }
  ]
};
