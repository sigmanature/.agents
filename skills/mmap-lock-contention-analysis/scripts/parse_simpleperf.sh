#!/usr/bin/env bash
set -u -o pipefail

# parse_simpleperf.sh - Parse simpleperf data and correlate with trace timestamps
# Usage: bash parse_simpleperf.sh <data_file> <out_dir>

DATA_FILE="${1:-}"
OUT_DIR="${2:-.}"

if [[ -z "$DATA_FILE" ]]; then
    echo "Usage: bash parse_simpleperf.sh <data_file> [out_dir]"
    exit 1
fi

mkdir -p "$OUT_DIR"

echo "=== Simpleperf Parse ==="
echo "data=$DATA_FILE"
echo "out=$OUT_DIR"

# Generate callstack report
echo "Generating callstack report..."
simpleperf report -g --dsos "$DATA_FILE" > "$OUT_DIR/simpleperf_report.txt" 2>&1

echo "Report: $OUT_DIR/simpleperf_report.txt"
echo ""
echo "Top entries:"
head -50 "$OUT_DIR/simpleperf_report.txt"
