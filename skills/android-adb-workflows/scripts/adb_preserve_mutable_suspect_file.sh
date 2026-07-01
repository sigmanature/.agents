#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
adb_preserve_mutable_suspect_file.sh

Lightweight device-side preservation of mutable Android /data suspect files
(typically .odex / .vdex). Creates hardlinks on-device so that PackageManager /
dexopt background cleanup cannot release the inode, keeping bad samples around
for post-mortem analysis.

Designed to run alongside force-dexopt loops (e.g. adb_dexopt_regen_loop.sh):
the background service can still delete/replace the original path, but every
historical inode that existed while this watcher was running remains pinned.

Usage (start device-side watcher before stress test):
  adb_preserve_mutable_suspect_file.sh --serial <SERIAL> \
    --file <PATH> [--file <PATH> ...] --out <HOST_DIR>

Usage (test hardlink speed / unlink safety):
  adb_preserve_mutable_suspect_file.sh --serial <SERIAL> --test \
    --file <PATH> [--file <PATH> ...] --out <HOST_DIR>

Usage (stop watcher and remove all hardlinks):
  adb_preserve_mutable_suspect_file.sh --serial <SERIAL> \
    --stop-and-cleanup --device-dir <PATH> --out <HOST_DIR>

Options:
  -s, --serial <SERIAL>     adb device serial
  -f, --file <PATH>         file to preserve (repeatable)
  --targets-file <FILE>     read target paths from file, one per line
  -o, --out <HOST_DIR>      host output directory
  -n, --keep-count <N>      keep only the latest N hardlink versions per path (default: 2)
  -i, --interval-sec <SEC>  polling interval on device (default: 1.0)
  --tombstone-triggered-stop
                            stop watcher when a new tombstone is detected and
                            pull preserved artifacts automatically
  --background              run tombstone-triggered-stop in background (host returns immediately)
  --stop-command <CMD>      command to run on host when tombstone is detected
  --test                    create 100 hardlinks per target, time it, then unlink all
  --stop-and-cleanup        stop the watcher and remove all *.hardlink in its keep dir
  --stop                    stop the watcher but leave hardlinks intact
  --device-dir <PATH>       explicit device watcher directory to stop/cleanup
  -h, --help                show this help

Outputs:
  <HOST_DIR>/meta.txt       device dir, pid, stop/cleanup command, and tombstone trigger info
  <HOST_DIR>/targets.txt    list of watched paths
  <HOST_DIR>/device_loop.sh copy of the script pushed to device
  <HOST_DIR>/tombstone_triggered.txt (when tombstone-triggered-stop fires)
EOF
}

SERIAL=""
FILES=()
TARGETS_FILE=""
OUT=""
KEEP_COUNT=2
INTERVAL_SEC="1.0"
STOP=0
STOP_AND_CLEANUP=0
TEST=0
TOMBSTONE_TRIGGERED_STOP=0
BACKGROUND=0
STOP_COMMAND=""
DEVICE_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--serial) SERIAL="${2:?missing serial}"; shift 2 ;;
    -f|--file) FILES+=("${2:?missing device path}"); shift 2 ;;
    --targets-file) TARGETS_FILE="${2:?missing targets file}"; shift 2 ;;
    -o|--out) OUT="${2:?missing host output dir}"; shift 2 ;;
    -n|--keep-count) KEEP_COUNT="${2:?missing keep count}"; shift 2 ;;
    -i|--interval-sec) INTERVAL_SEC="${2:?missing interval}"; shift 2 ;;
    --device-loop) shift ;;
    --tombstone-triggered-stop) TOMBSTONE_TRIGGERED_STOP=1; shift ;;
    --background) BACKGROUND=1; shift ;;
    --stop-command) STOP_COMMAND="${2:?missing stop command}"; shift 2 ;;
    --test) TEST=1; shift ;;
    --stop) STOP=1; shift ;;
    --stop-and-cleanup) STOP_AND_CLEANUP=1; shift ;;
    --device-dir) DEVICE_DIR="${2:?missing device dir}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -n "$TARGETS_FILE" ]; then
  if [ ! -f "$TARGETS_FILE" ]; then
    echo "Error: targets file not found: $TARGETS_FILE" >&2
    exit 2
  fi
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    FILES+=("$line")
  done < "$TARGETS_FILE"
fi

if [ "$STOP" -eq 1 ] || [ "$STOP_AND_CLEANUP" -eq 1 ]; then
  if [ -z "$DEVICE_DIR" ]; then
    echo "Error: --stop/--stop-and-cleanup requires --device-dir <path>" >&2
    exit 2
  fi
  if [ "$STOP_AND_CLEANUP" -eq 1 ]; then
    adb -s "$SERIAL" shell "su -c 'cat $(printf '%q' "${DEVICE_DIR}/pid") 2>/dev/null | xargs -r kill 2>/dev/null; rm -f $(printf '%q' "${DEVICE_DIR}")/*.hardlink; echo stopped_and_cleaned'"
  else
    adb -s "$SERIAL" shell "su -c 'cat $(printf '%q' "${DEVICE_DIR}/pid") 2>/dev/null | xargs -r kill 2>/dev/null; echo stopped'"
  fi
  exit 0
fi

if [ -z "$SERIAL" ] || [ "${#FILES[@]}" -eq 0 ] || [ -z "$OUT" ]; then
  usage >&2
  exit 2
fi

if ! [[ "$KEEP_COUNT" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: --keep-count must be a positive integer" >&2
  exit 2
fi

mkdir -p "$OUT"
adb -s "$SERIAL" wait-for-device

shell_quote() {
  printf "'"
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
  printf "'"
}

safe_serial="$(printf '%s' "$SERIAL" | tr -c 'A-Za-z0-9_.-' '_')"
unique_id="$(printf '%s_%s_%s' "$(date +%Y%m%d_%H%M%S)" "$$" "$(date +%N 2>/dev/null || printf 0)")"
device_dir="/data/local/tmp/preserve_${safe_serial}_${unique_id}"
device_loop_script="${device_dir}/loop.sh"
device_targets="${device_dir}/targets.txt"
device_pid_file="${device_dir}/pid"
device_log="${device_dir}/loop.log"

# Write and push target list.
{
  for f in "${FILES[@]}"; do
    printf '%s\n' "$f"
  done
} > "$OUT/targets.txt"
adb -s "$SERIAL" push "$OUT/targets.txt" "$device_targets" > "$OUT/adb_push_targets.stdout.txt" 2> "$OUT/adb_push_targets.stderr.txt" || {
  echo "failed to push target list to device" >&2
  exit 3
}

if [ "$TEST" -eq 1 ]; then
  # Quick hardlink/unlink benchmark: 100 links per target, then unlink all.
  cat > "$OUT/device_test.sh" <<EOF
#!/system/bin/sh
set -eu
KEEP="$device_dir"
TARGETS="$device_targets"
TEST_LOG="$device_log"
mkdir -p "\$KEEP"
: > "\$TEST_LOG"

benchmark() {
  local start end elapsed
  start=\$(date +%s%N 2>/dev/null || date +%s000000000)
  for i in \$(seq 1 100); do
    while IFS= read -r f; do
      [ -n "\$f" ] || continue
      [ -e "\$f" ] || continue
      ino=\$(stat -c %i "\$f" 2>/dev/null || echo 0)
      [ "\$ino" -ne 0 ] || continue
      base=\$(basename "\$f" | tr -c 'A-Za-z0-9_.-' '_')
      ln "\$f" "\$KEEP/\${base}_\${ino}_test_\${i}.hardlink" 2>/dev/null || true
    done < "\$TARGETS"
  done
  end=\$(date +%s%N 2>/dev/null || date +%s000000000)
  elapsed=\$(( (end - start) / 1000000 ))
  echo "hardlink_100x_${#FILES[@]}_targets elapsed_ms=\$elapsed" >> "\$TEST_LOG"
  echo "\$elapsed"
}

unlink_all() {
  local start end elapsed
  start=\$(date +%s%N 2>/dev/null || date +%s000000000)
  rm -f "\$KEEP"/*.hardlink
  end=\$(date +%s%N 2>/dev/null || date +%s000000000)
  elapsed=\$(( (end - start) / 1000000 ))
  echo "unlink_all elapsed_ms=\$elapsed" >> "\$TEST_LOG"
  echo "\$elapsed"
}

link_ms=\$(benchmark)
unlink_ms=\$(unlink_all)
echo "link_ms=\$link_ms unlink_ms=\$unlink_ms"
EOF

  adb -s "$SERIAL" push "$OUT/device_test.sh" "${device_dir}/test.sh" > "$OUT/adb_push_test.stdout.txt" 2> "$OUT/adb_push_test.stderr.txt" || {
    echo "failed to push test script to device" >&2
    exit 3
  }
  adb -s "$SERIAL" shell "su -c 'sh $(shell_quote "${device_dir}/test.sh")'" > "$OUT/test_result.txt" 2> "$OUT/test.err"
  adb -s "$SERIAL" pull "$device_log" "$OUT/test.log" >/dev/null 2>&1 || true

  echo "Hardlink benchmark done."
  echo "  result:    $(cat "$OUT/test_result.txt" 2>/dev/null || echo '<no output>')"
  echo "  host_out:  $OUT"
  echo "  device_log: $device_log"
  exit 0
fi

# Write the device-side watcher script. Variables are expanded here on the host
# so the script pushed to device has concrete paths and no quoting issues.
cat > "$OUT/device_loop.sh" <<EOF
#!/system/bin/sh
set -eu

KEEP="$device_dir"
TARGETS="$device_targets"
PID_FILE="$device_pid_file"
INTERVAL="$INTERVAL_SEC"
KEEP_COUNT="$KEEP_COUNT"
WATCH_TOMBSTONE="$TOMBSTONE_TRIGGERED_STOP"
LAST_TOMBSTONE=""

echo "\$\$" > "\$PID_FILE"

# Try to make this watcher as hard to OOM-kill as possible.
echo -1000 > /proc/self/oom_score_adj 2>/dev/null || true
renice 19 "\$\$" 2>/dev/null || true
ionice -c 3 -n 7 -p "\$\$" 2>/dev/null || true

log() {
  echo "\$(date +'%F %T') \$1" >> "$device_log"
}

log "watcher started pid=\$\$ keep=\$KEEP interval=\$INTERVAL keep_count=\$KEEP_COUNT watch_tombstone=\$WATCH_TOMBSTONE"

# If watching tombstones, remember the newest one at startup so we only react to new ones.
if [ "\$WATCH_TOMBSTONE" = "1" ]; then
  LAST_TOMBSTONE=\$(ls -t /data/tombstones/ 2>/dev/null | head -1)
fi

while :; do
  while IFS= read -r f; do
    [ -n "\$f" ] || continue
    [ -e "\$f" ] || continue
    ino=\$(stat -c %i "\$f" 2>/dev/null || echo 0)
    [ "\$ino" -ne 0 ] || continue
    base=\$(basename "\$f" | tr -c 'A-Za-z0-9_.-' '_')
    hard="\$KEEP/\${base}_\${ino}.hardlink"
    if [ ! -e "\$hard" ]; then
      if ln "\$f" "\$hard" 2>/dev/null; then
        log "hardlinked file=\$f inode=\$ino hard=\$hard"
      fi
    fi
  done < "\$TARGETS"

  # Trim older versions, keeping only the latest KEEP_COUNT per watched path.
  if [ "\$KEEP_COUNT" -gt 0 ]; then
    while IFS= read -r f; do
      [ -n "\$f" ] || continue
      base=\$(basename "\$f" | tr -c 'A-Za-z0-9_.-' '_')
      pattern="\$KEEP/\${base}"_*.hardlink
      ls -t \$pattern 2>/dev/null | awk "NR > \$KEEP_COUNT" | while IFS= read -r old; do
        [ -e "\$old" ] || continue
        rm -f "\$old"
        log "trimmed old=\$old"
      done
    done < "\$TARGETS"
  fi

  # Optional tombstone detection: if a new tombstone appears, immediately
  # hardlink any .odex/.vdex it references, record details, and exit so the host
  # can pull preserved artifacts and stop the stress test.
  if [ "\$WATCH_TOMBSTONE" = "1" ]; then
    current=\$(ls -t /data/tombstones/ 2>/dev/null | head -1)
    if [ -n "\$current" ] && [ "\$current" != "\$LAST_TOMBSTONE" ]; then
      LAST_TOMBSTONE="\$current"
      tpath="/data/tombstones/\$current"
      pkg=\$(grep -m1 '^Cmdline:' "\$tpath" 2>/dev/null | sed 's/^Cmdline: //')
      artifacts=\$(grep -oE '/data/app/[^ ]+\.(odex|vdex)' "\$tpath" 2>/dev/null | sort -u)
      {
        echo "tombstone=\$tpath"
        echo "timestamp=\$(date +'%F %T')"
        echo "package=\$pkg"
        echo "artifacts:"
        echo "\$artifacts"
      } > "\$KEEP/tombstone_triggered.txt"
      log "tombstone_detected file=\$tpath package=\$pkg"
      echo "\$artifacts" | while IFS= read -r af; do
        [ -n "\$af" ] || continue
        [ -e "\$af" ] || continue
        aino=\$(stat -c %i "\$af" 2>/dev/null || echo 0)
        [ "\$aino" -ne 0 ] || continue
        abase=\$(basename "\$af" | tr -c 'A-Za-z0-9_.-' '_')
        if ln "\$af" "\$KEEP/tombstone_\${abase}_\${aino}.hardlink" 2>/dev/null; then
          log "tombstone_hardlinked file=\$af inode=\$aino"
        fi
      done
      log "watcher exiting due to tombstone"
      exit 0
    fi
  fi

  sleep "\$INTERVAL"
done
EOF

adb -s "$SERIAL" push "$OUT/device_loop.sh" "$device_loop_script" > "$OUT/adb_push_loop.stdout.txt" 2> "$OUT/adb_push_loop.stderr.txt" || {
  echo "failed to push loop script to device" >&2
  exit 3
}

# Start the watcher on device. Use nohup + a short sleep so adb shell can exit
# without taking the background process with it.
adb -s "$SERIAL" shell "su -c 'chmod 755 $(shell_quote "$device_loop_script"); nohup sh $(shell_quote "$device_loop_script") > /dev/null 2>&1 & sleep 0.5; cat $(shell_quote "$device_pid_file") 2>/dev/null || echo unknown'" > "$OUT/pid.txt" 2> "$OUT/start.err"

pid="$(tr -d '\r' < "$OUT/pid.txt")"

{
  echo "serial=$SERIAL"
  echo "device_dir=$device_dir"
  echo "device_pid=$pid"
  echo "interval_sec=$INTERVAL_SEC"
  echo "keep_count=$KEEP_COUNT"
  echo "watch_tombstone=$TOMBSTONE_TRIGGERED_STOP"
  echo "targets:"
  cat "$OUT/targets.txt"
  echo ""
  echo "stop_command:"
  printf 'adb -s %s shell "su -c '"'"'cat %s | xargs -r kill'"'"'"\n' "$SERIAL" "$device_pid_file"
  echo ""
  echo "stop_and_cleanup_command:"
  printf 'adb -s %s shell "su -c '"'"'cat %s | xargs -r kill; rm -f %s/*.hardlink'"'"'"\n' "$SERIAL" "$device_pid_file" "$device_dir"
} > "$OUT/meta.txt"

if [ "$TOMBSTONE_TRIGGERED_STOP" -eq 1 ]; then
  # Write a host-side watcher that polls for the tombstone trigger file on device.
  cat > "$OUT/tombstone_watcher.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SERIAL="$SERIAL"
DEVICE_DIR="$device_dir"
PID_FILE="$device_pid_file"
OUT="$OUT"
STOP_COMMAND="$STOP_COMMAND"
EOF
  cat >> "$OUT/tombstone_watcher.sh" <<'EOF'
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
EOF
  chmod +x "$OUT/tombstone_watcher.sh"

  if [ "$BACKGROUND" -eq 1 ]; then
    nohup bash "$OUT/tombstone_watcher.sh" > "$OUT/tombstone_watcher.log" 2>&1 &
    watcher_pid=$!
    echo "$watcher_pid" > "$OUT/tombstone_watcher.pid"
    echo "Started device-side watcher and host-side tombstone watcher in background."
    echo "  device_dir: $device_dir"
    echo "  device_pid: $pid"
    echo "  watcher_pid: $watcher_pid"
    echo "  host_out:   $OUT"
    echo "  stop:       adb -s $SERIAL shell \"su -c 'cat $device_pid_file | xargs -r kill'\""
    echo "  stop_watcher: kill $watcher_pid"
    exit 0
  fi

  echo "Started device-side watcher in tombstone-triggered mode."
  echo "  device_dir: $device_dir"
  echo "  pid:        $pid"
  echo "  host_out:   $OUT"
  echo "  Waiting for a new tombstone in /data/tombstones/..."
  bash "$OUT/tombstone_watcher.sh"
  exit 0
fi

echo "Started device-side watcher."
echo "  device_dir: $device_dir"
echo "  pid:        $pid"
echo "  host_out:   $OUT"
echo "  stop:       adb -s $SERIAL shell \"su -c 'cat $device_pid_file | xargs -r kill'\""
