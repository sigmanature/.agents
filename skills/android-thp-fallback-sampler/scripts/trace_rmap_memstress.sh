#!/usr/bin/env bash
set -euo pipefail

SERIAL="${1:-18281FDF6007HB}"
OUT_DIR="${2:-/home/nzzhao/learn_os/output/trace_rmap_$(date +%Y%m%d_%H%M%S)}"
CYCLES="${CYCLES:-120}"
PKG_FILE="${PKG_FILE:-}"
KPROBE_FUNC="${KPROBE_FUNC:-folio_remove_rmap_ptes}"
KPROBE_NAME="${KPROBE_NAME:-rmap_ptes}"
CHUNK_MB="${CHUNK_MB:-100}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="${SCRIPT_DIR}/.."
MEMSTRESS="${SKILL_DIR}/scripts/run_memstress_and_collect_logs.py"

if [ -z "$PKG_FILE" ]; then
    echo "ERROR: PKG_FILE not set" >&2
    echo "Usage: PKG_FILE=/path/to/packages.txt $0 [serial] [out_dir]" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

echo "============================================"
echo " trace_rmap + memstress (kprobe)"
echo " serial:    $SERIAL"
echo " out_dir:   $OUT_DIR"
echo " kprobe:    $KPROBE_FUNC -> $KPROBE_NAME"
echo " chunk:     ${CHUNK_MB}MB"
echo " packages:  $PKG_FILE"
echo " cycles:    $CYCLES"
echo "============================================"

setup_kprobe() {
    local ftrace_dir="/sys/kernel/tracing"
    echo "[kprobe] setting up..."

    adb -s "$SERIAL" shell "su -c 'echo 0 > $ftrace_dir/tracing_on'" 2>/dev/null || true
    adb -s "$SERIAL" shell "su -c 'echo > $ftrace_dir/kprobe_events'" 2>/dev/null || true

    # kprobe_events write needs TTY on this device
    adb -s "$SERIAL" shell -t -t "su -c 'echo p:$KPROBE_NAME $KPROBE_FUNC > $ftrace_dir/kprobe_events'" 2>/dev/null
    local r
    r=$(adb -s "$SERIAL" shell "su -c 'cat $ftrace_dir/kprobe_events'" 2>/dev/null)
    if ! echo "$r" | grep -q "$KPROBE_NAME"; then
        echo "ERROR: kprobe setup failed. kprobe_events: $r" >&2
        exit 1
    fi

    adb -s "$SERIAL" shell "su -c 'echo 1 > $ftrace_dir/events/kprobes/$KPROBE_NAME/enable'" 2>/dev/null || true
    adb -s "$SERIAL" shell "su -c 'echo 1 > $ftrace_dir/options/stacktrace'" 2>/dev/null || true

    local buf_size_kb=$((CHUNK_MB * 1024 * 2))
    adb -s "$SERIAL" shell "su -c 'echo $buf_size_kb > $ftrace_dir/buffer_size_kb'" 2>/dev/null || true

    echo "[kprobe] ready"
}

start_trace_stream() {
    echo "[trace] starting stream to $OUT_DIR/trace_chunk_..."
    adb -s "$SERIAL" shell "su -c 'echo 1 > /sys/kernel/tracing/tracing_on'"

    adb -s "$SERIAL" shell "su -c 'cat /sys/kernel/tracing/trace_pipe'" 2>/dev/null \
        | split -b "${CHUNK_MB}M" - "$OUT_DIR/trace_chunk_" &
    TRACE_PID=$!
    echo "[trace] streaming PID=$TRACE_PID"
}

stop_trace() {
    echo "[trace] stopping..."
    adb -s "$SERIAL" shell "su -c 'echo 0 > /sys/kernel/tracing/tracing_on'" 2>/dev/null || true
    if [ -n "${TRACE_PID:-}" ]; then
        sleep 2
        kill "$TRACE_PID" 2>/dev/null || true
        wait "$TRACE_PID" 2>/dev/null || true
    fi
}

cleanup_kprobe() {
    echo "[kprobe] cleaning up..."
    adb -s "$SERIAL" shell "su -c 'echo 0 > /sys/kernel/tracing/events/kprobes/$KPROBE_NAME/enable'" 2>/dev/null || true
    adb -s "$SERIAL" shell "su -c 'echo > /sys/kernel/tracing/kprobe_events'" 2>/dev/null || true
    adb -s "$SERIAL" shell "su -c 'echo 0 > /sys/kernel/tracing/options/stacktrace'" 2>/dev/null || true
    adb -s "$SERIAL" shell "su -c 'echo 1 > /sys/kernel/tracing/tracing_on'" 2>/dev/null || true
}

cleanup() {
    stop_trace
    cleanup_kprobe
    echo "[done] trace files in $OUT_DIR/"
    ls -lh "$OUT_DIR"/trace_chunk_* 2>/dev/null || echo "  (no trace chunks)"
}

trap cleanup EXIT

setup_kprobe
start_trace_stream
sleep 2

echo "============================================"
echo " starting memstress ($CYCLES cycles)"
echo "============================================"

python3 "$MEMSTRESS" \
    --serial "$SERIAL" \
    --out-dir "$OUT_DIR" \
    --max-cycles "$CYCLES" \
    --mode launch_only \
    --no-crash-detect \
    --package-file "$PKG_FILE" \
    --hold-ms 15 \
    --launch-gap-ms 15 \
    --cycle-sleep-ms 1000 \
    --interval-s 60 \
    --use-su \
    --seed 20260617 \
    > "$OUT_DIR/memstress_stdout.log" 2> "$OUT_DIR/memstress_stderr.log"

RC=$?
echo "memstress exited rc=$RC"
