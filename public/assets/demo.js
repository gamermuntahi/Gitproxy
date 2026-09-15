/* ==========================================================================
   WebProxyLive - demo project for the interactive editor
   --------------------------------------------------------------------------
   These are real, working files (not placeholders) used by the miniature
   editor and the live PREVIEW tab so visitors can see exactly what a hosted
   static site looks like.

   IMPORTANT: this data is only ever rendered as text inside a sandboxed
   iframe / <pre>. It is never eval'd, never injected into this document, and
   never fetched from a user-supplied URL.
   ========================================================================== */

window.WPL_DEMO = {
  projectName: "my-site",
  entry: "index.html",

  /* Folder structure shown in the editor's explorer. */
  tree: [
    { type: "folder", name: "assets", depth: 1 },
    { type: "file", name: "logo.svg", path: "assets/logo.svg", depth: 2 },
    { type: "file", name: "index.html", path: "index.html", depth: 1 },
    { type: "file", name: "style.css", path: "style.css", depth: 1 },
    { type: "file", name: "app.js", path: "app.js", depth: 1 },
    { type: "file", name: "config.json", path: "config.json", depth: 1 },
    { type: "file", name: "README.md", path: "README.md", depth: 1 }
  ],

  files: {
    "index.html": {
      lang: "html",
      content: `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>My Static Site</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="hero">
    <img src="assets/logo.svg" alt="" width="48" height="48">
    <h1>Hello from a public repository</h1>
    <p>Everything here is a plain file. No build step, no server.</p>
    <button id="count" type="button">Clicked 0 times</button>
  </header>

  <footer>
    <p>Served by <a href="/">WebProxyLive</a></p>
  </footer>

  <script src="app.js"></script>
</body>
</html>
`
    },

    "style.css": {
      lang: "css",
      content: `:root {
  --ink: #e8ecf3;
  --dim: #9aa7bd;
  --accent: #5eead4;
  --panel: #0f1622;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: radial-gradient(circle at 30% 20%, #182034, #0a0e16 70%);
  color: var(--ink);
  font: 16px/1.6 system-ui, sans-serif;
}

.hero {
  max-width: 34rem;
  padding: 3rem 2.5rem;
  border: 1px solid #223049;
  border-radius: 18px;
  background: var(--panel);
  text-align: center;
}

h1 { margin: 1rem 0 0.5rem; font-size: 1.6rem; }
p  { color: var(--dim); margin: 0 0 1.5rem; }

button {
  border: 1px solid var(--accent);
  background: transparent;
  color: var(--accent);
  padding: 0.6rem 1.1rem;
  border-radius: 999px;
  font: inherit;
  cursor: pointer;
}

button:hover { background: rgba(94, 234, 212, 0.12); }

footer p { margin-top: 2rem; font-size: 0.85rem; }
a { color: var(--accent); }
`
    },

    "app.js": {
      lang: "js",
      content: `// A tiny bit of interactivity - this is all static hosting needs.
(function () {
  "use strict";

  var button = document.getElementById("count");
  var clicks = 0;

  if (!button) return;

  button.addEventListener("click", function () {
    clicks += 1;
    button.textContent = "Clicked " + clicks + " time" + (clicks === 1 ? "" : "s");
  });
})();
`
    },

    "config.json": {
      lang: "json",
      content: `{
  "site": "my-site",
  "version": "1.0.0",
  "source": "public repository",
  "features": {
    "relativePaths": true,
    "caching": true,
    "buildStep": false
  },
  "pages": ["index.html"]
}
`
    },

    "assets/logo.svg": {
      lang: "xml",
      content: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <circle cx="32" cy="32" r="29" fill="none" stroke="#5eead4" stroke-width="4"/>
  <path d="M12 32h40M32 8c10 12 10 36 0 48M32 8c-10 12-10 36 0 48"
        fill="none" stroke="#5eead4" stroke-width="3"/>
</svg>
`
    },

    "README.md": {
      lang: "md",
      content: `# My Static Site

A tiny static site that lives in a public repository.

## Files

- \`index.html\` - the page
- \`style.css\` - the styles
- \`app.js\` - a click counter
- \`assets/logo.svg\` - the logo

## Hosting

Because every path is relative, the same files work unchanged when they are
served through WebProxyLive.

\`\`\`text
https://your-deployment.vercel.app/<owner>.<repository>/
\`\`\`

No build step is required.
`
    }
  }
};
