const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
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

async function loadDefaults() {
  const { defaults, dry_run, group_by } = await api("/api/settings");
  $("#initial_delay").value = defaults.initial_delay;
  $("#retry_count").value = defaults.retry_count;
  $("#retry_interval").value = defaults.retry_interval;
  $("#response").value = defaults.response;
  $("#dry_run").checked = !!dry_run;
  $("#group_by").value = group_by || "heading";
}

async function saveDefaults(ev) {
  ev.preventDefault();
  const body = {
    initial_delay: parseFloat($("#initial_delay").value),
    retry_count: parseInt($("#retry_count").value, 10),
    retry_interval: parseFloat($("#retry_interval").value),
    response: $("#response").value,
    dry_run: $("#dry_run").checked,
    group_by: $("#group_by").value,
  };
  const status = $("#defaults-status");
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
    status.textContent = "Saved";
    status.className = "status ok";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

async function loadOverrides() {
  const container = $("#overrides");
  container.innerHTML = "";
  const [{ event_settings }, { events }] = await Promise.all([
    api("/api/settings"),
    api("/api/events"),
  ]);
  const byId = new Map(events.map((e) => [e.id, e]));
  const ids = Object.keys(event_settings);
  if (ids.length === 0) {
    container.innerHTML = `<p class="muted">No per-event overrides yet. Use the "Override" button on an event to add one.</p>`;
    return;
  }
  const tbl = document.createElement("table");
  tbl.innerHTML = `
    <thead>
      <tr>
        <th>Event</th><th>Initial delay</th><th>Retry count</th>
        <th>Retry interval</th><th>Response</th><th></th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  const tbody = tbl.querySelector("tbody");
  for (const id of ids) {
    const ov = event_settings[id];
    const e = byId.get(id);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(e ? e.heading : id)}<br/><small class="muted">${escapeHtml(e ? fmt(e.startTimestamp) : id)}</small></td>
      <td>${ov.initial_delay ?? "—"}</td>
      <td>${ov.retry_count ?? "—"}</td>
      <td>${ov.retry_interval ?? "—"}</td>
      <td>${escapeHtml(ov.response ?? "—")}</td>
      <td><button class="small" data-id="${escapeHtml(id)}">Clear</button></td>
    `;
    tr.querySelector("button").addEventListener("click", async () => {
      await api(`/api/event-settings/${id}`, { method: "DELETE" });
      loadOverrides();
    });
    tbody.appendChild(tr);
  }
  container.appendChild(tbl);
}

async function loadVersion() {
  try {
    const s = await api("/api/status");
    const vEl = document.querySelector("#nav-version");
    if (vEl && s.version) vEl.textContent = `v${s.version}`;
  } catch { /* non-critical */ }
}

$("#defaults-form").addEventListener("submit", saveDefaults);
loadDefaults().catch(() => {});
loadOverrides().catch(() => {});
loadVersion();
