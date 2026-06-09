#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

if ! command -v su >/dev/null 2>&1; then
  echo "NO_SU_BINARY"
  exit 1
fi

su -c 'id'
su -c 'PATH=/data/data/com.termux/files/usr/bin:$PATH; echo ROOT_PATH_OK; command -v sh; true'
