#!/usr/bin/env bash
set -u -o pipefail

CMD=${1:-start}
if [ $# -gt 0 ]; then
  shift
fi
if [ "${1:-}" = "--" ]; then
  shift
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RUN_DIR=${RUN_DIR:-/home/nzzhao/cf_runs/userdebug_test}
PORT=${PORT:-16520}
CID=${CID:-3}
VSOCK_PORT=${VSOCK_PORT:-5555}
RESUME=${RESUME:-true}
MEMORY_MB=${MEMORY_MB:-8192}
GUEST_ENFORCE_SECURITY=${GUEST_ENFORCE_SECURITY:-false}
REPORT_ANONYMOUS_USAGE_STATS=${REPORT_ANONYMOUS_USAGE_STATS:-n}
ADB_SERIAL=${ADB_SERIAL:-127.0.0.1:$PORT}

if [ -z "${ADB_BIN:-}" ]; then
  if [ -x /home/nzzhao/learn_os/android17/out/host/linux-x86/bin/adb ]; then
    ADB_BIN=/home/nzzhao/learn_os/android17/out/host/linux-x86/bin/adb
  else
    ADB_BIN=adb
  fi
fi

STATE_DIR=${STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/cvd-launch-with-adb-proxy-${USER}-${PORT}}
LOCK_FILE="$STATE_DIR/lock"
LOG_FILE="$STATE_DIR/launch.log"
LAST_RC_FILE="$STATE_DIR/last_rc"
PROXY_SCRIPT="$SCRIPT_DIR/cvd_manual_adb_proxy.sh"

mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -w 5 9; then
  echo "another cvd launch/proxy operation is active: $LOCK_FILE" >&2
  exit 2
fi

usage() {
  cat >&2 <<EOF
usage: RUN_DIR=$RUN_DIR RESUME=$RESUME MEMORY_MB=$MEMORY_MB $0 {start|stop|restart|status|proxy|log} [-- extra_launch_args...]

Defaults preserve app/userdata state: RESUME=true, MEMORY_MB=8192, guest_enforce_security=false.
Set RESUME=false explicitly for clean image/kernel/APEX validation.
EOF
}

adb_boot_completed() {
  "$ADB_BIN" -s "$ADB_SERIAL" shell 'getprop sys.boot_completed' 2>/dev/null \
    | tr -d '\r' | tail -1 | grep -qx '1'
}

adb_probe() {
  "$ADB_BIN" -s "$ADB_SERIAL" shell 'getprop sys.boot_completed; getprop ro.serialno; id; cat /proc/meminfo | head -1' 2>/dev/null
}

preflight_userns() {
  if ! command -v unshare >/dev/null 2>&1; then
    echo "warning: unshare not found; skipping host userns preflight" >&2
    return 0
  fi
  if unshare -Ur -m true >/dev/null 2>&1; then
    return 0
  fi
  echo "host userns/mount namespace preflight failed: unshare -Ur -m true" >&2
  sysctl kernel.unprivileged_userns_clone user.max_user_namespaces kernel.apparmor_restrict_unprivileged_userns 2>/dev/null >&2 || true
  echo "temporary Ubuntu/AppArmor workaround: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" >&2
  return 10
}

proxy_connect() {
  if [ ! -x "$PROXY_SCRIPT" ]; then
    echo "missing executable: $PROXY_SCRIPT" >&2
    return 4
  fi
  RUN_DIR="$RUN_DIR" PORT="$PORT" CID="$CID" VSOCK_PORT="$VSOCK_PORT" ADB_BIN="$ADB_BIN" "$PROXY_SCRIPT" connect
}

proxy_stop() {
  if [ -x "$PROXY_SCRIPT" ]; then
    RUN_DIR="$RUN_DIR" PORT="$PORT" CID="$CID" VSOCK_PORT="$VSOCK_PORT" ADB_BIN="$ADB_BIN" "$PROXY_SCRIPT" stop || true
  fi
}

wait_for_adb() {
  local timeout=${ADB_READY_TIMEOUT_SEC:-60}
  local waited=0
  while [ "$waited" -lt "$timeout" ]; do
    if adb_boot_completed; then
      adb_probe || true
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "adb $ADB_SERIAL did not report sys.boot_completed=1 within ${timeout}s" >&2
  return 12
}

start_cvd() {
  : > "$LOG_FILE"
  {
    echo "start_time=$(date -Is)"
    echo "run_dir=$RUN_DIR"
    echo "resume=$RESUME"
    echo "memory_mb=$MEMORY_MB"
    echo "adb_serial=$ADB_SERIAL"
  } >> "$LOG_FILE"

  if proxy_connect >/tmp/cvd-launch-proxy-precheck.$$.log 2>&1 && adb_boot_completed; then
    cat /tmp/cvd-launch-proxy-precheck.$$.log
    rm -f /tmp/cvd-launch-proxy-precheck.$$.log
    echo "CVD already booted; proxy is connected at $ADB_SERIAL"
    adb_probe || true
    return 0
  fi
  cat /tmp/cvd-launch-proxy-precheck.$$.log >> "$LOG_FILE" 2>/dev/null || true
  rm -f /tmp/cvd-launch-proxy-precheck.$$.log
  proxy_stop >/dev/null 2>&1 || true

  preflight_userns || return $?

  if [ ! -x "$RUN_DIR/bin/launch_cvd" ]; then
    echo "missing executable: $RUN_DIR/bin/launch_cvd" >&2
    return 5
  fi

  local args=(
    --daemon
    --resume="$RESUME"
    --system_image_dir="$RUN_DIR"
    --guest_enforce_security="$GUEST_ENFORCE_SECURITY"
    --memory_mb="$MEMORY_MB"
    --report_anonymous_usage_stats="$REPORT_ANONYMOUS_USAGE_STATS"
  )

  echo "launching CVD from $RUN_DIR; log=$LOG_FILE"
  (
    cd "$RUN_DIR" || exit 6
    exec 9>&-
    ANDROID_HOST_OUT="${ANDROID_HOST_OUT:-$RUN_DIR}" ./bin/launch_cvd "${args[@]}" "$@"
  ) 2>&1 | tee -a "$LOG_FILE"
  local rc=${PIPESTATUS[0]}
  echo "$rc" > "$LAST_RC_FILE"
  if [ "$rc" -ne 0 ]; then
    echo "launch_cvd failed rc=$rc; log=$LOG_FILE" >&2
    return "$rc"
  fi

  proxy_connect || return $?
  wait_for_adb
}

status_cvd() {
  echo "run_dir=$RUN_DIR"
  echo "adb_serial=$ADB_SERIAL"
  if [ -x "$PROXY_SCRIPT" ]; then
    RUN_DIR="$RUN_DIR" PORT="$PORT" CID="$CID" VSOCK_PORT="$VSOCK_PORT" ADB_BIN="$ADB_BIN" "$PROXY_SCRIPT" status || true
  fi
  "$ADB_BIN" devices -l || true
  adb_probe || true
  echo "log=$LOG_FILE"
}

stop_cvd() {
  proxy_stop
  if [ -x "$RUN_DIR/bin/stop_cvd" ]; then
    (cd "$RUN_DIR" && ANDROID_HOST_OUT="${ANDROID_HOST_OUT:-$RUN_DIR}" ./bin/stop_cvd)
    return $?
  fi
  echo "missing executable: $RUN_DIR/bin/stop_cvd" >&2
  return 5
}

case "$CMD" in
  start) start_cvd "$@" ;;
  proxy) proxy_connect ;;
  stop) stop_cvd ;;
  restart) stop_cvd || true; start_cvd "$@" ;;
  status) status_cvd ;;
  log) sed -n '1,240p' "$LOG_FILE" 2>/dev/null || true ;;
  -h|--help|help) usage ;;
  *) usage; exit 64 ;;
esac
