(() => {
  const form = document.getElementById('login-form');
  const btn = document.getElementById('login-btn');
  const err = document.getElementById('login-error');
  const pwInput = document.getElementById('password');
  const pwToggle = document.getElementById('pw-toggle');
  const cfBtn = document.getElementById('cf-btn');
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

    const loggedOut = new URLSearchParams(location.search).has('logged_out');
    if (methods.cloudflare && !loggedOut) {
      // Try silent CF login — server reads the Cf-Access-Jwt-Assertion header
      const cfRes = await fetch('/auth/cf', { method: 'POST' });
      if (cfRes.ok) { location.replace('/'); return; }

      // CF is configured but we aren't authenticated through it yet —
      // show the CF button so the user can initiate the Cloudflare Access flow
      cfBtn.href = `https://${methods.cf_team_domain}/cdn-cgi/access/login/${location.hostname}`;
      cfBtn.hidden = false;
      cfDivider.hidden = false;
    }
  })();

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
