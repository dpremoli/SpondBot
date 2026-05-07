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
  currentUser = u;
});

// ---- Utilities ----
function fmt(ts) {
  if (!ts) return "—";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
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
let _selectionDirty = false;
let currentUser = null;

const _groupHeaderRefs = new Map();

function patchGroupHeader(key) {
  const ref = _groupHeaderRefs.get(key);
  if (!ref) return;
  const { countEl, selectAllEl, selectableSessions } = ref;
  const selectedCount = selectableSessions.filter((s) => s.selected).length;
  const total = selectableSessions.length;
  const acceptedCount = ref.allSessions.filter((s) => s.accepted && !s.waitlisted).length;
  const waitlistedCount = ref.allSessions.filter((s) => s.waitlisted).length;
  countEl.textContent =
    `${selectedCount}/${total} selected` +
    (acceptedCount ? ` · ${acceptedCount} accepted` : "") +
    (waitlistedCount ? ` · ${waitlistedCount} waitlisted` : "");
  selectAllEl.checked = total > 0 && selectedCount === total;
  selectAllEl.indeterminate = selectedCount > 0 && selectedCount < total;
}

async function loadConfig() {
  const cfg = await api("/api/config");
  selectedSet = new Set(cfg.selected_event_ids || []);
  groupByMode = cfg.group_by || "heading";
  $("#group_by").value = groupByMode;
  try { showPast = localStorage.getItem("__show_past") === "true"; } catch { showPast = false; }
  $("#show-past").checked = showPast;
}

async function loadEvents() {
  const status = $("#events-status");
  try {
    const { events } = await api("/api/events");
    cachedEvents = events;
    for (const e of events) e.selected = selectedSet.has(e.id);
    renderGroups();
    renderNextUp();
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
  _groupHeaderRefs.clear();
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
      groups.set(k, { key: k, label: groupLabel(e, groupByMode, k), groupName: e.groupName || e.groupId || "", sessions: [] });
    }
    groups.get(k).sessions.push(e);
  }

  const sorted = [...groups.values()].sort((a, b) => {
    const ta = Math.min(...a.sessions.map((s) => new Date(s.startTimestamp || 0).getTime() || Infinity));
    const tb = Math.min(...b.sessions.map((s) => new Date(s.startTimestamp || 0).getTime() || Infinity));
    return ta - tb;
  });

  for (const g of sorted) {
    g.sessions.sort((a, b) => new Date(a.startTimestamp || 0) - new Date(b.startTimestamp || 0));
    container.appendChild(renderGroup(g));
  }
}

function renderGroup(g) {
  const wrap = document.createElement("div");
  wrap.className = "group";

  const selectableSessions = g.sessions.filter((s) => !isPast(s));
  const selectedCount = selectableSessions.filter((s) => s.selected).length;
  const total = selectableSessions.length;
  const acceptedCount = g.sessions.filter((s) => s.accepted && !s.waitlisted).length;
  const waitlistedCount = g.sessions.filter((s) => s.waitlisted).length;

  const uniqueEventIds = [...new Set(g.sessions.map((s) => s.id).filter(Boolean))];
  const idTooltip = uniqueEventIds.length ? `Event ID${uniqueEventIds.length > 1 ? "s" : ""}:\n${uniqueEventIds.join("\n")}` : "";

  const header = document.createElement("div");
  header.className = "group-header";
  header.innerHTML = `
    <button class="twisty" aria-expanded="false">▸</button>
    <span class="group-title" title="${escapeHtml(idTooltip)}">${escapeHtml(g.label)}</span>
    <span class="group-meta">${escapeHtml(g.groupName)}</span>
    <span class="group-count">${selectedCount}/${total} selected${
    acceptedCount ? ` · ${acceptedCount} accepted` : ""
  }${waitlistedCount ? ` · ${waitlistedCount} waitlisted` : ""}</span>
    <label class="select-all" title="Select all future sessions">
      <input type="checkbox" ${total > 0 && selectedCount === total ? "checked" : ""} ${total === 0 ? "disabled" : ""} />
      all
    </label>
  `;
  const twisty = header.querySelector(".twisty");
  const selectAll = header.querySelector(".select-all input");

  if (selectedCount > 0 && selectedCount < total) selectAll.indeterminate = true;

  _groupHeaderRefs.set(g.key, {
    countEl: header.querySelector(".group-count"),
    selectAllEl: selectAll,
    selectableSessions,
    allSessions: g.sessions,
  });

  const body = document.createElement("div");
  body.className = "group-body";
  body.style.display = "none";

  const tbl = document.createElement("table");
  tbl.className = "sessions";
  const showName = groupByMode !== "heading";
  tbl.innerHTML = `
    <thead>
      <tr>
        <th></th>
        ${showName ? "<th>Event</th>" : ""}
        <th>Invite opens</th><th>Starts</th><th>Ends</th><th>Status</th><th>Override</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  const tbody = tbl.querySelector("tbody");
  for (const s of g.sessions) tbody.appendChild(renderSession(s, g.key));
  body.appendChild(tbl);

  twisty.addEventListener("click", () => {
    const open = body.style.display !== "none";
    body.style.display = open ? "none" : "";
    twisty.textContent = open ? "▸" : "▾";
    twisty.setAttribute("aria-expanded", String(!open));
  });

  selectAll.addEventListener("change", () => {
    const on = selectAll.checked;
    for (const s of selectableSessions) {
      if (s.paymentRequired) continue;
      s.selected = on;
      if (on) selectedSet.add(s.id);
      else selectedSet.delete(s.id);
    }
    _selectionDirty = true;
    patchGroupHeader(g.key);
  });

  wrap.append(header, body);
  return wrap;
}

function renderSession(s, gKey) {
  const tr = document.createElement("tr");
  tr.classList.add("clickable");
  tr.title = "Click to see bot activity for this event";
  const past = isPast(s);
  if (past) tr.classList.add("past");
  if (s.waitlisted) tr.classList.add("waitlisted");
  else if (s.accepted) tr.classList.add("accepted");
  else if (s.failed) tr.classList.add("failed");
  else if (isAvailable(s)) tr.classList.add("available");

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = s.selected;
  cb.disabled = past;
  if (s.paymentRequired) {
    cb.disabled = true;
    cb.checked = false;
    cb.title = "Payment required — cannot be auto-accepted by the bot";
  }
  if (s.selected && !s.paymentRequired) tr.classList.add("selected-row");
  cb.addEventListener("click", (ev) => ev.stopPropagation());
  cb.addEventListener("change", () => {
    s.selected = cb.checked;
    if (cb.checked) { selectedSet.add(s.id); tr.classList.add("selected-row"); }
    else { selectedSet.delete(s.id); tr.classList.remove("selected-row"); }
    _selectionDirty = true;
    patchGroupHeader(gKey);
  });
  const tdCb = document.createElement("td");
  tdCb.className = "td-cb";
  tdCb.appendChild(cb);

  const tdInvite = document.createElement("td");
  tdInvite.dataset.label = "Invite opens";
  if (s.inviteTime) {
    tdInvite.innerHTML = `${escapeHtml(fmt(s.inviteTime))}<small class="muted td-sub">${escapeHtml(fmtRel(s.inviteTime))}</small>`;
  } else {
    tdInvite.innerHTML = `<span class="muted">open now</span><small class="td-sub">&nbsp;</small>`;
  }

  const tdStart = document.createElement("td");
  tdStart.dataset.label = "Starts";
  tdStart.textContent = fmt(s.startTimestamp);
  const tdEnd = document.createElement("td");
  tdEnd.dataset.label = "Ends";
  tdEnd.textContent = fmt(s.endTimestamp);

  const tdStatus = document.createElement("td");
  const [statusLabel, statusCls] = past
    ? ["past", "muted"]
    : s.waitlisted ? ["waitlisted", "waitlisted"]
    : s.accepted ? ["accepted", "accepted"]
    : s.failed ? ["failed", "failed"]
    : isAvailable(s) ? ["open", "open"]
    : ["scheduled", "scheduled"];
  const paymentBadge = s.paymentRequired
    ? ` <span class="status-pill status-pill--payment" title="Payment required — accept manually in the Spond app">💳 payment</span>`
    : "";
  tdStatus.dataset.label = "Status";
  tdStatus.innerHTML = `<span class="status-pill status-pill--${statusCls}">${statusLabel}</span>${paymentBadge}`;

  const tdOv = document.createElement("td");
  tdOv.className = "td-actions";
  if (!past) {
    if (isAvailable(s) && !s.accepted && !s.waitlisted) {
      const acceptBtn = document.createElement("button");
      acceptBtn.className = "small";
      acceptBtn.textContent = "Accept now";
      acceptBtn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        acceptBtn.disabled = true;
        acceptBtn.textContent = "Firing…";
        try {
          await api(`/api/events/${s.id}/accept`, { method: "POST" });
          acceptBtn.textContent = "Fired ✓";
        } catch (e) {
          acceptBtn.textContent = "Failed";
          acceptBtn.title = e.message;
          acceptBtn.disabled = false;
        }
      });
      tdOv.appendChild(acceptBtn);
    }

    const refreshBtn = document.createElement("button");
    refreshBtn.className = "small ghost";
    refreshBtn.textContent = "↺";
    refreshBtn.title = "Refresh this event from Spond";
    refreshBtn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      refreshBtn.disabled = true;
      try { await api(`/api/events/${s.id}/refresh`, { method: "POST" }); await loadEvents(); }
      catch (e) { refreshBtn.title = e.message; }
      finally { refreshBtn.disabled = false; }
    });
    tdOv.appendChild(refreshBtn);

    if (currentUser?.is_admin) {
      const ovBtn = document.createElement("button");
      ovBtn.className = "small ghost";
      ovBtn.textContent = s.hasOverride ? "Edit override" : "Override";
      ovBtn.addEventListener("click", (ev) => { ev.stopPropagation(); openOverrideDialog(s); });
      tdOv.appendChild(ovBtn);
    }
  }

  tr.addEventListener("click", () => tlOpenEventModal(s.id, s.heading, {
    startTimestamp: s.startTimestamp, endTimestamp: s.endTimestamp,
    inviteTime: s.inviteTime, armed_ts: s.armed_ts,
    accepted: s.accepted && !s.waitlisted, waitlisted: s.waitlisted, failed: s.failed,
    location: s.location, groupName: s.groupName,
    acceptedCount: s.acceptedCount, declinedCount: s.declinedCount,
    waitinglistCount: s.waitinglistCount, unansweredCount: s.unansweredCount,
    maxAccepted: s.maxAccepted, isFull: s.isFull,
    paymentRequired: s.paymentRequired,
  }));

  if (groupByMode !== "heading") {
    const tdName = document.createElement("td");
    tdName.textContent = s.heading || "—";
    tr.append(tdCb, tdName, tdInvite, tdStart, tdEnd, tdStatus, tdOv);
  } else {
    tr.append(tdCb, tdInvite, tdStart, tdEnd, tdStatus, tdOv);
  }
  return tr;
}

async function openOverrideDialog(s) {
  const { effective, override } = await api(`/api/event-settings/${s.id}`);
  const base = override || {};
  const initial = prompt(`Override for "${s.heading}"\n(blank to inherit default)\n\nInitial delay (s) [${effective.initial_delay}]:`, base.initial_delay ?? "");
  if (initial === null) return;
  const count = prompt(`Retry count [${effective.retry_count}]:`, base.retry_count ?? "");
  if (count === null) return;
  const interval = prompt(`Retry interval (s) [${effective.retry_interval}]:`, base.retry_interval ?? "");
  if (interval === null) return;
  const response = prompt(`Response — accepted | declined | unconfirmed [${effective.response}]:`, base.response ?? "");
  if (response === null) return;
  const body = {
    initial_delay: initial === "" ? null : parseFloat(initial),
    retry_count: count === "" ? null : parseInt(count, 10),
    retry_interval: interval === "" ? null : parseFloat(interval),
    response: response === "" ? null : response.trim(),
  };
  try {
    await api(`/api/event-settings/${s.id}`, { method: "POST", body: JSON.stringify(body) });
    await loadEvents();
  } catch (e) { alert(e.message); }
}

async function saveSelection() {
  const status = $("#events-status");
  try {
    await api("/api/selection", { method: "POST", body: JSON.stringify({ event_ids: [...selectedSet] }) });
    _selectionDirty = false;
    status.textContent = `Saved ${selectedSet.size} selected`;
    status.className = "status ok";
    await loadStatus();
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

window.addEventListener("beforeunload", (e) => {
  if (_selectionDirty) {
    e.preventDefault();
    e.returnValue = "You have unsaved event selections. Leave without saving?";
  }
});

let _lastStatus = null;

async function loadStatus() {
  try {
    _lastStatus = await api("/api/status");
    renderStatus(_lastStatus);
  } catch { /* non-critical */ }
}

function renderStatus(s) {
  const vEl = $("#nav-version");
  if (vEl && s.version) vEl.textContent = `v${s.version}`;
  const dot = $("#status-dot");
  const text = $("#status-text");
  if (s.last_error) {
    dot.className = "dot err";
    text.textContent = s.last_error;
  } else if (!s.logged_in && s.last_tick_ts === null) {
    dot.className = "dot warn";
    text.textContent = "not logged in — configure Spond credentials in Settings";
  } else {
    dot.className = "dot ok";
    const last = s.last_tick_ts ? fmtRel(s.last_tick_ts) : "never";
    const failedNote = s.failed_count ? ` · ${s.failed_count} failed` : "";
    text.textContent = `healthy · last check ${last} · ${s.events_cached} events · ${s.scheduled_count} armed${failedNote}`;
  }
  $("#status-dry").hidden = !s.dry_run;
  renderNextUp();
}

function renderNextUp() {
  const container = $("#status-next-up");
  if (!container) return;
  const now = Date.now() / 1000;
  const upcoming = cachedEvents
    .filter(e => e.armed_ts && e.armed_ts > now && !e.accepted && !e.waitlisted && !e.failed)
    .sort((a, b) => a.armed_ts - b.armed_ts)
    .slice(0, 5);

  container.innerHTML = "";
  if (upcoming.length === 0) { container.hidden = true; return; }
  container.hidden = false;

  const label = document.createElement("div");
  label.className = "next-up-label";
  label.textContent = "Next up";
  container.appendChild(label);

  for (const e of upcoming) {
    const row = document.createElement("div");
    row.className = "next-up-row";
    row.title = "View event details";

    // Left: name + group
    const left = document.createElement("div");
    left.className = "next-up-left";
    const name = document.createElement("span");
    name.className = "next-up-name";
    name.textContent = e.heading || "—";
    left.appendChild(name);
    if (e.groupName && e.groupName !== e.heading) {
      const grp = document.createElement("small");
      grp.className = "next-up-group";
      grp.textContent = e.groupName;
      left.appendChild(grp);
    }

    // Right: times + accept button
    const right = document.createElement("div");
    right.className = "next-up-right";

    const times = document.createElement("div");
    times.className = "next-up-times";
    const rel = document.createElement("span");
    rel.className = "next-up-time";
    rel.textContent = fmtRel(e.armed_ts);
    const exact = document.createElement("small");
    exact.className = "next-up-exact";
    exact.textContent = fmt(e.armed_ts);
    times.append(rel, exact);

    const acceptBtn = document.createElement("button");
    acceptBtn.className = "small next-up-accept";
    acceptBtn.textContent = "Accept now";
    acceptBtn.title = "Fire immediately";
    acceptBtn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      acceptBtn.disabled = true;
      acceptBtn.textContent = "…";
      try {
        await api(`/api/events/${e.id}/accept`, { method: "POST" });
        await Promise.all([loadEvents(), loadStatus()]);
      } catch (err) {
        acceptBtn.textContent = "Failed";
        acceptBtn.title = err.message;
        setTimeout(() => { acceptBtn.textContent = "Accept now"; acceptBtn.disabled = false; }, 3000);
      }
    });

    right.append(times, acceptBtn);
    row.append(left, right);

    row.addEventListener("click", () => tlOpenEventModal(e.id, e.heading, {
      startTimestamp: e.startTimestamp, endTimestamp: e.endTimestamp,
      inviteTime: e.inviteTime, armed_ts: e.armed_ts,
      accepted: false, waitlisted: false, failed: false,
      location: e.location, groupName: e.groupName,
      acceptedCount: e.acceptedCount, declinedCount: e.declinedCount,
      waitinglistCount: e.waitinglistCount, unansweredCount: e.unansweredCount,
      maxAccepted: e.maxAccepted, isFull: e.isFull,
      paymentRequired: e.paymentRequired,
    }));

    container.appendChild(row);
  }
}

function fmtCountdown(totalSecs) {
  const s = Math.floor(totalSecs);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return m > 0 ? `${m}m ${String(sec).padStart(2, "0")}s` : `${sec}s`;
}

function updateUpcomingBanner() {
  const banner = $("#upcoming-banner");
  const list = $("#upcoming-list");
  if (!banner || !list) return;
  const now = Date.now();
  const WINDOW_MS = 5 * 60 * 1000;
  const soon = cachedEvents.filter((e) => {
    if (!e.startTimestamp) return false;
    const start = new Date(e.startTimestamp).getTime();
    const delta = start - now;
    return delta > -60_000 && delta <= WINDOW_MS;
  });
  if (soon.length === 0) { banner.hidden = true; return; }
  banner.hidden = false;
  list.innerHTML = soon
    .sort((a, b) => new Date(a.startTimestamp) - new Date(b.startTimestamp))
    .map((e) => {
      const start = new Date(e.startTimestamp).getTime();
      const delta = (start - now) / 1000;
      const countdown = delta > 0
        ? `<strong>${fmtCountdown(delta)}</strong>`
        : `<strong class="started">started</strong>`;
      return `<span class="upcoming-event">${escapeHtml(e.heading || "—")} — ${countdown}</span>`;
    })
    .join("");
}

setInterval(() => {
  if (_lastStatus) renderStatus(_lastStatus);
  updateUpcomingBanner();
}, 1000);

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

$("#refresh").addEventListener("click", manualRefresh);
$("#status-refresh")?.addEventListener("click", manualRefresh);
$("#save-selection").addEventListener("click", saveSelection);

$("#group_by").addEventListener("change", async () => {
  groupByMode = $("#group_by").value;
  try {
    const cur = await api("/api/settings");
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ ...cur.defaults, dry_run: cur.dry_run, group_by: groupByMode }),
    });
  } catch (e) { console.warn("group_by persist failed:", e.message); }
  renderGroups();
});

$("#show-past").addEventListener("change", () => {
  showPast = $("#show-past").checked;
  try { localStorage.setItem("__show_past", String(showPast)); } catch { /* ok */ }
  renderGroups();
});

(function () {
  const backdrop = document.querySelector("#event-modal");
  if (!backdrop) return;
  const sheet = backdrop.querySelector(".modal");

  function closeModal() {
    if (window.matchMedia("(max-width: 699px)").matches) {
      sheet.style.transition = "transform .22s ease-in, opacity .22s ease-in";
      sheet.style.transform = "translateY(100%)";
      sheet.style.opacity = "0";
      setTimeout(() => {
        backdrop.hidden = true;
        sheet.style.cssText = "";
      }, 230);
    } else {
      backdrop.hidden = true;
    }
  }

  document.querySelector("#modal-close").addEventListener("click", closeModal);
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  // Swipe-to-dismiss: track finger drag on the sheet
  let _startY = 0;
  let _dragging = false;

  sheet.addEventListener("touchstart", (e) => {
    if (sheet.scrollTop > 0) return; // don't intercept when content is scrolled
    _startY = e.touches[0].clientY;
    _dragging = true;
    sheet.style.transition = "none";
  }, { passive: true });

  sheet.addEventListener("touchmove", (e) => {
    if (!_dragging) return;
    const dy = e.touches[0].clientY - _startY;
    if (dy <= 0) { // scrolling up — let the sheet scroll normally
      _dragging = false;
      sheet.style.transition = "";
      sheet.style.transform = "";
      return;
    }
    e.preventDefault(); // block pull-to-refresh while dragging down
    sheet.style.transform = `translateY(${dy}px)`;
  }, { passive: false });

  function _endDrag(endY) {
    if (!_dragging) return;
    _dragging = false;
    const dy = endY - _startY;
    if (dy > 80) {
      closeModal();
    } else {
      // Snap back with a springy feel
      sheet.style.transition = "transform .2s cubic-bezier(.34,1.56,.64,1)";
      sheet.style.transform = "";
      sheet.addEventListener("transitionend", () => { sheet.style.transition = ""; }, { once: true });
    }
  }

  sheet.addEventListener("touchend", (e) => { _endDrag(e.changedTouches[0].clientY); }, { passive: true });
  sheet.addEventListener("touchcancel", (e) => { _endDrag(_startY); }, { passive: true }); // snap back on cancel
})();

// ---- View toggle ----
let _calView = false;
let _calYear = new Date().getFullYear();
let _calMonth = new Date().getMonth();

$("#view-list").addEventListener("click", () => {
  _calView = false;
  $("#view-list").classList.add("view-btn--active");
  $("#view-cal").classList.remove("view-btn--active");
  $("#event-groups").hidden = false;
  $("#cal-view").hidden = true;
  $("#group-by-label").hidden = false;
});

$("#view-cal").addEventListener("click", () => {
  _calView = true;
  $("#view-cal").classList.add("view-btn--active");
  $("#view-list").classList.remove("view-btn--active");
  $("#event-groups").hidden = true;
  $("#cal-view").hidden = false;
  $("#group-by-label").hidden = true;
  renderCalendar();
});

// ---- Calendar view ----
function renderCalendar() {
  const container = $("#cal-view");
  container.innerHTML = "";

  const nav = document.createElement("div");
  nav.className = "cal-nav";
  const prevBtn = document.createElement("button");
  prevBtn.className = "ghost small";
  prevBtn.textContent = "‹ Prev";
  const nextBtn = document.createElement("button");
  nextBtn.className = "ghost small";
  nextBtn.textContent = "Next ›";
  const monthLabel = document.createElement("span");
  monthLabel.className = "cal-month-label";
  monthLabel.textContent = new Date(_calYear, _calMonth, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });

  prevBtn.addEventListener("click", () => {
    _calMonth--;
    if (_calMonth < 0) { _calMonth = 11; _calYear--; }
    renderCalendar();
  });
  nextBtn.addEventListener("click", () => {
    _calMonth++;
    if (_calMonth > 11) { _calMonth = 0; _calYear++; }
    renderCalendar();
  });

  nav.append(prevBtn, monthLabel, nextBtn);
  container.appendChild(nav);

  const dayMap = new Map();
  for (const e of cachedEvents) {
    if (!e.startTimestamp) continue;
    const d = new Date(e.startTimestamp);
    if (d.getFullYear() !== _calYear || d.getMonth() !== _calMonth) continue;
    const key = `${_calYear}-${String(_calMonth + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    if (!dayMap.has(key)) dayMap.set(key, []);
    dayMap.get(key).push(e);
  }

  const grid = document.createElement("div");
  grid.className = "cal-grid";

  for (const name of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) {
    const h = document.createElement("div");
    h.className = "cal-day-name";
    h.textContent = name;
    grid.appendChild(h);
  }

  let startDow = new Date(_calYear, _calMonth, 1).getDay();
  startDow = startDow === 0 ? 6 : startDow - 1;
  for (let i = 0; i < startDow; i++) {
    const cell = document.createElement("div");
    cell.className = "cal-day cal-day--empty";
    grid.appendChild(cell);
  }

  const daysInMonth = new Date(_calYear, _calMonth + 1, 0).getDate();
  const today = new Date();

  for (let day = 1; day <= daysInMonth; day++) {
    const cell = document.createElement("div");
    cell.className = "cal-day";
    const isToday = today.getFullYear() === _calYear && today.getMonth() === _calMonth && today.getDate() === day;
    if (isToday) cell.classList.add("cal-day--today");

    const numEl = document.createElement("div");
    numEl.className = "cal-day-num";
    numEl.textContent = day;
    cell.appendChild(numEl);

    const key = `${_calYear}-${String(_calMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    for (const e of (dayMap.get(key) || [])) {
      const pill = document.createElement("div");
      pill.className = "cal-pill cal-pill--" + (
        e.waitlisted ? "waitlisted" : e.accepted ? "accepted" : e.failed ? "failed" :
        isPast(e) ? "past" : "scheduled"
      );
      pill.textContent = e.heading || "—";
      pill.title = `${e.heading || "—"}\n${fmt(e.startTimestamp)}`;
      pill.addEventListener("click", () => tlOpenEventModal(e.id, e.heading, {
        startTimestamp: e.startTimestamp, endTimestamp: e.endTimestamp,
        inviteTime: e.inviteTime, armed_ts: e.armed_ts,
        accepted: e.accepted && !e.waitlisted, waitlisted: e.waitlisted, failed: e.failed,
        location: e.location, groupName: e.groupName,
        acceptedCount: e.acceptedCount, declinedCount: e.declinedCount,
        waitinglistCount: e.waitinglistCount, unansweredCount: e.unansweredCount,
        maxAccepted: e.maxAccepted, isFull: e.isFull,
        paymentRequired: e.paymentRequired,
      }));
      cell.appendChild(pill);
    }

    grid.appendChild(cell);
  }

  container.appendChild(grid);
}

loadConfig()
  .then(() => Promise.all([loadEvents(), loadStatus()]))
  .catch(() => {});
setInterval(loadStatus, 15_000);
