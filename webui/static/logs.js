const $ = (sel) => document.querySelector(sel);

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    let detail;
    try { const j = await res.json(); detail = j.detail || res.statusText; }
    catch { detail = await res.text(); }
    throw new Error(detail);
  }
  return res.json();
}

async function load() {
  const status = $("#logs-status");
  try {
    const { entries } = await api("/api/history?limit=500");
    tlRenderByDay(
      $("#timeline"),
      entries,
      (e) => tlOpenEventModal(e.event_id, e.heading, {
        startTimestamp: e.startTimestamp,
        inviteTime: e.inviteTime,
        accepted: e.ok && !e.waitlisted,
        waitlisted: e.waitlisted,
        failed: !e.ok,
      })
    );
    status.textContent = `${entries.length} entries`;
    status.className = "status ok";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

function closeModal() { $("#event-modal").hidden = true; }

$("#modal-close").addEventListener("click", closeModal);
$("#event-modal").addEventListener("click", (e) => { if (e.target === $("#event-modal")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

async function loadVersion() {
  try {
    const s = await api("/api/status");
    const vEl = document.querySelector("#nav-version");
    if (vEl && s.version) vEl.textContent = `v${s.version}`;
  } catch { /* non-critical */ }
}

$("#reload").addEventListener("click", load);
load();
loadVersion();
setInterval(load, 30000);
