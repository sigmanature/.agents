#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=adb_helpers.sh
source "$SCRIPT_DIR/adb_helpers.sh"

PKG=""
APK=""
ITERS=3
FILTERS="speed-profile"
REASON="cmdline"
SCOPE="--full"
OUTDIR=""
ENABLE_TRACEFS=0
POST_START_OPEN_WINDOW_SEC=0
STOP_TRACE_ON_CRASH=1
ARTIFACT_SNAPSHOTS=1
INVARIANT_FREEZE=1
ARTIFACT_SETTLE_SEC=5
CLEAR_PROFILES=0
FORCE_MERGE_PROFILE=0
DELETE_DEXOPT=0
INSTALL_GRANT=1
LAUNCH_DURING_COMPILE=1
LAUNCH_INTERVAL=3
HOME_AFTER_LAUNCH=1
FORCE_STOP_BEFORE_LAUNCH=1
FORCE_STOP_AFTER_LAUNCH=1
FOREGROUND_HOLD=5
POST_COMPILE_COLD_START_DELAYS="0,5"
SERIAL="${SERIAL:-}"
LAUNCH_COMPONENT=""
TRACE_PIPE_PID=""
LOGCAT_PID=""
DMESG_STREAM_PID=""
TRACE_PID_LOG=""
tracefs_root="/sys/kernel/tracing"
TOOL_RUN_DEVICE_OATDUMP="$SCRIPT_DIR/run_device_oatdump.sh"
TOOL_VDEXDUMP="$SCRIPT_DIR/vdexdump_min.py"
TOOL_PM_ART_DUMP_SUMMARY="$SCRIPT_DIR/pm_art_dump_summary.py"
TOOL_OAT_ARTIFACT_MANIFEST="$SCRIPT_DIR/oat_artifact_manifest.py"
TOOL_INVARIANT_FREEZE="$SCRIPT_DIR/adb_oat_invariant_freeze.sh"
TOOL_EXTRACT_CNFE_CLASSES="$SCRIPT_DIR/extract_cnfe_classes.py"
CURRENT_ITER_DIR=""
ARTIFACT_CRASH_SENTINEL=""
PROBE_CLASSES_FILE=""
MAX_PROBE_CLASSES=16
ITER_LOGCAT_START_LINE=1

usage() {
  cat <<'USAGE'
adb_oat_rewrite_capture.sh: install/launch a package, force repeated oat/vdex rewrites,
and optionally capture the tracefs syscall window around dex2oat and post-compile cold starts.

Usage:
  adb_oat_rewrite_capture.sh --package <pkg.name> [options]

Options:
  -s, --serial <serial>        Target a specific device
  -p, --package <pkg>          Package name (required)
  --apk <path.apk>             Host APK path; if given, installs with `adb install -r`
  -n, --iters <N>              Iterations (default: 3)
  --filters <csv>              Compiler filters to cycle (default: speed-profile)
  --reason <reason>            Compilation reason for `pm compile -r` (default: cmdline)
  --scope <flag>               One of: --full | --primary-dex | --secondary-dex (default: --full)
  --tracefs                    Enable tracefs capture around compile and post-compile launches (requires working `su`)
  --post-start-open-window-sec <sec>
                               Keep tracefs unfiltered for N seconds after each launch before retargeting to app PIDs
                               (default: 0, disabled)
  --no-stop-trace-on-crash     In post-start open-window mode, do not stop tracing early when crash markers appear
  --no-artifact-snapshots      Disable artifact snapshots (`pm art dump` + `oatdump` + `vdexdump`)
  --no-invariant-freeze        Skip one-shot invariant input capture before S0
  --artifact-settle-sec <sec>  Wait N seconds after compile returns before the settled snapshot (default: 5)
  --clear-profiles             Run `pm art clear-app-profiles` before each compile
  --force-merge-profile        Add `--force-merge-profile` to `pm compile`
  --delete-dexopt              Run `pm delete-dexopt` before each compile
  --no-grant                   Do not pass `-g` to `adb install`
  --no-launch-loop             Do not relaunch the app while compile is running
  --launch-interval <sec>      Seconds between launch attempts during compile (default: 3)
  --no-force-stop-before-launch
                               Do not kill the app before each launch cycle
  --no-force-stop-after-launch Do not kill the app after each launch cycle
  --foreground-hold <sec>      Keep the launched app in foreground before HOME/force-stop (default: 5)
  --post-cold-start-delays <csv>
                               Cold-start delays after compile, in seconds (default: 0,5)
  --no-post-cold-starts        Skip the post-compile cold-start sequence
  --keep-foreground            Do not send HOME after each launch if post-stop is disabled
  -o, --outdir <dir>           Output directory (default: ./oat_rewrite_<ts>_<pkg>)
  -h, --help                   Show help

Notes:
  - Stable repro defaults are now the proven Huoshan speed-profile path:
    single-filter `speed-profile`, cold-kill cadence `launch_interval=3`,
    `foreground_hold=5`, and delayed cold starts `0,5`.
  - Multi-filter rewrite-window experiments still work, but are now explicit opt-in via `--filters`.
  - Launch pressure is cold-start oriented by default: force-stop before each launch cycle,
    then delayed cold starts after compile returns.
  - With `--tracefs`, the script keeps tracing alive through the post-compile cold-start rounds
    and re-targets `set_event_pid` from dex2oat to the launched app process when possible.
  - Current tracefs capture is pid/tid-focused, not syscall-family-filtered: it enables
    raw_syscalls/sys_enter + sys_exit for traced tasks, plus selected f2fs rename/unlink/sync
    events, so non-filesystem syscalls such as futex can legitimately appear in the trace.
  - `--post-start-open-window-sec` keeps the first post-launch seconds fully open, which is useful
    when the target app crashes too fast for stable app-PID retargeting.
  - `--tracefs` fails fast if `adb shell su -c id` is not currently usable.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    -p|--package) PKG="${2:-}"; shift 2;;
    --apk) APK="${2:-}"; shift 2;;
    -n|--iters) ITERS="${2:-}"; shift 2;;
    --filters) FILTERS="${2:-}"; shift 2;;
    --reason) REASON="${2:-}"; shift 2;;
    --scope) SCOPE="${2:-}"; shift 2;;
    --tracefs) ENABLE_TRACEFS=1; shift;;
    --post-start-open-window-sec) POST_START_OPEN_WINDOW_SEC="${2:-}"; shift 2;;
    --no-stop-trace-on-crash) STOP_TRACE_ON_CRASH=0; shift;;
    --no-artifact-snapshots) ARTIFACT_SNAPSHOTS=0; shift;;
    --no-invariant-freeze) INVARIANT_FREEZE=0; shift;;
    --artifact-settle-sec) ARTIFACT_SETTLE_SEC="${2:-}"; shift 2;;
    --clear-profiles) CLEAR_PROFILES=1; shift;;
    --force-merge-profile) FORCE_MERGE_PROFILE=1; shift;;
    --delete-dexopt) DELETE_DEXOPT=1; shift;;
    --no-grant) INSTALL_GRANT=0; shift;;
    --no-launch-loop) LAUNCH_DURING_COMPILE=0; shift;;
    --launch-interval) LAUNCH_INTERVAL="${2:-}"; shift 2;;
    --no-force-stop-before-launch) FORCE_STOP_BEFORE_LAUNCH=0; shift;;
    --no-force-stop-after-launch) FORCE_STOP_AFTER_LAUNCH=0; shift;;
    --foreground-hold) FOREGROUND_HOLD="${2:-}"; shift 2;;
    --post-cold-start-delays) POST_COMPILE_COLD_START_DELAYS="${2:-}"; shift 2;;
    --no-post-cold-starts) POST_COMPILE_COLD_START_DELAYS=""; shift;;
    --keep-foreground) HOME_AFTER_LAUNCH=0; shift;;
    -o|--outdir) OUTDIR="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$PKG" ]]; then
  echo "Error: --package is required" >&2
  usage
  exit 2
fi

case "$SCOPE" in
  --full|--primary-dex|--secondary-dex) ;;
  *)
    echo "Error: --scope must be one of --full/--primary-dex/--secondary-dex" >&2
    exit 2
    ;;
esac

if ! [[ "$POST_START_OPEN_WINDOW_SEC" =~ ^[0-9]+$ ]]; then
  echo "Error: --post-start-open-window-sec must be a non-negative integer" >&2
  exit 2
fi

if ! [[ "$ARTIFACT_SETTLE_SEC" =~ ^[0-9]+$ ]]; then
  echo "Error: --artifact-settle-sec must be a non-negative integer" >&2
  exit 2
fi

TS="$(date +%Y%m%d_%H%M%S)"
SAFE_PKG="${PKG//[^a-zA-Z0-9._-]/_}"
OUTDIR="${OUTDIR:-./oat_rewrite_${TS}_${SAFE_PKG}}"
mkdir -p "$OUTDIR"
PROBE_CLASSES_FILE="$OUTDIR/probe_classes_seed.txt"

has_su() {
  adb_sh_sh 'command -v su >/dev/null 2>&1 && su -c id >/dev/null 2>&1'
}

cleanup() {
  if [[ -n "$TRACE_PIPE_PID" ]] && kill -0 "$TRACE_PIPE_PID" >/dev/null 2>&1; then
    kill "$TRACE_PIPE_PID" >/dev/null 2>&1 || true
    wait "$TRACE_PIPE_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$LOGCAT_PID" ]] && kill -0 "$LOGCAT_PID" >/dev/null 2>&1; then
    kill "$LOGCAT_PID" >/dev/null 2>&1 || true
    wait "$LOGCAT_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$DMESG_STREAM_PID" ]] && kill -0 "$DMESG_STREAM_PID" >/dev/null 2>&1; then
    kill "$DMESG_STREAM_PID" >/dev/null 2>&1 || true
    wait "$DMESG_STREAM_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

summarize_art_dump_file() {
  local input_path="$1"
  local output_path="$2"
  if [[ -s "$input_path" ]]; then
    python3 "$TOOL_PM_ART_DUMP_SUMMARY" "$input_path" > "$output_path" 2>/dev/null || true
  fi
}

start_dmesg_stream() {
  local out="$1"
  local err="$2"
  adb_exec_out_su "dmesg -wT" > "$out" 2> "$err" &
  DMESG_STREAM_PID=$!
}

capture_dmesg_after() {
  local out="$1"
  local err="$2"
  if has_su; then
    adb_su_sh "dmesg -T" > "$out" 2> "$err" || true
  else
    {
      echo "dmesg capture skipped: root unavailable from adb shell"
      echo "Observed: adb shell su -c id failed"
    } > "$err"
  fi
}

{
  echo "serial: ${SERIAL:-<default>}"
  echo "package: $PKG"
  echo "apk: ${APK:-<none>}"
  echo "iters: $ITERS"
  echo "filters: $FILTERS"
  echo "reason: $REASON"
  echo "scope: $SCOPE"
  echo "tracefs: $ENABLE_TRACEFS"
  echo "post_start_open_window_sec: $POST_START_OPEN_WINDOW_SEC"
  echo "stop_trace_on_crash: $STOP_TRACE_ON_CRASH"
  echo "artifact_snapshots: $ARTIFACT_SNAPSHOTS"
  echo "invariant_freeze: $INVARIANT_FREEZE"
  echo "artifact_settle_sec: $ARTIFACT_SETTLE_SEC"
  echo "launch_during_compile: $LAUNCH_DURING_COMPILE"
  echo "launch_interval: $LAUNCH_INTERVAL"
  echo "force_stop_before_launch: $FORCE_STOP_BEFORE_LAUNCH"
  echo "force_stop_after_launch: $FORCE_STOP_AFTER_LAUNCH"
  echo "foreground_hold: $FOREGROUND_HOLD"
  echo "post_compile_cold_start_delays: ${POST_COMPILE_COLD_START_DELAYS:-<disabled>}"
} > "$OUTDIR/meta.txt"

adb_host logcat -v threadtime -b all > "$OUTDIR/logcat_all_threadtime.txt" 2>/dev/null &
LOGCAT_PID=$!

if has_su; then
  start_dmesg_stream "$OUTDIR/dmesg_stream.txt" "$OUTDIR/dmesg_stream.err"
else
  {
    echo "dmesg stream skipped: root unavailable from adb shell"
    echo "Observed: adb shell su -c id failed"
  } > "$OUTDIR/dmesg_stream.err"
fi

adb_sh getprop > "$OUTDIR/getprop.txt" || true
adb_sh date > "$OUTDIR/device_date.txt" || true
adb_sh uptime > "$OUTDIR/uptime.txt" || true

if [[ -n "$APK" ]]; then
  if [[ ! -f "$APK" ]]; then
    echo "APK not found: $APK" >&2
    exit 2
  fi
  install_args=(install -r)
  if [[ "$INSTALL_GRANT" -eq 1 ]]; then
    install_args+=( -g )
  fi
  install_args+=( "$APK" )
  adb_host "${install_args[@]}" > "$OUTDIR/install.txt"
fi

if [[ "$ARTIFACT_SNAPSHOTS" -eq 1 ]]; then
  [[ -x "$TOOL_RUN_DEVICE_OATDUMP" ]] || chmod +x "$TOOL_RUN_DEVICE_OATDUMP"
  [[ -f "$TOOL_VDEXDUMP" ]] || {
    echo "Missing tool: $TOOL_VDEXDUMP" >&2
    exit 2
  }
  [[ -f "$TOOL_PM_ART_DUMP_SUMMARY" ]] || {
    echo "Missing tool: $TOOL_PM_ART_DUMP_SUMMARY" >&2
    exit 2
  }
  [[ -f "$TOOL_OAT_ARTIFACT_MANIFEST" ]] || {
    echo "Missing tool: $TOOL_OAT_ARTIFACT_MANIFEST" >&2
    exit 2
  }
  [[ -f "$TOOL_EXTRACT_CNFE_CLASSES" ]] || {
    echo "Missing tool: $TOOL_EXTRACT_CNFE_CLASSES" >&2
    exit 2
  }
fi

if [[ "$INVARIANT_FREEZE" -eq 1 ]]; then
  [[ -x "$TOOL_INVARIANT_FREEZE" ]] || chmod +x "$TOOL_INVARIANT_FREEZE"
fi

adb_sh pm path "$PKG" > "$OUTDIR/pm_path.txt"
adb_sh pm art dump "$PKG" > "$OUTDIR/art_dump_initial.txt" || true
summarize_art_dump_file "$OUTDIR/art_dump_initial.txt" "$OUTDIR/art_dump_initial_summary.json"
adb_sh cmd package resolve-activity --brief "$PKG" > "$OUTDIR/resolve_activity.txt" || true
LAUNCH_COMPONENT="$(awk 'index($0, "/") > 0 {print; exit}' "$OUTDIR/resolve_activity.txt" | tr -d '\r')"
echo "launch_component: ${LAUNCH_COMPONENT:-<fallback-monkey>}" >> "$OUTDIR/meta.txt"

if [[ "$INVARIANT_FREEZE" -eq 1 ]]; then
  bash "$TOOL_INVARIANT_FREEZE" --serial "$SERIAL" --package "$PKG" --outdir "$OUTDIR/invariant_inputs_v1" \
    > "$OUTDIR/invariant_freeze.log" 2>&1
fi

if [[ "$ENABLE_TRACEFS" -eq 1 ]] && ! has_su; then
  {
    echo "tracefs requested but root is not usable from adb shell."
    echo 'Observed: `adb shell su -c id` failed.'
    echo "See: references/tracefs_root_diagnostics.md"
  } > "$OUTDIR/tracefs_blocked.txt"
  echo "Tracefs requested but root is not usable from adb shell." >&2
  exit 3
fi

prepare_tracefs() {
  adb_su_sh "test -d $tracefs_root || exit 2"
  adb_su_sh "echo 0 > $tracefs_root/tracing_on || true"
  adb_su_sh ": > $tracefs_root/trace || true"
  adb_su_sh "echo > $tracefs_root/set_event_pid || true"
  adb_su_sh "test -e $tracefs_root/trace_clock && echo mono > $tracefs_root/trace_clock || true"
  for evt in \
    sched/sched_process_fork \
    sched/sched_process_exec \
    raw_syscalls/sys_enter \
    raw_syscalls/sys_exit \
    f2fs/f2fs_rename_start \
    f2fs/f2fs_rename_end \
    f2fs/f2fs_unlink_enter \
    f2fs/f2fs_unlink_exit \
    f2fs/f2fs_sync_file_enter \
    f2fs/f2fs_sync_file_exit \
  ; do
    adb_su_sh "test -e $tracefs_root/events/$evt/enable && echo 1 > $tracefs_root/events/$evt/enable || true"
  done
  adb_su_sh "echo 1 > $tracefs_root/tracing_on"
}

start_trace_pipe() {
  local out="$1"
  adb_exec_out_su "cat $tracefs_root/trace_pipe" > "$out" 2>/dev/null &
  TRACE_PIPE_PID=$!
}

trace_marker_note() {
  local msg="$1"
  if [[ "$ENABLE_TRACEFS" -eq 1 ]]; then
    adb_su_sh "test -e $tracefs_root/trace_marker && printf '%s\n' $(_sh_single_quote "$msg") > $tracefs_root/trace_marker || true"
  fi
}

stop_trace_capture() {
  local iter_dir="$1"
  adb_su_sh "echo 0 > $tracefs_root/tracing_on || true"
  adb_exec_out_su "cat $tracefs_root/trace" > "$iter_dir/trace_snapshot.txt" 2>/dev/null || true
  adb_su_sh "echo > $tracefs_root/set_event_pid || true"
  adb_su_sh ": > $tracefs_root/trace || true"
  if [[ -n "$TRACE_PIPE_PID" ]] && kill -0 "$TRACE_PIPE_PID" >/dev/null 2>&1; then
    kill "$TRACE_PIPE_PID" >/dev/null 2>&1 || true
    wait "$TRACE_PIPE_PID" >/dev/null 2>&1 || true
  fi
  TRACE_PIPE_PID=""
}

read_trace_pid_filter() {
  adb_exec_out_su "cat $tracefs_root/set_event_pid 2>/dev/null || true" 2>/dev/null | tr '\r\n' ' '
}

proc_discovery_sh() {
  local cmd="$1"
  if [[ "$ENABLE_TRACEFS" -eq 1 ]] && has_su; then
    adb_su_sh "$cmd"
  else
    adb_sh_sh "$cmd"
  fi
}

pid_tids() {
  local pid="$1"
  proc_discovery_sh "ls /proc/$pid/task 2>/dev/null | tr '\n' ' '" | tr -d '\r'
}

find_named_pids() {
  local proc_name="$1"
  local pids
  pids="$(proc_discovery_sh "pidof $proc_name 2>/dev/null || true" | tr -d '\r')"
  if [[ -z "$pids" ]]; then
    pids="$(proc_discovery_sh "ps -A -o PID,NAME | awk '\$2==\"$proc_name\" {print \$1}' | tr '\n' ' '" | tr -d '\r')"
  fi
  printf '%s' "$pids"
}

find_first_named_pid() {
  local proc_name="$1"
  find_named_pids "$proc_name" | awk '{print $1}'
}

merge_pid_words() {
  tr ' ' '\n' | awk 'NF && !seen[$0]++ {printf "%s ", $0}'
}

append_trace_tids_for_pid() {
  local pid="$1"
  local log_path="$2"
  local label="$3"
  local current tids merged
  tids="$(pid_tids "$pid")"
  if [[ -z "$tids" ]]; then
    return 1
  fi
  current="$(read_trace_pid_filter)"
  merged="$(printf '%s %s\n' "$current" "$tids" | merge_pid_words)"
  if [[ -n "$merged" ]]; then
    adb_su_sh "echo $merged > $tracefs_root/set_event_pid"
    if [[ -n "$TRACE_PID_LOG" ]]; then
      printf '[%s] trace_pids=%s\n' "$label" "$merged" >> "$TRACE_PID_LOG"
    fi
    printf '[%s] trace_tids_appended_for_pid=%s tids=%s\n' "$label" "$pid" "$tids" >> "$log_path"
    return 0
  fi
  return 1
}

pkg_pids() {
  find_named_pids "$PKG"
}

wait_for_pkg_pids() {
  local timeout_secs="${1:-5}"
  local deadline pids
  deadline=$(( $(date +%s) + timeout_secs ))
  while :; do
    pids="$(pkg_pids)"
    if [[ -n "$pids" ]]; then
      printf '%s' "$pids"
      return 0
    fi
    if [[ "$(date +%s)" -ge "$deadline" ]]; then
      return 1
    fi
    sleep 0.2
  done
}

record_pkg_pids() {
  local log_path="$1"
  local label="$2"
  local pids
  pids="$(wait_for_pkg_pids 3 || true)"
  printf '[%s] package_pids=%s\n' "$label" "${pids:-<none>}" >> "$log_path"
}

set_trace_to_app_pids() {
  local log_path="$1"
  local label="$2"
  local pids pid found=0
  pids="$(wait_for_pkg_pids 3 || true)"
  if [[ -z "$pids" ]]; then
    printf '[%s] trace_app_pids=<none>\n' "$label" >> "$log_path"
    return 1
  fi
  for pid in $pids; do
    if append_trace_tids_for_pid "$pid" "$log_path" "$label"; then
      found=1
    fi
  done
  [[ "$found" -eq 1 ]]
}

warn_if_trace_still_unfiltered() {
  local log_path="$1"
  local label="$2"
  local current
  current="$(read_trace_pid_filter)"
  if [[ -z "${current// }" ]]; then
    printf '[%s] trace_scope_warning=still_unfiltered\n' "$label" >> "$log_path"
    if [[ -n "$TRACE_PID_LOG" ]]; then
      printf '[%s] trace_scope_warning=still_unfiltered\n' "$label" >> "$TRACE_PID_LOG"
    fi
  fi
}

trace_open_unfiltered_window() {
  local log_path="$1"
  local label="$2"
  if [[ "$ENABLE_TRACEFS" -eq 1 ]]; then
    adb_su_sh "echo > $tracefs_root/set_event_pid || true"
    if [[ -n "$TRACE_PID_LOG" ]]; then
      printf '[%s] trace_pids=<all>\n' "$label" >> "$TRACE_PID_LOG"
    fi
    printf '[%s] trace_pid_filter=ALL\n' "$label" >> "$log_path"
  fi
}

logcat_line_count() {
  if [[ -f "$OUTDIR/logcat_all_threadtime.txt" ]]; then
    wc -l < "$OUTDIR/logcat_all_threadtime.txt"
  else
    echo 0
  fi
}

logcat_pkg_crash_since() {
  local start_line="$1"
  local file="$OUTDIR/logcat_all_threadtime.txt"
  local from_line=$((start_line + 1))
  [[ -f "$file" ]] || return 1
  sed -n "${from_line},\$p" "$file" | grep -E -m1 "(am_crash.*${PKG}|Process: ${PKG}([ :]|$)|Process: ${PKG}:|Unable to start activity .*${PKG})" >/dev/null 2>&1
}

wait_post_start_open_window() {
  local launch_log="$1"
  local phase="$2"
  local start_line="$3"
  local deadline now
  local hit=1
  if [[ "$POST_START_OPEN_WINDOW_SEC" -le 0 ]]; then
    return 1
  fi
  deadline=$(( $(date +%s) + POST_START_OPEN_WINDOW_SEC ))
  printf '[%s] trace_open_window_sec=%s start_line=%s\n' "$phase" "$POST_START_OPEN_WINDOW_SEC" "$start_line" >> "$launch_log"
  trace_marker_note "oat_capture:${phase}:post_start_open_begin"
  while :; do
    if logcat_pkg_crash_since "$start_line"; then
      hit=0
      printf '[%s] crash_marker_detected=1\n' "$phase" >> "$launch_log"
      trace_marker_note "oat_capture:${phase}:crash_marker_detected"
      if [[ "$ARTIFACT_SNAPSHOTS" -eq 1 ]] && [[ -n "$CURRENT_ITER_DIR" ]] && [[ ! -e "$ARTIFACT_CRASH_SENTINEL" ]]; then
        mkdir -p "$(dirname "$ARTIFACT_CRASH_SENTINEL")"
        : > "$ARTIFACT_CRASH_SENTINEL"
        capture_artifact_snapshot "$CURRENT_ITER_DIR/artifact_snapshots/S3_crash_edge_${phase}" "S3_crash_edge_${phase}" "" "$start_line"
      fi
      if [[ "$STOP_TRACE_ON_CRASH" -eq 1 ]]; then
        adb_su_sh "echo 0 > $tracefs_root/tracing_on || true"
        printf '[%s] tracing_on=0 reason=crash_marker\n' "$phase" >> "$launch_log"
      fi
      break
    fi
    now="$(date +%s)"
    if [[ "$now" -ge "$deadline" ]]; then
      printf '[%s] crash_marker_detected=0 window_timeout=1\n' "$phase" >> "$launch_log"
      trace_marker_note "oat_capture:${phase}:post_start_open_timeout"
      break
    fi
    sleep 0.2
  done
  return "$hit"
}

wait_for_pkg_gone() {
  local timeout_secs="${1:-10}"
  local pids=""
  for _ in $(seq 1 "$timeout_secs"); do
    pids="$(pkg_pids)"
    if [[ -z "$pids" ]]; then
      return 0
    fi
    sleep 1
  done
  pids="$(pkg_pids)"
  [[ -z "$pids" ]]
}

force_stop_pkg() {
  local log_path="$1"
  local label="$2"
  printf '[%s] am force-stop %s\n' "$label" "$PKG" >> "$log_path"
  adb_sh am force-stop "$PKG" >> "$log_path" 2>&1 || true
  if wait_for_pkg_gone 10; then
    printf '[%s] package_gone=1\n' "$label" >> "$log_path"
  else
    printf '[%s] package_gone=0 lingering_pids=%s\n' "$label" "$(pkg_pids)" >> "$log_path"
  fi
}

launch_once() {
  local launch_log="$1"
  local phase="${2:-launch}"
  local logcat_start_line=0
  {
    echo "=== $phase start $(date +%Y-%m-%dT%H:%M:%S) ==="
  } >> "$launch_log"
  if [[ "$FORCE_STOP_BEFORE_LAUNCH" -eq 1 ]]; then
    force_stop_pkg "$launch_log" "$phase pre"
  fi
  trace_open_unfiltered_window "$launch_log" "$phase pre_start"
  logcat_start_line="$(logcat_line_count)"
  printf '[%s] logcat_start_line=%s\n' "$phase" "$logcat_start_line" >> "$launch_log"
  if [[ -n "$LAUNCH_COMPONENT" ]]; then
    adb_sh am start -W -S -n "$LAUNCH_COMPONENT" >> "$launch_log" 2>&1 || true
  else
    adb_sh monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >> "$launch_log" 2>&1 || true
  fi
  record_pkg_pids "$launch_log" "$phase post_start"
  if [[ "$ENABLE_TRACEFS" -eq 1 ]]; then
    if [[ "$POST_START_OPEN_WINDOW_SEC" -gt 0 ]]; then
      if wait_post_start_open_window "$launch_log" "$phase post_start" "$logcat_start_line"; then
        :
      elif adb_exec_out_su "cat $tracefs_root/tracing_on" 2>/dev/null | tr -d '\r' | grep -qx '1'; then
        set_trace_to_app_pids "$launch_log" "$phase post_start_window_end" || true
        warn_if_trace_still_unfiltered "$launch_log" "$phase post_start_window_end"
      fi
    else
      set_trace_to_app_pids "$launch_log" "$phase post_start" || true
      warn_if_trace_still_unfiltered "$launch_log" "$phase post_start"
    fi
  fi
  if [[ "$FOREGROUND_HOLD" -gt 0 ]]; then
    sleep "$FOREGROUND_HOLD"
  fi
  if [[ "$FORCE_STOP_AFTER_LAUNCH" -eq 1 ]]; then
    force_stop_pkg "$launch_log" "$phase post"
  elif [[ "$HOME_AFTER_LAUNCH" -eq 1 ]]; then
    adb_sh input keyevent KEYCODE_HOME >> "$launch_log" 2>&1 || true
  fi
  {
    echo "=== $phase end $(date +%Y-%m-%dT%H:%M:%S) ==="
    echo
  } >> "$launch_log"
}

run_post_compile_cold_starts() {
  local iter_dir="$1"
  local launch_log="$iter_dir/post_compile_cold_starts.txt"
  local delay
  local round=0
  if [[ -z "$POST_COMPILE_COLD_START_DELAYS" ]]; then
    return 0
  fi
  if [[ "$ENABLE_TRACEFS" -eq 1 ]]; then
    adb_su_sh "echo > $tracefs_root/set_event_pid || true"
    if [[ -n "$TRACE_PID_LOG" ]]; then
      printf '[post_compile] trace_pids=<reset>\n' >> "$TRACE_PID_LOG"
    fi
  fi
  IFS=',' read -r -a POST_DELAY_ARR <<<"$POST_COMPILE_COLD_START_DELAYS"
  for delay in "${POST_DELAY_ARR[@]}"; do
    delay="${delay//[[:space:]]/}"
    [[ -z "$delay" ]] && continue
    round=$((round + 1))
    printf 'round=%d delay=%s\n' "$round" "$delay" >> "$launch_log"
    if [[ "$delay" -gt 0 ]]; then
      sleep "$delay"
    fi
    launch_once "$launch_log" "post_compile_round_${round}_delay_${delay}s"
  done
}

append_effective_summary() {
  local summary_path="$1"
  local log_path="$2"
  local requested_filter="$3"
  if [[ ! -s "$summary_path" ]]; then
    return 0
  fi
  python3 - "$summary_path" "$requested_filter" >> "$log_path" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
requested = sys.argv[2]
entry = next((item for item in summary.get("entries", []) if item.get("abi") == "arm64"), None)
if entry is None and summary.get("entries"):
    entry = summary["entries"][0]
if entry is None:
    sys.exit(0)
status = entry.get("status") or "<unknown>"
reason = entry.get("reason") or "<unknown>"
location = entry.get("location") or "<unknown>"
mismatch = "1" if requested and requested != status else "0"
print(f"requested_filter={requested or '<unset>'}")
print(f"effective_filter={status}")
print(f"effective_reason={reason}")
print(f"effective_location={location}")
print(f"effective_filter_mismatch={mismatch}")
PY
}

current_apk_path() {
  adb_sh pm path "$PKG" | tr -d '\r' | sed -n 's/^package://p' | head -n1
}

artifact_path_suffix() {
  local path="$1"
  local package_dir="$2"
  local suffix="${path#"$package_dir/"}"
  if [[ "$suffix" == "$path" ]]; then
    suffix="$(basename "$path")"
  fi
  printf '%s' "${suffix//\//__}"
}

seed_probe_classes() {
  cat > "$PROBE_CLASSES_FILE" <<'EOF'
com.bytedance.android.live.base.model.message.EntertainmentPaidData
com.bytedance.android.live.base.model.message.HotWord
EOF
}

sanitize_probe_token() {
  printf '%s' "$1" | sed 's/[^A-Za-z0-9._-]/_/g'
}

merge_probe_class_files() {
  local out_file="$1"
  shift
  {
    for input_file in "$@"; do
      [[ -f "$input_file" ]] || continue
      cat "$input_file"
    done
  } | awk -v max="$MAX_PROBE_CLASSES" '
    NF && !seen[$0]++ {
      print
      count += 1
      if (count >= max) {
        exit
      }
    }
  ' > "$out_file"
}

refresh_probe_classes_for_snapshot() {
  local snapshot_dir="$1"
  local logcat_start_line="${2:-1}"
  local runtime_json="$snapshot_dir/probe_classes_runtime.json"
  local runtime_txt="$snapshot_dir/probe_classes_runtime.txt"
  local merged_txt="$snapshot_dir/probe_classes_all.txt"

  [[ -f "$PROBE_CLASSES_FILE" ]] || seed_probe_classes

  if [[ -f "$OUTDIR/logcat_all_threadtime.txt" ]]; then
    python3 "$TOOL_EXTRACT_CNFE_CLASSES" \
      --package "$PKG" \
      --start-line "$logcat_start_line" \
      "$OUTDIR/logcat_all_threadtime.txt" > "$runtime_json" 2>/dev/null || true
    python3 "$TOOL_EXTRACT_CNFE_CLASSES" \
      --classes-only \
      --package "$PKG" \
      --start-line "$logcat_start_line" \
      "$OUTDIR/logcat_all_threadtime.txt" > "$runtime_txt" 2>/dev/null || true
  else
    printf '{}\n' > "$runtime_json"
    : > "$runtime_txt"
  fi

  merge_probe_class_files "$merged_txt" "$PROBE_CLASSES_FILE" "$runtime_txt"
}

probe_oat_classes() {
  local snapshot_dir="$1"
  local suffix="$2"
  local artifact_path="$3"
  local apk_path="$4"
  local class_file="$5"
  local summary_file="$snapshot_dir/${suffix}.class_probes.tsv"
  local class_name probe_tag probe_out probe_err rc matched
  local -a probe_classes=()

  [[ -s "$class_file" ]] || return 0
  printf 'class\trc\tmatched\tstdout\tstderr\n' > "$summary_file"
  mapfile -t probe_classes < "$class_file"

  for class_name in "${probe_classes[@]}"; do
    [[ -n "$class_name" ]] || continue
    probe_tag="$(sanitize_probe_token "$class_name")"
    probe_out="$snapshot_dir/${suffix}.probe.${probe_tag}.txt"
    probe_err="$snapshot_dir/${suffix}.probe.${probe_tag}.stderr.txt"
    if bash "$TOOL_RUN_DEVICE_OATDUMP" \
      --serial "$SERIAL" \
      --mode list-classes \
      --class-filter "$class_name" \
      --require-match \
      --oat-file "$artifact_path" \
      --apk-path "$apk_path" \
      --out "$probe_out" > /dev/null 2>"$probe_err"; then
      rc=0
      matched=1
    else
      rc=$?
      matched=0
    fi
    [[ -s "$probe_err" ]] || rm -f "$probe_err"
    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$class_name" "$rc" "$matched" "$(basename "$probe_out")" "$(basename "$probe_err")" >> "$summary_file"
  done
}

record_artifact_file_meta() {
  local artifact_path="$1"
  local meta_file="$2"
  adb_su_sh "ls -li '$artifact_path' 2>/dev/null || true" >> "$meta_file"
  adb_su_sh "ls -ln '$artifact_path' 2>/dev/null || true" >> "$meta_file"
  adb_su_sh "command -v sha256sum >/dev/null 2>&1 && sha256sum '$artifact_path' 2>/dev/null || true" >> "$meta_file"
}

capture_artifact_snapshot() {
  local snapshot_dir="$1"
  local label="$2"
  local requested_filter="${3:-}"
  local logcat_start_line="${4:-1}"
  local apk_path package_dir artifact_list meta_file artifact_path suffix local_raw
  local rc_path rc
  local -a artifact_paths=()
  local -a manifest_args=()
  if [[ "$ARTIFACT_SNAPSHOTS" -ne 1 ]]; then
    return 0
  fi

  mkdir -p "$snapshot_dir"
  {
    echo "label: $label"
    echo "host_wall: $(date +%Y-%m-%dT%H:%M:%S)"
  } > "$snapshot_dir/meta.txt"
  adb_sh date >> "$snapshot_dir/meta.txt" 2>/dev/null || true
  adb_sh pm art dump "$PKG" > "$snapshot_dir/pm_art_dump.txt" || true
  summarize_art_dump_file "$snapshot_dir/pm_art_dump.txt" "$snapshot_dir/pm_art_dump_summary.json"
  apk_path="$(current_apk_path)"
  printf '%s\n' "$apk_path" > "$snapshot_dir/apk_path.txt"
  [[ -n "$apk_path" ]] || return 0
  refresh_probe_classes_for_snapshot "$snapshot_dir" "$logcat_start_line"
  package_dir="${apk_path%/*}"
  printf '%s\n' "$package_dir" > "$snapshot_dir/package_dir.txt"
  artifact_list="$(adb_su_sh "find '$package_dir/oat' -maxdepth 2 -type f \\( -name '*.odex' -o -name '*.oat' -o -name '*.vdex' \\) 2>/dev/null | sort" | tr -d '\r' || true)"
  printf '%s\n' "$artifact_list" | sed '/^$/d' > "$snapshot_dir/artifact_files.txt"
  meta_file="$snapshot_dir/artifact_meta.txt"
  : > "$meta_file"

  mapfile -t artifact_paths < "$snapshot_dir/artifact_files.txt"
  for artifact_path in "${artifact_paths[@]}"; do
    [[ -z "$artifact_path" ]] && continue
    suffix="$(artifact_path_suffix "$artifact_path" "$package_dir")"
    {
      echo "artifact: $artifact_path"
      record_artifact_file_meta "$artifact_path" "$meta_file"
    } >> "$meta_file"

    case "$artifact_path" in
      *.odex|*.oat)
        rc_path="$snapshot_dir/${suffix}.header.rc"
        if bash "$TOOL_RUN_DEVICE_OATDUMP" --serial "$SERIAL" --oat-file "$artifact_path" --apk-path "$apk_path" \
          --out "$snapshot_dir/${suffix}.header.txt" > /dev/null 2>&1; then
          rc=0
        else
          rc=$?
        fi
        printf '%s\n' "$rc" > "$rc_path"
        probe_oat_classes "$snapshot_dir" "$suffix" "$artifact_path" "$apk_path" "$snapshot_dir/probe_classes_all.txt"
        ;;
      *.vdex)
        local_raw="$snapshot_dir/${suffix}"
        adb_exec_out_su "cat $(_sh_single_quote "$artifact_path")" > "$local_raw" 2>/dev/null || true
        rc_path="$snapshot_dir/${suffix}.rc"
        if [[ -s "$local_raw" ]]; then
          if python3 "$TOOL_VDEXDUMP" --json --strict "$local_raw" > "$snapshot_dir/${suffix}.json" 2>/dev/null; then
            rc=0
          else
            rc=$?
          fi
          printf '%s\n' "$rc" > "$rc_path"
        else
          printf '%s\n' "1" > "$rc_path"
        fi
        ;;
    esac
  done

  manifest_args=(
    --snapshot-dir "$snapshot_dir"
    --requested-reason "$REASON"
    --out "$snapshot_dir/invariant_manifest_v1.json"
  )
  if [[ -n "$requested_filter" ]]; then
    manifest_args+=( --requested-filter "$requested_filter" )
  fi
  if python3 "$TOOL_OAT_ARTIFACT_MANIFEST" "${manifest_args[@]}" > /dev/null 2>"$snapshot_dir/invariant_manifest_v1.stderr.txt"; then
    printf '0\n' > "$snapshot_dir/invariant_manifest_v1.rc"
    rm -f "$snapshot_dir/invariant_manifest_v1.stderr.txt"
  else
    printf '%s\n' "$?" > "$snapshot_dir/invariant_manifest_v1.rc"
  fi
}

IFS=',' read -r -a FILTER_ARR <<<"$FILTERS"
if [[ "${#FILTER_ARR[@]}" -eq 0 ]]; then
  echo "Error: empty --filters" >&2
  exit 2
fi

capture_artifact_snapshot "$OUTDIR/artifact_snapshots/S0_initial_state" "S0_initial_state" ""

for ((i=1; i<=ITERS; i++)); do
  iter_dir="$OUTDIR/iter$(printf '%02d' "$i")"
  mkdir -p "$iter_dir"
  CURRENT_ITER_DIR="$iter_dir"
  ARTIFACT_CRASH_SENTINEL="$iter_dir/artifact_snapshots/.s3_captured"
  filter="${FILTER_ARR[$(((i-1) % ${#FILTER_ARR[@]}))]}"
  TRACE_PID_LOG="$iter_dir/trace_pid_updates.txt"
  echo "iteration=$i filter=$filter" | tee "$iter_dir/summary.txt"

  adb_sh pm art dump "$PKG" > "$iter_dir/art_dump_before.txt" || true
  summarize_art_dump_file "$iter_dir/art_dump_before.txt" "$iter_dir/art_dump_before_summary.json"

  if [[ "$DELETE_DEXOPT" -eq 1 ]]; then
    adb_sh pm delete-dexopt "$PKG" > "$iter_dir/delete_dexopt.txt" || true
  fi

  if [[ "$CLEAR_PROFILES" -eq 1 ]]; then
    adb_sh pm art clear-app-profiles "$PKG" > "$iter_dir/clear_profiles.txt" || true
  fi

  if [[ "$ENABLE_TRACEFS" -eq 1 ]]; then
    prepare_tracefs
    start_trace_pipe "$iter_dir/trace_pipe.txt"
  fi

  compile_args=(pm compile "$SCOPE" -r "$REASON" -f -m "$filter")
  if [[ "$FORCE_MERGE_PROFILE" -eq 1 ]]; then
    compile_args+=( --force-merge-profile )
  fi
  compile_args+=( "$PKG" )
  printf '%q ' "${compile_args[@]}" > "$iter_dir/compile_cmd.txt"
  printf '\n' >> "$iter_dir/compile_cmd.txt"
  ITER_LOGCAT_START_LINE="$(logcat_line_count)"

  set +e
  adb_sh "${compile_args[@]}" > "$iter_dir/compile_out.txt" 2>&1 &
  compile_pid=$!
  set -e

  launch_pid=""
  if [[ "$LAUNCH_DURING_COMPILE" -eq 1 ]]; then
    (
      while kill -0 "$compile_pid" >/dev/null 2>&1; do
        launch_once "$iter_dir/launch_loop.txt" "compile_window"
        sleep "$LAUNCH_INTERVAL"
      done
    ) &
    launch_pid=$!
  fi

  if [[ "$ENABLE_TRACEFS" -eq 1 ]]; then
    found_pid=""
    for _ in $(seq 1 120); do
      if ! kill -0 "$compile_pid" >/dev/null 2>&1; then
        break
      fi
      found_pid="$(find_first_named_pid dex2oat64)"
      if [[ -n "$found_pid" ]]; then
        printf '%s\n' "$found_pid" > "$iter_dir/dex2oat_pid.txt"
        append_trace_tids_for_pid "$found_pid" "$iter_dir/compile_out.txt" dex2oat || true
        adb_sh_sh "ps -A -o PID,PPID,NAME,ARGS | grep '[d]ex2oat64'" > "$iter_dir/dex2oat_ps.txt" || true
        break
      fi
      sleep 1
    done
    if [[ -z "$found_pid" ]]; then
      warn_if_trace_still_unfiltered "$iter_dir/compile_out.txt" dex2oat_lookup
    fi
  fi

  set +e
  wait "$compile_pid"
  compile_rc=$?
  set -e
  printf '%s\n' "$compile_rc" > "$iter_dir/compile_rc.txt"

  if [[ -n "$launch_pid" ]] && kill -0 "$launch_pid" >/dev/null 2>&1; then
    kill "$launch_pid" >/dev/null 2>&1 || true
    wait "$launch_pid" >/dev/null 2>&1 || true
  fi

  adb_sh pm art dump "$PKG" > "$iter_dir/art_dump_after.txt" || true
  summarize_art_dump_file "$iter_dir/art_dump_after.txt" "$iter_dir/art_dump_after_summary.json"
  append_effective_summary "$iter_dir/art_dump_after_summary.json" "$iter_dir/summary.txt" "$filter"
  capture_artifact_snapshot "$iter_dir/artifact_snapshots/S1_post_compile_return" "S1_post_compile_return" "$filter" "$ITER_LOGCAT_START_LINE"
  if [[ "$ARTIFACT_SETTLE_SEC" -gt 0 ]]; then
    sleep "$ARTIFACT_SETTLE_SEC"
  fi
  capture_artifact_snapshot "$iter_dir/artifact_snapshots/S2_settled_post_compile" "S2_settled_post_compile" "$filter" "$ITER_LOGCAT_START_LINE"
  run_post_compile_cold_starts "$iter_dir"

  if [[ "$ENABLE_TRACEFS" -eq 1 ]]; then
    stop_trace_capture "$iter_dir"
  fi

done

capture_dmesg_after "$OUTDIR/dmesg_after.txt" "$OUTDIR/dmesg_after.err"

cleanup
trap - EXIT
echo "Done: $OUTDIR"
