// Shared nav init — included by every page
(function () {
  // Pre-fill from cache immediately to avoid flash on page transitions
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
