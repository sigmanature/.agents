#!/usr/bin/env bash
set -euo pipefail

# launch_memstress_detached.sh
# A small host-side wrapper to reliably detach a long-running memstress+sampling run
# (setsid + pidfile written from inside the detached shell).
#
# This avoids brittle nested-quote one-liners and standardizes:
# - outdir naming
# - pidfile placement
# - stdout/stderr capture
#
# Requires: python3, adb; and if sampling needs root-only stats, pass --use-su.

SERIAL=""
REPO_DIR=""
OUTROOT=""
DURATION_S="7200"
INTERVAL_S="60"
PACKAGE_FILE=""
PACKAGES=()
HEAVY_FILE=""
EXTRA_ARGS=()

usage() {
  cat <<'EOF'
launch_memstress_detached.sh: detach android-thp-fallback-sampler memstress run

Usage:
  launch_memstress_detached.sh --repo <repo_dir> --serial <SERIAL> (--package-file <pkgs.txt> | --package <pkg> ...) [options] [-- <extra args...>]

Required:
  --repo <dir>            Repo/workdir where relative paths resolve (host-side)
  --serial <SERIAL>       adb device serial
  Either one of:
    --package-file <file>   Package list file (host-side path; can be relative to --repo)
    --package <pkg>         Inline package name (repeatable; auto-writes $OUTDIR/packages.txt)

Common options:
  --heavy-package-file <file>  Explicit heavy package list (optional)
  --out-root <dir>             Output root directory (default: <repo>/output)
  --duration-s <sec>           Default: 7200
  --interval-s <sec>           Default: 60

Pass-through:
  Any args after `--` are appended to run_memstress_and_collect_logs.py.

Example (quickhome subset):
  ./scripts/launch_memstress_detached.sh \
    --repo /home/nzzhao/learn_os/output/top100_install_20260325_dual \
    --serial 21121FDF600C4G \
    --package-file ./quickhome_packages_21121FDF600C4G_20260414.txt \
    --heavy-package-file ./quickhome_heavy_packages_21121FDF600C4G_20260414.txt \
    -- --use-su --thp-ensure-mode none --clear-logcat --burst-size 4 --heavy-per-burst 2 --hold-ms 300 --launch-gap-ms 300 --cycle-sleep-ms 200

Example (with oat prune watcher enabled inside runner):
  ./scripts/launch_memstress_detached.sh \
    --repo /home/nzzhao/learn_os/output/top100_install_20260325_dual \
    --serial 21121FDF600C4G \
    --package-file ./selected_packages_camera_wechat_uc_aweme_huoshan_20260420.txt \
    -- --use-su --oat-prune-watch --oat-prune-poll-s 2 --hold-ms 100 --launch-gap-ms 0 --cycle-sleep-ms 0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO_DIR="${2:-}"; shift 2;;
    --serial|-s) SERIAL="${2:-}"; shift 2;;
    --package-file) PACKAGE_FILE="${2:-}"; shift 2;;
    --package) PACKAGES+=("${2:-}"); shift 2;;
    --heavy-package-file) HEAVY_FILE="${2:-}"; shift 2;;
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

if [[ -z "$PACKAGE_FILE" && ${#PACKAGES[@]} -eq 0 ]]; then
  echo "Missing workload targets: pass --package-file or at least one --package." >&2
  usage
  exit 2
fi

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repo dir not found: $REPO_DIR" >&2
  exit 2
fi

if [[ -z "$OUTROOT" ]]; then
  OUTROOT="$REPO_DIR/output"
fi

THP_SIZE=""
if command -v adb >/dev/null 2>&1; then
  for sz in 16 32 64; do
    mode=$(adb -s "$SERIAL" shell "cat /sys/kernel/mm/transparent_hugepage/hugepages-${sz}kB/enabled" 2>/dev/null | tr -d '\r')
    if [[ "$mode" == *"[always]"* ]]; then
      THP_SIZE="${sz}k"
      break
    fi
  done
fi
if [[ -z "$THP_SIZE" ]]; then
  THP_SIZE="auto"
fi

TS="$(date +%Y%m%d_%H%M%S)"
OUTDIR="$OUTROOT/memstress_${TS}_${SERIAL}_${THP_SIZE}"
mkdir -p "$OUTDIR"

PY="/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts/run_memstress_and_collect_logs.py"

if [[ -z "$PACKAGE_FILE" && ${#PACKAGES[@]} -gt 0 ]]; then
  PACKAGE_FILE="$OUTDIR/packages.txt"
  : >"$PACKAGE_FILE"
  for p in "${PACKAGES[@]}"; do
    [[ -n "$p" ]] || continue
    echo "$p" >>"$PACKAGE_FILE"
  done
fi

export REPO_DIR OUTDIR SERIAL DURATION_S INTERVAL_S PACKAGE_FILE HEAVY_FILE PY

setsid -f bash -lc '
  cd "$REPO_DIR" || exit 2
  echo $$ > "$OUTDIR/host_pid.txt"
  exec env PYTHONUNBUFFERED=1 python3 "$PY" \
    --serial "$SERIAL" \
    --out-dir "$OUTDIR" \
    --duration-s "$DURATION_S" --interval-s "$INTERVAL_S" \
    --package-file "$PACKAGE_FILE" \
    '"${HEAVY_FILE:+--heavy-package-file \"$HEAVY_FILE\"}"' \
    '"${EXTRA_ARGS[*]:+${EXTRA_ARGS[*]}}"' \
    >"$OUTDIR/host_stdout.txt" 2>"$OUTDIR/host_stderr.txt"
'

# Wait briefly for pidfile to appear.
for _ in $(seq 1 50); do
  [[ -s "$OUTDIR/host_pid.txt" ]] && break
  sleep 0.1
done

PID=""
[[ -s "$OUTDIR/host_pid.txt" ]] && PID="$(cat "$OUTDIR/host_pid.txt")"
echo "launched pid=${PID:-UNKNOWN} outdir=$OUTDIR"
