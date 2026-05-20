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

// ---- Bulk overrides ----
let _currentOverrides = []; // kept in sync by loadBulkOverrides

function goSettingsFromForm(prefix) {
  const body = {};
  const minD = parseFloat(document.getElementById(`${prefix}min-delay`).value);
  const maxD = parseFloat(document.getElementById(`${prefix}max-delay`).value);
  const rc   = parseInt(document.getElementById(`${prefix}retry-count`).value, 10);
  const ri   = parseFloat(document.getElementById(`${prefix}retry-interval`).value);
  const resp = document.getElementById(`${prefix}response`).value;
  if (!isNaN(minD)) body.initial_delay = minD;
  if (!isNaN(maxD)) body.initial_delay_max = maxD;
  if (!isNaN(rc))   body.retry_count = rc;
  if (!isNaN(ri))   body.retry_interval = ri;
  if (resp)         body.response = resp;
  return body;
}

function goSettingsSummary(s) {
  const parts = [];
  if (s.initial_delay != null) parts.push(`min ${s.initial_delay}s`);
  if (s.initial_delay_max != null) parts.push(`max ${s.initial_delay_max}s`);
  if (s.retry_count != null) parts.push(`${s.retry_count} retries`);
  if (s.retry_interval != null) parts.push(`${s.retry_interval}s interval`);
  if (s.response) parts.push(s.response);
  return parts.length ? parts.join(" · ") : "no settings";
}

function renderOverrideCard(uid, ov, container) {
  const card = document.createElement("div");
  card.dataset.oid = ov.id;
  card.style.cssText = "border:1px solid var(--border);border-radius:var(--radius);margin-bottom:.65rem;overflow:hidden";

  const typeBadge = ov.type === "event_heading" ? "Event series" : "Spond group";
  const summary = goSettingsSummary(ov.settings || {});

  card.innerHTML = `
    <div class="go-card-header" style="display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem;padding:.75rem 1rem;cursor:pointer;user-select:none">
      <div>
        <span class="tag" style="font-size:.72rem;margin-right:.4rem">${escapeHtml(typeBadge)}</span>
        <strong>${escapeHtml(ov.target_name || ov.target_id)}</strong>
        <div class="muted go-summary" style="font-size:.82rem;margin-top:.2rem">${escapeHtml(summary)}</div>
      </div>
      <span class="go-chevron" style="font-size:.85rem;color:var(--muted);flex-shrink:0;margin-top:.15rem">▾</span>
    </div>
    <div class="go-edit-form" hidden style="padding:.75rem 1rem 1rem;border-top:1px solid var(--border)">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem .75rem;margin-bottom:.65rem">
        <label>Min delay (s)<input type="number" step="0.05" min="0" class="go-ef-min-delay" placeholder="—" value="${ov.settings?.initial_delay ?? ""}" /></label>
        <label>Max delay (s)<input type="number" step="0.05" min="0" class="go-ef-max-delay" placeholder="—" value="${ov.settings?.initial_delay_max ?? ""}" /></label>
        <label>Retry count<input type="number" step="1" min="0" class="go-ef-retry-count" placeholder="—" value="${ov.settings?.retry_count ?? ""}" /></label>
        <label>Retry interval (s)<input type="number" step="0.05" min="0.05" class="go-ef-retry-interval" placeholder="—" value="${ov.settings?.retry_interval ?? ""}" /></label>
        <label>Response
          <select class="go-ef-response">
            <option value="">— inherit from defaults —</option>
            <option value="accepted" ${ov.settings?.response === "accepted" ? "selected" : ""}>accepted</option>
            <option value="declined" ${ov.settings?.response === "declined" ? "selected" : ""}>declined</option>
            <option value="unconfirmed" ${ov.settings?.response === "unconfirmed" ? "selected" : ""}>unconfirmed</option>
          </select>
        </label>
      </div>
      <div class="form-footer">
        <button class="go-ef-save">Save</button>
        <button class="ghost go-ef-cancel">Cancel</button>
        <button class="ghost err-btn go-del-btn">Delete</button>
        <span class="go-ef-status status"></span>
      </div>
    </div>`;

  const header = card.querySelector(".go-card-header");
  const editForm = card.querySelector(".go-edit-form");
  const chevron = card.querySelector(".go-chevron");

  header.addEventListener("click", () => {
    const opening = editForm.hidden;
    editForm.hidden = !opening;
    chevron.textContent = opening ? "▴" : "▾";
  });

  card.querySelector(".go-ef-cancel").addEventListener("click", () => {
    editForm.hidden = true;
    chevron.textContent = "▾";
  });

  card.querySelector(".go-ef-save").addEventListener("click", async () => {
    const body = {};
    const minD = parseFloat(card.querySelector(".go-ef-min-delay").value);
    const maxD = parseFloat(card.querySelector(".go-ef-max-delay").value);
    const rc   = parseInt(card.querySelector(".go-ef-retry-count").value, 10);
    const ri   = parseFloat(card.querySelector(".go-ef-retry-interval").value);
    const resp = card.querySelector(".go-ef-response").value;
    if (!isNaN(minD)) body.initial_delay = minD;
    if (!isNaN(maxD)) body.initial_delay_max = maxD;
    if (!isNaN(rc))   body.retry_count = rc;
    if (!isNaN(ri))   body.retry_interval = ri;
    if (resp)         body.response = resp;
    const statusEl = card.querySelector(".go-ef-status");
    try {
      await api(`/admin/users/${uid}/bulk-overrides/${ov.id}`, {
        method: "PATCH", body: JSON.stringify({ settings: body }),
      });
      ov.settings = body;
      const idx = _currentOverrides.findIndex(o => o.id === ov.id);
      if (idx !== -1) _currentOverrides[idx].settings = body;
      statusEl.textContent = "Saved";
      statusEl.className = "status ok";
      card.querySelector(".go-summary").textContent = goSettingsSummary(body);
      editForm.hidden = true;
      chevron.textContent = "▾";
    } catch (err) {
      statusEl.textContent = err.message;
      statusEl.className = "status err";
    }
  });

  card.querySelector(".go-del-btn").addEventListener("click", async () => {
    if (!confirm(`Delete override for "${ov.target_name}"?`)) return;
    await api(`/admin/users/${uid}/bulk-overrides/${ov.id}`, { method: "DELETE" });
    _currentOverrides = _currentOverrides.filter(o => o.id !== ov.id);
    card.remove();
    if (!container.querySelector("[data-oid]")) {
      container.innerHTML = '<p class="muted">No bulk overrides yet.</p>';
    }
  });

  return card;
}

async function loadBulkOverrides(uid) {
  const container = $("#go-cards-list");
  container.innerHTML = "";
  const addBtn = $("#go-add-btn");
  addBtn.hidden = !uid;
  if (!uid) return;
  const { overrides } = await api(`/admin/users/${uid}/bulk-overrides`);
  _currentOverrides = overrides;
  if (!overrides.length) {
    container.innerHTML = '<p class="muted">No bulk overrides yet.</p>';
  } else {
    overrides.forEach(ov => container.appendChild(renderOverrideCard(uid, ov, container)));
  }
}

async function loadAddFormTargets(uid, type) {
  const sel = $("#goa-target");
  sel.innerHTML = '<option value="">Loading…</option>';
  try {
    const endpoint = type === "event_heading"
      ? `/admin/users/${uid}/headings`
      : `/admin/users/${uid}/groups`;
    const items = await api(endpoint);
    sel.innerHTML = '<option value="">Select…</option>';
    items.forEach(it => {
      const opt = document.createElement("option");
      opt.value = it.id; opt.textContent = it.name;
      sel.appendChild(opt);
    });
  } catch { sel.innerHTML = '<option value="">Error loading</option>'; }
}

async function initGroupOverrides() {
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
    $("#go-add-form").hidden = true;
    await loadBulkOverrides(uid);
    if (uid) await loadAddFormTargets(uid, $("#goa-type").value);
  });

  $("#go-add-btn").addEventListener("click", () => {
    $("#go-add-form").hidden = false;
    $("#go-add-btn").hidden = true;
  });

  $("#goa-cancel").addEventListener("click", () => {
    const uid = $("#go-user-select").value;
    if (uid) $("#go-add-btn").hidden = false;
    $("#go-add-form").hidden = true;
    $("#goa-dup-warn").hidden = true;
    $("#goa-status").textContent = "";
  });

  $("#goa-type").addEventListener("change", async () => {
    const uid = $("#go-user-select").value;
    $("#goa-dup-warn").hidden = true;
    if (uid) await loadAddFormTargets(uid, $("#goa-type").value);
  });

  $("#goa-target").addEventListener("change", () => {
    const type = $("#goa-type").value;
    const targetId = $("#goa-target").value;
    const dupWarn = $("#goa-dup-warn");
    if (targetId && _currentOverrides.some(o => o.type === type && o.target_id === targetId)) {
      const label = type === "event_heading" ? "event series" : "Spond group";
      dupWarn.textContent = `⚠️ An override for this ${label} already exists. Tap its card above to edit it.`;
      dupWarn.hidden = false;
    } else {
      dupWarn.hidden = true;
    }
  });

  $("#goa-save").addEventListener("click", async () => {
    const uid = $("#go-user-select").value;
    const targetSel = $("#goa-target");
    if (!uid || !targetSel.value) return;
    const body = {
      type: $("#goa-type").value,
      target_id: targetSel.value,
      target_name: targetSel.options[targetSel.selectedIndex].textContent,
      settings: goSettingsFromForm("goa-"),
    };
    const statusEl = $("#goa-status");
    try {
      await api(`/admin/users/${uid}/bulk-overrides`, { method: "POST", body: JSON.stringify(body) });
      statusEl.textContent = "";
      $("#go-add-form").hidden = true;
      $("#go-add-btn").hidden = false;
      // reset add form
      ["goa-min-delay","goa-max-delay","goa-retry-count","goa-retry-interval"].forEach(id => document.getElementById(id).value = "");
      document.getElementById("goa-response").value = "";
      await loadBulkOverrides(uid);
    } catch (err) {
      statusEl.textContent = err.message;
      statusEl.className = "status err";
    }
  });
}

// ---- Wire up ----
$("#creds-form").addEventListener("submit", saveCreds);
$("#defaults-form").addEventListener("submit", saveDefaults);
$("#pw-form").addEventListener("submit", changePassword);

loadCreds().catch(() => {});
loadDefaults().catch(() => {});
loadOverrides().catch(() => {});
loadVersion();
