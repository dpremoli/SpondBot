// Shared nav init — included by every page
(function () {
  // ── Theme ──────────────────────────────────────────────────────────────────
  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "light") root.setAttribute("data-theme", "light");
    else if (theme === "dark") root.setAttribute("data-theme", "dark");
    else root.removeAttribute("data-theme");
  }
  const savedTheme = localStorage.getItem("__theme");
  if (savedTheme) applyTheme(savedTheme);

  function toggleTheme() {
    const isDark = !document.documentElement.hasAttribute("data-theme")
      ? window.matchMedia("(prefers-color-scheme: light)").matches ? false : true
      : document.documentElement.getAttribute("data-theme") === "dark";
    const next = isDark ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem("__theme", next);
    document.querySelectorAll(".theme-btn").forEach(b => {
      b.textContent = next === "dark" ? "☀️" : "🌙";
      b.title = next === "dark" ? "Switch to light mode" : "Switch to dark mode";
    });
  }

  // Inject theme button into nav
  const nav = document.querySelector("#top-nav");
  if (nav) {
    const btn = document.createElement("button");
    btn.className = "ghost small theme-btn";
    const currentlyDark = savedTheme === "dark" || (!savedTheme && !window.matchMedia("(prefers-color-scheme: light)").matches);
    btn.textContent = currentlyDark ? "☀️" : "🌙";
    btn.title = currentlyDark ? "Switch to light mode" : "Switch to dark mode";
    btn.addEventListener("click", toggleTheme);
    const logout = nav.querySelector("#nav-logout");
    if (logout) nav.insertBefore(btn, logout);
    else nav.appendChild(btn);
  }

  // ── Pre-fill cache ────────────────────────────────────────────────────────
  const cachedUser = localStorage.getItem("__username");
  const cachedVersion = localStorage.getItem("__version");
  if (cachedUser) document.querySelectorAll(".nav-user").forEach(el => el.textContent = cachedUser);
  if (cachedVersion) document.querySelectorAll(".nav-version").forEach(el => el.textContent = cachedVersion);
  if (localStorage.getItem("__is_admin") === "1") {
    document.querySelectorAll("#nav-admin, #bottom-admin").forEach(el => { el.hidden = false; });
  }

  fetch("/auth/me").then(r => {
    if (!r.ok) { location.replace("/login"); return null; }
    return r.json();
  }).then(u => {
    if (!u) return;
    localStorage.setItem("__username", u.username);
    document.querySelectorAll(".nav-user").forEach(el => el.textContent = u.username);
    document.querySelectorAll(".nav-version").forEach(el => {
      fetch("/api/status").then(r => r.ok ? r.json() : null).then(s => {
        if (s?.version) {
          const v = `v${s.version}`;
          el.textContent = v;
          localStorage.setItem("__version", v);
        }
      }).catch(() => {});
    });
    const isAdmin = !!u.is_admin;
    localStorage.setItem("__is_admin", isAdmin ? "1" : "0");
    if (isAdmin) {
      document.querySelectorAll("#nav-admin, #bottom-admin").forEach(el => { el.hidden = false; });
    } else {
      document.querySelectorAll("#nav-admin, #bottom-admin").forEach(el => { el.hidden = true; });
    }
  });

  document.querySelectorAll("#nav-logout, #mobile-logout").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch("/auth/logout", { method: "POST" });
      location.replace("/login");
    });
  });
})();
