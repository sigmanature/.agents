#!/usr/bin/env bash
set -euo pipefail
RUN_DIR=""
SERIAL=""
TAG="evidence"
OUT_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    --serial) SERIAL="$2"; shift 2;;
    --tag) TAG="$2"; shift 2;;
    --out-dir) OUT_DIR="$2"; shift 2;;
    -h|--help) echo "Usage: cf_collect_evidence.sh --run-dir RUN_DIR [--out-dir DIR] [--tag TAG]"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$RUN_DIR" && -x "$RUN_DIR/bin/adb" ]] || { echo "invalid --run-dir" >&2; exit 1; }
ADB=("$RUN_DIR/bin/adb")
[[ -n "$SERIAL" ]] && ADB+=( -s "$SERIAL" )
TS=$(date +%Y%m%d_%H%M%S)
OUT_DIR=${OUT_DIR:-"$RUN_DIR/evidence_${TAG}_${TS}"}
mkdir -p "$OUT_DIR"
shadb(){ "${ADB[@]}" shell "$@"; }

"${ADB[@]}" wait-for-device
shadb getprop > "$OUT_DIR/getprop.txt" || true
shadb 'id' > "$OUT_DIR/adb_id.txt" || true
shadb 'cat /proc/self/status | grep CapEff || true' > "$OUT_DIR/capeff.txt" || true
shadb 'mount | grep " /data " || true' > "$OUT_DIR/mount_data.txt" || true
shadb 'ls -ld /sys/kernel/tracing /sys/kernel/debug/tracing 2>/dev/null || true' > "$OUT_DIR/tracefs_paths.txt" || true
shadb 'cat /sys/kernel/tracing/available_events 2>/dev/null || true' > "$OUT_DIR/available_events.txt" || true
shadb 'find /sys/kernel/tracing/events -maxdepth 2 -name enable 2>/dev/null | sed "s#/sys/kernel/tracing/events/##;s#/enable##" | sort | head -n 5000 || true' > "$OUT_DIR/events_list.txt" || true
shadb 'dmesg -T 2>/dev/null || dmesg 2>/dev/null || true' > "$OUT_DIR/dmesg.txt" || true
"${ADB[@]}" logcat -b all -v threadtime -d > "$OUT_DIR/logcat_all_threadtime.txt" 2> "$OUT_DIR/logcat_stderr.txt" || true

echo "[evidence] $OUT_DIR"
