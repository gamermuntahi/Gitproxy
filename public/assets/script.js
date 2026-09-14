/* GitProxy landing page behaviour.
 * Pure vanilla JS: parses a GitHub repository reference, builds a proxy URL,
 * and wires up copy / open interactions. No frameworks, no dependencies.
 */
(function () {
  "use strict";

  var OWNER_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
  var REPO_RE = /^[A-Za-z0-9._-]{1,100}$/;

  var form = document.getElementById("builderForm");
  var repoInput = document.getElementById("repoInput");
  var branchInput = document.getElementById("branchInput");
  var generateBtn = document.getElementById("generateBtn");
  var openBtn = document.getElementById("openBtn");
  var errorEl = document.getElementById("builderError");
  var resultEl = document.getElementById("result");
  var resultUrlEl = document.getElementById("resultUrl");
  var copyBtn = document.getElementById("copyBtn");
  var toastEl = document.getElementById("toast");
  var heroExample = document.getElementById("heroExample");

  /* Show the real deployment origin instead of the placeholder. */
  function applyOrigin() {
    if (!heroExample) return;
    var placeholder = heroExample.querySelector(".origin-placeholder");
    if (placeholder) {
      placeholder.textContent = window.location.origin;
    }
  }

  function showToast(message) {
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.hidden = false;
    /* force reflow so the transition runs */
    void toastEl.offsetWidth;
    toastEl.classList.add("is-visible");
    window.clearTimeout(showToast._timer);
    showToast._timer = window.setTimeout(function () {
      toastEl.classList.remove("is-visible");
      window.setTimeout(function () {
        toastEl.hidden = true;
      }, 220);
    }, 1800);
  }

  function showError(message) {
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.hidden = false;
    if (repoInput) repoInput.setAttribute("aria-invalid", "true");
  }

  function clearError() {
    if (!errorEl) return;
    errorEl.hidden = true;
    errorEl.textContent = "";
    if (repoInput) repoInput.removeAttribute("aria-invalid");
  }

  /* Parse "owner/repo", "owner/repo@branch" or any github.com URL. */
  function parseRepo(raw) {
    if (!raw) return null;
    var value = raw.trim();

    /* Full GitHub URL variants: https://github.com/o/r(.git)(/tree/branch)... */
    var urlMatch = value.match(
      /^https?:\/\/(?:www\.)?github\.com\/([^/\s]+)\/([^/\s#?]+)(?:\/tree\/([^\s#?]+))?/i
    );
    if (urlMatch) {
      return {
        owner: decodeURIComponent(urlMatch[1]),
        repo: stripGit(decodeURIComponent(urlMatch[2])),
        branch: urlMatch[3] ? decodeURIComponent(urlMatch[3]) : ""
      };
    }

    /* Strip a leading github.com/ that was pasted without a scheme. */
    value = value.replace(/^(?:www\.)?github\.com\//i, "");

    var branch = "";
    var atIndex = value.lastIndexOf("@");
    if (atIndex > -1) {
      branch = value.slice(atIndex + 1).trim();
      value = value.slice(0, atIndex);
    }

    var parts = value.replace(/^\/+|\/+$/g, "").split("/");
    if (parts.length < 2) return null;

    return {
      owner: parts[0].trim(),
      repo: stripGit(parts[1].trim()),
      branch: branch
    };
  }

  function stripGit(name) {
    return name.replace(/\.git$/i, "");
  }

  /* Clean URLs encode the branch in a single path segment, so a branch with a
   * slash (e.g. feat/site) cannot be expressed there. We still accept it and
   * point the user at the explicit API form of the URL. */
  function validateBranch(branch) {
    if (!branch) return true;
    if (branch.length > 255) return false;
    if (branch.indexOf("..") !== -1) return false;
    if (branch.indexOf("//") !== -1) return false;
    if (/^[/\\]|[\\/]$/.test(branch)) return false;
    return /^[A-Za-z0-9._\/-]+$/.test(branch);
  }

  function branchHasSlash(branch) {
    return !!branch && branch.indexOf("/") !== -1;
  }

  function buildApiUrl(owner, repo, branch, file) {
    var query =
      "?owner=" +
      encodeURIComponent(owner) +
      "&repo=" +
      encodeURIComponent(repo) +
      (branch ? "&branch=" + encodeURIComponent(branch) : "") +
      (file ? "&file=" + encodeURIComponent(file) : "");
    return window.location.origin + "/api/github" + query;
  }

  function buildUrl(owner, repo, branch) {
    /* Branches with a slash cannot be expressed as a single clean path
     * segment, so fall back to the explicit API form of the URL. */
    if (branchHasSlash(branch)) {
      return buildApiUrl(owner, repo, branch, "");
    }
    if (branch) {
      return window.location.origin + "/" + owner + "." + repo + "@" + branch + "/";
    }
    return window.location.origin + "/" + owner + "." + repo + "/";
  }

  function resolveTarget() {
    var parsed = parseRepo(repoInput ? repoInput.value : "");
    var typedBranch = branchInput ? branchInput.value.trim() : "";

    if (!parsed) {
      showError(
        'Enter a repository as "owner/repo" (for example: gamermuntahi/MyWebsite).'
      );
      return null;
    }

    if (!OWNER_RE.test(parsed.owner)) {
      showError('"' + parsed.owner + '" is not a valid GitHub username.');
      return null;
    }

    if (!REPO_RE.test(parsed.repo)) {
      showError('"' + parsed.repo + '" is not a valid repository name.');
      return null;
    }

    var branch = typedBranch || parsed.branch || "";

    if (!validateBranch(branch)) {
      showError("That branch name is not valid.");
      return null;
    }

    clearError();
    return { owner: parsed.owner, repo: parsed.repo, branch: branch };
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    /* Fallback for older / non-secure contexts. */
    return new Promise(function (resolve, reject) {
      try {
        var temp = document.createElement("textarea");
        temp.value = text;
        temp.setAttribute("readonly", "");
        temp.style.position = "fixed";
        temp.style.opacity = "0";
        document.body.appendChild(temp);
        temp.select();
        var ok = document.execCommand("copy");
        document.body.removeChild(temp);
        ok ? resolve() : reject(new Error("copy failed"));
      } catch (err) {
        reject(err);
      }
    });
  }

  function renderResult(target) {
    var url = buildUrl(target.owner, target.repo, target.branch);
    if (resultUrlEl) resultUrlEl.textContent = url;
    if (resultEl) resultEl.hidden = false;
    return url;
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var target = resolveTarget();
      if (!target) {
        if (resultEl) resultEl.hidden = true;
        return;
      }
      var url = renderResult(target);
      showToast("URL generated");
      try {
        window.history.replaceState(null, "", "#builder");
      } catch (err) {
        /* ignore history errors */
      }
      return url;
    });
  }

  if (openBtn) {
    openBtn.addEventListener("click", function () {
      var target = resolveTarget();
      if (!target) return;
      var url = renderResult(target);
      window.open(url, "_blank", "noopener");
    });
  }

  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var target = resolveTarget();
      if (!target) return;
      var url = resultUrlEl && resultUrlEl.textContent
        ? resultUrlEl.textContent
        : buildUrl(target.owner, target.repo, target.branch);
      copyText(url).then(
        function () {
          showToast("URL copied to clipboard");
        },
        function () {
          showToast("Could not copy automatically");
        }
      );
    });
  }

  /* Generic copy buttons via [data-copy-target]. */
  Array.prototype.forEach.call(
    document.querySelectorAll("[data-copy-target]"),
    function (button) {
      button.addEventListener("click", function () {
        var id = button.getAttribute("data-copy-target");
        var source = document.getElementById(id);
        if (!source) return;
        var text = source.getAttribute("href") || source.textContent || "";
        copyText(text.trim()).then(
          function () {
            showToast("Copied");
          },
          function () {
            showToast("Could not copy automatically");
          }
        );
      });
    }
  );

  if (repoInput) {
    repoInput.addEventListener("input", clearError);
    repoInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        if (form) {
          form.dispatchEvent(new Event("submit", { cancelable: true }));
        }
      }
    });
  }

  applyOrigin();
})();
