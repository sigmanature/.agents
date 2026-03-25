#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

set -a
[ -f ./.vars.sh ] && . ./.vars.sh
set +a

HOST="${VM_SSH_HOST:-127.0.0.1}"
PORT="${VM_SSH_PORT:-5022}"
USER_NAME="${VM_SSH_USER:-root}"
PASSWORD="${VM_SSH_PASSWORD:-1}"

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <remote command>" >&2
  exit 2
fi

command -v sshpass >/dev/null

sshpass -p "$PASSWORD" ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=5 \
  -p "$PORT" \
  "$USER_NAME@$HOST" \
  "$@"
