#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PRESERVE_SCRIPT="$SCRIPT_DIR/adb_preserve_mutable_suspect_file.sh"
MEMSTRESS_SCRIPT="$SCRIPT_DIR/../../android-thp-fallback-sampler/scripts/run_memstress_and_collect_logs.py"

usage() {
  cat <<'EOF'
run_dex2oat_memstress_preserve_workflow.sh

Run a combined stress workflow:
  - memstress: rapidly launch/hold apps in the foreground
  - device-side dex2oat loop: continuously delete-dexopt + compile in the background
  - device-side preserve watcher: hardlink odex/vdex and watch for tombstones
  - stop everything when a tombstone is detected and pull preserved artifacts

Usage:
  run_dex2oat_memstress_preserve_workflow.sh \
    --serial <SERIAL> \
    --packages-file <FILE> \
    --targets-file <FILE> \
    --out-dir <DIR> \
    [options]

Options:
  -s, --serial <SERIAL>       adb device serial
  --packages-file <FILE>     one package name per line for memstress
  --targets-file <FILE>     one odex/vdex path per line for preserve watcher
  -o, --out-dir <DIR>       output directory
  --max-cycles <N>          memstress max cycles (default: 100)
  --interval-s <SEC>        memstress sampling interval (default: 10)
  --hold-ms <MS>            memstress hold time per launch (default: 200)
  --dex-interval-sec <SEC>  dex2oat loop switch interval (default: 0.015)
  --no-stop-on-tombstone    do not stop on tombstone; run until memstress finishes
  -h, --help                show this help
EOF
}

SERIAL=""
PACKAGES_FILE=""
TARGETS_FILE=""
OUT_DIR=""
MAX_CYCLES=100
INTERVAL_S=10
HOLD_MS=200
DEX_INTERVAL="0.015"
STOP_ON_TOMBSTONE=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--serial) SERIAL="${2:?missing serial}"; shift 2 ;;
    --packages-file) PACKAGES_FILE="${2:?missing packages file}"; shift 2 ;;
    --targets-file) TARGETS_FILE="${2:?missing targets file}"; shift 2 ;;
    -o|--out-dir) OUT_DIR="${2:?missing out dir}"; shift 2 ;;
    --max-cycles) MAX_CYCLES="${2:?missing max cycles}"; shift 2 ;;
    --interval-s) INTERVAL_S="${2:?missing interval}"; shift 2 ;;
    --hold-ms) HOLD_MS="${2:?missing hold ms}"; shift 2 ;;
    --dex-interval-sec) DEX_INTERVAL="${2:?missing dex interval}"; shift 2 ;;
    --no-stop-on-tombstone) STOP_ON_TOMBSTONE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$SERIAL" ] || [ -z "$PACKAGES_FILE" ] || [ -z "$TARGETS_FILE" ] || [ -z "$OUT_DIR" ]; then
  usage >&2
  exit 2
fi

if [ ! -f "$PRESERVE_SCRIPT" ]; then
  echo "Error: preserve script not found: $PRESERVE_SCRIPT" >&2
  exit 2
fi
if [ ! -f "$MEMSTRESS_SCRIPT" ]; then
  echo "Error: memstress script not found: $MEMSTRESS_SCRIPT" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"

adb -s "$SERIAL" wait-for-device

# 1. Start preserve + tombstone watcher in background.
PRESERVE_OUT="$OUT_DIR/preserve"
"$PRESERVE_SCRIPT" \
  --serial "$SERIAL" \
  --device-loop \
  --tombstone-triggered-stop \
  --background \
  --interval-sec 2.0 \
  --targets-file "$TARGETS_FILE" \
  --out "$PRESERVE_OUT" > "$OUT_DIR/preserve_start.log" 2>&1

device_dir=""
if [ -f "$PRESERVE_OUT/meta.txt" ]; then
  device_dir="$(grep '^device_dir=' "$PRESERVE_OUT/meta.txt" | cut -d= -f2-)"
fi
if [ -z "$device_dir" ]; then
  echo "Error: failed to start preserve watcher. See $OUT_DIR/preserve_start.log" >&2
  exit 3
fi

echo "[workflow] preserve watcher started: $device_dir"

# 2. Start device-side dex2oat loop.
DEX_LOOP_SCRIPT="$OUT_DIR/dex2oat_loop.sh"
DEX_LOOP_DEVICE="/data/local/tmp/dex2oat_loop_$$.sh"
DEX_PID_FILE="/data/local/tmp/dex2oat_40apps_loop.pid"
DEX_LOG="/data/local/tmp/dex2oat_40apps_loop.log"

cat > "$DEX_LOOP_SCRIPT" <<EOF
#!/system/bin/sh
set -u

PKGS="$(tr '\n' ' ' < "$PACKAGES_FILE")"
LOG="$DEX_LOG"
PID_FILE="$DEX_PID_FILE"
INTERVAL="$DEX_INTERVAL"

echo "\$\$" > "\$PID_FILE"
: > "\$LOG"
echo "\$(date +'%F %T') dex2oat loop started pid=\$\$ interval=\$INTERVAL" >> "\$LOG"

cycle=0
while :; do
  cycle=\$((cycle + 1))
  for pkg in \$PKGS; do
    echo "\$(date +'%F %T') cycle=\$cycle pkg=\$pkg action=delete-dexopt" >> "\$LOG"
    pm delete-dexopt "\$pkg" >/dev/null 2>&1 || true
    echo "\$(date +'%F %T') cycle=\$cycle pkg=\$pkg action=compile" >> "\$LOG"
    pm compile --full -r cmdline -f -m speed-profile "\$pkg" >/dev/null 2>&1 || true
    sleep "\$INTERVAL"
  done
done
EOF

adb -s "$SERIAL" push "$DEX_LOOP_SCRIPT" "$DEX_LOOP_DEVICE" > "$OUT_DIR/adb_push_dex2oat_loop.log" 2>&1
adb -s "$SERIAL" shell "nohup sh $DEX_LOOP_DEVICE > /dev/null 2>&1 &" > "$OUT_DIR/dex2oat_start.log" 2>&1
sleep 0.5
if ! adb -s "$SERIAL" shell "cat $DEX_PID_FILE | xargs -r kill -0 2>/dev/null && echo alive || echo dead" | tr -d '\r' | grep -q alive; then
  echo "Error: dex2oat loop failed to start. See $OUT_DIR/dex2oat_start.log" >&2
  exit 3
fi

echo "[workflow] dex2oat loop started"

# 3. Start memstress in background.
MEMSTRESS_OUT="$OUT_DIR/memstress"
mkdir -p "$MEMSTRESS_OUT"
nohup python3 "$MEMSTRESS_SCRIPT" \
  --serial "$SERIAL" \
  --max-cycles "$MAX_CYCLES" \
  --interval-s "$INTERVAL_S" \
  --out-dir "$MEMSTRESS_OUT" \
  --package-file "$PACKAGES_FILE" \
  --hold-ms "$HOLD_MS" \
  --no-network-check \
  --no-device-prepare > "$OUT_DIR/memstress.log" 2>&1 &
memstress_pid=$!
echo "$memstress_pid" > "$OUT_DIR/memstress.pid"

echo "[workflow] memstress started pid=$memstress_pid"

# 4. Wait for tombstone or memstress finish.
stop_all() {
  echo "[workflow] stopping all components..."
  adb -s "$SERIAL" shell "cat $DEX_PID_FILE 2>/dev/null | xargs -r kill 2>/dev/null || true" >/dev/null 2>&1 || true
  adb -s "$SERIAL" shell "cat $device_dir/pid 2>/dev/null | xargs -r kill 2>/dev/null || true" >/dev/null 2>&1 || true
  if [ -n "${memstress_pid:-}" ] && kill -0 "$memstress_pid" >/dev/null 2>&1; then
    kill "$memstress_pid" >/dev/null 2>&1 || true
    wait "$memstress_pid" >/dev/null 2>&1 || true
  fi
  # Also kill the host-side tombstone watcher if it is still running.
  if [ -f "$PRESERVE_OUT/tombstone_watcher.pid" ]; then
    local tw_pid
    tw_pid="$(cat "$PRESERVE_OUT/tombstone_watcher.pid")"
    kill "$tw_pid" >/dev/null 2>&1 || true
  fi
}

if [ "$STOP_ON_TOMBSTONE" -eq 1 ]; then
  echo "[workflow] waiting for tombstone trigger..."
  while :; do
    if adb -s "$SERIAL" shell "test -f $device_dir/tombstone_triggered.txt && echo triggered || echo waiting" 2>/dev/null | tr -d '\r' | grep -q triggered; then
      echo "[workflow] tombstone detected."
      break
    fi
    if ! kill -0 "$memstress_pid" >/dev/null 2>&1; then
      echo "[workflow] memstress finished without tombstone."
      break
    fi
    sleep 2
  done
  stop_all
  echo "[workflow] done. Artifacts: $PRESERVE_OUT"
else
  echo "[workflow] running in background mode. Memstress pid=$memstress_pid. Press Ctrl-C to stop."
  wait "$memstress_pid" || true
  stop_all
  echo "[workflow] done. Artifacts: $PRESERVE_OUT"
fi
