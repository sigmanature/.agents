#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
adb_oat_tmp_watcher.sh: poll the current package artifact directory on-device and stream sightings.

Usage:
  adb_oat_tmp_watcher.sh --serial <SERIAL> --package <pkg.name> --output <file> [options]
  adb_oat_tmp_watcher.sh --serial <SERIAL> --package-dir </data/app/.../pkg-...> --output <file> [options]

Options:
  --serial <SERIAL>          adb device serial
  --package <pkg.name>       package name; resolves current /data/app path on every poll
  --package-dir <path>       explicit package install directory if you already know it
  --output <file>            host output file
  --interval-sec <sec>       poll interval in seconds (default: 0.05)
  --include-final            also log *.odex / *.vdex / *.oat besides *.tmp / *.backup
  -h, --help                 show help
EOF
}

SERIAL=""
PACKAGE=""
PACKAGE_DIR=""
OUTPUT=""
INTERVAL_SEC="0.05"
INCLUDE_FINAL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="${2:-}"; shift 2 ;;
    --package) PACKAGE="${2:-}"; shift 2 ;;
    --package-dir) PACKAGE_DIR="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --interval-sec) INTERVAL_SEC="${2:-}"; shift 2 ;;
    --include-final) INCLUDE_FINAL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$SERIAL" || -z "$OUTPUT" ]]; then
  usage >&2
  exit 2
fi

if [[ -z "$PACKAGE" && -z "$PACKAGE_DIR" ]]; then
  echo "Error: one of --package or --package-dir is required." >&2
  exit 2
fi

mkdir -p "$(dirname -- "$OUTPUT")"

if [[ "$INCLUDE_FINAL" -eq 1 ]]; then
  DEVICE_BODY=$(cat <<EOF
LAST_DIR=""
while true; do
  U=\$(cut -d' ' -f1 /proc/uptime 2>/dev/null)
  NOW=\$(date +%Y-%m-%dT%H:%M:%S 2>/dev/null)
  PKG_DIR='$PACKAGE_DIR'
  if [ -n '$PACKAGE' ]; then
    PKG_PATH=\$(pm path '$PACKAGE' 2>/dev/null | sed -n 's/^package://p' | head -n1)
    PKG_DIR=\${PKG_PATH%/base.apk}
  fi
  [ -n "\$PKG_DIR" ] || { sleep '$INTERVAL_SEC'; continue; }
  if [ "\$PKG_DIR" != "\$LAST_DIR" ]; then
    printf '%s uptime=%s PACKAGE_DIR=%s\n' "\$NOW" "\$U" "\$PKG_DIR"
    LAST_DIR="\$PKG_DIR"
  fi
  find "\$PKG_DIR" -maxdepth 4 -type f \\( -name '*.tmp' -o -name '*.backup' -o -name '*.odex' -o -name '*.vdex' -o -name '*.oat' \\) 2>/dev/null | sort | while read -r P; do
    printf '%s uptime=%s %s\n' "\$NOW" "\$U" "\$P"
  done
  sleep '$INTERVAL_SEC'
done
EOF
)
else
  DEVICE_BODY=$(cat <<EOF
LAST_DIR=""
while true; do
  U=\$(cut -d' ' -f1 /proc/uptime 2>/dev/null)
  NOW=\$(date +%Y-%m-%dT%H:%M:%S 2>/dev/null)
  PKG_DIR='$PACKAGE_DIR'
  if [ -n '$PACKAGE' ]; then
    PKG_PATH=\$(pm path '$PACKAGE' 2>/dev/null | sed -n 's/^package://p' | head -n1)
    PKG_DIR=\${PKG_PATH%/base.apk}
  fi
  [ -n "\$PKG_DIR" ] || { sleep '$INTERVAL_SEC'; continue; }
  if [ "\$PKG_DIR" != "\$LAST_DIR" ]; then
    printf '%s uptime=%s PACKAGE_DIR=%s\n' "\$NOW" "\$U" "\$PKG_DIR"
    LAST_DIR="\$PKG_DIR"
  fi
  find "\$PKG_DIR" -maxdepth 4 -type f \\( -name '*.tmp' -o -name '*.backup' \\) 2>/dev/null | sort | while read -r P; do
    printf '%s uptime=%s %s\n' "\$NOW" "\$U" "\$P"
  done
  sleep '$INTERVAL_SEC'
done
EOF
)
fi

exec adb -s "$SERIAL" exec-out su -c "sh -c $(printf '%q' "$DEVICE_BODY")" > "$OUTPUT"
