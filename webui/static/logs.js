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

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function rowClass(e) {
  if (!e.ok) return "failed";
  if (e.waitlisted) return "waitlisted";
  return "accepted";
}

function eventLabel(e) {
  const name = e.heading || e.event_id;
  if (!e.startTimestamp) return escapeHtml(name);
  const d = new Date(e.startTimestamp);
  if (isNaN(d.getTime())) return escapeHtml(name);
  const dateStr = d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  return `${escapeHtml(name)} <span class="muted" style="font-size:.8em">${dateStr}</span>`;
}

function resultText(e) {
  if (!e.ok) return e.error || "failed";
  if (e.dry_run) return "ok (dry-run)";
  if (e.waitlisted) return "waitlisted";
  return "accepted";
}

async function load() {
  const status = $("#logs-status");
  try {
    const { entries } = await api("/api/history?limit=500");
    const tbody = document.querySelector("#history tbody");
    tbody.innerHTML = "";
    if (entries.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">No activity yet.</td></tr>`;
      status.textContent = "";
      return;
    }
    for (const e of entries) {
      const tr = document.createElement("tr");
      tr.className = rowClass(e) + " clickable";
      tr.title = "Click to see full attempt log for this event";
      tr.innerHTML = `
        <td>${escapeHtml(fmt(e.ts))}</td>
        <td>${eventLabel(e)}</td>
        <td>${escapeHtml(e.response || "—")}</td>
        <td>${e.attempt ?? "—"}</td>
        <td class="result">${escapeHtml(resultText(e))}</td>
      `;
      tr.addEventListener("click", () => openEventModal(e.event_id, e.heading));
      tbody.appendChild(tr);
    }
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
  const tbody = document.querySelector("#modal-table tbody");

  title.textContent = heading || eventId;
  idEl.textContent = `event id: ${eventId}`;
  tbody.innerHTML = `<tr><td colspan="5" class="muted">Loading…</td></tr>`;
  modal.hidden = false;

  try {
    const { entries } = await api(`/api/history?event_id=${encodeURIComponent(eventId)}`);
    tbody.innerHTML = "";
    if (entries.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">No history for this event.</td></tr>`;
      return;
    }
    // Show oldest first in the modal so the timeline reads top-to-bottom
    for (const e of [...entries].reverse()) {
      const tr = document.createElement("tr");
      tr.className = rowClass(e);
      const detail = e.error
        ? escapeHtml(e.error)
        : e.dry_run
        ? '<span class="muted">dry-run</span>'
        : "";
      tr.innerHTML = `
        <td>${escapeHtml(fmt(e.ts))}</td>
        <td>${e.attempt ?? "—"}</td>
        <td>${escapeHtml(e.response || "—")}</td>
        <td class="result">${escapeHtml(resultText(e))}</td>
        <td>${detail}</td>
      `;
      tbody.appendChild(tr);
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="err">${escapeHtml(err.message)}</td></tr>`;
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
