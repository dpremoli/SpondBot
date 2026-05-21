(() => {
  const form = document.getElementById('login-form');
  const btn = document.getElementById('login-btn');
  const err = document.getElementById('login-error');
  const pwInput = document.getElementById('password');
  const pwToggle = document.getElementById('pw-toggle');
  const cfBtn = document.getElementById('cf-btn');
  const cfSwitch = document.getElementById('cf-switch');
  const cfDivider = document.getElementById('cf-divider');

  pwToggle.addEventListener('click', () => {
    const show = pwInput.type === 'password';
    pwInput.type = show ? 'text' : 'password';
    pwToggle.textContent = show ? '🙈' : '👁';
  });

  (async () => {
    // Already have a valid session — skip login entirely
    const meRes = await fetch('/auth/me');
    if (meRes.ok) { location.replace('/'); return; }

    // Check which auth methods are available
    const methodsRes = await fetch('/auth/methods');
    if (!methodsRes.ok) return;
    const methods = await methodsRes.json();

    if (methods.cloudflare) {
      const loggedOut = new URLSearchParams(location.search).has('logged_out');
      if (!loggedOut) {
        // Try silent CF login — server reads the Cf-Access-Jwt-Assertion header
        const cfRes = await fetch('/auth/cf', { method: 'POST' });
        if (cfRes.ok) { location.replace('/'); return; }
      }
      // Always show the CF button — lets the user re-authenticate after logout.
      // The page is itself behind Cloudflare Access, so the browser already has
      // a valid CF_Authorization cookie; clicking the button just re-runs the
      // silent SSO handshake against /auth/cf.
      cfBtn.hidden = false;
      cfDivider.hidden = false;
      // Team-wide CF logout clears the SSO session so the user can pick a
      // different Google account on the next sign-in. The per-app endpoint
      // only drops the app cookie and CF would silently reissue it.
      if (methods.cf_team_domain) {
        const returnTo = encodeURIComponent(location.origin + '/login');
        cfSwitch.href = `https://${methods.cf_team_domain}/cdn-cgi/access/logout?returnTo=${returnTo}`;
        cfSwitch.hidden = false;
      }
    }
  })();

  cfBtn.addEventListener('click', async e => {
    e.preventDefault();
    err.hidden = true;
    try {
      const r = await fetch('/auth/cf', { method: 'POST' });
      if (r.ok) { location.replace('/'); return; }
      err.textContent = 'Cloudflare SSO sign-in failed — try refreshing, or use a username and password below.';
      err.hidden = false;
    } catch {
      err.textContent = 'Network error — please try again.';
      err.hidden = false;
    }
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    err.hidden = true;
    btn.disabled = true;
    btn.textContent = 'Signing in…';
    try {
      const res = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: document.getElementById('username').value,
          password: pwInput.value,
        }),
      });
      if (res.ok) {
        location.replace('/');
        return;
      }
      const data = await res.json().catch(() => ({}));
      err.textContent = res.status === 429
        ? 'Too many attempts — try again in a minute.'
        : (data.detail || 'Invalid username or password.');
      err.hidden = false;
    } catch {
      err.textContent = 'Network error — please try again.';
      err.hidden = false;
    }
    btn.disabled = false;
    btn.textContent = 'Sign in';
  });
})();
