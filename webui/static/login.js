(() => {
  // Redirect if already authenticated
  fetch('/auth/me').then(r => { if (r.ok) location.replace('/'); });

  const form = document.getElementById('login-form');
  const btn = document.getElementById('login-btn');
  const err = document.getElementById('login-error');
  const pwInput = document.getElementById('password');
  const pwToggle = document.getElementById('pw-toggle');

  pwToggle.addEventListener('click', () => {
    const show = pwInput.type === 'password';
    pwInput.type = show ? 'text' : 'password';
    pwToggle.textContent = show ? '🙈' : '👁';
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
