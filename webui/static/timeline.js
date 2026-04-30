// Shared timeline renderer — loaded by both logs.html and index.html

function tlFmt(ts) {
  if (!ts) return "—";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
}

function tlFmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function tlFmtDate(ts) {
  if (!ts) return "—";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function tlDayKey(ts) {
  if (!ts) return "unknown";
  const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
  return isNaN(d.getTime()) ? "unknown" : d.toISOString().slice(0, 10);
}

function tlEscape(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function tlEntryClass(e) {
  if (!e.ok) return "failed";
  if (e.waitlisted) return "waitlisted";
  return "accepted";
}

function tlResultText(e) {
  if (!e.ok) return e.error || "failed";
  if (e.dry_run) return "dry-run";
  if (e.waitlisted) return "waitlisted";
  return "accepted";
}

function tlEventLabel(e) {
  const name = e.heading || e.event_id;
  if (!e.startTimestamp) return tlEscape(name);
  const d = new Date(e.startTimestamp);
  if (isNaN(d.getTime())) return tlEscape(name);
  const dateStr = d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  return `${tlEscape(name)} <span class="tl-event-date">${dateStr}</span>`;
}

// Build a single timeline node.
// label: override the card title (used in per-event modal to avoid repetition)
// clickHandler: makes card clickable
function tlMakeNode(e, { label = null, clickHandler = null } = {}) {
  const cls = tlEntryClass(e);
  const node = document.createElement("div");
  node.className = `tl-node ${cls}`;

  const dot = document.createElement("div");
  dot.className = "tl-dot";
  dot.textContent = cls === "accepted" ? "✓" : cls === "waitlisted" ? "~" : "✕";

  const card = document.createElement("div");
  card.className = "tl-card";

  const header = document.createElement("div");
  header.className = "tl-card-header";

  const nameSpan = document.createElement("span");
  nameSpan.className = "tl-card-name";
  // Use provided label (e.g. "First attempt") or event name for the main log
  nameSpan.innerHTML = label ? tlEscape(label) : tlEventLabel(e);

  const meta = document.createElement("span");
  meta.className = "tl-card-meta";
  const res = tlResultText(e);
  const badges = [`<span class="tl-badge tl-badge--${cls}">${tlEscape(res)}</span>`];
  if (e.dry_run) badges.push(`<span class="tl-badge tl-badge--muted">dry-run</span>`);
  meta.innerHTML = badges.join("");

  const time = document.createElement("span");
  time.className = "tl-card-time";
  time.textContent = tlFmtTime(e.ts);
  time.title = tlFmt(e.ts);

  header.append(nameSpan, meta, time);
  card.appendChild(header);

  if (e.error) {
    const errEl = document.createElement("div");
    errEl.className = "tl-card-detail";
    errEl.textContent = e.error;
    card.appendChild(errEl);
  }

  node.append(dot, card);

  if (clickHandler) {
    card.classList.add("clickable");
    card.title = "Click to see full attempt log for this event";
    card.addEventListener("click", clickHandler);
  }

  return node;
}

// Main log view: group entries by day, each card shows event name + result
function tlRenderByDay(container, entries, clickHandler = null) {
  container.innerHTML = "";
  if (entries.length === 0) {
    container.innerHTML = `<p class="muted tl-empty">No activity yet.</p>`;
    return;
  }

  const groups = new Map();
  for (const e of entries) {
    const key = tlDayKey(e.ts);
    if (!groups.has(key)) groups.set(key, { label: tlFmtDate(e.ts), entries: [] });
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
      track.appendChild(tlMakeNode(e, {
        clickHandler: clickHandler ? () => clickHandler(e) : null,
      }));
    }
    section.appendChild(track);
    container.appendChild(section);
  }
}

// Per-event modal timeline: narrative labels, no event name repetition
function tlRenderEventHistory(container, entries) {
  container.innerHTML = "";
  if (entries.length === 0) {
    container.innerHTML = `<p class="muted tl-empty">No bot activity recorded for this event yet.</p>`;
    return;
  }

  const sorted = [...entries].sort((a, b) => {
    const ta = new Date(typeof a.ts === "number" ? a.ts * 1000 : a.ts).getTime();
    const tb = new Date(typeof b.ts === "number" ? b.ts * 1000 : b.ts).getTime();
    return ta - tb;
  });

  // Group by day so multi-day retry sequences are clear
  const groups = new Map();
  for (const e of sorted) {
    const key = tlDayKey(e.ts);
    if (!groups.has(key)) groups.set(key, { label: tlFmtDate(e.ts), entries: [] });
    groups.get(key).entries.push(e);
  }

  let globalIdx = 0;
  const total = sorted.length;

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
      const isFirst = globalIdx === 0;
      const isLast = globalIdx === total - 1;
      let label;
      if (total === 1) {
        label = "Bot responded";
      } else if (isFirst) {
        label = "First attempt";
      } else if (isLast) {
        const cls = tlEntryClass(e);
        if (cls === "accepted") label = "Accepted";
        else if (cls === "waitlisted") label = "Waitlisted";
        else label = `Gave up (attempt ${globalIdx + 1})`;
      } else {
        label = `Retry ${globalIdx + 1}`;
      }
      track.appendChild(tlMakeNode(e, { label }));
      globalIdx++;
    }

    section.appendChild(track);
    container.appendChild(section);
  }
}

// Render the event metadata summary header inside the modal
function tlRenderEventMeta(container, meta) {
  // meta: { heading, startTimestamp, endTimestamp, inviteTime, accepted, waitlisted, failed, id }
  const statusText = meta.accepted && !meta.waitlisted ? "accepted"
    : meta.waitlisted ? "waitlisted"
    : meta.failed ? "failed"
    : "pending";
  const statusCls = meta.accepted && !meta.waitlisted ? "accepted"
    : meta.waitlisted ? "waitlisted"
    : meta.failed ? "failed"
    : "";

  const start = meta.startTimestamp ? tlFmt(meta.startTimestamp) : null;
  const invite = meta.inviteTime ? tlFmt(meta.inviteTime) : null;

  container.innerHTML = `
    <div class="event-meta-grid">
      ${start ? `<div class="event-meta-item"><span class="event-meta-label">Event starts</span><span class="event-meta-value">${tlEscape(start)}</span></div>` : ""}
      ${invite ? `<div class="event-meta-item"><span class="event-meta-label">Invite opened</span><span class="event-meta-value">${tlEscape(invite)}</span></div>` : ""}
      <div class="event-meta-item"><span class="event-meta-label">Bot status</span><span class="event-meta-value tl-badge tl-badge--${tlEscape(statusCls)}">${tlEscape(statusText)}</span></div>
    </div>
  `;
}

// Open the shared event-history modal (requires #event-modal in the page)
async function tlOpenEventModal(eventId, heading, meta = {}) {
  const modal = document.querySelector("#event-modal");
  const title = document.querySelector("#modal-title");
  const idEl = document.querySelector("#modal-event-id");
  const metaContainer = document.querySelector("#modal-event-meta");
  const modalTl = document.querySelector("#modal-timeline");

  title.textContent = heading || eventId;
  idEl.textContent = `ID: ${eventId}`;
  if (metaContainer) tlRenderEventMeta(metaContainer, { ...meta, id: eventId });
  modalTl.innerHTML = `<p class="muted tl-empty">Loading…</p>`;
  modal.hidden = false;

  try {
    const res = await fetch(`/api/history?event_id=${encodeURIComponent(eventId)}`);
    const { entries } = await res.json();
    tlRenderEventHistory(modalTl, entries);
  } catch (err) {
    modalTl.innerHTML = `<p class="tl-empty" style="color:var(--err)">${tlEscape(String(err.message))}</p>`;
  }
}
