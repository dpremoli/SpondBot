const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) { location.replace("/login"); throw new Error("Unauthorized"); }
  if (!res.ok) {
    let detail;
    try { const json = await res.json(); detail = json.detail || res.statusText; }
    catch { detail = await res.text(); }
    throw new Error(detail);
  }
  return res.json();
}

// ---- Auth init (nav handled by nav.js) ----
fetch("/auth/me").then(r => {
  if (!r.ok) { location.replace("/login"); return null; }
  return r.json();
}).then(u => {
  if (!u) return;
  if (!u.is_admin) {
    const botSection = $("#section-bot-defaults");
    if (botSection) botSection.hidden = true;
    const ovSection = $("#section-overrides");
    if (ovSection) ovSection.hidden = true;
    const goSection = $("#section-group-overrides");
    if (goSection) goSection.hidden = true;
  } else {
    initGroupOverrides();
  }
});

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function fmt(ts) {
  if (!ts) return "—";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

// ---- Spond credentials ----
async function loadCreds() {
  const cfg = await api("/api/config");
  $("#username").value = cfg.username || "";
  $("#group_ids").value = (cfg.group_ids || []).join("\n");
  if (cfg.has_password) {
    $("#password").placeholder = "(saved — leave blank to keep)";
  }
}

async function saveCreds(ev) {
  ev.preventDefault();
  const status = $("#creds-status");
  status.textContent = "Saving…";
  status.className = "status";
  const pw = $("#password").value;
  const body = {
    username: $("#username").value.trim(),
    password: pw,
    group_ids: $("#group_ids").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
  };
  if (!body.username) { status.textContent = "Username required"; status.className = "status err"; return; }
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify(body) });
    status.textContent = pw ? "Verified & saved" : "Saved";
    status.className = "status ok";
    if (pw) { $("#password").value = ""; $("#password").placeholder = "(saved — leave blank to keep)"; }
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

// ---- Bot defaults ----
async function loadDefaults() {
  const { defaults, dry_run, group_by } = await api("/api/settings");
  $("#initial_delay").value = defaults.initial_delay;
  $("#initial_delay_max").value = defaults.initial_delay_max ?? defaults.initial_delay;
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
    initial_delay_max: parseFloat($("#initial_delay_max").value),
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

// ---- Per-event overrides ----
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
        <th>Event</th><th>Min delay</th><th>Max delay</th><th>Retry count</th>
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
      <td>${ov.initial_delay_max ?? "—"}</td>
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

// ---- Change password ----
async function changePassword(ev) {
  ev.preventDefault();
  const status = $("#pw-status");
  const newPw = $("#pw-new").value;
  const confirm = $("#pw-confirm").value;
  if (newPw !== confirm) {
    status.textContent = "New passwords do not match.";
    status.className = "status err";
    return;
  }
  status.textContent = "Saving…";
  status.className = "status";
  try {
    await api("/auth/me/password", {
      method: "PATCH",
      body: JSON.stringify({ current_password: $("#pw-current").value, new_password: newPw }),
    });
    status.textContent = "Password changed.";
    status.className = "status ok";
    $("#pw-current").value = "";
    $("#pw-new").value = "";
    $("#pw-confirm").value = "";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

// ---- Version ----
async function loadVersion() {
  try {
    const s = await api("/api/status");
    const vEl = $("#nav-version");
    if (vEl && s.version) vEl.textContent = `v${s.version}`;
  } catch { /* non-critical */ }
}

// ---- Group overrides ----
let _goGroupMap = {}; // group_id → name, for the current user

async function initGroupOverrides() {
  // Populate user dropdown
  try {
    const users = await api("/admin/users");
    const sel = $("#go-user-select");
    users.forEach(u => {
      const opt = document.createElement("option");
      opt.value = u.id; opt.textContent = u.username;
      sel.appendChild(opt);
    });
  } catch { /* non-critical */ }

  $("#go-user-select").addEventListener("change", async () => {
    const uid = $("#go-user-select").value;
    const groupSel = $("#go-group-select");
    groupSel.innerHTML = '<option value="">Select group…</option>';
    groupSel.disabled = !uid;
    $("#go-form").hidden = true;
    $("#go-overrides-table").innerHTML = "";
    _goGroupMap = {};
    if (!uid) return;
    await loadGoGroups(uid);
    await loadGoOverridesTable(uid);
  });

  $("#go-group-select").addEventListener("change", async () => {
    const uid = $("#go-user-select").value;
    const gid = $("#go-group-select").value;
    if (!uid || !gid) { $("#go-form").hidden = true; return; }
    // Pre-fill form with existing override if any
    try {
      const { group_settings } = await api(`/admin/users/${uid}/group-settings`);
      const ov = (group_settings || {})[gid] || {};
      $("#go-initial-delay").value = ov.initial_delay ?? "";
      $("#go-initial-delay-max").value = ov.initial_delay_max ?? "";
      $("#go-retry-count").value = ov.retry_count ?? "";
      $("#go-retry-interval").value = ov.retry_interval ?? "";
      $("#go-response").value = ov.response ?? "";
    } catch { /* ignore */ }
    $("#go-form").hidden = false;
    $("#go-status").textContent = "";
  });

  $("#go-form").addEventListener("submit", async e => {
    e.preventDefault();
    const uid = $("#go-user-select").value;
    const gid = $("#go-group-select").value;
    if (!uid || !gid) return;
    const body = {};
    const minD = parseFloat($("#go-initial-delay").value);
    const maxD = parseFloat($("#go-initial-delay-max").value);
    const rc   = parseInt($("#go-retry-count").value, 10);
    const ri   = parseFloat($("#go-retry-interval").value);
    const resp = $("#go-response").value;
    if (!isNaN(minD)) body.initial_delay = minD;
    if (!isNaN(maxD)) body.initial_delay_max = maxD;
    if (!isNaN(rc))   body.retry_count = rc;
    if (!isNaN(ri))   body.retry_interval = ri;
    if (resp)         body.response = resp;
    const status = $("#go-status");
    try {
      await api(`/admin/users/${uid}/group-settings/${gid}`, { method: "POST", body: JSON.stringify(body) });
      status.textContent = "Saved";
      status.className = "status ok";
      await loadGoOverridesTable(uid);
    } catch (err) {
      status.textContent = err.message;
      status.className = "status err";
    }
  });
}

async function loadGoGroups(uid) {
  const groups = await api(`/admin/users/${uid}/groups`);
  const sel = $("#go-group-select");
  sel.innerHTML = '<option value="">Select group…</option>';
  _goGroupMap = {};
  groups.forEach(g => {
    _goGroupMap[g.id] = g.name;
    const opt = document.createElement("option");
    opt.value = g.id; opt.textContent = g.name;
    sel.appendChild(opt);
  });
}

async function loadGoOverridesTable(uid) {
  const container = $("#go-overrides-table");
  container.innerHTML = "";
  const { group_settings } = await api(`/admin/users/${uid}/group-settings`);
  const entries = Object.entries(group_settings || {});
  if (!entries.length) {
    container.innerHTML = '<p class="muted" style="margin-top:.75rem">No group overrides yet.</p>';
    return;
  }
  const tbl = document.createElement("table");
  tbl.innerHTML = `
    <thead><tr>
      <th>Group</th><th>Min delay</th><th>Max delay</th>
      <th>Retry count</th><th>Retry interval</th><th>Response</th><th></th>
    </tr></thead>`;
  const tbody = document.createElement("tbody");
  for (const [gid, ov] of entries) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(_goGroupMap[gid] || gid)}</td>
      <td>${ov.initial_delay ?? "—"}</td>
      <td>${ov.initial_delay_max ?? "—"}</td>
      <td>${ov.retry_count ?? "—"}</td>
      <td>${ov.retry_interval ?? "—"}</td>
      <td>${escapeHtml(ov.response ?? "—")}</td>
      <td><button class="small" data-gid="${escapeHtml(gid)}">Clear</button></td>`;
    tr.querySelector("button").addEventListener("click", async () => {
      await api(`/admin/users/${uid}/group-settings/${gid}`, { method: "DELETE" });
      await loadGoOverridesTable(uid);
    });
    tbody.appendChild(tr);
  }
  tbl.appendChild(tbody);
  container.appendChild(tbl);
}

// ---- Wire up ----
$("#creds-form").addEventListener("submit", saveCreds);
$("#defaults-form").addEventListener("submit", saveDefaults);
$("#pw-form").addEventListener("submit", changePassword);

loadCreds().catch(() => {});
loadDefaults().catch(() => {});
loadOverrides().catch(() => {});
loadVersion();
