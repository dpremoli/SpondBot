(() => {
  // Auth guard (nav handled by nav.js)
  let currentUser = null;
  fetch('/auth/me').then(r => {
    if (!r.ok) { location.replace('/login'); return null; }
    return r.json();
  }).then(u => {
    if (!u) return;
    if (!u.is_admin) { location.replace('/'); return; }
    currentUser = u;
    init();
  });

  // ---- Tab switching ----
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabPanels = {
    users: document.getElementById('tab-users'),
    activity: document.getElementById('tab-activity'),
    bots: document.getElementById('tab-bots'),
    events: document.getElementById('tab-events'),
  };

  tabBtns.forEach(btn => btn.addEventListener('click', () => {
    tabBtns.forEach(b => b.classList.remove('tab-btn--active'));
    btn.classList.add('tab-btn--active');
    Object.entries(tabPanels).forEach(([k, el]) => el.hidden = k !== btn.dataset.tab);
    if (btn.dataset.tab === 'activity') loadActivity();
    if (btn.dataset.tab === 'bots') loadBots();
    if (btn.dataset.tab === 'events') loadAdminEvents();
  }));

  function init() {
    loadUsers();
    document.getElementById('activity-reload').addEventListener('click', loadActivity);
    document.getElementById('bots-reload').addEventListener('click', loadBots);
    document.getElementById('events-user-select').addEventListener('change', loadAdminEvents);
    document.getElementById('events-reload').addEventListener('click', loadAdminEvents);
  }

  // ---- Users ----
  let editingUid = null;
  let userMap = {}; // uid → username, populated by loadUsers

  async function loadUsers() {
    const res = await fetch('/admin/users');
    if (!res.ok) return;
    const users = await res.json();
    // Build uid → username map for activity filter
    userMap = {};
    users.forEach(u => { userMap[u.id] = u.username; });
    renderUsers(users);
    // Populate activity filter
    const sel = document.getElementById('activity-user-filter');
    const prev = sel.value;
    sel.innerHTML = '<option value="">All users</option>';
    users.forEach(u => {
      const opt = document.createElement('option');
      opt.value = u.id; opt.textContent = u.username;
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;

    // Populate events user select
    const evSel = document.getElementById('events-user-select');
    const evPrev = evSel.value;
    evSel.innerHTML = '<option value="">Select user…</option>';
    users.forEach(u => {
      const opt = document.createElement('option');
      opt.value = u.id; opt.textContent = u.username;
      evSel.appendChild(opt);
    });
    if (evPrev) evSel.value = evPrev;
  }

  function renderUsers(users) {
    const tbody = document.getElementById('users-tbody');
    tbody.innerHTML = '';
    users.forEach(u => {
      const tr = document.createElement('tr');
      const typeBadge = u.is_sso
        ? '<span class="tag" title="Provisioned via Cloudflare SSO">SSO</span>'
        : '<span class="tag">Local</span>';
      const emailHint = u.is_sso && u.email
        ? `<br/><small class="muted">${esc(u.email)}</small>`
        : '';
      tr.innerHTML = `
        <td>${esc(u.username)}${emailHint}</td>
        <td>${typeBadge}</td>
        <td>${u.is_admin ? '<span class="tag admin-tag">Admin</span>' : '<span class="tag">User</span>'}</td>
        <td class="muted" style="font-size:.8rem">${new Date(u.created_at).toLocaleDateString()}</td>
        <td>
          <div class="td-actions">
            <button class="ghost small" data-action="edit" data-uid="${u.id}" data-username="${esc(u.username)}" data-admin="${u.is_admin}" data-sso="${u.is_sso ? '1' : ''}">Edit</button>
            <button class="ghost small err-btn" data-action="delete" data-uid="${u.id}" data-username="${esc(u.username)}" ${u.id === currentUser?.id ? 'disabled title="Cannot delete own account"' : ''}>Delete</button>
          </div>
        </td>`;
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll('[data-action="edit"]').forEach(btn => btn.addEventListener('click', () => openEditModal(btn.dataset.uid, btn.dataset.username, btn.dataset.admin === 'true', btn.dataset.sso === '1')));
    tbody.querySelectorAll('[data-action="delete"]').forEach(btn => btn.addEventListener('click', () => deleteUser(btn.dataset.uid, btn.dataset.username)));
  }

  document.getElementById('new-user-btn').addEventListener('click', () => openNewModal());

  let editingIsSso = false;

  function openNewModal() {
    editingUid = null;
    editingIsSso = false;
    document.getElementById('user-modal-title').textContent = 'New user';
    document.getElementById('uf-submit').textContent = 'Create';
    document.getElementById('uf-username').value = '';
    document.getElementById('uf-username').disabled = false;
    document.getElementById('uf-password').value = '';
    document.getElementById('uf-pw-label').querySelector('input').placeholder = 'min 8 characters';
    document.getElementById('uf-pw-label').hidden = false;
    document.getElementById('uf-admin').checked = false;
    document.getElementById('uf-error').hidden = true;
    document.getElementById('user-modal').hidden = false;
  }

  function openEditModal(uid, username, isAdmin, isSso) {
    editingUid = uid;
    editingIsSso = !!isSso;
    document.getElementById('user-modal-title').textContent = `Edit ${username}`;
    document.getElementById('uf-submit').textContent = 'Save';
    document.getElementById('uf-username').value = username;
    document.getElementById('uf-username').disabled = true;
    document.getElementById('uf-password').value = '';
    document.getElementById('uf-pw-label').querySelector('input').placeholder = 'Leave blank to keep current';
    // SSO accounts have no local password — managed by the identity provider.
    document.getElementById('uf-pw-label').hidden = !!isSso;
    document.getElementById('uf-admin').checked = isAdmin;
    document.getElementById('uf-error').hidden = true;
    document.getElementById('user-modal').hidden = false;
  }

  function closeUserModal() {
    document.getElementById('user-modal').hidden = true;
  }

  document.getElementById('user-modal-close').addEventListener('click', closeUserModal);
  document.getElementById('uf-cancel').addEventListener('click', closeUserModal);

  document.getElementById('user-form').addEventListener('submit', async e => {
    e.preventDefault();
    const errEl = document.getElementById('uf-error');
    errEl.hidden = true;
    const pw = document.getElementById('uf-password').value;
    let res;
    if (editingUid) {
      const body = { is_admin: document.getElementById('uf-admin').checked };
      if (pw && !editingIsSso) body.password = pw;
      res = await fetch(`/admin/users/${editingUid}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    } else {
      res = await fetch('/admin/users', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: document.getElementById('uf-username').value, password: pw, is_admin: document.getElementById('uf-admin').checked }),
      });
    }
    if (res.ok) {
      closeUserModal();
      loadUsers();
    } else {
      const d = await res.json().catch(() => ({}));
      errEl.textContent = d.detail || 'Error saving user.';
      errEl.hidden = false;
    }
  });

  async function deleteUser(uid, username) {
    if (!confirm(`Delete user "${username}"? This cannot be undone.`)) return;
    const res = await fetch(`/admin/users/${uid}`, { method: 'DELETE' });
    if (res.ok) {
      loadUsers();
    } else {
      const d = await res.json().catch(() => ({}));
      document.getElementById('users-status').textContent = d.detail || 'Delete failed.';
    }
  }

  // ---- Activity ----
  async function loadActivity() {
    const filterUid = document.getElementById('activity-user-filter').value;
    const res = await fetch('/admin/activity?limit=200');
    if (!res.ok) return;
    const { entries } = await res.json();
    const filtered = filterUid
      ? entries.filter(e => e.username === userMap[filterUid])
      : entries;
    const container = document.getElementById('admin-timeline');
    container.innerHTML = '';
    if (!filtered.length) { container.textContent = 'No activity yet.'; return; }
    tlRenderByDay(container, filtered);
  }

  document.getElementById('activity-user-filter').addEventListener('change', loadActivity);

  // ---- Bot Status ----
  async function loadBots() {
    const res = await fetch('/admin/status');
    if (!res.ok) return;
    const statuses = await res.json();
    const grid = document.getElementById('bots-grid');
    grid.innerHTML = '';
    if (!statuses.length) { grid.textContent = 'No active schedulers.'; return; }
    statuses.forEach(s => {
      const card = document.createElement('div');
      card.className = 'card';
      const noSpond = !s.logged_in && !s.last_tick_ts && !s.last_error;
      const dot = s.last_error ? '🔴' : s.logged_in ? '🟢' : '⚪';
      const statusText = noSpond
        ? `<span class="muted">no Spond credentials</span>`
        : s.last_error
          ? `<span class="err-text">${esc(s.last_error)}</span>`
          : `<span style="color:var(--ok)">ok</span>`;

      const lastTick = s.last_tick_ts
        ? new Date(s.last_tick_ts * 1000).toLocaleTimeString()
        : '—';
      const pollInterval = s.poll_interval ? `${s.poll_interval}s` : '—';

      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.65rem">
          <strong style="font-size:1rem">${dot} ${esc(s.username || s.user_id)}</strong>
          <span class="muted" style="font-size:.78rem">${s.dry_run ? '<span class="tag dry-tag">DRY RUN</span>' : ''}</span>
        </div>
        <div class="bot-stat-grid">
          <span class="muted">Status</span><span>${statusText}</span>
          <span class="muted">Last tick</span><span>${lastTick}</span>
          <span class="muted">Poll interval</span><span>${pollInterval}</span>
          <span class="muted">Events cached</span><span>${s.events_cached}</span>
          <span class="muted">Scheduled</span><span>${s.scheduled_count} pending</span>
          <span class="muted">Accepted</span><span>${s.accepted_count}</span>
          <span class="muted">Failed</span><span>${s.failed_count}</span>
        </div>`;

      const pending = s.pending_events || [];
      if (pending.length) {
        const section = document.createElement('div');
        section.className = 'bot-pending-events';
        const label = document.createElement('div');
        label.className = 'bot-pending-label';
        label.textContent = 'Upcoming auto-accepts';
        section.appendChild(label);
        pending.forEach(ev => {
          const row = document.createElement('div');
          row.className = 'bot-pending-row';
          const fireDate = new Date(ev.fire_ts * 1000);
          const relMs = fireDate - Date.now();
          const relH = (relMs / 3_600_000).toFixed(1);
          const relText = relMs < 0 ? 'overdue' : relH < 24 ? `in ${relH}h` : `in ${(relMs / 86_400_000).toFixed(1)}d`;
          row.innerHTML = `<span>${esc(ev.heading || ev.event_id)}</span><span class="bot-pending-time">${relText} · ${fireDate.toLocaleString(undefined, { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' })}</span>`;
          section.appendChild(row);
        });
        card.appendChild(section);
      }

      grid.appendChild(card);
    });
  }

  // ---- Admin Events ----
  async function loadAdminEvents() {
    const uid = document.getElementById('events-user-select').value;
    const container = document.getElementById('admin-events-list');
    if (!uid) { container.innerHTML = '<p class="muted">Select a user to view their events.</p>'; return; }
    container.innerHTML = '<p class="muted">Loading…</p>';
    const res = await fetch(`/admin/users/${uid}/events`);
    if (!res.ok) { container.innerHTML = `<p class="err-text">Failed to load events.</p>`; return; }
    const { events } = await res.json();
    if (!events || !events.length) { container.innerHTML = '<p class="muted">No events cached for this user.</p>'; return; }
    const tbl = document.createElement('table');
    tbl.className = 'data-table';
    tbl.innerHTML = `<thead><tr><th>Event</th><th>Start</th><th>Status</th><th>Actions</th></tr></thead>`;
    const tbody = document.createElement('tbody');
    events.sort((a, b) => (a.startTimestamp || '').localeCompare(b.startTimestamp || ''));
    for (const ev of events) {
      const tr = document.createElement('tr');
      const start = ev.startTimestamp ? new Date(ev.startTimestamp).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—';
      let statusBadge;
      if (ev.accepted) statusBadge = '<span class="tag" style="background:var(--ok);color:#fff">Accepted</span>';
      else if (ev.waitlisted) statusBadge = '<span class="tag">Waitlisted</span>';
      else if (ev.failed) statusBadge = '<span class="tag err-btn">Failed</span>';
      else if (ev.paymentRequired) statusBadge = '<span class="tag">Payment required</span>';
      else if (ev.armed_ts) {
        const d = new Date(ev.armed_ts * 1000);
        statusBadge = `<span class="tag">Scheduled ${d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}</span>`;
      } else if (ev.selected) statusBadge = '<span class="tag">Selected</span>';
      else statusBadge = '<span class="muted">—</span>';
      tr.innerHTML = `
        <td>${esc(ev.heading || ev.id)}<br/><small class="muted">${esc(ev.groupName || '')}</small></td>
        <td class="muted" style="font-size:.82rem">${start}</td>
        <td>${statusBadge}</td>
        <td>
          <div class="td-actions">
            <button class="ghost small" data-action="accept" data-eid="${esc(ev.id)}" data-uid="${esc(uid)}" ${ev.accepted ? 'disabled' : ''}>Accept</button>
            <button class="ghost small err-btn" data-action="decline" data-eid="${esc(ev.id)}" data-uid="${esc(uid)}">Decline</button>
          </div>
        </td>`;
      tbody.appendChild(tr);
    }
    tbl.appendChild(tbody);
    container.innerHTML = '';
    container.appendChild(tbl);

    container.querySelectorAll('[data-action="accept"]').forEach(btn => btn.addEventListener('click', async () => {
      btn.disabled = true; btn.textContent = '…';
      const r = await fetch(`/admin/users/${btn.dataset.uid}/events/${btn.dataset.eid}/accept`, { method: 'POST' });
      btn.textContent = r.ok ? 'Fired' : 'Error';
    }));
    container.querySelectorAll('[data-action="decline"]').forEach(btn => btn.addEventListener('click', async () => {
      if (!confirm('Decline this event on behalf of the user?')) return;
      btn.disabled = true; btn.textContent = '…';
      const r = await fetch(`/admin/users/${btn.dataset.uid}/events/${btn.dataset.eid}/decline`, { method: 'POST' });
      btn.textContent = r.ok ? 'Fired' : 'Error';
    }));
  }

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
})();
