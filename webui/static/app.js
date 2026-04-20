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
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString();
}

function fmtRel(ts) {
  if (!ts) return "";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return "";
  const delta = (d.getTime() - Date.now()) / 1000;
  const abs = Math.abs(delta);
  const past = delta < 0;
  let v;
  if (abs < 60) v = `${Math.round(abs)}s`;
  else if (abs < 3600) v = `${Math.round(abs / 60)}m`;
  else if (abs < 86400) v = `${(abs / 3600).toFixed(1)}h`;
  else v = `${Math.round(abs / 86400)}d`;
  return past ? `${v} ago` : `in ${v}`;
}

function isAvailable(event) {
  if (!event.inviteTime) return true;
  return new Date(event.inviteTime).getTime() <= Date.now();
}

function isPast(event) {
  const t = event.endTimestamp || event.startTimestamp;
  if (!t) return false;
  return new Date(t).getTime() < Date.now();
}

function groupKey(e, mode) {
  if (mode === "day" || mode === "week" || mode === "year") {
    const t = e.startTimestamp ? new Date(e.startTimestamp) : null;
    if (!t || isNaN(t.getTime())) return "unknown";
    if (mode === "day") return t.toISOString().slice(0, 10);
    if (mode === "year") return String(t.getFullYear());
    // ISO week
    const d = new Date(Date.UTC(t.getFullYear(), t.getMonth(), t.getDate()));
    const day = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - day);
    const y = d.getUTCFullYear();
    const yearStart = new Date(Date.UTC(y, 0, 1));
    const w = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
    return `${y}-W${String(w).padStart(2, "0")}`;
  }
  return `${e.groupId || ""}::${(e.heading || "").trim().toLowerCase()}`;
}

function groupLabel(e, mode, key) {
  if (mode === "heading") return e.heading || "(untitled)";
  if (mode === "day") {
    const d = new Date(key);
    return isNaN(d.getTime())
      ? key
      : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  }
  if (mode === "week") return `Week ${key}`;
  if (mode === "year") return key;
  return key;
}

let cachedEvents = [];
let selectedSet = new Set();
let groupByMode = "heading";
let showPast = false;

async function loadConfig() {
  const cfg = await api("/api/config");
  $("#username").value = cfg.username || "";
  $("#group_ids").value = (cfg.group_ids || []).join("\n");
  if (cfg.has_password) {
    $("#password").placeholder = "(saved — leave blank to keep)";
  }
  selectedSet = new Set(cfg.selected_event_ids || []);
  groupByMode = cfg.group_by || "heading";
  $("#group_by").value = groupByMode;
}

$("#group_by").addEventListener("change", async () => {
  groupByMode = $("#group_by").value;
  // persist via settings endpoint
  try {
    const cur = await api("/api/settings");
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        initial_delay: cur.defaults.initial_delay,
        retry_count: cur.defaults.retry_count,
        retry_interval: cur.defaults.retry_interval,
        response: cur.defaults.response,
        dry_run: cur.dry_run,
        group_by: groupByMode,
      }),
    });
  } catch (e) {
    console.warn("group_by persist failed:", e.message);
  }
  renderGroups();
});

$("#show-past").addEventListener("change", () => {
  showPast = $("#show-past").checked;
  renderGroups();
});

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
    await loadEvents();
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

async function loadEvents() {
  const status = $("#events-status");
  try {
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

async function manualRefresh() {
  const status = $("#events-status");
  try {
    const { last_tick_ts } = await api("/api/status");
    if (last_tick_ts) {
      const ageSec = Date.now() / 1000 - last_tick_ts;
      if (ageSec < 900) {
        const ok = confirm(
          `Last refresh was ${Math.round(ageSec / 60)} min ago.\n` +
            `Spond rate-limits aggressively — refresh anyway?`
        );
        if (!ok) return;
      }
    }
    status.textContent = "Refreshing…";
    status.className = "status";
    await api("/api/refresh", { method: "POST" });
    await loadEvents();
    await loadStatus();
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

function renderGroups() {
  const container = $("#event-groups");
  container.innerHTML = "";
  let events = cachedEvents;
  if (!showPast) events = events.filter((e) => !isPast(e));

  if (events.length === 0) {
    container.innerHTML = `<p class="muted">No events to show.${
      !showPast && cachedEvents.length > 0 ? " (past events hidden)" : ""
    }</p>`;
    return;
  }

  const groups = new Map();
  for (const e of events) {
    const k = groupKey(e, groupByMode);
    if (!groups.has(k)) {
      groups.set(k, {
        key: k,
        label: groupLabel(e, groupByMode, k),
        groupName: e.groupName || e.groupId || "",
        sessions: [],
      });
    }
    groups.get(k).sessions.push(e);
  }

  const sorted = [...groups.values()].sort((a, b) => {
    const ta = Math.min(...a.sessions.map((s) => new Date(s.startTimestamp || 0).getTime() || Infinity));
    const tb = Math.min(...b.sessions.map((s) => new Date(s.startTimestamp || 0).getTime() || Infinity));
    return ta - tb;
  });

  for (const g of sorted) {
    g.sessions.sort(
      (a, b) => new Date(a.startTimestamp || 0) - new Date(b.startTimestamp || 0)
    );
    container.appendChild(renderGroup(g));
  }
}

function renderGroup(g) {
  const wrap = document.createElement("div");
  wrap.className = "group";

  const selectableSessions = g.sessions.filter((s) => !isPast(s));
  const selectedCount = selectableSessions.filter((s) => s.selected).length;
  const total = selectableSessions.length;
  const acceptedCount = g.sessions.filter((s) => s.accepted).length;

  const header = document.createElement("div");
  header.className = "group-header";
  header.innerHTML = `
    <button class="twisty" aria-expanded="false">▸</button>
    <span class="group-title">${escapeHtml(g.label)}</span>
    <span class="group-meta">${escapeHtml(g.groupName)}</span>
    <span class="group-count">${selectedCount}/${total} selected${
    acceptedCount ? ` · ${acceptedCount} accepted` : ""
  }</span>
    <label class="select-all" title="Select all future sessions">
      <input type="checkbox" ${total > 0 && selectedCount === total ? "checked" : ""} ${
    total === 0 ? "disabled" : ""
  } />
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
        <th>Invite opens</th>
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

  if (selectedCount > 0 && selectedCount < total) selectAll.indeterminate = true;

  twisty.addEventListener("click", () => {
    const open = body.style.display !== "none";
    body.style.display = open ? "none" : "";
    twisty.textContent = open ? "▸" : "▾";
    twisty.setAttribute("aria-expanded", String(!open));
  });

  selectAll.addEventListener("change", () => {
    const on = selectAll.checked;
    for (const s of selectableSessions) {
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
  const past = isPast(s);
  if (past) tr.classList.add("past");
  if (s.accepted) tr.classList.add("accepted");
  else if (isAvailable(s)) tr.classList.add("available");

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = s.selected;
  cb.disabled = past;
  cb.addEventListener("change", () => {
    s.selected = cb.checked;
    if (cb.checked) selectedSet.add(s.id);
    else selectedSet.delete(s.id);
    renderGroups();
  });
  const tdCb = document.createElement("td");
  tdCb.appendChild(cb);

  const tdInvite = document.createElement("td");
  if (s.inviteTime) {
    const abs = fmt(s.inviteTime);
    const rel = fmtRel(s.inviteTime);
    tdInvite.innerHTML = `${escapeHtml(abs)}<br/><small class="muted">${escapeHtml(rel)}</small>`;
  } else {
    tdInvite.innerHTML = `<span class="muted">open now</span>`;
  }

  const tdStart = document.createElement("td");
  tdStart.textContent = fmt(s.startTimestamp);
  const tdEnd = document.createElement("td");
  tdEnd.textContent = fmt(s.endTimestamp);

  const tdStatus = document.createElement("td");
  tdStatus.textContent = past
    ? "past"
    : s.accepted
    ? "accepted"
    : isAvailable(s)
    ? "open"
    : "scheduled";

  const tdOv = document.createElement("td");
  if (!past) {
    const ovBtn = document.createElement("button");
    ovBtn.className = "small";
    ovBtn.textContent = s.hasOverride ? "Edit override" : "Override";
    ovBtn.addEventListener("click", () => openOverrideDialog(s));
    tdOv.appendChild(ovBtn);
  }

  tr.append(tdCb, tdInvite, tdStart, tdEnd, tdStatus, tdOv);
  return tr;
}

async function openOverrideDialog(s) {
  const { effective, override } = await api(`/api/event-settings/${s.id}`);
  const base = override || {};
  const initial = prompt(
    `Override for "${s.heading}"\n(blank to inherit default)\n\nInitial delay (s) [${effective.initial_delay}]:`,
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
    await loadEvents();
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
    await loadStatus();
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    const dot = $("#status-dot");
    const text = $("#status-text");
    if (s.last_error) {
      dot.className = "dot err";
      text.textContent = s.last_error;
    } else if (!s.logged_in && s.last_tick_ts === null) {
      dot.className = "dot warn";
      text.textContent = "not logged in";
    } else {
      dot.className = "dot ok";
      const last = s.last_tick_ts ? fmtRel(s.last_tick_ts) : "never";
      text.textContent = `healthy · last check ${last} · ${s.events_cached} events · ${s.scheduled_count} armed`;
    }
    const next = $("#status-next");
    if (s.next_fire_ts) {
      next.textContent = `next: ${s.next_event_heading || "—"} ${fmtRel(s.next_fire_ts)}`;
    } else {
      next.textContent = "";
    }
    $("#status-dry").hidden = !s.dry_run;
  } catch {
    // status bar shouldn't throw
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

$("#creds-form").addEventListener("submit", saveCreds);
$("#refresh").addEventListener("click", manualRefresh);
$("#save-selection").addEventListener("click", saveSelection);

loadConfig()
  .then(() => Promise.all([loadEvents(), loadStatus()]))
  .catch(() => {});
setInterval(loadStatus, 15000);
