const $ = (sel) => document.querySelector(sel);

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    let detail;
    try {
      const json = await res.json();
      detail = json.detail || res.statusText;
    } catch {
      detail = await res.text();
    }
    throw new Error(detail);
  }
  return res.json();
}

function fmt(ts) {
  if (!ts) return "—";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
}

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function fmtDate(ts) {
  if (!ts) return "—";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function dayKey(ts) {
  if (!ts) return "unknown";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  return isNaN(d.getTime()) ? "unknown" : d.toISOString().slice(0, 10);
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function entryClass(e) {
  if (!e.ok) return "failed";
  if (e.waitlisted) return "waitlisted";
  return "accepted";
}

function entryIcon(e) {
  if (!e.ok) return "✕";
  if (e.waitlisted) return "~";
  return "✓";
}

function resultText(e) {
  if (!e.ok) return e.error || "failed";
  if (e.dry_run) return "dry-run";
  if (e.waitlisted) return "waitlisted";
  return "accepted";
}

function eventLabel(e) {
  const name = e.heading || e.event_id;
  if (!e.startTimestamp) return escapeHtml(name);
  const d = new Date(e.startTimestamp);
  if (isNaN(d.getTime())) return escapeHtml(name);
  const dateStr = d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  return `${escapeHtml(name)} <span class="tl-event-date">${dateStr}</span>`;
}

// Build a timeline node element for a single log entry
function makeNode(e, clickable) {
  const cls = entryClass(e);
  const node = document.createElement("div");
  node.className = `tl-node ${cls}`;

  const dot = document.createElement("div");
  dot.className = "tl-dot";
  dot.textContent = entryIcon(e);

  const card = document.createElement("div");
  card.className = "tl-card";

  const header = document.createElement("div");
  header.className = "tl-card-header";

  const nameSpan = document.createElement("span");
  nameSpan.className = "tl-card-name";
  nameSpan.innerHTML = eventLabel(e);

  const meta = document.createElement("span");
  meta.className = "tl-card-meta";

  const badges = [];
  if (e.response) badges.push(`<span class="tl-badge">${escapeHtml(e.response)}</span>`);
  const res = resultText(e);
  badges.push(`<span class="tl-badge tl-badge--${cls}">${escapeHtml(res)}</span>`);
  if (e.attempt) badges.push(`<span class="tl-badge tl-badge--muted">attempt ${e.attempt}</span>`);
  meta.innerHTML = badges.join("");

  const time = document.createElement("span");
  time.className = "tl-card-time";
  time.textContent = fmtTime(e.ts);
  time.title = fmt(e.ts);

  header.append(nameSpan, meta, time);
  card.appendChild(header);

  if (e.error) {
    const err = document.createElement("div");
    err.className = "tl-card-detail";
    err.textContent = e.error;
    card.appendChild(err);
  }

  node.append(dot, card);

  if (clickable) {
    card.classList.add("clickable");
    card.title = "Click to see full attempt log for this event";
    card.addEventListener("click", () => openEventModal(e.event_id, e.heading));
  }

  return node;
}

// Render a full timeline grouped by day into a container element
function renderTimeline(container, entries, clickable = true) {
  container.innerHTML = "";

  if (entries.length === 0) {
    container.innerHTML = `<p class="muted tl-empty">No activity yet.</p>`;
    return;
  }

  // Group by day
  const groups = new Map();
  for (const e of entries) {
    const key = dayKey(e.ts);
    if (!groups.has(key)) groups.set(key, { label: fmtDate(e.ts), entries: [] });
    groups.get(key).entries.push(e);
  }

  for (const [, group] of groups) {
    const section = document.createElement("div");
    section.className = "tl-section";

    const dayLabel = document.createElement("div");
    dayLabel.className = "tl-day-label";
    dayLabel.textContent = group.label;
    section.appendChild(dayLabel);

    const track = document.createElement("div");
    track.className = "tl-track";

    for (const e of group.entries) {
      track.appendChild(makeNode(e, clickable));
    }

    section.appendChild(track);
    container.appendChild(section);
  }
}

async function load() {
  const status = $("#logs-status");
  try {
    const { entries } = await api("/api/history?limit=500");
    renderTimeline($("#timeline"), entries, true);
    status.textContent = `${entries.length} entries`;
    status.className = "status ok";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

async function openEventModal(eventId, heading) {
  const modal = $("#event-modal");
  const title = $("#modal-title");
  const idEl = $("#modal-event-id");
  const modalTl = $("#modal-timeline");

  title.textContent = heading || eventId;
  idEl.textContent = `ID: ${eventId}`;
  modalTl.innerHTML = `<p class="muted tl-empty">Loading…</p>`;
  modal.hidden = false;

  try {
    const { entries } = await api(`/api/history?event_id=${encodeURIComponent(eventId)}`);
    // Oldest first so timeline reads top-to-bottom
    renderTimeline(modalTl, [...entries].reverse(), false);
  } catch (err) {
    modalTl.innerHTML = `<p class="tl-empty err">${escapeHtml(err.message)}</p>`;
  }
}

function closeModal() {
  $("#event-modal").hidden = true;
}

$("#modal-close").addEventListener("click", closeModal);
$("#event-modal").addEventListener("click", (e) => {
  if (e.target === $("#event-modal")) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

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
