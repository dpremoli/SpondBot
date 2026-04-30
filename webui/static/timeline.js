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

  // Only show detail when error text adds something beyond the result badge
  if (e.error && e.error !== tlResultText(e)) {
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

// Per-event modal timeline: narrative labels, no event name repetition, grouped by day
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

// Render the 4-step lifecycle header inside the modal
// meta: { inviteTime, armed_ts, startTimestamp, endTimestamp, accepted, waitlisted, failed }
// historyEntries: sorted array of history entries for this event (to derive first attempt + outcome)
function tlRenderLifecycle(container, meta, historyEntries) {
  const sorted = historyEntries
    ? [...historyEntries].sort((a, b) => {
        const ta = new Date(typeof a.ts === "number" ? a.ts * 1000 : a.ts).getTime();
        const tb = new Date(typeof b.ts === "number" ? b.ts * 1000 : b.ts).getTime();
        return ta - tb;
      })
    : [];

  const firstEntry = sorted[0] || null;
  const lastEntry = sorted[sorted.length - 1] || null;

  const outcomeText = !lastEntry ? "pending"
    : !lastEntry.ok ? (lastEntry.error || "failed")
    : lastEntry.waitlisted ? "waitlisted"
    : "accepted";
  const outcomeCls = !lastEntry ? ""
    : !lastEntry.ok ? "failed"
    : lastEntry.waitlisted ? "waitlisted"
    : "accepted";

  const steps = [
    {
      label: "Invite opens",
      value: meta.inviteTime ? tlFmt(meta.inviteTime) : "—",
      active: !!meta.inviteTime,
    },
    {
      label: "Bot armed",
      value: meta.armed_ts ? tlFmt(meta.armed_ts) : "—",
      active: !!meta.armed_ts,
      note: meta.armed_ts ? "scheduled to fire" : null,
    },
    {
      label: "First attempt",
      value: firstEntry ? tlFmt(firstEntry.ts) : "—",
      active: !!firstEntry,
    },
    {
      label: "Outcome",
      value: `<span class="tl-badge tl-badge--${tlEscape(outcomeCls)}">${tlEscape(outcomeText)}</span>`,
      isHtml: true,
      active: !!lastEntry,
    },
  ];

  container.innerHTML = `
    <div class="lifecycle">
      ${steps.map((s, i) => `
        <div class="lifecycle-step${s.active ? " lifecycle-step--active" : ""}">
          <div class="lifecycle-step-num">${i + 1}</div>
          <div class="lifecycle-step-body">
            <div class="lifecycle-step-label">${tlEscape(s.label)}</div>
            <div class="lifecycle-step-value">${s.isHtml ? s.value : tlEscape(s.value)}</div>
          </div>
        </div>
        ${i < steps.length - 1 ? '<div class="lifecycle-connector"></div>' : ""}
      `).join("")}
    </div>
  `;
}

// Open the shared event-history modal (requires #event-modal in the page)
async function tlOpenEventModal(eventId, heading, meta = {}) {
  const modal = document.querySelector("#event-modal");
  const title = document.querySelector("#modal-title");
  const idEl = document.querySelector("#modal-event-id");
  const lifecycleContainer = document.querySelector("#modal-lifecycle");
  const modalTl = document.querySelector("#modal-timeline");

  title.textContent = heading || eventId;
  idEl.textContent = `ID: ${eventId}`;

  // Render lifecycle with placeholders while loading
  if (lifecycleContainer) tlRenderLifecycle(lifecycleContainer, meta, []);
  modalTl.innerHTML = `<p class="muted tl-empty">Loading…</p>`;
  modal.hidden = false;

  try {
    const res = await fetch(`/api/history?event_id=${encodeURIComponent(eventId)}`);
    const { entries } = await res.json();
    // Re-render lifecycle now that we have history entries
    if (lifecycleContainer) tlRenderLifecycle(lifecycleContainer, meta, entries);
    tlRenderEventHistory(modalTl, entries);
  } catch (err) {
    modalTl.innerHTML = `<p class="tl-empty" style="color:var(--err)">${tlEscape(String(err.message))}</p>`;
  }
}
