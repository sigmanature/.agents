#!/usr/bin/env bash
set -u -o pipefail

usage() {
  cat <<'EOF'
Usage: tools/sync_kernel_code_auto.sh [options]

Auto-sync daemon: periodically check for modified code files in the source
kernel tree and sync them to the shadow repository.

Options:
  -s, --source DIR       Source tree. Default: common_my_dec
  -d, --dest DIR         Shadow repo. Default: common_kernel_code
  -r, --remote URL       Git remote URL
  -b, --branch BRANCH    Branch to push. Default: main
  -i, --interval SEC     Check interval in seconds. Default: 300 (5 min)
      --daemon           Run as background daemon (nohup + disown)
      --stop             Stop the running daemon
      --status           Show daemon status
  -h, --help             Show this help

Examples:
  # Start daemon in background, check every 5 minutes
  bash tools/sync_kernel_code_auto.sh --daemon

  # Start with custom interval
  bash tools/sync_kernel_code_auto.sh --daemon --interval 60

  # Stop daemon
  bash tools/sync_kernel_code_auto.sh --stop

  # Check status
  bash tools/sync_kernel_code_auto.sh --status
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

source_dir="common_my_dec"
dest_dir="common_kernel_code"
remote_url=""
branch="main"
interval=300
daemon=0
stop=0
status=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--source)   [ "$#" -ge 2 ] || die "$1 requires a value"; source_dir=$2; shift 2 ;;
    -d|--dest)     [ "$#" -ge 2 ] || die "$1 requires a value"; dest_dir=$2; shift 2 ;;
    -r|--remote)   [ "$#" -ge 2 ] || die "$1 requires a value"; remote_url=$2; shift 2 ;;
    -b|--branch)   [ "$#" -ge 2 ] || die "$1 requires a value"; branch=$2; shift 2 ;;
    -i|--interval) [ "$#" -ge 2 ] || die "$1 requires a value"; interval=$2; shift 2 ;;
    --daemon)      daemon=1; shift ;;
    --stop)        stop=1; shift ;;
    --status)      status=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

pid_file="${TMPDIR:-/tmp}/sync_kernel_code_auto_${source_dir}.pid"
log_file="${TMPDIR:-/tmp}/sync_kernel_code_auto_${source_dir}.log"

if [ "$status" -eq 1 ]; then
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "Daemon running (PID: $(cat "$pid_file"))"
    echo "Log: $log_file"
    tail -n 5 "$log_file" 2>/dev/null || echo "No log entries yet"
  else
    echo "Daemon not running"
    [ -f "$pid_file" ] && rm -f "$pid_file"
  fi
  exit 0
fi

if [ "$stop" -eq 1 ]; then
  if [ -f "$pid_file" ]; then
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "Stopped daemon (PID: $pid)"
    else
      echo "Daemon not running (stale PID file)"
    fi
    rm -f "$pid_file"
  else
    echo "No PID file found, daemon not running"
  fi
  exit 0
fi

sync_cmd="bash $(dirname "$0")/sync.sh --source $source_dir --dest $dest_dir --branch $branch --push"
[ -n "$remote_url" ] && sync_cmd="$sync_cmd --remote $remote_url"

sync_once() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Checking for changes..." >> "$log_file"

  tmp_check=$(mktemp "${TMPDIR:-/tmp}/sync_check.XXXXXX")
  git -C "$source_dir" status --porcelain --no-renames | awk -v prefix="$source_dir/" '
  { fname = substr($0, 4); print prefix fname }
  ' | awk '
  {
    path = $0
    n = split(path, parts, "/")
    name = parts[n]
    keep = 0
    if (name == "Makefile" || name == "Kbuild" || name == "BUILD" || name == "BUILD.bazel" || name == "WORKSPACE" || name == "MODULE.bazel" || name == "Android.bp" || name == "Android.mk") keep = 1
    else if (name ~ /^Kconfig/) keep = 1
    else if (name ~ /\.(c|cc|cpp|cxx|h|hpp|hh|hxx|S|s|rs|py|sh|bash|pl|pm|awk|y|l|dts|dtsi|dtso|asn1|ld|lds|bzl|mk|bp|inc|tbl|uc|go)$/) keep = 1
    else if (name ~ /\.rs\.in$/) keep = 1
    if (!keep) next
    if (name ~ /^\.#/ || name ~ /~$/ || name ~ /\.tmp$/ || name ~ /\.sw[opx]$/ || name ~ /\.cmd$/ || name ~ /\.o$/ || name ~ /\.ko$/ || name ~ /\.mod$/ || name ~ /\.mod\.c$/ || name ~ /\.order$/ || name ~ /\.symvers$/) next
    print path
  }
  ' > "$tmp_check"

  count=$(wc -l < "$tmp_check" | tr -d ' ')
  rm -f "$tmp_check"

  if [ "$count" -gt 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Found $count modified files, syncing..." >> "$log_file"
    if $sync_cmd >> "$log_file" 2>&1; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync completed successfully" >> "$log_file"
    else
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync failed, will retry next cycle" >> "$log_file"
    fi
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No changes found" >> "$log_file"
  fi
}

if [ "$daemon" -eq 1 ]; then
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    die "Daemon already running (PID: $(cat "$pid_file"))"
  fi

  echo "Starting daemon (interval: ${interval}s, log: $log_file)"

  (
    while true; do
      sync_once
      sleep "$interval"
    done
  ) &

  daemon_pid=$!
  disown
  echo "$daemon_pid" > "$pid_file"
  echo "Daemon started (PID: $daemon_pid)"
else
  sync_once
fi
