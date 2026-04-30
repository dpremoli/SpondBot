const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail;
    try { const j = await res.json(); detail = j.detail || res.statusText; }
    catch { detail = await res.text(); }
    throw new Error(detail);
  }
  return res.json();
}

// ── state ──────────────────────────────────────────────────────────────────
let allEntries = [];
let activeStatus = "all";   // "all" | "accepted" | "waitlisted" | "failed"
let activeText = "";
let currentModalEventId = null;

// ── filtering ───────────────────────────────────────────────────────────────
function entryMatchesStatus(e, status) {
  if (status === "all") return true;
  if (status === "accepted") return e.ok && !e.waitlisted;
  if (status === "waitlisted") return e.waitlisted;
  if (status === "failed") return !e.ok;
  return true;
}

function applyFilters() {
  const text = activeText.toLowerCase();
  const filtered = allEntries.filter((e) => {
    if (!entryMatchesStatus(e, activeStatus)) return false;
    if (text) {
      const name = (e.heading || e.event_id || "").toLowerCase();
      if (!name.includes(text)) return false;
    }
    return true;
  });

  tlRenderByDay(
    $("#timeline"),
    filtered,
    (e) => openModal(e.event_id, e.heading, {
      startTimestamp: e.startTimestamp,
      inviteTime: e.inviteTime,
      accepted: e.ok && !e.waitlisted,
      waitlisted: e.waitlisted,
      failed: !e.ok,
    })
  );

  const total = allEntries.length;
  const shown = filtered.length;
  const status = $("#logs-status");
  status.textContent = shown === total
    ? `${total} entries`
    : `${shown} of ${total} entries`;
  status.className = "status ok";
}

// ── load ────────────────────────────────────────────────────────────────────
async function load() {
  const status = $("#logs-status");
  try {
    const { entries } = await api("/api/history?limit=500");
    allEntries = entries;
    applyFilters();
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

// ── modal ───────────────────────────────────────────────────────────────────
function openModal(eventId, heading, meta = {}) {
  currentModalEventId = eventId;
  tlOpenEventModal(eventId, heading, meta);
}

function closeModal() {
  $("#event-modal").hidden = true;
  currentModalEventId = null;
}

// ── clear ────────────────────────────────────────────────────────────────────
async function clearAll() {
  if (!confirm("Clear all logs? This cannot be undone.")) return;
  try {
    await api("/api/history", { method: "DELETE" });
    allEntries = [];
    applyFilters();
    const status = $("#logs-status");
    status.textContent = "Cleared";
    status.className = "status ok";
  } catch (e) {
    alert("Failed to clear logs: " + e.message);
  }
}

async function clearEvent() {
  if (!currentModalEventId) return;
  const name = $("#modal-title").textContent;
  if (!confirm(`Clear all log entries for "${name}"? This cannot be undone.`)) return;
  try {
    await api(`/api/history?event_id=${encodeURIComponent(currentModalEventId)}`, { method: "DELETE" });
    closeModal();
    await load();
  } catch (e) {
    alert("Failed to clear event logs: " + e.message);
  }
}

// ── event wiring ─────────────────────────────────────────────────────────────
$("#reload").addEventListener("click", load);
$("#clear-all").addEventListener("click", clearAll);
$("#modal-clear-event").addEventListener("click", clearEvent);
$("#modal-close").addEventListener("click", closeModal);
$("#event-modal").addEventListener("click", (e) => { if (e.target === $("#event-modal")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// Filter text
$("#filter-text").addEventListener("input", (e) => {
  activeText = e.target.value.trim();
  applyFilters();
});

// Status pills
document.querySelectorAll(".pill").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".pill").forEach((b) => b.classList.remove("pill--active"));
    btn.classList.add("pill--active");
    activeStatus = btn.dataset.status;
    applyFilters();
  });
});

async function loadVersion() {
  try {
    const s = await api("/api/status");
    const vEl = document.querySelector("#nav-version");
    if (vEl && s.version) vEl.textContent = `v${s.version}`;
  } catch { /* non-critical */ }
}

load();
loadVersion();
setInterval(load, 30000);
