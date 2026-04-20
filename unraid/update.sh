#!/usr/bin/env bash
# Unraid helper: pulls the latest SpondBot source and rebuilds the container.
# Run manually, or schedule via the "User Scripts" plugin.
#
# Expected layout on Unraid:
#   /mnt/user/appdata/spondbot/        <- clone of this repo
#   /mnt/user/appdata/spondbot/data/   <- persistent config (auto-created)

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
docker compose up -d --build
