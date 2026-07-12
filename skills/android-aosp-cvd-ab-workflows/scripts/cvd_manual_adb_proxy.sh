#!/usr/bin/env bash
set -u -o pipefail

CMD=${1:-status}
RUN_DIR=${RUN_DIR:-/home/nzzhao/cf_runs/userdebug_test}
PORT=${PORT:-16520}
CID=${CID:-3}
VSOCK_PORT=${VSOCK_PORT:-5555}
ADB_BIN=${ADB_BIN:-adb}
STATE_DIR=${STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/cvd-manual-adb-proxy-${USER}-${PORT}}
LOCK_FILE="$STATE_DIR/lock"
PID_FILE="$STATE_DIR/pid"
PGID_FILE="$STATE_DIR/pgid"
LOG_FILE="$STATE_DIR/proxy.log"
META_FILE="$STATE_DIR/meta"
STOP_FILE="$STATE_DIR/stop_reason"

mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -w 5 9; then
  echo "another cvd_manual_adb_proxy operation is active: $LOCK_FILE" >&2
  exit 2
fi

proxy_bin() {
  printf '%s/bin/socket_vsock_proxy' "$RUN_DIR"
}

read_pid() {
  [ -s "$PID_FILE" ] || return 1
  cat "$PID_FILE"
}

read_pgid() {
  [ -s "$PGID_FILE" ] || return 1
  cat "$PGID_FILE"
}

is_live() {
  local pid
  pid=$(read_pid) || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -F "socket_vsock_proxy" >/dev/null || return 1
}

port_ready() {
  ss -ltnp 2>/dev/null | grep -E ":${PORT}\b" | grep -F "socket_vsock" >/dev/null
}

stop_proxy() {
  local reason=${1:-manual-stop}
  echo "$reason" > "$STOP_FILE"
  "$ADB_BIN" disconnect "127.0.0.1:$PORT" >/dev/null 2>&1 || true
  if ! is_live; then
    rm -f "$PID_FILE" "$PGID_FILE"
    echo "not running"
    return 0
  fi

  local pid pgid
  pid=$(read_pid)
  pgid=$(read_pgid || true)
  if [ -n "${pgid:-}" ]; then
    kill -TERM "-$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi

  local waited=0
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 50 ]; do
    sleep 0.1
    waited=$((waited + 1))
  done

  if kill -0 "$pid" 2>/dev/null; then
    if [ -n "${pgid:-}" ]; then
      kill -KILL "-$pgid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    else
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi

  rm -f "$PID_FILE" "$PGID_FILE"
  echo "stopped pid=$pid reason=$reason"
}

start_proxy() {
  if is_live; then
    echo "already running pid=$(read_pid) port=$PORT state=$STATE_DIR" >&2
    return 3
  fi
  local bin
  bin=$(proxy_bin)
  if [ ! -x "$bin" ]; then
    echo "missing executable: $bin" >&2
    return 4
  fi
  : > "$LOG_FILE"
  {
    echo "start_time=$(date -Is)"
    echo "run_dir=$RUN_DIR"
    echo "port=$PORT"
    echo "cid=$CID"
    echo "vsock_port=$VSOCK_PORT"
    echo "binary=$bin"
  } > "$META_FILE"

  (
    exec 9>&-
    exec setsid "$bin" \
      --server_type=tcp \
      --server_tcp_port="$PORT" \
      --client_type=vsock \
      --client_vsock_id="$CID" \
      --client_vsock_port="$VSOCK_PORT" \
      --label=adb_manual
  ) >"$LOG_FILE" 2>&1 &
  local pid=$!
  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  echo "$pid" > "$PID_FILE"
  echo "${pgid:-$pid}" > "$PGID_FILE"

  local waited=0
  while [ "$waited" -lt 100 ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "proxy exited before readiness; log=$LOG_FILE" >&2
      rm -f "$PID_FILE" "$PGID_FILE"
      return 5
    fi
    if port_ready; then
      echo "ready pid=$pid pgid=${pgid:-$pid} adb=127.0.0.1:$PORT log=$LOG_FILE"
      return 0
    fi
    sleep 0.1
    waited=$((waited + 1))
  done

  echo "proxy did not listen on port $PORT within timeout; log=$LOG_FILE" >&2
  stop_proxy readiness-timeout >/dev/null || true
  return 6
}

status_proxy() {
  if is_live; then
    echo "running pid=$(read_pid) pgid=$(read_pgid || echo '?') port=$PORT ready=$(port_ready && echo yes || echo no) state=$STATE_DIR"
  else
    echo "stopped state=$STATE_DIR"
  fi
}

connect_adb() {
  if ! is_live || ! port_ready; then
    start_proxy || return $?
  fi
  "$ADB_BIN" connect "127.0.0.1:$PORT"
  "$ADB_BIN" devices -l
}

case "$CMD" in
  start) start_proxy ;;
  stop) stop_proxy manual-stop ;;
  restart) stop_proxy restart >/dev/null || true; start_proxy ;;
  status) status_proxy ;;
  connect) connect_adb ;;
  log) sed -n '1,200p' "$LOG_FILE" 2>/dev/null || true ;;
  *)
    echo "usage: RUN_DIR=$RUN_DIR PORT=$PORT CID=$CID VSOCK_PORT=$VSOCK_PORT $0 {start|stop|restart|status|connect|log}" >&2
    exit 64
    ;;
esac
