# SpondBot

A self-hosted web app that auto-accepts [Spond](https://spond.com/) event
invites the moment they open. Built on the unofficial
[Spond Python library](https://github.com/Olen/Spond) (bundled in `spond/`).

## Features

- **Instant accept** — fires the RSVP within a configurable delay (default
  0.3 s) from when the invite window opens, with automatic retries.
- **Multi-user** — each account gets its own bot scheduler; an admin can
  manage all users and monitor every bot from the dashboard.
- **Per-event overrides** — set a different delay, retry count, response type,
  or dry-run flag for individual events.
- **Calendar view** — switch between a grouped list and a month calendar.
- **Activity logs** — full history of every accept attempt, filterable by
  event name and outcome.
- **Light / dark theme** — auto-detected from the OS preference, overridable
  per session.
- **Dry-run mode** — log what _would_ happen without actually sending any
  response to Spond.
- **Tight scheduling** — polling rate steps up from every 15 minutes to every
  2 minutes when a scheduled accept is within 5 minutes of firing.

## Security

- Session tokens are JWT, stored in `HttpOnly` / `Secure` cookies.
- Spond passwords are encrypted at rest with a per-instance [Fernet](https://cryptography.io/en/latest/fernet/) key.
- Rate limiting on auth endpoints.
- Only authenticated users can reach any API endpoint.

## Run locally

```bash
pip install -r requirements.txt
uvicorn webui.app:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000> and log in with the default admin credentials
printed to stdout on first launch.

## Run with Docker Compose

```bash
docker compose up -d --build
```

The `./data` directory is bind-mounted into the container so your config
survives rebuilds.  The app is exposed on port **8888** by default; change
the `ports` entry in `docker-compose.yml` if needed.

## Run on Unraid

The `unraid/update.sh` script clones this repo (or updates it) and runs
`docker compose up -d --build`. Use it to bootstrap the service and schedule
it with the **User Scripts** plugin to auto-update.

1. Install the **User Scripts** plugin from Community Applications.
2. Add a new script called `spondbot-update` and paste the contents of
   [`unraid/update.sh`](unraid/update.sh).
3. Edit the variables at the top if your paths differ:
   - `REPO_DIR` — where to clone (default `/mnt/user/appdata/spondbot`)
   - `REPO_URL` — URL of this repo
   - `BRANCH` — default `main`
4. Run it once manually (`Run in background`).
5. Open `http://<unraid-ip>:8888` and log in.
6. Schedule the script (e.g. daily at 04:00) to keep the container updated.

Requirements on the Unraid host: `git` and the Docker Compose plugin (both
included in modern Unraid). No extra setup inside the container.

## Finding your Spond group IDs

Log into Spond in a browser, open a group, and copy the hex ID from the URL
(`…/group/<GROUP_ID>/…`). Paste one ID per line in **Settings → Spond
Account → Group IDs**. Leave the field blank to query every group you belong
to.

## Configuration

All configuration is stored in `data/config.json` (created automatically).
This file contains encrypted Spond credentials and bot settings — keep it
private and never commit it.

| Setting | Default | Description |
|---------|---------|-------------|
| Initial delay | 0.3 s | Time to wait after an invite opens before accepting |
| Retry count | 10 | Extra attempts if the first accept fails |
| Retry interval | 0.3 s | Gap between retries |
| Response | accepted | `accepted`, `declined`, or `unconfirmed` |
| Dry-run | off | Log actions without sending anything to Spond |

## Project layout

```
spond/          bundled Spond API client (upstream: github.com/Olen/Spond)
webui/          FastAPI app + Jinja2-free static frontend
  app.py        main application, scheduler, API routes
  auth.py       JWT auth, user management
  static/       HTML, CSS, JS (no build step)
tests/          pytest suite
unraid/         Unraid bootstrap / update script
Dockerfile
docker-compose.yml
```

## Tests

```bash
pip install pytest
pytest tests/
```

## Disclaimer

This project was built with the assistance of an AI coding tool. While care has
been taken to follow security best practices, there may be unknown
vulnerabilities in the code that have not been identified or audited.

**Do not use a password you reuse elsewhere for your Spond account in this
app.** Use a unique, throwaway password for the Spond account you connect to
SpondBot. This app is intended for personal, low-stakes automation — treat it
accordingly.

## License

GPL-3.0 — inherits from the upstream Spond library.
