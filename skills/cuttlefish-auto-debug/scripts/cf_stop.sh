#!/usr/bin/env bash
set -euo pipefail
RUN_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    -h|--help) echo "Usage: cf_stop.sh --run-dir RUN_DIR"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$RUN_DIR" && -x "$RUN_DIR/bin/stop_cvd" ]] || { echo "invalid --run-dir" >&2; exit 1; }
HOME="$RUN_DIR" "$RUN_DIR/bin/stop_cvd" || true
ps aux | grep -E 'launch_cvd|run_cvd|crosvm|qemu|cvd' | grep -v grep || true
