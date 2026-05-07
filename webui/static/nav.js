// Shared nav init — included by every page
(function () {
  // ── Theme ──────────────────────────────────────────────────────────────────
  // (theme is already applied by the inline <head> script to prevent flash)
  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "light") root.setAttribute("data-theme", "light");
    else if (theme === "dark") root.setAttribute("data-theme", "dark");
    else root.removeAttribute("data-theme");
  }
  const savedTheme = localStorage.getItem("__theme");

  function toggleTheme() {
    const isDark = !document.documentElement.hasAttribute("data-theme")
      ? !window.matchMedia("(prefers-color-scheme: light)").matches
      : document.documentElement.getAttribute("data-theme") === "dark";
    const next = isDark ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem("__theme", next);
    document.querySelectorAll(".theme-btn").forEach(b => {
      b.textContent = next === "dark" ? "☀️" : "🌙";
      b.title = next === "dark" ? "Switch to light mode" : "Switch to dark mode";
    });
  }

  // Inject fixed version badge (bottom-right)
  const _vBadge = document.createElement("span");
  _vBadge.className = "app-version-badge";
  _vBadge.id = "app-version-badge";
  document.body.appendChild(_vBadge);

  // Wire up all theme buttons (hardcoded in HTML now, not injected)
  const currentlyDark = savedTheme === "dark" || (!savedTheme && !window.matchMedia("(prefers-color-scheme: light)").matches);
  document.querySelectorAll(".theme-btn").forEach(btn => {
    btn.textContent = currentlyDark ? "☀️" : "🌙";
    btn.title = currentlyDark ? "Switch to light mode" : "Switch to dark mode";
    btn.addEventListener("click", toggleTheme);
  });

  // ── Account dropdown ────────────────────────────────────────────────────────
  const accountBtn = document.getElementById("nav-account-btn");
  const accountMenu = document.getElementById("nav-account-menu");
  if (accountBtn && accountMenu) {
    accountBtn.addEventListener("click", e => {
      e.stopPropagation();
      accountMenu.hidden = !accountMenu.hidden;
    });
    document.addEventListener("click", () => { accountMenu.hidden = true; });
  }

  // ── Logout ──────────────────────────────────────────────────────────────────
  async function doLogout() {
    await fetch("/auth/logout", { method: "POST" });
    location.replace("/login");
  }
  document.querySelectorAll("#mobile-logout").forEach(btn => {
    btn.addEventListener("click", doLogout);
  });

  // ── Pre-fill from cache (prevents flash before auth fetch completes) ────────
  const cachedUser = localStorage.getItem("__username");
  const cachedVersion = localStorage.getItem("__version");
  if (cachedUser) document.querySelectorAll(".nav-user").forEach(el => el.textContent = cachedUser);
  if (cachedVersion) {
    const badge = document.getElementById("app-version-badge");
    if (badge) badge.textContent = cachedVersion;
  }
  if (localStorage.getItem("__is_admin") === "1") {
    const leftNav = document.getElementById("left-nav");
    if (leftNav) leftNav.hidden = false;
    document.querySelectorAll("#bottom-admin").forEach(el => { el.hidden = false; });
  }

  // ── Auth fetch ──────────────────────────────────────────────────────────────
  fetch("/auth/me").then(r => {
    if (!r.ok) { location.replace("/login"); return null; }
    return r.json();
  }).then(u => {
    if (!u) return;

    // Update username
    localStorage.setItem("__username", u.username);
    document.querySelectorAll(".nav-user").forEach(el => el.textContent = u.username);

    // Version (piggyback on status fetch)
    fetch("/api/status").then(r => r.ok ? r.json() : null).then(s => {
      if (s?.version) {
        const v = `v${s.version}`;
        const badge = document.getElementById("app-version-badge");
        if (badge) badge.textContent = v;
        localStorage.setItem("__version", v);
      }
    }).catch(() => {});

    const isAdmin = !!u.is_admin;
    localStorage.setItem("__is_admin", isAdmin ? "1" : "0");

    // Left-nav: admins only
    const leftNav = document.getElementById("left-nav");
    if (leftNav) leftNav.hidden = !isAdmin;
    document.querySelectorAll("#bottom-admin").forEach(el => { el.hidden = !isAdmin; });

    // Mark active left-nav item
    const path = window.location.pathname;
    const activeId = path === "/" ? "ln-events"
      : path.startsWith("/admin") ? "ln-dashboard"
      : path.startsWith("/logs") ? "ln-logs"
      : path.startsWith("/settings") ? "ln-settings"
      : null;
    if (activeId) document.getElementById(activeId)?.classList.add("ln-item--active");

    // Build account dropdown based on role
    if (accountMenu) {
      accountMenu.innerHTML = "";

      if (isAdmin) {
        // Admin: Switch account + Log out
        const sw = mkItem("button", "Switch account");
        sw.addEventListener("click", doLogout); // logout → re-login as different user
        accountMenu.appendChild(sw);
      } else {
        // User: Settings + Log out
        const sl = mkItem("a", "Settings");
        sl.href = "/settings";
        accountMenu.appendChild(sl);
      }

      const div = document.createElement("div");
      div.className = "nav-account-divider";
      accountMenu.appendChild(div);

      const logout = mkItem("button", "Log out");
      logout.classList.add("nav-account-item--logout");
      logout.addEventListener("click", doLogout);
      accountMenu.appendChild(logout);
    }
  });

  function mkItem(tag, text) {
    const el = document.createElement(tag);
    el.className = "nav-account-item";
    el.textContent = text;
    return el;
  }
})();
