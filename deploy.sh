#!/usr/bin/env bash
# Ship the working tree to the NAS and rebuild services.
# Usage: ./deploy.sh [service...]   (default: all services)
# creds.txt: line 1 = ssh user, line 2 = sudo password (stdin only, never echoed).
set -euo pipefail

HOST=192.168.2.191
REMOTE_DIR=/volume1/cinema/Cinema
# DSM does not put docker on the non-interactive ssh PATH, so use the absolute path.
REMOTE_DOCKER=/usr/local/bin/docker

cd "$(dirname "$0")"
[ -f creds.txt ] || { echo "creds.txt not found" >&2; exit 1; }
USER_NAME=$(sed -n 1p creds.txt | tr -d '\r')
TARGET="$USER_NAME@$HOST"

if ! ssh -o BatchMode=yes "$TARGET" true; then
  echo "SSH key auth failed. Run once, then retry:" >&2
  echo "  ssh-copy-id $TARGET" >&2
  exit 1
fi

# tar is run from "." so member names start with "./"; the excludes below are
# anchored to the repo root. __pycache__ and .pytest_cache are excluded at any depth.
tar --anchored \
    --exclude=./.git --exclude=./app/.venv --exclude=./creds.txt --exclude=./.env \
    --exclude=./config --exclude=./.superpowers \
    --no-anchored \
    --exclude=__pycache__ --exclude=.pytest_cache \
    -czf - . \
  | ssh "$TARGET" "mkdir -p '$REMOTE_DIR' && tar -xzf - -C '$REMOTE_DIR'"

# Quote each service name for the remote shell (never splice raw "$*").
SERVICE_ARGS=$(printf '%q ' "$@")

sed -n 2p creds.txt | tr -d '\r' \
  | ssh "$TARGET" "cd '$REMOTE_DIR' && sudo -S -p '' $REMOTE_DOCKER compose up -d --build --no-deps $SERVICE_ARGS"
