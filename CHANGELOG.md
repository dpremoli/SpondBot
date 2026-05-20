# Changelog

## [1.1.0] — 2026-05-20

### Added
- **Sniper timing range** — `initial_delay_max` lets you set a random fire
  window (min–max) so the bot doesn't always respond at the exact same offset.
  Both the global Bot Defaults and per-event/bulk override forms expose the
  new field.
- **Bulk overrides** — admins can apply custom settings to an entire event
  series (matched by heading) or a whole Spond group for any user.  Priority
  sits between global defaults and per-event overrides.  Managed under
  **Settings → Bulk overrides** with tap-to-expand cards and a duplicate guard
  that warns when adding a second rule for the same target.
- **Admin events tab** — new **Users → Events** panel lets admins view any
  user's cached events and manually trigger accept or decline.
- **Auto token refresh** — when Spond returns a `tokenExpired` 401 error the
  bot re-authenticates in-place and retries the failed request, eliminating the
  need for a manual restart after a long idle period.

### Fixed
- Spond group dropdown in the add-override form was always empty; now uses
  `recipients.group.id` as the canonical key, matching what `settings_for()`
  resolves against.
- Cancel button in the add-override form had a spurious `hidden = false` line
  that caused a visible flicker.

---

## [1.0.0] — 2026-04-01

Initial public release.

- Multi-user FastAPI web app with per-user bot schedulers.
- JWT auth with HttpOnly cookies; Fernet-encrypted Spond credentials at rest.
- Per-event overrides, dry-run mode, grouped event list + calendar view.
- Admin dashboard: user management, activity timeline, bot status panel.
- Docker Compose + Unraid bootstrap script.
