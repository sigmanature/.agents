#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

pkg update -y
pkg install -y openssh fio python

if command -v termux-setup-storage >/dev/null 2>&1; then
  termux-setup-storage || true
fi

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

mkdir -p ~/.ssh
chmod 700 ~/.ssh

PUB_FILE="$HOME/storage/downloads/termux_fio.pub"
if [ ! -f "$PUB_FILE" ]; then
  PUB_FILE="/sdcard/Download/termux_fio.pub"
fi
if [ ! -f "$PUB_FILE" ]; then
  echo "missing public key: expected ~/storage/downloads/termux_fio.pub or /sdcard/Download/termux_fio.pub" >&2
  exit 1
fi

pub="$(cat "$PUB_FILE")"
touch ~/.ssh/authorized_keys
if ! grep -qxF "$pub" ~/.ssh/authorized_keys; then
  printf '%s\n' "$pub" >> ~/.ssh/authorized_keys
fi
sed -i 's/\r$//' ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

sshd -p 8022 2>/dev/null || true

printf 'USER=%s\n' "$(whoami)"
printf 'HOME=%s\n' "$HOME"
printf 'SSHD=%s\n' "$(pgrep -af sshd || true)"
printf 'AUTHORIZED_KEYS=%s\n' "$(wc -l < ~/.ssh/authorized_keys 2>/dev/null || echo 0)"
printf 'FIO=%s\n' "$(command -v fio || true)"
printf 'PYTHON=%s\n' "$(command -v python || true)"
