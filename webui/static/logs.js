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
      tr.className = e.ok ? "accepted" : "failed";
      const resultText = e.ok
        ? e.dry_run
          ? "ok (dry-run)"
          : "ok"
        : e.error || "failed";
      tr.innerHTML = `
        <td>${escapeHtml(fmt(e.ts))}</td>
        <td>${escapeHtml(e.heading || e.event_id)}</td>
        <td>${escapeHtml(e.response || "—")}</td>
        <td>${e.attempt ?? "—"}</td>
        <td>${escapeHtml(resultText)}</td>
      `;
      tbody.appendChild(tr);
    }
    status.textContent = `${entries.length} entries`;
    status.className = "status ok";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

$("#reload").addEventListener("click", load);
load();
setInterval(load, 30000);
