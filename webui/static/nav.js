// Shared nav init — included by every page
(function () {
  fetch("/auth/me").then(r => {
    if (!r.ok) { location.replace("/login"); return null; }
    return r.json();
  }).then(u => {
    if (!u) return;
    document.querySelectorAll(".nav-user").forEach(el => el.textContent = u.username);
    document.querySelectorAll(".nav-version").forEach(el => {
      fetch("/api/status").then(r => r.ok ? r.json() : null).then(s => {
        if (s?.version) el.textContent = `v${s.version}`;
      }).catch(() => {});
    });
    if (u.is_admin) {
      document.querySelectorAll("#nav-admin, #bottom-admin").forEach(el => { el.hidden = false; });
    }
  });

  document.querySelectorAll("#nav-logout, #mobile-logout").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch("/auth/logout", { method: "POST" });
      location.replace("/login");
    });
  });
})();
