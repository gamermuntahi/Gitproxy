/* ==========================================================================
   WebProxyLive - frontend behaviour
   --------------------------------------------------------------------------
   Vanilla JavaScript only. No frameworks, no build step.
   Everything here is progressive: if a section is absent from the page the
   matching initialiser simply does nothing.
   ========================================================================== */

(function () {
  "use strict";

  var CFG = window.WPL_CONFIG || {};
  var DEMO = window.WPL_DEMO || { files: {}, tree: [] };

  /* ---------------------------------------------------------------------- *
   * Small helpers
   * ---------------------------------------------------------------------- */

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  var AMP = "&" + "amp;";
  var LT = "&" + "lt;";
  var GT = "&" + "gt;";
  var QUOT = "&" + "#34;";
  var APOS = "&" + "#39;";

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, AMP)
      .replace(/</g, LT)
      .replace(/>/g, GT)
      .replace(/"/g, QUOT)
      .replace(/'/g, APOS);
  }

  function debounce(fn, wait) {
    var timer;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, wait);
    };
  }

  /* ---------------------------------------------------------------------- *
   * Toast
   * ---------------------------------------------------------------------- */

  var toastEl = $("#toast");
  var toastTimer;

  function toast(message, tone) {
    if (!toastEl) return;
    toastEl.hidden = false;
    toastEl.textContent = message;
    toastEl.setAttribute("data-tone", tone || "info");
    // Force a reflow so the transition always runs.
    void toastEl.offsetWidth;
    toastEl.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.classList.remove("is-visible");
      setTimeout(function () { toastEl.hidden = true; }, 300);
    }, 2600);
  }

  /* ---------------------------------------------------------------------- *
   * Clipboard
   * ---------------------------------------------------------------------- */

  function copyText(text) {
    if (!text) return Promise.reject(new Error("nothing to copy"));

    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }

    return new Promise(function (resolve, reject) {
      try {
        var area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        var ok = document.execCommand("copy");
        document.body.removeChild(area);
        ok ? resolve() : reject(new Error("copy blocked"));
      } catch (err) {
        reject(err);
      }
    });
  }

  function wireCopyButtons() {
    document.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-copy-target]");
      if (!btn) return;
      var source = document.getElementById(btn.getAttribute("data-copy-target"));
      if (!source) return;
      var value = source.textContent.trim();
      var href = source.getAttribute && source.getAttribute("href");
      if (href && href.indexOf("http") !== 0 && href.charAt(0) === "/") {
        value = window.location.origin + href;
      }
      copyText(value)
        .then(function () { toast("Copied to clipboard", "ok"); })
        .catch(function () { toast("Could not copy - select it manually", "bad"); });
    });
  }

  /* ---------------------------------------------------------------------- *
   * Branding (from the single config file)
   * ---------------------------------------------------------------------- */

  function applyBranding() {
    if (CFG.name) {
      $$("[data-brand-name]").forEach(function (node) { node.textContent = CFG.name; });
      document.title = CFG.name + " · Turn public files into live websites";
    }
    if (CFG.tagline) {
      $$("[data-brand-tagline]").forEach(function (node) { node.textContent = CFG.tagline; });
    }
    if (CFG.logo && CFG.logo.fallbackText) {
      $$("[data-brand-fallback]").forEach(function (node) {
        node.textContent = CFG.logo.fallbackText;
      });
      var headerFallback = $("#brandFallback");
      if (headerFallback) headerFallback.textContent = CFG.logo.fallbackText;
    }

    var logo = $("#brandLogo");
    var mark = logo && logo.closest(".brand-mark");
    if (logo && CFG.logo && CFG.logo.src) {
      logo.alt = CFG.logo.alt || "";
      // If the remote placeholder cannot load, the text mark stays visible.
      logo.addEventListener("load", function () {
        if (mark) mark.classList.add("has-logo");
      });
      logo.addEventListener("error", function () {
        if (mark) mark.classList.remove("has-logo");
      });
      logo.src = CFG.logo.src;
    } else if (mark) {
      mark.classList.remove("has-logo");
    }

    // Navigation
    var navList = $("#siteNav ul");
    if (navList && Array.isArray(CFG.nav)) {
      navList.innerHTML = "";
      CFG.nav.forEach(function (item) {
        var li = el("li");
        var a = el("a", "nav-link", item.label);
        a.href = item.href;
        li.appendChild(a);
        navList.appendChild(li);
      });
    }

    // Footer links
    var footerLinks = $("#footerLinks");
    if (footerLinks && Array.isArray(CFG.footerLinks)) {
      CFG.footerLinks.forEach(function (item) {
        var a = el("a", null, item.label);
        a.href = item.href;
        footerLinks.appendChild(a);
      });
    }

    // Technical status
    var footerStatus = $("#footerStatus");
    if (footerStatus && Array.isArray(CFG.status)) {
      CFG.status.forEach(function (item) {
        var row = el("div", "status-row");
        row.appendChild(el("span", "status-mark"));
        var wrapEl = el("span");
        wrapEl.appendChild(el("span", "status-label", item.label));
        wrapEl.appendChild(el("span", "status-note", item.note));
        row.appendChild(wrapEl);
        footerStatus.appendChild(row);
      });
    }
  }

  /* ---------------------------------------------------------------------- *
   * Theme
   * ---------------------------------------------------------------------- */

  var THEME_KEY = "wpl-theme";

  function setTheme(theme, announce) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}

    var toggle = $("#themeToggle");
    if (toggle) toggle.setAttribute("aria-pressed", theme === "light" ? "true" : "false");

    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "light" ? "#f6f8fc" : "#070a11");

    if (announce) toast(theme === "light" ? "Light theme" : "Dark theme");
  }

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") || "dark";
  }

  function initTheme() {
    var toggle = $("#themeToggle");
    if (!toggle) return;
    toggle.setAttribute("aria-pressed", currentTheme() === "light" ? "true" : "false");
    toggle.addEventListener("click", function () {
      setTheme(currentTheme() === "light" ? "dark" : "light", true);
    });
    // Follow the OS only while the visitor has not made an explicit choice.
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: light)");
      var onChange = function (event) {
        var saved = null;
        try { saved = localStorage.getItem(THEME_KEY); } catch (e) {}
        if (!saved) setTheme(event.matches ? "light" : "dark");
      };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  }

  /* ---------------------------------------------------------------------- *
   * Navigation: mobile menu, scroll state, active link, back to top
   * ---------------------------------------------------------------------- */

  function initNav() {
    var toggle = $("#menuToggle");
    var nav = $("#siteNav");
    if (toggle && nav) {
      toggle.addEventListener("click", function () {
        var open = nav.classList.toggle("is-open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      });
      nav.addEventListener("click", function (event) {
        if (event.target.closest("a")) {
          nav.classList.remove("is-open");
          toggle.setAttribute("aria-expanded", "false");
          toggle.setAttribute("aria-label", "Open menu");
        }
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && nav.classList.contains("is-open")) {
          nav.classList.remove("is-open");
          toggle.setAttribute("aria-expanded", "false");
          toggle.focus();
        }
      });
    }

    var header = $("#siteHeader");
    var toTop = $("#toTop");
    var onScroll = function () {
      var y = window.scrollY || window.pageYOffset;
      if (header) header.classList.toggle("is-stuck", y > 8);
      if (toTop) toTop.classList.toggle("is-visible", y > 600);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    if (toTop) {
      toTop.addEventListener("click", function () {
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    }

    // Highlight the section currently in view.
    var sections = $$("main section[id]");
    var links = $$(".nav-link");
    if (sections.length && links.length && "IntersectionObserver" in window) {
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var id = entry.target.id;
          links.forEach(function (link) {
            var isCurrent = link.getAttribute("href") === "#" + id;
            link.classList.toggle("is-current", isCurrent);
            if (isCurrent) link.setAttribute("aria-current", "true");
            else link.removeAttribute("aria-current");
          });
        });
      }, { rootMargin: "-45% 0px -50% 0px", threshold: 0 });
      sections.forEach(function (section) { observer.observe(section); });
    }
  }

  /* ---------------------------------------------------------------------- *
   * Scroll reveal
   * ---------------------------------------------------------------------- */

  function initReveal() {
    var items = $$(".reveal");
    if (!items.length) return;
    if (!("IntersectionObserver" in window)) {
      items.forEach(function (item) { item.classList.add("is-visible"); });
      return;
    }
    var observer = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          obs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });
    items.forEach(function (item) { observer.observe(item); });
  }

  /* ---------------------------------------------------------------------- *
   * URL generator
   * ---------------------------------------------------------------------- */

  var GH_OWNER_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
  var GH_REPO_RE = /^[A-Za-z0-9_.-]{1,100}$/;
  var GH_BRANCH_RE = /^[A-Za-z0-9][A-Za-z0-9._\/-]{0,254}$/;
  var DRIVE_ID_RE = /^[A-Za-z0-9_-]{10,128}$/;
  var DRIVE_URL_RE = /\/folders\/([A-Za-z0-9_-]{10,128})/;

  function parseGithub(raw) {
    var value = String(raw || "").trim();
    if (!value) return { error: "Enter a repository first." };

    value = value.replace(/^https?:\/\//i, "").replace(/\.git$/i, "");
    value = value.replace(/^www\./i, "");

    var branch = "";
    var owner = "";
    var repo = "";

    var hostMatch = /^(?:github\.com)\/(.+)$/i.exec(value);
    if (hostMatch) {
      value = hostMatch[1];
    } else if (/^github\.com$/i.test(value)) {
      return { error: "Add the owner and repository name after github.com/." };
    }

    value = value.replace(/^\/+|\/+$/g, "");
    if (!value) return { error: "Enter a repository first." };

    // owner/repo@branch or owner.repo@branch
    var atIndex = value.indexOf("@");
    var branchPart = "";
    if (atIndex !== -1) {
      branchPart = value.slice(atIndex + 1);
      value = value.slice(0, atIndex);
    }

    var parts = value.split("/").filter(Boolean);
    if (parts.length === 1) {
      var head = parts[0];
      if (head.indexOf(".") !== -1) {
        var dot = head.indexOf(".");
        owner = head.slice(0, dot);
        repo = head.slice(dot + 1);
      } else {
        repo = head;
      }
    } else {
      owner = parts[0];
      repo = parts[1];
    }

    if (!owner) {
      return {
        error: "Include the owner, for example octocat/Hello-World.",
        hint: "You can leave the owner out if you paste it as owner/repository."
      };
    }

    if (!GH_OWNER_RE.test(owner) || owner.indexOf("--") !== -1) {
      return { error: "That owner name is not a valid GitHub username." };
    }
    if (!GH_REPO_RE.test(repo) || repo === "." || repo === ".." || /\.git$/i.test(repo)) {
      return { error: "That repository name is not valid." };
    }
    if (branchPart && !GH_BRANCH_RE.test(branchPart)) {
      return { error: "That branch name is not valid." };
    }

    return { owner: owner, repo: repo, branch: branchPart };
  }

  function normalisePath(raw) {
    var value = String(raw || "").trim();
    if (!value) return { path: "", trailingSlash: false };

    if (/^https?:\/\//i.test(value)) {
      return { error: "Use a path relative to the source, not a full URL." };
    }
    if (value.indexOf("\\") !== -1) {
      return { error: "Use forward slashes, not backslashes." };
    }

    var hadLeading = value.charAt(0) === "/";
    var trailingSlash = value.charAt(value.length - 1) === "/";
    value = value.replace(/^\/+/, "");

    var segments = value.split("/").filter(function (segment) { return segment.length > 0; });
    if (segments.length > 40) {
      return { error: "That path is nested too deeply." };
    }
    for (var i = 0; i < segments.length; i += 1) {
      var segment = segments[i];
      if (segment === "." || segment === "..") {
        return { error: "Relative path segments like '..' are not allowed." };
      }
      if (segment.length > 255) {
        return { error: "One of the path segments is too long." };
      }
      if (/[\u0000-\u001f]/.test(segment)) {
        return { error: "The path contains control characters." };
      }
    }

    if (hadLeading) {
      // A leading slash means "from the source root"; it is not meaningful here.
    }

    return { path: segments.join("/"), trailingSlash: trailingSlash };
  }

  function extractDriveId(raw) {
    var value = String(raw || "").trim();
    if (!value) return { error: "Enter a Drive folder link or ID." };

    var match = DRIVE_URL_RE.exec(value);
    if (match) return { id: match[1] };

    if (/drive\.google\.com|docs\.google\.com/i.test(value)) {
      var idMatch = /[?&](?:id|folderId)=([A-Za-z0-9_-]{10,128})/.exec(value);
      if (idMatch) return { id: idMatch[1] };
      return { error: "That link does not contain a folder ID. Open the folder itself, not a file." };
    }

    value = value.replace(/^[<>"']+|[<>"']+$/g, "");
    if (!DRIVE_ID_RE.test(value)) {
      return { error: "That does not look like a Drive folder ID." };
    }
    return { id: value };
  }

  function initGenerator() {
    var card = $(".generator-card");
    if (!card) return;

    var tabs = $$(".tab", card);
    var panels = {
      github: $("#panelGithub"),
      drive: $("#panelDrive")
    };

    var ghRepo = $("#ghRepo");
    var ghBranch = $("#ghBranch");
    var ghPath = $("#ghPath");
    var drFolder = $("#drFolder");
    var drPath = $("#drPath");

    var preview = $("#urlPreview");
    var statusEl = $("#urlStatus");
    var statusText = $("#urlStatusText");
    var copyBtn = $("#copyUrl");
    var openBtn = $("#openUrl");
    var clearBtn = $("#clearUrl");
    var exampleBtn = $("#exampleUrl");

    var activeTab = "github";
    var lastUrl = "";

    function setStatus(state, text) {
      if (statusEl) statusEl.setAttribute("data-state", state);
      if (statusText) statusText.textContent = text;
    }

    function setError(input, errorEl, message) {
      if (!input || !errorEl) return;
      if (message) {
        errorEl.textContent = message;
        errorEl.hidden = false;
        input.classList.add("is-invalid");
        input.setAttribute("aria-invalid", "true");
      } else {
        errorEl.textContent = "";
        errorEl.hidden = true;
        input.classList.remove("is-invalid");
        input.removeAttribute("aria-invalid");
      }
    }

    function selectTab(name, focus) {
      activeTab = name;
      tabs.forEach(function (tab) {
        var isActive = tab.getAttribute("data-tab") === name;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
        tab.tabIndex = isActive ? 0 : -1;
        if (isActive && focus) tab.focus();
      });
      Object.keys(panels).forEach(function (key) {
        var panel = panels[key];
        if (!panel) return;
        var show = key === name;
        panel.classList.toggle("is-active", show);
        panel.hidden = !show;
      });
      update();
    }

    function update() {
      if (!preview) return;
      lastUrl = "";

      if (activeTab === "github") {
        setError(ghRepo, $("#ghRepoError"), "");
        setError(ghBranch, $("#ghBranchError"), "");
        setError(ghPath, $("#ghPathError"), "");

        var repoRaw = ghRepo ? ghRepo.value.trim() : "";
        var branchRaw = ghBranch ? ghBranch.value.trim() : "";
        var pathRaw = ghPath ? ghPath.value.trim() : "";

        if (!repoRaw && !branchRaw && !pathRaw) {
          preview.textContent = "/<username>.<repository>/";
          setStatus("idle", "Waiting for input");
          updateOpen("#");
          return;
        }

        var parsed = parseGithub(repoRaw);
        if (parsed.error) {
          setError(ghRepo, $("#ghRepoError"), parsed.error);
          preview.textContent = "/<username>.<repository>/";
          setStatus("bad", "Fix the repository");
          updateOpen("#");
          return;
        }

        var pathInfo = normalisePath(pathRaw);
        if (pathInfo.error) {
          setError(ghPath, $("#ghPathError"), pathInfo.error);
          preview.textContent = "/<username>.<repository>/";
          setStatus("bad", "Fix the path");
          updateOpen("#");
          return;
        }

        if (branchRaw && !GH_BRANCH_RE.test(branchRaw)) {
          setError(ghBranch, $("#ghBranchError"), "That branch name is not valid.");
          preview.textContent = "/<username>.<repository>/";
          setStatus("bad", "Fix the branch");
          updateOpen("#");
          return;
        }

        var url = "/" + parsed.owner + "." + parsed.repo;
        if (branchRaw) url += "@" + branchRaw;
        url += "/";
        if (pathInfo.path) url += pathInfo.path;
        if (pathInfo.trailingSlash && pathInfo.path) url += "/";

        preview.textContent = url;
        lastUrl = url;
        setStatus("ok", "Ready");
        updateOpen(url);
        return;
      }

      // Google Drive
      setError(drFolder, $("#drFolderError"), "");
      setError(drPath, $("#drPathError"), "");

      var folderRaw = drFolder ? drFolder.value.trim() : "";
      var drivePathRaw = drPath ? drPath.value.trim() : "";

      if (!folderRaw && !drivePathRaw) {
        preview.textContent = "/drive/<folder-id>/";
        setStatus("idle", "Waiting for input");
        updateOpen("#");
        return;
      }

      var folder = extractDriveId(folderRaw);
      if (folder.error) {
        setError(drFolder, $("#drFolderError"), folder.error);
        preview.textContent = "/drive/<folder-id>/";
        setStatus("bad", "Fix the folder");
        updateOpen("#");
        return;
      }

      var drivePath = normalisePath(drivePathRaw);
      if (drivePath.error) {
        setError(drPath, $("#drPathError"), drivePath.error);
        preview.textContent = "/drive/<folder-id>/";
        setStatus("bad", "Fix the path");
        updateOpen("#");
        return;
      }

      var driveUrl = "/drive/" + folder.id + "/";
      if (drivePath.path) driveUrl += drivePath.path;
      if (drivePath.trailingSlash && drivePath.path) driveUrl += "/";

      preview.textContent = driveUrl;
      lastUrl = driveUrl;
      setStatus("ok", "Ready");
      updateOpen(driveUrl);
    }

    function updateOpen(url) {
      if (!openBtn) return;
      if (!url || url === "#") {
        openBtn.setAttribute("aria-disabled", "true");
        openBtn.setAttribute("href", "#");
        openBtn.tabIndex = -1;
      } else {
        openBtn.removeAttribute("aria-disabled");
        openBtn.setAttribute("href", url);
        openBtn.tabIndex = 0;
      }
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () {
        selectTab(tab.getAttribute("data-tab"));
      });
      tab.addEventListener("keydown", function (event) {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        var next = (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        selectTab(tabs[next].getAttribute("data-tab"), true);
      });
    });

    [ghRepo, ghBranch, ghPath, drFolder, drPath].forEach(function (input) {
      if (!input) return;
      input.addEventListener("input", update);
      input.addEventListener("blur", update);
    });

    if (copyBtn) {
      copyBtn.addEventListener("click", function () {
        if (!lastUrl) {
          toast("Build a valid URL first", "bad");
          return;
        }
        copyText(window.location.origin + lastUrl)
          .then(function () { toast("URL copied", "ok"); })
          .catch(function () { toast("Could not copy", "bad"); });
      });
    }

    if (openBtn) {
      openBtn.addEventListener("click", function (event) {
        if (!lastUrl) {
          event.preventDefault();
          toast("Build a valid URL first", "bad");
        }
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        [ghRepo, ghBranch, ghPath, drFolder, drPath].forEach(function (input) {
          if (input) input.value = "";
        });
        update();
        if (ghRepo) ghRepo.focus();
        toast("Cleared");
      });
    }

    if (exampleBtn) {
      exampleBtn.addEventListener("click", function () {
        if (activeTab === "github") {
          if (ghRepo) ghRepo.value = CFG.githubExample || "octocat/Hello-World";
          if (ghBranch) ghBranch.value = "";
          if (ghPath) ghPath.value = "";
        } else {
          if (drFolder) drFolder.value = CFG.driveExample || "";
          if (drPath) drPath.value = "";
        }
        update();
        toast("Example loaded");
      });
    }

    // Open the right tab when arriving from a deep link or hero example.
    selectTab(activeTab);
  }

  /* ---------------------------------------------------------------------- *
   * VS Code demo
   * ---------------------------------------------------------------------- */

  var LANG_LABELS = {
    html: "HTML", css: "CSS", js: "JavaScript",
    json: "JSON", md: "Markdown", xml: "XML"
  };

  var KEYWORDS = [
    "function", "return", "var", "let", "const", "if", "else", "for", "while",
    "new", "this", "true", "false", "null", "document", "window", "use strict"
  ];

  function highlight(code, lang) {
    var escaped = escapeHtml(code);

    if (lang === "json") {
      return escaped
        .replace(/("[^&]*?")(\s*:)/g, '<span class="tok-key">$1</span>$2')
        .replace(/(:\s*)("[^&]*?")/g, '$1<span class="tok-str">$2</span>')
        .replace(/\b(true|false|null)\b/g, '<span class="tok-num">$1</span>')
        .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');
    }

    if (lang === "html" || lang === "xml") {
      var out = escaped
        .replace(/(<!--[\s\S]*?-->)/g, '<span class="tok-com">$1</span>')
        .replace(/(<\/?)([a-zA-Z][\w:-]*)/g, '$1<span class="tok-tag">$2</span>')
        .replace(/([a-zA-Z-]+)(=)("[^&]*?")/g, '<span class="tok-attr">$1</span>$2<span class="tok-str">$3</span>');
      return out;
    }

    if (lang === "css") {
      return escaped
        .replace(/(\/\*[\s\S]*?\*\/)/g, '<span class="tok-com">$1</span>')
        .replace(/([a-z-]+)(\s*:)([^;{}\n]+)/g, '<span class="tok-attr">$1</span>$2<span class="tok-str">$3</span>')
        .replace(/(--[\w-]+)/g, '<span class="tok-key">$1</span>')
        .replace(/(#[0-9a-fA-F]{3,8})\b/g, '<span class="tok-num">$1</span>');
    }

    if (lang === "md") {
      return escaped
        .replace(/(^|\n)(#{1,6} .*)/g, '$1<span class="tok-tag">$2</span>')
        .replace(/(`[^`\n]+`)/g, '<span class="tok-str">$1</span>')
        .replace(/(\*\*[^*\n]+\*\*)/g, '<span class="tok-key">$1</span>')
        .replace(/(```[\s\S]*?```)/g, '<span class="tok-com">$1</span>');
    }

    // JavaScript
    var jsOut = escaped
      .replace(/(\/\/[^\n]*)/g, '<span class="tok-com">$1</span>')
      .replace(/('[^&\n]*?'|"[^&\n]*?")/g, '<span class="tok-str">$1</span>');
    jsOut = jsOut.replace(new RegExp("\\b(" + KEYWORDS.join("|") + ")\\b", "g"),
      '<span class="tok-key">$1</span>');
    jsOut = jsOut.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');
    return jsOut;
  }

  function buildPreviewDocument() {
    var files = DEMO.files || {};
    var html = (files["index.html"] && files["index.html"].content) || "";
    var css = (files["style.css"] && files["style.css"].content) || "";
    var js = (files["app.js"] && files["app.js"].content) || "";
    var logo = (files["assets/logo.svg"] && files["assets/logo.svg"].content) || "";

    if (logo) {
      var dataUri = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(logo);
      html = html.split('src="assets/logo.svg"').join('src="' + dataUri + '"');
    }
    if (css) {
      html = html.replace('<link rel="stylesheet" href="style.css">',
        "<style>\n" + css + "\n</style>");
    }
    if (js) {
      html = html.replace('<script src="app.js"></script>',
        "<script>\n" + js + "\n</script>");
    }
    return html;
  }

  function initDemo() {
    var root = $("#vscode");
    if (!root) return;

    var treeEl = $("#explorerTree");
    var tabsEl = $("#editorTabs");
    var codeEl = $("#editorCode");
    var gutterEl = $("#editorGutter");
    var paneEl = $("#editorPane");
    var previewPane = $("#previewPane");
    var previewFrame = $("#previewFrame");
    var copyCodeBtn = $("#copyCode");
    var sbFile = $("#sbFile");
    var sbLang = $("#sbLang");

    var state = { active: DEMO.entry || "index.html", open: [] };

    function fileMeta(path) {
      return (DEMO.files && DEMO.files[path]) || { lang: "text", content: "" };
    }

    function renderTree() {
      if (!treeEl) return;
      treeEl.innerHTML = "";
      (DEMO.tree || []).forEach(function (node) {
        var li = el("li");
        var button = el("button", "explorer-item depth-" + (node.depth || 1));
        button.type = "button";

        var icon = el("span", "ex-icon", node.type === "folder" ? "▸" : "·");
        button.appendChild(icon);
        button.appendChild(el("span", null, node.name));

        if (node.type === "folder") {
          button.classList.add("is-folder");
          button.setAttribute("aria-disabled", "true");
        } else {
          if (node.path === state.active) button.classList.add("is-active");
          button.addEventListener("click", function () { openFile(node.path); });
        }
        li.appendChild(button);
        treeEl.appendChild(li);
      });
    }

    function renderTabs() {
      if (!tabsEl) return;
      tabsEl.innerHTML = "";
      state.open.forEach(function (path) {
        var tab = el("button", "editor-tab");
        tab.type = "button";
        tab.setAttribute("role", "tab");
        var isActive = path === state.active;
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
        if (isActive) tab.classList.add("is-active");
        tab.appendChild(el("span", null, path.split("/").pop()));

        var close = el("span", "tab-close", "×");
        close.setAttribute("role", "button");
        close.setAttribute("aria-label", "Close " + path);
        close.addEventListener("click", function (event) {
          event.stopPropagation();
          closeFile(path);
        });
        tab.appendChild(close);
        tab.addEventListener("click", function () { openFile(path, true); });
        tabsEl.appendChild(tab);
      });
    }

    function renderCode() {
      var meta = fileMeta(state.active);
      if (codeEl) codeEl.innerHTML = highlight(meta.content, meta.lang);

      if (gutterEl) {
        var lines = meta.content.replace(/\n$/, "").split("\n").length;
        var numbers = [];
        for (var i = 1; i <= lines; i += 1) numbers.push(i);
        gutterEl.textContent = numbers.join("\n");
      }
      if (sbFile) sbFile.textContent = state.active;
      if (sbLang) sbLang.textContent = LANG_LABELS[meta.lang] || "Plain text";
    }

    function openFile(path, keepFocus) {
      if (!DEMO.files || !DEMO.files[path]) return;
      state.active = path;
      if (state.open.indexOf(path) === -1) state.open.push(path);

      // Switch back to code view when a file is chosen.
      setView("code");

      renderTree();
      renderTabs();
      renderCode();
      if (keepFocus) {
        var active = $(".editor-tab.is-active", tabsEl);
        if (active) active.focus();
      }
      if (previewFrame && !previewPane.hidden) refreshPreview();
    }

    function closeFile(path) {
      var index = state.open.indexOf(path);
      if (index === -1) return;
      state.open.splice(index, 1);
      if (state.active === path) {
        state.active = state.open[index] || state.open[index - 1] || (DEMO.entry || "index.html");
        if (state.open.indexOf(state.active) === -1) state.open.push(state.active);
      }
      renderTree();
      renderTabs();
      renderCode();
    }

    function setView(view) {
      $$(".vt-btn", root).forEach(function (btn) {
        var isActive = btn.getAttribute("data-view") === view;
        btn.classList.toggle("is-active", isActive);
        btn.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      if (paneEl) paneEl.hidden = view !== "code";
      if (previewPane) previewPane.hidden = view !== "preview";
      if (view === "preview") refreshPreview();
    }

    var lastDoc = "";
    function refreshPreview() {
      if (!previewFrame) return;
      var doc = buildPreviewDocument();
      if (doc === lastDoc) return;
      lastDoc = doc;
      previewFrame.setAttribute("srcdoc", doc);
    }

    $$(".vt-btn", root).forEach(function (btn) {
      btn.addEventListener("click", function () {
        setView(btn.getAttribute("data-view"));
      });
    });

    if (copyCodeBtn) {
      copyCodeBtn.addEventListener("click", function () {
        var meta = fileMeta(state.active);
        copyText(meta.content)
          .then(function () { toast("File copied", "ok"); })
          .catch(function () { toast("Could not copy", "bad"); });
      });
    }

    // Start with the entry file open.
    state.open = [state.active];
    renderTree();
    renderTabs();
    renderCode();
  }

  /* ---------------------------------------------------------------------- *
   * Compatibility table
   * ---------------------------------------------------------------------- */

  function initCompatibility() {
    var yes = $("#compatYes");
    var no = $("#compatNo");
    if (!yes || !no) return;

    (CFG.supported || []).forEach(function (item) {
      var target = item.kind === "ok" ? yes : no;
      var li = el("li");
      var name = el("span", "compat-name");
      var code = el("span", "mono");
      code.textContent = item.name;
      name.appendChild(code);
      li.appendChild(name);
      li.appendChild(el("span", "compat-note-inline", item.note));
      target.appendChild(li);
    });
  }

  /* ---------------------------------------------------------------------- *
   * Troubleshooting entries
   * ---------------------------------------------------------------------- */

  var TROUBLESHOOTING = [
    {
      code: "400", tone: "warn", title: "Bad request",
      body: "The URL was understandable but something in it failed validation.",
      fix: "Re-check the owner, repository, branch or folder ID. Relative segments like <span class=\"mono\">..</span> and backslashes are never allowed."
    },
    {
      code: "400", tone: "warn", title: "Invalid repository format",
      body: "GitHub URLs need a dot between the owner and the repository, followed by a slash.",
      fix: "Use <span class=\"mono\">/owner.repository/</span>, not <span class=\"mono\">/owner/repository/</span>."
    },
    {
      code: "404", tone: "bad", title: "Repository not found",
      body: "GitHub returned 404, which covers a typo, a private repository and a deleted repository alike.",
      fix: "Confirm the repository is public and the spelling is exact. Private repositories cannot be served."
    },
    {
      code: "404", tone: "bad", title: "index.html is missing",
      body: "The folder was reached but no <span class=\"mono\">index.html</span> or <span class=\"mono\">index.htm</span> exists there.",
      fix: "Add <span class=\"mono\">index.html</span> to the folder you opened, or request the file by name."
    },
    {
      code: "404", tone: "bad", title: "Drive file not found",
      body: "Drive paths are resolved one segment at a time inside the selected folder.",
      fix: "Check the spelling and the nesting. File names are matched exactly, then case-insensitively."
    },
    {
      code: "403", tone: "warn", title: "GitHub rate limit",
      body: "Anonymous GitHub requests are rate limited, and the limit is shared across users.",
      fix: "Wait a short while and reload. Setting an optional <span class=\"mono\">GITHUB_TOKEN</span> raises the limit."
    },
    {
      code: "429", tone: "warn", title: "Too many requests",
      body: "Throttling was applied upstream before the file could be fetched.",
      fix: "Pause for a moment and retry. Avoid linking many large files at once."
    },
    {
      code: "413", tone: "warn", title: "File too large",
      body: "Files above 25 MB are not proxied, so the request is refused before any bytes are streamed.",
      fix: "Keep individual assets small, or host large files elsewhere and link to them directly."
    },
    {
      code: "502", tone: "bad", title: "Upstream unavailable",
      body: "The source could not be reached, or the request timed out.",
      fix: "Retry in a few seconds. If it persists, the upstream provider may be having an incident."
    },
    {
      code: "CSS", tone: "info", title: "Styles are not loading",
      body: "The stylesheet request 404s, or the page links to an absolute path that does not exist here.",
      fix: "Use a relative link such as <span class=\"mono\">&lt;link href=\"style.css\"&gt;</span>. Absolute paths that start with the domain root will not resolve."
    },
    {
      code: "JS", tone: "info", title: "Scripts are not running",
      body: "The script 404s, or it uses modules that need a matching MIME type.",
      fix: "Link the script relatively and confirm the file exists at that path. Check the browser console for the exact URL that failed."
    },
    {
      code: "IMG", tone: "info", title: "Images are not appearing",
      body: "Image paths are case-sensitive on some hosts, and spaces must be encoded.",
      fix: "Match the exact file name and case, and prefer simple names without spaces."
    },
    {
      code: "SHARE", tone: "warn", title: "Google Drive access denied",
      body: "The folder (or a file inside it) is not shared publicly, so Google returned no public listing and no public download.",
      fix: "Set the folder's General access to <span class=\"mono\">Anyone with the link</span> with Viewer rights, then reload."
    },
    {
      code: "ENUM", tone: "warn", title: "Google Drive resource unavailable",
      body: "Google returned no anonymous listing for this folder. Public folder enumeration has no guaranteed filesystem-style equivalent without Google's API.",
      fix: "Share as <span class=\"mono\">Anyone with the link</span> and retry. If Google still refuses, supply the known file IDs with <span class=\"mono\">?manifest=</span> — for example <span class=\"mono\">/drive/FOLDER_ID/?manifest=index.html:FILE_ID</span>. Check <span class=\"mono\">/status</span> for capability details."
    },
    {
      code: "LOCAL", tone: "info", title: "Works locally but not here",
      body: "The site probably relies on absolute paths, a server runtime or a build step.",
      fix: "Switch to relative paths, remove server-side code, and make sure the entry point is <span class=\"mono\">index.html</span>."
    }
  ];

  function initTroubleshooting() {
    var grid = $("#tsGrid");
    if (!grid) return;

    var search = $("#tsSearch");
    var count = $("#tsCount");
    var empty = $("#tsEmpty");
    var cards = [];

    TROUBLESHOOTING.forEach(function (item) {
      var card = el("article", "ts-card");
      card.setAttribute("data-tone", item.tone);
      card.setAttribute("data-search",
        (item.code + " " + item.title + " " + item.body + " " + item.fix)
          .replace(/<[^>]+>/g, " ").toLowerCase());

      var head = el("div", "ts-head");
      head.appendChild(el("span", "ts-code", item.code));
      head.appendChild(el("h3", null, item.title));
      card.appendChild(head);

      var body = el("p");
      body.innerHTML = item.body;
      card.appendChild(body);

      var fix = el("p", "ts-fix");
      fix.innerHTML = "<strong>Fix:</strong> " + item.fix;
      card.appendChild(fix);

      grid.appendChild(card);
      cards.push(card);
    });

    function applyFilter() {
      var query = search ? search.value.trim().toLowerCase() : "";
      var shown = 0;
      cards.forEach(function (card) {
        var match = !query || card.getAttribute("data-search").indexOf(query) !== -1;
        card.hidden = !match;
        if (match) shown += 1;
      });
      if (count) count.textContent = shown + " of " + cards.length + " shown";
      if (empty) empty.hidden = shown !== 0;
    }

    if (search) search.addEventListener("input", debounce(applyFilter, 120));
    applyFilter();
  }

  /* ---------------------------------------------------------------------- *
   * FAQ
   * ---------------------------------------------------------------------- */

  var FAQ = [
    ["What exactly is WebProxyLive?",
     "A proxy service that serves public files as a normal website. You give it a public GitHub repository or a public Google Drive folder, and it serves the files over your deployment's own domain with correct MIME types and caching."],
    ["Do I need an account?",
     "No. There are no user accounts, no sign-up and no repository registration. The URL itself carries every piece of information needed to resolve a request."],
    ["Do I need a database?",
     "No. Nothing about the source is stored. Each request is resolved on demand, and the only cache is a short-lived in-memory folder listing that disappears with the instance."],
    ["How do I host a GitHub repository?",
     "Make the repository public, add <span class=\"mono\">index.html</span>, then open <span class=\"mono\">/owner.repository/</span>. Relative links inside the repository keep working."],
    ["Will my CSS, JavaScript and images load?",
     "Yes. Files are served with MIME types derived from their extensions, and relative paths resolve through the same proxy, so ordinary static sites work unchanged."],
    ["How is the default branch detected?",
     "GitHub's <span class=\"mono\">HEAD</span> symbolic reference is used, so WebProxyLive always follows the repository's current default branch without a single API call."],
    ["Can I use a specific branch?",
     "Yes. Add <span class=\"mono\">@branch</span> before the slash: <span class=\"mono\">/owner.repo@gh-pages/</span>."],
    ["Can I link to a single file instead of a folder?",
     "Yes. Append the path, for example <span class=\"mono\">/owner.repo/about.html</span> or <span class=\"mono\">/owner.repo/css/style.css</span>."],
    ["What happens if there is no index.html?",
     "A directory request tries <span class=\"mono\">index.html</span> and then <span class=\"mono\">index.htm</span>. If neither exists you get a clear 404 page explaining what to add."],
    ["Can I host a private repository?",
     "No. Private sources cannot be read anonymously, so they are not supported. Only public repositories and publicly shared folders work."],
    ["How do I host a Google Drive folder?",
     "Create a folder, upload your site, set sharing to <span class=\"mono\">Anyone with the link</span> (Viewer), copy the folder ID, then open <span class=\"mono\">/drive/YOUR_FOLDER_ID/</span>. The generator extracts the folder ID from a full Drive link. The folder ID is never treated as a download link: the server discovers the folder through Google's public embed view and fetches each file itself."],
    ["Is a Google API key required for Drive?",
     "No. Drive hosting uses <strong>no API key, no OAuth, no database and no extra backend</strong>. It reads Google's public folder embed view, the public download endpoint and the public thumbnail endpoint. There is no credential in the deployment at all, so nothing secret can leak."],
    ["Why is my Drive folder returning 403?",
     "Either the folder is not shared publicly, or Google returned no anonymous listing for it. First set General access to <span class=\"mono\">Anyone with the link</span> so anonymous reads are allowed. If Google still refuses to enumerate it, the proxy says so honestly and you can supply the known file IDs with <span class=\"mono\">?manifest=</span>."],
    ["What is the Drive limitation with no key?",
     "Google does not provide unrestricted, filesystem-style, credential-free enumeration of arbitrary public folders in every situation. Public folder discovery here is <em>best effort</em>: when Google refuses, the proxy returns a clear explanation rather than pretending the folder is empty. Supplying known file IDs with <span class=\"mono\">?manifest=</span> works in that case without adding a key."],
    ["What is the ?manifest= parameter?",
     "A zero-credential fallback that maps a path to a Drive file ID: <span class=\"mono\">/drive/FOLDER_ID/?manifest=index.html:FILE_ID</span>, several pairs separated by <span class=\"mono\">;</span>, inline JSON, or the ID of a public JSON file in Drive. Manifest values are validated as file IDs, so a URL-shaped value is rejected and never fetched."],
    ["What happens when a Drive folder has no index.html?",
     "Instead of a 404 you get a professional directory browser listing the folder's subfolders and files with type labels and clickable links, so a public folder is still useful even if it was never built as a website."],
    ["Can Drive be used for private files?",
     "No, and it should not be. Anything in a public folder is readable by anyone who has the URL. Never place private or sensitive documents in a hosted folder."],
    ["How deep can Drive folders go?",
     "Each path segment is resolved inside the folder that the previous segment pointed at, up to 40 segments. Because lookups only ever happen inside the selected folder, escaping it is impossible."],
    ["Are there file size limits?",
     "Yes. Files above 25 MB are refused with an explanation, for both sources, so a single request can never consume excessive bandwidth."],
    ["Why did GitHub return a rate limit error?",
     "Anonymous GitHub requests are rate limited and the budget is shared. Wait a moment and retry, or set an optional <span class=\"mono\">GITHUB_TOKEN</span> environment variable to raise the limit."],
    ["Is WebProxyLive safe against SSRF?",
     "Yes by construction. The upstream destination is always built from validated identifiers, requests are restricted to an allow-list of GitHub and Google owned hosts, and every redirect hop is re-validated."],
    ["Can someone use this to fetch any URL?",
     "No. A visitor can never supply a host or a full URL for the server to fetch. Only validated owners, repositories, folder IDs and path segments are accepted."],
    ["How is path traversal prevented?",
     "<span class=\"mono\">..</span>, backslashes, null bytes, control characters and over-long or overly deep paths are rejected before anything is fetched."],
    ["Does it work without JavaScript?",
     "The landing page and its generator rely on JavaScript, but the proxy itself does not. A plain HTML link to a proxy URL works with scripts disabled."],
    ["What response headers are added?",
     "Safe security headers such as <span class=\"mono\">X-Content-Type-Options: nosniff</span> and a referrer policy. Upstream cookies and hop-by-hop headers are dropped."],
    ["How long are files cached?",
     "HTML and XML use a short cache with stale-while-revalidate. Static assets use a longer one. Entity tags and last-modified values are passed through so conditional requests work."],
    ["Does it support HTTP or HEAD?",
     "<span class=\"mono\">GET</span>, <span class=\"mono\">HEAD</span> and <span class=\"mono\">OPTIONS</span> are handled. HEAD returns the same headers without a body."],
    ["Can I run server-side code?",
     "No. There is no PHP, Node or Python runtime for hosted sites. Only static files are served, and nothing from a repository or folder is ever executed by this service."],
    ["Does it work on my phone?",
     "Yes. The interface is responsive and touch friendly, and the proxy output is plain HTML, so it renders on any device."],
    ["Is there a limit on how many projects I can host?",
     "No. Nothing is registered, so there is no list to fill up. Any number of public sources can be referenced by URL."],
    ["Do I need to deploy anything myself?",
     "Only to get your own domain. Deploy this project to Vercel and every proxy URL becomes available under your deployment's hostname. Neither GitHub nor Drive hosting needs any configuration — there is no required environment variable."],
    ["How can I check what the deployment supports?",
     "Open <span class=\"mono\">/status</span>. It reports whether the GitHub proxy is available, that Drive runs in no-key mode, and that no API key, OAuth, database or external backend is required. It never exposes a secret, because there are none."]
  ];

  function initFaq() {
    var list = $("#faqList");
    if (!list) return;

    FAQ.forEach(function (entry, index) {
      var item = el("div", "faq-item");
      var answerId = "faq-a-" + index;

      var button = el("button", "faq-q");
      button.type = "button";
      button.setAttribute("aria-expanded", "false");
      button.setAttribute("aria-controls", answerId);

      var label = el("span");
      label.innerHTML = entry[0];
      button.appendChild(label);

      var sign = el("span", "faq-sign", "+");
      sign.setAttribute("aria-hidden", "true");
      button.appendChild(sign);

      var answer = el("div", "faq-a");
      answer.id = answerId;
      var paragraph = el("p");
      paragraph.innerHTML = entry[1];
      answer.appendChild(paragraph);

      button.addEventListener("click", function () {
        var open = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", open ? "false" : "true");
        answer.style.maxHeight = open ? "0px" : answer.scrollHeight + 24 + "px";
      });

      item.appendChild(button);
      item.appendChild(answer);
      list.appendChild(item);
    });
  }

  /* ---------------------------------------------------------------------- *
   * Docs tabs
   * ---------------------------------------------------------------------- */

  function initDocs() {
    var tabs = $$(".doc-tab");
    if (!tabs.length) return;

    function activate(name, focus) {
      tabs.forEach(function (tab) {
        var isActive = tab.getAttribute("data-doc") === name;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
        tab.tabIndex = isActive ? 0 : -1;
        if (isActive && focus) tab.focus();
      });
      $$("[data-doc-panel]").forEach(function (panel) {
        var show = panel.getAttribute("data-doc-panel") === name;
        panel.classList.toggle("is-active", show);
        panel.hidden = !show;
      });
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { activate(tab.getAttribute("data-doc")); });
      tab.addEventListener("keydown", function (event) {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        var next = (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        activate(tabs[next].getAttribute("data-doc"), true);
      });
    });
  }

  /* ---------------------------------------------------------------------- *
   * Keyboard shortcuts
   * ---------------------------------------------------------------------- */

  function initShortcuts() {
    document.addEventListener("keydown", function (event) {
      var target = event.target;
      var typing = target && (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable
      );

      if (typing && event.key !== "Escape") return;

      if (event.key === "/" || (event.key === "g" && !event.ctrlKey && !event.metaKey)) {
        var repoInput = $("#ghRepo");
        if (repoInput) {
          event.preventDefault();
          var generator = $("#generator");
          if (generator) generator.scrollIntoView({ behavior: "smooth", block: "start" });
          setTimeout(function () { repoInput.focus(); }, 320);
        }
        return;
      }

      if (event.key === "t" || event.key === "T") {
        setTheme(currentTheme() === "light" ? "dark" : "light", true);
      }
    });
  }

  /* ---------------------------------------------------------------------- *
   * Boot
   * ---------------------------------------------------------------------- */

  function boot() {
    applyBranding();
    initTheme();
    initNav();
    initReveal();
    initGenerator();
    initDemo();
    initCompatibility();
    initTroubleshooting();
    initFaq();
    initDocs();
    wireCopyButtons();
    initShortcuts();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
