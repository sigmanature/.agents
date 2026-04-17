#!/usr/bin/env bash
set -euo pipefail

# Capture suite for the debuggable WAL+checkpoint instrumentation repro app.
#
# Collects (best-effort; depends on root mode and device build):
# - logcat (threadtime)
# - dmesg (stream + snapshot)
# - perfetto trace (ftrace: raw_syscalls + f2fs + block best-effort)
# - inode snapshots for /data/user/0/<pkg>/databases (to map pid/ino in kernel logs)
# - app-side artifact snapshots (db/wal/shm) if the test reports corruption

SERIAL=""
OUT_DIR=""

# NOTE: do not use bash special variable name `SECONDS` here.
DURATION_S=300
WRITERS=1
READERS=0
CHECKPOINT="TRUNCATE"
SYNCHRONOUS="FULL"
UPDATES_PER_TXN=200
ROWS=2048
BLOB_BYTES=4096
CHECK_EVERY=1
PATTERN_SAMPLE=10

ENABLE_PERFETTO=1
ENABLE_DMESG_STREAM=1
ENABLE_DB_POLL=1
DB_POLL_MS=200
PREP_SECONDS=3

PKG="com.learnos.sqlitewalrepro"
RUNNER="com.learnos.sqlitewalrepro.test/androidx.test.runner.AndroidJUnitRunner"

# Root method:
# - auto: detect (adbd root vs su vs none)
# - adbd: assume `adb shell` is already root (e.g., after `adb root` on Cuttlefish)
# - su: use `su -c ...` for root-required commands (typical on Pixel user builds)
# - none: skip root-required steps
ROOT_METHOD="auto"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --serial <SERIAL>          adb serial (optional if single device)
  --out-dir <DIR>            output dir (default: walrepro_capture_YYYYmmdd_HHMMSS)

Workload knobs (passed to am instrument):
  --seconds <N>              run seconds (default: $DURATION_S)
  --writers <N>              writer threads (default: $WRITERS)
  --readers <N>              reader threads (default: $READERS)
  --checkpoint <MODE>        PASSIVE|FULL|RESTART|TRUNCATE (default: $CHECKPOINT)
  --synchronous <MODE>       OFF|NORMAL|FULL|EXTRA (default: $SYNCHRONOUS)
  --updates-per-txn <N>      updates per txn (default: $UPDATES_PER_TXN)
  --rows <N>                 rows (default: $ROWS)
  --blob-bytes <N>           payload size bytes (default: $BLOB_BYTES)
  --check-every <N>          quick_check interval (default: $CHECK_EVERY)
  --pattern-sample <N>       semantic sample size (default: $PATTERN_SAMPLE)

Repro app identity:
  --pkg <NAME>               app package (default: $PKG)
  --runner <PKG/RUNNER>      instrumentation runner (default: $RUNNER)

Capture toggles:
  --no-perfetto              disable perfetto capture
  --no-dmesg-stream          disable streaming dmesg -w (still snapshots before/after)
  --no-db-poll               disable polling inode snapshots during run
  --db-poll-ms <N>           poll interval (default: $DB_POLL_MS)
  --prep-seconds <N>         short warmup to learn db inode (default: $PREP_SECONDS, 0 disables)

Root handling:
  --root-method <M>          auto|adbd|su|none (default: $ROOT_METHOD)

Output:
  walrepro_capture_YYYYmmdd_HHMMSS/
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2;;
    --out-dir) OUT_DIR="$2"; shift 2;;
    --seconds) DURATION_S="$2"; shift 2;;
    --writers) WRITERS="$2"; shift 2;;
    --readers) READERS="$2"; shift 2;;
    --checkpoint) CHECKPOINT="$2"; shift 2;;
    --synchronous) SYNCHRONOUS="$2"; shift 2;;
    --updates-per-txn) UPDATES_PER_TXN="$2"; shift 2;;
    --rows) ROWS="$2"; shift 2;;
    --blob-bytes) BLOB_BYTES="$2"; shift 2;;
    --check-every) CHECK_EVERY="$2"; shift 2;;
    --pattern-sample) PATTERN_SAMPLE="$2"; shift 2;;
    --pkg) PKG="$2"; shift 2;;
    --runner) RUNNER="$2"; shift 2;;
    --no-perfetto) ENABLE_PERFETTO=0; shift 1;;
    --no-dmesg-stream) ENABLE_DMESG_STREAM=0; shift 1;;
    --no-db-poll) ENABLE_DB_POLL=0; shift 1;;
    --db-poll-ms) DB_POLL_MS="$2"; shift 2;;
    --prep-seconds) PREP_SECONDS="$2"; shift 2;;
    --root-method) ROOT_METHOD="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

need_cmd() { command -v "$1" >/dev/null 2>&1; }
need_cmd adb || { echo "adb not found in PATH" >&2; exit 1; }

ADB=(adb)
if [[ -n "$SERIAL" ]]; then
  ADB=(adb -s "$SERIAL")
fi

escape_for_double_quotes() {
  # Escape for a command string embedded inside "...":
  # - backslash, double quote, dollar, backtick
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\\$/\\\\$/g' -e 's/`/\\`/g'
}

adb_shell() {
  # One-string contract: caller passes a single shell command string.
  "${ADB[@]}" shell "$1"
}

detect_root_method() {
  case "$ROOT_METHOD" in
    auto|adbd|su|none) ;;
    *)
      echo "Invalid --root-method: $ROOT_METHOD (want auto|adbd|su|none)" >&2
      exit 2
      ;;
  esac

  if [[ "$ROOT_METHOD" != "auto" ]]; then
    return 0
  fi

  local uid
  uid="$("${ADB[@]}" shell id -u 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [[ "$uid" == "0" ]]; then
    ROOT_METHOD="adbd"
    return 0
  fi

  if "${ADB[@]}" shell 'command -v su >/dev/null 2>&1 && su -c "id -u" >/dev/null 2>&1'; then
    local su_uid
    su_uid="$("${ADB[@]}" shell 'su -c "id -u"' 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
    if [[ "$su_uid" == "0" ]]; then
      ROOT_METHOD="su"
      return 0
    fi
  fi

  ROOT_METHOD="none"
}

adb_shell_root() {
  local cmd="$1"
  case "$ROOT_METHOD" in
    adbd)
      "${ADB[@]}" shell "$cmd"
      ;;
    su)
      local escaped
      escaped="$(printf '%s' "$cmd" | escape_for_double_quotes)"
      "${ADB[@]}" shell "su -c \"$escaped\""
      ;;
    none)
      return 1
      ;;
    *)
      echo "BUG: unexpected ROOT_METHOD=$ROOT_METHOD" >&2
      return 1
      ;;
  esac
}

adb_exec_out_root_cat() {
  local path="$1"
  case "$ROOT_METHOD" in
    adbd)
      "${ADB[@]}" exec-out cat "$path"
      ;;
    su)
      "${ADB[@]}" exec-out su -c "cat '$path'"
      ;;
    none)
      return 1
      ;;
    *)
      echo "BUG: unexpected ROOT_METHOD=$ROOT_METHOD" >&2
      return 1
      ;;
  esac
}

OUT_DIR="${OUT_DIR:-walrepro_capture_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$OUT_DIR"

detect_root_method

DB_DIR="/data/user/0/${PKG}/databases"

echo "[+] Device: $("${ADB[@]}" get-serialno 2>/dev/null || true)"
echo "[+] Root method: $ROOT_METHOD"
echo "[+] Output: $OUT_DIR"

echo "[+] Device fingerprint: $(adb_shell 'getprop ro.build.fingerprint' | tr -d '\r' || true)"

echo "[+] Clearing log buffers..."
"${ADB[@]}" logcat -c || true
adb_shell_root 'dmesg -c >/dev/null 2>&1 || true' || true

echo "[+] Capturing dmesg snapshot (before)..."
adb_shell_root 'dmesg -T' >"$OUT_DIR/dmesg_before.txt" 2>"$OUT_DIR/dmesg_before.err" || true

echo "[+] Clearing app data: $PKG"
adb_shell "pm clear \"$PKG\"" >"$OUT_DIR/pm_clear.txt" 2>"$OUT_DIR/pm_clear.err" || true

LOGCAT_PID=""
DMESG_PID=""
DBPOLL_PID=""
PERFETTO_PID=""
PERFETTO_TRACE_DEV=""

cleanup() {
  set +e
  if [[ -n "${DBPOLL_PID}" ]]; then kill "${DBPOLL_PID}" >/dev/null 2>&1 || true; fi
  if [[ -n "${DMESG_PID}" ]]; then kill "${DMESG_PID}" >/dev/null 2>&1 || true; fi
  if [[ -n "${LOGCAT_PID}" ]]; then kill "${LOGCAT_PID}" >/dev/null 2>&1 || true; fi
  wait >/dev/null 2>&1 || true

  echo "[+] Capturing dmesg snapshot (after)..."
  adb_shell_root 'dmesg -T' >"$OUT_DIR/dmesg_after.txt" 2>"$OUT_DIR/dmesg_after.err" || true

  if [[ -n "$PERFETTO_TRACE_DEV" ]]; then
    echo "[+] Pulling perfetto trace..."
    # /data/misc/perfetto-traces is not readable by adb pull on user builds.
    if adb_exec_out_root_cat "$PERFETTO_TRACE_DEV" >"$OUT_DIR/perfetto.pftrace" 2>"$OUT_DIR/perfetto_pull.err"; then
      :
    else
      SD_TRACE="/sdcard/Download/$(basename "$PERFETTO_TRACE_DEV")"
      adb_shell_root "cp '$PERFETTO_TRACE_DEV' '$SD_TRACE' && chmod 0644 '$SD_TRACE'" >/dev/null 2>&1 || true
      "${ADB[@]}" pull "$SD_TRACE" "$OUT_DIR/perfetto.pftrace" >/dev/null 2>&1 || true
    fi
  fi

  echo "[+] Pulling app-side artifacts (if any)..."
  "${ADB[@]}" pull "/sdcard/Android/data/${PKG}/files/wal_repro_artifacts" \
    "$OUT_DIR/wal_repro_artifacts" >/dev/null 2>&1 || true
}
trap cleanup EXIT

arm_klog_for_inode_best_effort() {
  # Optional: if you have f2fs klog knobs in /sys/fs/f2fs/<dev>/..., arm them for ino filtering.
  # If sysfs writes are denied (SELinux / missing caps), ignore and keep going.
  local ino="$1"
  local f2fs_dev
  f2fs_dev="$(adb_shell_root "ls -1 /sys/fs/f2fs 2>/dev/null | grep '^dm-' | head -n 1" \
    | tr -d '\r' | tail -n 1 || true)"
  if [[ -z "${f2fs_dev:-}" ]]; then
    return 0
  fi
  local klog_sysfs="/sys/fs/f2fs/${f2fs_dev}"
  adb_shell_root "echo 0 > '$klog_sysfs/klog_wb_enable'" >/dev/null 2>&1 || true
  adb_shell_root "echo 1 > '$klog_sysfs/klog_wb_detail'" >/dev/null 2>&1 || true
  adb_shell_root "echo 0 > '$klog_sysfs/klog_wb_sample'" >/dev/null 2>&1 || true
  adb_shell_root "echo 0 > '$klog_sysfs/klog_wb_idx_lo'" >/dev/null 2>&1 || true
  adb_shell_root "echo 0 > '$klog_sysfs/klog_wb_idx_hi'" >/dev/null 2>&1 || true
  adb_shell_root "echo '$ino' > '$klog_sysfs/klog_wb_ino'" >/dev/null 2>&1 || true
  adb_shell_root "echo 1 > '$klog_sysfs/klog_wb_enable'" >/dev/null 2>&1 || true
}

if [[ "$PREP_SECONDS" != "0" ]]; then
  echo "[+] Prep run (${PREP_SECONDS}s) to learn repro.db inode..."
  adb_shell "am instrument -w -r \
    -e seconds \"$PREP_SECONDS\" \
    -e writers 1 \
    -e readers 0 \
    -e checkpoint \"$CHECKPOINT\" \
    -e synchronous \"$SYNCHRONOUS\" \
    -e updatesPerTxn 20 \
    -e rows 256 \
    -e blobBytes \"$BLOB_BYTES\" \
    -e checkEvery 0 \
    -e patternSample 0 \
    \"$RUNNER\"" \
    >"$OUT_DIR/instrument_prep_stdout.txt" 2>"$OUT_DIR/instrument_prep_stderr.txt" || true

  DB_INO="$(adb_shell_root "ls -li '$DB_DIR/repro.db' 2>/dev/null | sed -e 's/^ *//' | cut -d' ' -f1" \
    | tr -d '\r' | tail -n 1 || true)"
  echo "[+] repro.db inode (best-effort) = ${DB_INO:-unknown}" | tee "$OUT_DIR/db_inode.txt"
  if [[ -n "${DB_INO:-}" ]]; then
    echo "[+] Arming f2fs klog filter for ino=$DB_INO (best-effort)..."
    arm_klog_for_inode_best_effort "$DB_INO" || true
  fi

  echo "[+] Clearing log buffers after prep..."
  "${ADB[@]}" logcat -c || true
  adb_shell_root 'dmesg -c >/dev/null 2>&1 || true' || true
fi

echo "[+] Starting logcat capture..."
"${ADB[@]}" logcat -v threadtime >"$OUT_DIR/logcat_threadtime.txt" 2>"$OUT_DIR/logcat_threadtime.err" &
LOGCAT_PID=$!

if [[ "$ENABLE_DMESG_STREAM" == "1" ]]; then
  echo "[+] Starting dmesg -w capture..."
  (adb_shell_root 'dmesg -w' >"$OUT_DIR/dmesg_stream.txt" 2>"$OUT_DIR/dmesg_stream.err") &
  DMESG_PID=$!
fi

if [[ "$ENABLE_DB_POLL" == "1" ]]; then
  echo "[+] Polling DB dir inode snapshots: $DB_DIR"
  (
    while true; do
      ts_wall="$(date +%s%3N)"
      adb_shell_root "echo \"ts_wall_ms=$ts_wall\"; ls -li '$DB_DIR' 2>/dev/null || true" \
        >>"$OUT_DIR/db_dir_lsli_samples.txt" 2>>"$OUT_DIR/db_dir_lsli_samples.err" || true
      sleep "$(awk "BEGIN{print $DB_POLL_MS/1000}")"
    done
  ) &
  DBPOLL_PID=$!
fi

if [[ "$ENABLE_PERFETTO" == "1" ]]; then
  echo "[+] Starting perfetto capture..."
  PERFETTO_TRACE_DEV="/data/misc/perfetto-traces/walrepro_$(date +%Y%m%d_%H%M%S).pftrace"
  CFG="$OUT_DIR/perfetto_cfg.txt"
  {
    echo "buffers: { size_kb: 262144 fill_policy: RING_BUFFER }"
    echo "data_sources: { config { name: \"linux.ftrace\" ftrace_config {"
    echo "  ftrace_events: \"sched/sched_switch\""
    echo "  ftrace_events: \"raw_syscalls/sys_enter\""
    echo "  ftrace_events: \"raw_syscalls/sys_exit\""
    echo "  ftrace_events: \"f2fs/f2fs_datawrite_start\""
    echo "  ftrace_events: \"f2fs/f2fs_datawrite_end\""
    echo "  ftrace_events: \"f2fs/f2fs_do_write_data_page\""
    echo "  ftrace_events: \"f2fs/f2fs_file_write_iter\""
    echo "  ftrace_events: \"f2fs/f2fs_replace_atomic_write_block\""
    echo "  ftrace_events: \"block/block_rq_issue\""
    echo "  ftrace_events: \"block/block_rq_complete\""
    echo "  atrace_apps: \"*\""
    echo "} } }"
    echo "duration_ms: $((DURATION_S * 1000))"
  } >"$CFG"

  (
    # Best-effort: if perfetto fails to arm, keep going with logcat/dmesg.
    adb_shell_root "perfetto -c - --txt -o '$PERFETTO_TRACE_DEV'" <"$CFG" \
      >"$OUT_DIR/perfetto_stdout.txt" 2>"$OUT_DIR/perfetto_stderr.txt" || true
  ) &
  PERFETTO_PID=$!
  sleep 1
fi

echo "[+] Running instrumentation workload..."
adb_shell "am instrument -w -r \
  -e seconds \"$DURATION_S\" \
  -e writers \"$WRITERS\" \
  -e readers \"$READERS\" \
  -e checkpoint \"$CHECKPOINT\" \
  -e synchronous \"$SYNCHRONOUS\" \
  -e updatesPerTxn \"$UPDATES_PER_TXN\" \
  -e rows \"$ROWS\" \
  -e blobBytes \"$BLOB_BYTES\" \
  -e checkEvery \"$CHECK_EVERY\" \
  -e patternSample \"$PATTERN_SAMPLE\" \
  \"$RUNNER\"" \
  >"$OUT_DIR/instrument_stdout.txt" 2>"$OUT_DIR/instrument_stderr.txt" || true

echo "[+] Instrumentation finished."

if [[ -n "${PERFETTO_PID}" ]]; then
  echo "[+] Waiting perfetto to finish..."
  wait "${PERFETTO_PID}" >/dev/null 2>&1 || true
fi

