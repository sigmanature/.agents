#!/usr/bin/env bash
set -euo pipefail
RUN_DIR=""
SERIAL=""
APK=""
INSTR=""
EXTRA_ARGS=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    --serial) SERIAL="$2"; shift 2;;
    --apk) APK="$2"; shift 2;;
    --instrumentation) INSTR="$2"; shift 2;;
    --extra-args) EXTRA_ARGS="$2"; shift 2;;
    -h|--help)
      cat <<'EOF'
Usage: cf_run_instrumentation.sh --run-dir RUN_DIR [--apk APK] --instrumentation package/runner [--extra-args '...']
EOF
      exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$RUN_DIR" && -x "$RUN_DIR/bin/adb" ]] || { echo "invalid --run-dir" >&2; exit 1; }
[[ -n "$INSTR" ]] || { echo "--instrumentation is required" >&2; exit 2; }
ADB=("$RUN_DIR/bin/adb")
[[ -n "$SERIAL" ]] && ADB+=( -s "$SERIAL" )
"${ADB[@]}" wait-for-device
if [[ -n "$APK" ]]; then
  "${ADB[@]}" install -r -g "$APK"
fi
"${ADB[@]}" shell pm list instrumentation
# shellcheck disable=SC2086
"${ADB[@]}" shell am instrument -w $EXTRA_ARGS "$INSTR"
