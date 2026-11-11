#!/usr/bin/env bash
# Ship the working tree to the NAS and rebuild services.
# Usage: ./deploy.sh [service...]   (default: all services)
# creds.txt: line 1 = ssh user, line 2 = sudo password (stdin only, never echoed).
set -euo pipefail

HOST=192.168.2.191
REMOTE_DIR=/volume1/cinema/Cinema

cd "$(dirname "$0")"
[ -f creds.txt ] || { echo "creds.txt not found" >&2; exit 1; }
USER_NAME=$(sed -n 1p creds.txt)
TARGET="$USER_NAME@$HOST"

if ! ssh -o BatchMode=yes "$TARGET" true 2>/dev/null; then
  echo "SSH key auth failed. Run once, then retry:" >&2
  echo "  ssh-copy-id $TARGET" >&2
  exit 1
fi

tar --exclude=.git --exclude=app/.venv --exclude=creds.txt --exclude=.env \
    --exclude=config --exclude=__pycache__ -czf - . \
  | ssh "$TARGET" "mkdir -p '$REMOTE_DIR' && tar -xzf - -C '$REMOTE_DIR'"

sed -n 2p creds.txt \
  | ssh "$TARGET" "cd '$REMOTE_DIR' && sudo -S -p '' docker compose up -d --build --no-deps $*"
