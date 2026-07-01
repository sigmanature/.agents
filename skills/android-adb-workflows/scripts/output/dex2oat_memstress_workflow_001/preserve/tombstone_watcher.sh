#!/usr/bin/env bash
set -euo pipefail
SERIAL="18281FDF6007HB"
DEVICE_DIR="/data/local/tmp/preserve_18281FDF6007HB_20260701_221747_3677644_136621299"
PID_FILE="/data/local/tmp/preserve_18281FDF6007HB_20260701_221747_3677644_136621299/pid"
OUT="./output/dex2oat_memstress_workflow_001/preserve"
STOP_COMMAND=""
shell_quote() {
  printf "'"
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
  printf "'"
}

poll_until_triggered() {
  while :; do
    sleep 1
    if adb -s "$SERIAL" shell "su -c 'test -f $(shell_quote "${DEVICE_DIR}/tombstone_triggered.txt") && echo triggered || echo waiting'" 2>/dev/null | tr -d '\r' | grep -q triggered; then
      break
    fi
    if ! adb -s "$SERIAL" shell "su -c 'cat $(shell_quote "$PID_FILE") 2>/dev/null | xargs -r kill -0 2>/dev/null && echo alive || echo dead'" 2>/dev/null | tr -d '\r' | grep -q alive; then
      echo "watcher process exited unexpectedly" >&2
      break
    fi
  done
}

pull_artifacts() {
  mkdir -p "$OUT"
  adb -s "$SERIAL" pull "${DEVICE_DIR}/tombstone_triggered.txt" "$OUT/tombstone_triggered.txt" > "$OUT/adb_pull_tombstone_info.stdout.txt" 2> "$OUT/adb_pull_tombstone_info.stderr.txt" || true
  adb -s "$SERIAL" pull "${DEVICE_DIR}/loop.log" "$OUT/loop.log" > "$OUT/adb_pull_loop_log.stdout.txt" 2> "$OUT/adb_pull_loop_log.stderr.txt" || true
  adb -s "$SERIAL" shell "su -c 'find $(shell_quote "$DEVICE_DIR") -maxdepth 1 -type f -name \"*.hardlink\" -print'" 2>/dev/null | tr -d '\r' > "$OUT/hardlink_list.txt"
  while IFS= read -r hpath; do
    [ -n "$hpath" ] || continue
    fname="$(basename "$hpath")"
    adb -s "$SERIAL" pull "$hpath" "$OUT/$fname" > "$OUT/adb_pull_${fname}.stdout.txt" 2> "$OUT/adb_pull_${fname}.stderr.txt" || true
  done < "$OUT/hardlink_list.txt"
}

main() {
  poll_until_triggered
  echo "Tombstone detected. Pulling preserved artifacts..."
  pull_artifacts
  if [ -n "$STOP_COMMAND" ]; then
    echo "Running stop-command: $STOP_COMMAND"
    eval "$STOP_COMMAND" || true
  fi
  echo "Done. Preserved artifacts are in: $OUT"
}

main
