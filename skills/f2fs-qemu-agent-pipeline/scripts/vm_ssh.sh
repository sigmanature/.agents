#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

set -a
[ -f ./.vars.sh ] && . ./.vars.sh
set +a

INSTANCE_NAME=""

if [ "${1:-}" = "--instance" ]; then
  INSTANCE_NAME="${2:-}"
  shift 2
  if [ -z "$INSTANCE_NAME" ]; then
    echo "usage: $0 [--instance <name>] <remote command>" >&2
    exit 2
  fi
  INSTANCE_ENV="./myscripts/vm_instances/${INSTANCE_NAME}/instance.env"
  if [ ! -f "$INSTANCE_ENV" ]; then
    echo "ERROR: instance env not found: $INSTANCE_ENV" >&2
    exit 2
  fi
  # shellcheck disable=SC1090
  set -a
  . "$INSTANCE_ENV"
  set +a
fi

HOST="${VM_SSH_HOST:-127.0.0.1}"
PORT="${VM_SSH_PORT:-5022}"
USER_NAME="${VM_SSH_USER:-root}"
PASSWORD="${VM_SSH_PASSWORD:-1}"

if [ "$#" -eq 0 ]; then
  echo "usage: $0 [--instance <name>] <remote command>" >&2
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
