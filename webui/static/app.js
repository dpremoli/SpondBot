const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || res.statusText);
  }
  return res.json();
}

function fmt(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

function isAvailable(event) {
  if (!event.inviteTime) return true;
  return new Date(event.inviteTime).getTime() <= Date.now();
}

function groupKey(e) {
  return `${e.groupId || ""}::${(e.heading || "").trim().toLowerCase()}`;
}

let cachedEvents = [];
let selectedSet = new Set();

async function loadConfig() {
  const cfg = await api("/api/config");
  $("#username").value = cfg.username || "";
  $("#group_ids").value = (cfg.group_ids || []).join("\n");
  if (cfg.has_password) {
    $("#password").placeholder = "(saved — leave blank to keep)";
  }
  selectedSet = new Set(cfg.selected_event_ids || []);
}

async function saveCreds(ev) {
  ev.preventDefault();
  const status = $("#creds-status");
  status.textContent = "Saving…";
  status.className = "status";
  const body = {
    username: $("#username").value.trim(),
    password: $("#password").value,
    group_ids: $("#group_ids").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
  };
  if (!body.password) {
    status.textContent = "Enter password to re-verify";
    status.className = "status err";
    return;
  }
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify(body) });
    status.textContent = "Verified";
    status.className = "status ok";
    await refreshEvents();
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

async function refreshEvents() {
  const status = $("#events-status");
  status.textContent = "Refreshing…";
  status.className = "status";
  try {
    await api("/api/refresh", { method: "POST" });
    const { events } = await api("/api/events");
    cachedEvents = events;
    for (const e of events) e.selected = selectedSet.has(e.id);
    renderGroups();
    status.textContent = `${events.length} events`;
    status.className = "status ok";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

function renderGroups() {
  const container = $("#event-groups");
  container.innerHTML = "";
  if (cachedEvents.length === 0) {
    container.innerHTML = `<p class="muted">No events yet. Save credentials and hit Refresh.</p>`;
    return;
  }

  const groups = new Map();
  for (const e of cachedEvents) {
    const k = groupKey(e);
    if (!groups.has(k)) {
      groups.set(k, {
        heading: e.heading || "(untitled)",
        groupName: e.groupName || e.groupId || "",
        sessions: [],
      });
    }
    groups.get(k).sessions.push(e);
  }

  const sorted = [...groups.values()].sort((a, b) => {
    const ta = Math.min(...a.sessions.map((s) => new Date(s.startTimestamp || 0).getTime()));
    const tb = Math.min(...b.sessions.map((s) => new Date(s.startTimestamp || 0).getTime()));
    return ta - tb;
  });

  for (const g of sorted) {
    g.sessions.sort((a, b) =>
      new Date(a.startTimestamp || 0) - new Date(b.startTimestamp || 0)
    );
    container.appendChild(renderGroup(g));
  }
}

function renderGroup(g) {
  const wrap = document.createElement("div");
  wrap.className = "group";

  const selectedCount = g.sessions.filter((s) => s.selected).length;
  const total = g.sessions.length;
  const acceptedCount = g.sessions.filter((s) => s.accepted).length;

  const header = document.createElement("div");
  header.className = "group-header";
  header.innerHTML = `
    <button class="twisty" aria-expanded="false">▸</button>
    <span class="group-title">${escapeHtml(g.heading)}</span>
    <span class="group-meta">${escapeHtml(g.groupName)}</span>
    <span class="group-count">${selectedCount}/${total} selected${acceptedCount ? ` · ${acceptedCount} accepted` : ""}</span>
    <label class="select-all" title="Select all sessions">
      <input type="checkbox" ${selectedCount === total ? "checked" : ""} ${selectedCount > 0 && selectedCount < total ? 'class="indeterminate"' : ""} />
      all
    </label>
  `;
  const twisty = header.querySelector(".twisty");
  const selectAll = header.querySelector(".select-all input");

  const body = document.createElement("div");
  body.className = "group-body";
  body.style.display = "none";

  const tbl = document.createElement("table");
  tbl.className = "sessions";
  tbl.innerHTML = `
    <thead>
      <tr>
        <th></th>
        <th>Available</th>
        <th>Starts</th>
        <th>Ends</th>
        <th>Status</th>
        <th>Override</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  const tbody = tbl.querySelector("tbody");
  for (const s of g.sessions) tbody.appendChild(renderSession(s));
  body.appendChild(tbl);

  // indeterminate state
  if (selectedCount > 0 && selectedCount < total) selectAll.indeterminate = true;

  twisty.addEventListener("click", () => {
    const open = body.style.display !== "none";
    body.style.display = open ? "none" : "";
    twisty.textContent = open ? "▸" : "▾";
    twisty.setAttribute("aria-expanded", String(!open));
  });

  selectAll.addEventListener("change", () => {
    const on = selectAll.checked;
    for (const s of g.sessions) {
      s.selected = on;
      if (on) selectedSet.add(s.id);
      else selectedSet.delete(s.id);
    }
    renderGroups();
  });

  wrap.append(header, body);
  return wrap;
}

function renderSession(s) {
  const tr = document.createElement("tr");
  if (s.accepted) tr.classList.add("accepted");
  else if (isAvailable(s)) tr.classList.add("available");

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = s.selected;
  cb.addEventListener("change", () => {
    s.selected = cb.checked;
    if (cb.checked) selectedSet.add(s.id);
    else selectedSet.delete(s.id);
    renderGroups();
  });
  const tdCb = document.createElement("td");
  tdCb.appendChild(cb);

  const tdInvite = document.createElement("td");
  tdInvite.textContent = fmt(s.inviteTime);
  const tdStart = document.createElement("td");
  tdStart.textContent = fmt(s.startTimestamp);
  const tdEnd = document.createElement("td");
  tdEnd.textContent = fmt(s.endTimestamp);

  const tdStatus = document.createElement("td");
  tdStatus.textContent = s.accepted
    ? "accepted"
    : isAvailable(s)
    ? "open"
    : "scheduled";

  const tdOv = document.createElement("td");
  const ovBtn = document.createElement("button");
  ovBtn.className = "small";
  ovBtn.textContent = s.hasOverride ? "Edit override" : "Override";
  ovBtn.addEventListener("click", () => openOverrideDialog(s));
  tdOv.appendChild(ovBtn);

  tr.append(tdCb, tdInvite, tdStart, tdEnd, tdStatus, tdOv);
  return tr;
}

async function openOverrideDialog(s) {
  const { effective, override } = await api(`/api/event-settings/${s.id}`);
  const base = override || {};
  const initial = prompt(
    `Override for "${s.heading}"\n` +
      `(blank to inherit default; shown: effective value)\n\n` +
      `Initial delay (s) [${effective.initial_delay}]:`,
    base.initial_delay ?? ""
  );
  if (initial === null) return;
  const count = prompt(`Retry count [${effective.retry_count}]:`, base.retry_count ?? "");
  if (count === null) return;
  const interval = prompt(`Retry interval (s) [${effective.retry_interval}]:`, base.retry_interval ?? "");
  if (interval === null) return;
  const response = prompt(
    `Response — accepted | declined | unconfirmed [${effective.response}]:`,
    base.response ?? ""
  );
  if (response === null) return;

  const body = {
    initial_delay: initial === "" ? null : parseFloat(initial),
    retry_count: count === "" ? null : parseInt(count, 10),
    retry_interval: interval === "" ? null : parseFloat(interval),
    response: response === "" ? null : response.trim(),
  };
  try {
    await api(`/api/event-settings/${s.id}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await refreshEvents();
  } catch (e) {
    alert(e.message);
  }
}

async function saveSelection() {
  const status = $("#events-status");
  try {
    await api("/api/selection", {
      method: "POST",
      body: JSON.stringify({ event_ids: [...selectedSet] }),
    });
    status.textContent = `Saved ${selectedSet.size} selected`;
    status.className = "status ok";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

$("#creds-form").addEventListener("submit", saveCreds);
$("#refresh").addEventListener("click", refreshEvents);
$("#save-selection").addEventListener("click", saveSelection);

loadConfig().then(refreshEvents).catch(() => {});
setInterval(refreshEvents, 30000);
