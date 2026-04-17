#!/usr/bin/env bash
set -euo pipefail

# Convenience wrapper: UC Browser + Douyin + Huoshan (TikTok China variants)
#
# Uses the generic detacher `launch_memstress_detached.sh` and passes:
# - inline package list (no package-file needed)
# - a sensible default memstress cadence (burst=1, short dwell, HOME exit)
#
# You can override any python args by appending them after `--`.

SERIAL=""
REPO_DIR=""
OUTROOT=""
DURATION_S="21600"   # 6h
INTERVAL_S="60"
EXTRA_ARGS=()

DEFAULT_ARGS=(
  --use-su
  --clear-logcat
  --burst-size 1
  --heavy-per-burst 0
  --hold-ms 1500
  --launch-gap-ms 300
  --cycle-sleep-ms 200
)

usage() {
  cat <<'EOF'
launch_memstress_uc_douyin_huoshan_detached.sh: detach THP sampler + memstress for 3 apps

Runs (repeat launch + short dwell + HOME):
  - com.UCMobile
  - com.ss.android.ugc.aweme
  - com.ss.android.ugc.live

Usage:
  launch_memstress_uc_douyin_huoshan_detached.sh --repo <repo_dir> --serial <SERIAL> [options] [-- <extra args...>]

Required:
  --repo <dir>        Host-side workdir for relative paths (out-root default is <repo>/output)
  --serial <SERIAL>   adb device serial

Options:
  --out-root <dir>    Output root directory (default: <repo>/output)
  --duration-s <sec>  Default: 21600 (6h)
  --interval-s <sec>  Default: 60

Pass-through:
  Any args after `--` are appended to run_memstress_and_collect_logs.py (and override defaults if duplicated).

Example:
  ./scripts/launch_memstress_uc_douyin_huoshan_detached.sh \
    --repo /home/nzzhao/learn_os/output/top100_install_20260325_dual \
    --serial 21121FDF600C4G \
    -- --hold-ms 3000 --interval-s 30
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO_DIR="${2:-}"; shift 2;;
    --serial|-s) SERIAL="${2:-}"; shift 2;;
    --out-root) OUTROOT="${2:-}"; shift 2;;
    --duration-s) DURATION_S="${2:-}"; shift 2;;
    --interval-s) INTERVAL_S="${2:-}"; shift 2;;
    --) shift; EXTRA_ARGS+=("$@"); break;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$REPO_DIR" || -z "$SERIAL" ]]; then
  echo "Missing required args." >&2
  usage
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DETACHER="$SCRIPT_DIR/launch_memstress_detached.sh"

set -x
"$DETACHER" \
  --repo "$REPO_DIR" \
  --serial "$SERIAL" \
  ${OUTROOT:+--out-root "$OUTROOT"} \
  --duration-s "$DURATION_S" \
  --interval-s "$INTERVAL_S" \
  --package com.UCMobile \
  --package com.ss.android.ugc.aweme \
  --package com.ss.android.ugc.live \
  -- "${DEFAULT_ARGS[@]}" "${EXTRA_ARGS[@]}"
