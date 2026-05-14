#!/usr/bin/env bash
set -u -o pipefail

# capture_simpleperf.sh - Parallel simpleperf callstack capture for mmap contention analysis
# Usage: bash capture_simpleperf.sh <serial> <victim_package> <duration_s> <out_dir>

SERIAL="${1:-}"
VICTIM_PKG="${2:-}"
DURATION_S="${3:-300}"
OUT_DIR="${4:-/tmp/simpleperf}"

if [[ -z "$SERIAL" || -z "$VICTIM_PKG" ]]; then
    echo "Usage: bash capture_simpleperf.sh <serial> <victim_package> <duration_s> <out_dir>"
    exit 1
fi

mkdir -p "$OUT_DIR"

echo "=== Simpleperf Capture ==="
echo "serial=$SERIAL"
echo "victim=$VICTIM_PKG"
echo "duration=${DURATION_S}s"
echo "out_dir=$OUT_DIR"

# Wait for victim process to appear
PID=""
for i in $(seq 1 60); do
    PID="$(adb -s "$SERIAL" shell pidof "$VICTIM_PKG" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
    if [[ -n "$PID" ]]; then
        echo "Victim PID found: $PID"
        break
    fi
    sleep 1
done

if [[ -z "$PID" ]]; then
    echo "ERROR: Victim process not found after 60s"
    exit 1
fi

# Record callstacks for the victim PID (default cpu-cycles sampling)
adb -s "$SERIAL" shell "simpleperf record -g \
    -p $PID \
    -o /data/local/tmp/simpleperf_${VICTIM_PKG//./_}.data \
    --duration $DURATION_S \
    2>&1" | tee "$OUT_DIR/simpleperf.log"

# Pull results
adb -s "$SERIAL" pull "/data/local/tmp/simpleperf_${VICTIM_PKG//./_}.data" "$OUT_DIR/"
adb -s "$SERIAL" pull "/data/local/tmp/simpleperf_${VICTIM_PKG//./_}.data.perf.data" "$OUT_DIR/" 2>/dev/null || true

echo "=== Simpleperf capture complete ==="
echo "Data: $OUT_DIR/simpleperf_${VICTIM_PKG//./_}.data"
echo ""
echo "To parse:"
echo "  simpleperf report -g --dsos $OUT_DIR/simpleperf_${VICTIM_PKG//./_}.data"
