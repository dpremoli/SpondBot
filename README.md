# SpondBot

Web UI that auto-accepts [Spond](https://spond.com/) event invites the moment
they open. Built on the unofficial [Spond Python library](https://github.com/Olen/Spond)
(bundled in `spond/`).

## What it does

- You enter your Spond username, password, and the group IDs you care about.
- It lists every event in those groups with the time the invite becomes
  available (`inviteTime`).
- Tick the events you want auto-accepted.
- `0.3s` after each selected event opens, SpondBot fires an accept. If the
  call fails (event not yet live, transient error, etc.) it retries up to
  **10 more times, one every 0.3s**.

Config is persisted to `data/config.json` so the bot survives restarts.

## Run locally

```bash
pip install -r requirements.txt
uvicorn webui.app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000.

## Run with Docker Compose

```bash
docker compose up -d --build
```

The `./data` directory is bind-mounted into the container so your config
survives rebuilds.

## Run on Unraid (pulling from git)

The `unraid/update.sh` script clones this repo (or updates it if already
cloned) and `docker compose up -d --build`s it. Use it once to bootstrap, and
schedule it with the **User Scripts** plugin to keep in sync with `main`.

1. Install the **User Scripts** plugin from Community Applications.
2. Add a new script called `spondbot-update` and paste the contents of
   [unraid/update.sh](unraid/update.sh) (or download and reference it).
3. Edit the variables at the top if your paths differ:
   - `REPO_DIR` — where to clone (default `/mnt/user/appdata/spondbot`)
   - `REPO_URL` — your fork of this repo
   - `BRANCH` — default `main`
4. Run it once manually (`Run in background`). It will:
   - clone the repo into `/mnt/user/appdata/spondbot`
   - create `data/` for persistent config
   - build and start the `spondbot` container on port `8000`
5. Open `http://<unraid-ip>:8000` and configure credentials + group IDs.
6. Schedule the script (e.g. daily at 04:00) so the container auto-updates
   whenever you push to `main`.

Requirements on the Unraid host: `git` and the Docker Compose plugin (both
shipped on modern Unraid). The container itself needs no extra setup.

### Finding your group IDs

Log into Spond in a browser, open a group, and copy the hex ID from the URL
(`…/group/<GROUP_ID>/…`). Paste one per line into the UI. Leave the field
blank to query every group you belong to.

## Layout

```
spond/      # bundled Spond API client
webui/      # FastAPI app + static frontend
unraid/     # Unraid update/bootstrap script
Dockerfile
docker-compose.yml
```

## License

GPL-3.0 — inherits from the upstream Spond library.
