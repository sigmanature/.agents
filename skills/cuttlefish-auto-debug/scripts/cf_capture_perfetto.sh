#!/usr/bin/env bash
set -euo pipefail
RUN_DIR=""
SERIAL=""
SECONDS=120
TAG="trace"
OUT_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    --serial) SERIAL="$2"; shift 2;;
    --seconds) SECONDS="$2"; shift 2;;
    --tag) TAG="$2"; shift 2;;
    --out-dir) OUT_DIR="$2"; shift 2;;
    --) shift; break;;
    -h|--help)
      cat <<'EOF'
Usage: cf_capture_perfetto.sh --run-dir RUN_DIR [--seconds N] [--tag TAG] [--out-dir DIR] [-- WORKLOAD...]

Captures perfetto linux.ftrace events. If WORKLOAD is provided, it runs while the trace is active.
EOF
      exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done
WORKLOAD=("$@")
[[ -n "$RUN_DIR" && -x "$RUN_DIR/bin/adb" ]] || { echo "invalid --run-dir" >&2; exit 1; }
ADB=("$RUN_DIR/bin/adb")
[[ -n "$SERIAL" ]] && ADB+=( -s "$SERIAL" )
TS=$(date +%Y%m%d_%H%M%S)
OUT_DIR=${OUT_DIR:-"$RUN_DIR/evidence_${TAG}_${TS}"}
mkdir -p "$OUT_DIR"
CFG="$OUT_DIR/perfetto_${TAG}.txt"
TRACE_DEV="/data/misc/perfetto-traces/${TAG}_${TS}.pftrace"
TRACE_HOST="$OUT_DIR/${TAG}_${TS}.pftrace"

event_exists(){ "${ADB[@]}" shell "test -e /sys/kernel/tracing/events/$1/enable" >/dev/null 2>&1; }
add_event(){ echo "      ftrace_events: \"$1\"" >> "$CFG"; }

"${ADB[@]}" wait-for-device
"${ADB[@]}" root || true
"${ADB[@]}" wait-for-device
"${ADB[@]}" shell 'mount -t tracefs nodev /sys/kernel/tracing 2>/dev/null || true' >/dev/null || true

cat > "$CFG" <<EOF
buffers: { size_kb: 131072 fill_policy: RING_BUFFER }
data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
EOF

# Always useful if present.
for ev in \
  sched/sched_switch \
  raw_syscalls/sys_enter raw_syscalls/sys_exit \
  syscalls/sys_enter_openat syscalls/sys_enter_write syscalls/sys_enter_pwrite64 \
  syscalls/sys_enter_fsync syscalls/sys_enter_fdatasync syscalls/sys_enter_ftruncate \
  syscalls/sys_enter_renameat2 syscalls/sys_enter_unlinkat \
  block/block_rq_issue block/block_rq_complete \
  f2fs/f2fs_file_write_iter f2fs/f2fs_do_write_data_page \
  f2fs/f2fs_sync_file_enter f2fs/f2fs_sync_file_exit \
  f2fs/f2fs_replace_atomic_write_block; do
  if event_exists "$ev"; then add_event "$ev"; fi
done

cat >> "$CFG" <<EOF
    }
  }
}
duration_ms: $((SECONDS * 1000))
EOF

cp "$CFG" "$OUT_DIR/perfetto_config_used.txt"

echo "[perfetto] config: $CFG"
echo "[perfetto] output: $TRACE_DEV"

if [[ ${#WORKLOAD[@]} -gt 0 ]]; then
  "${ADB[@]}" shell "perfetto -c - --txt -o '$TRACE_DEV'" < "$CFG" > "$OUT_DIR/perfetto_stdout.txt" 2> "$OUT_DIR/perfetto_stderr.txt" &
  PERF_PID=$!
  sleep 3
  echo "[workload] ${WORKLOAD[*]}" | tee "$OUT_DIR/workload_command.txt"
  set +e
  "${WORKLOAD[@]}" > "$OUT_DIR/workload_stdout.txt" 2> "$OUT_DIR/workload_stderr.txt"
  WORK_RC=$?
  set -e
  wait "$PERF_PID" || true
  echo "$WORK_RC" > "$OUT_DIR/workload_exit_code.txt"
else
  "${ADB[@]}" shell "perfetto -c - --txt -o '$TRACE_DEV'" < "$CFG" > "$OUT_DIR/perfetto_stdout.txt" 2> "$OUT_DIR/perfetto_stderr.txt"
fi

"${ADB[@]}" pull "$TRACE_DEV" "$TRACE_HOST"
if [[ ! -s "$TRACE_HOST" ]]; then
  echo "pulled trace is missing or empty: $TRACE_HOST" >&2
  exit 1
fi

echo "[perfetto] pulled: $TRACE_HOST"
"$(dirname "$0")/cf_collect_evidence.sh" --run-dir "$RUN_DIR" --serial "$SERIAL" --out-dir "$OUT_DIR" --tag "$TAG" || true
