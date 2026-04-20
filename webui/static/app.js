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

async function loadConfig() {
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
  const body = {
    username: $("#username").value.trim(),
    password: $("#password").value,
    group_ids: $("#group_ids").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
  };
  if (!body.password) {
    const cfg = await api("/api/config");
    if (!cfg.has_password) {
      status.textContent = "Password required";
      status.className = "status err";
      return;
    }
    // fetch-and-keep not supported server-side; ask user to enter it once
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
    renderEvents(events);
    status.textContent = `${events.length} events`;
    status.className = "status ok";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

function renderEvents(events) {
  const tbody = $("#events tbody");
  tbody.innerHTML = "";
  events.sort((a, b) => {
    const ta = new Date(a.inviteTime || a.startTimestamp || 0).getTime();
    const tb = new Date(b.inviteTime || b.startTimestamp || 0).getTime();
    return ta - tb;
  });
  for (const e of events) {
    const tr = document.createElement("tr");
    if (e.accepted) tr.classList.add("accepted");
    else if (isAvailable(e)) tr.classList.add("available");

    const tdCheck = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = e.selected;
    cb.dataset.eventId = e.id;
    tdCheck.appendChild(cb);

    const tdHead = document.createElement("td");
    tdHead.textContent = e.heading || e.id;

    const tdGroup = document.createElement("td");
    tdGroup.textContent = e.groupName || e.groupId || "";

    const tdInvite = document.createElement("td");
    tdInvite.textContent = fmt(e.inviteTime);

    const tdStart = document.createElement("td");
    tdStart.textContent = fmt(e.startTimestamp);

    const tdStatus = document.createElement("td");
    tdStatus.textContent = e.accepted
      ? "accepted"
      : isAvailable(e)
      ? "open"
      : "scheduled";

    tr.append(tdCheck, tdHead, tdGroup, tdInvite, tdStart, tdStatus);
    tbody.appendChild(tr);
  }
}

async function saveSelection() {
  const status = $("#events-status");
  const ids = Array.from(
    document.querySelectorAll("#events tbody input[type=checkbox]:checked")
  ).map((cb) => cb.dataset.eventId);
  try {
    await api("/api/selection", {
      method: "POST",
      body: JSON.stringify({ event_ids: ids }),
    });
    status.textContent = `Saved ${ids.length} selected`;
    status.className = "status ok";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status err";
  }
}

$("#creds-form").addEventListener("submit", saveCreds);
$("#refresh").addEventListener("click", refreshEvents);
$("#save-selection").addEventListener("click", saveSelection);

loadConfig().then(refreshEvents).catch(() => {});
setInterval(refreshEvents, 30000);
