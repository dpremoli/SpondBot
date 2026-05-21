#!/usr/bin/env bash
# Unraid helper: pulls the latest SpondBot source and rebuilds the container.
# Run manually, or schedule via the "User Scripts" plugin.
#
# Expected layout on Unraid:
#   /mnt/user/appdata/spondbot/        <- clone of this repo
#   /mnt/user/appdata/spondbot/data/   <- persistent config (auto-created)
#   /mnt/user/appdata/spondbot/.env    <- your secrets (never committed)
#
# Cloudflare Zero Trust SSO (optional):
#   Set these in the environment or export them before running this script,
#   and they will be written into .env automatically on first run.
#
#   CF_TEAM_DOMAIN=yourteam.cloudflareaccess.com
#   CF_AUD=your-aud-tag
#   CF_ADMIN_EMAILS=you@example.com

set -euo pipefail

REPO_DIR="${REPO_DIR:-/mnt/user/appdata/spondbot}"
REPO_URL="${REPO_URL:-https://github.com/dpremoli/SpondBot.git}"
BRANCH="${BRANCH:-main}"

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "Cloning $REPO_URL -> $REPO_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
else
  echo "Updating $REPO_DIR"
  git -C "$REPO_DIR" fetch --all --prune
  git -C "$REPO_DIR" reset --hard "origin/$BRANCH"
fi

cd "$REPO_DIR"
mkdir -p data

# Always write .env from environment variables so CF settings stay in sync.
ENV_FILE="$REPO_DIR/.env"
echo "Writing $ENV_FILE from environment variables..."
cat > "$ENV_FILE" << EOF
# SpondBot environment — written by update.sh on every run.
# This file is gitignored and will never be committed.

# Cloudflare Zero Trust SSO (leave blank to disable)
CF_TEAM_DOMAIN=${CF_TEAM_DOMAIN:-}
CF_AUD=${CF_AUD:-}
CF_ADMIN_EMAILS=${CF_ADMIN_EMAILS:-}
EOF
echo ".env written."

docker compose up -d --build
