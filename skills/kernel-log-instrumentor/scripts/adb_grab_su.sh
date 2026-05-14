#!/usr/bin/env bash
set -u
set -o pipefail

ADB_BIN="${ADB_BIN:-adb}"
OUT_ROOT="adb_cap_$(date +%F_%H%M%S)"

STATE_INTERVAL_SEC="${STATE_INTERVAL_SEC:-0.10}"   # 100ms
SCAN_INTERVAL_SEC="${SCAN_INTERVAL_SEC:-0.25}"     # 250ms
SU_FORCE="${SU_FORCE:-auto}"                       # auto | su0 | suc
BUGREPORT_AFTER_SEC="${BUGREPORT_AFTER_SEC:-0}"    # 0=disabled

# Optional: tune f2fs wbdbg module params after device online.
# All writes are executed as: adb shell su -c "<cmd>" (when root is available).
F2FS_WBDBG_APPLY="${F2FS_WBDBG_APPLY:-0}"          # 0=disabled, 1=apply params
F2FS_WBDBG_DELAY_SEC="${F2FS_WBDBG_DELAY_SEC:-15}" # apply delay after session start
F2FS_WBDBG_ENABLE="${F2FS_WBDBG_ENABLE:-1}"        # /sys/module/f2fs/parameters/wbdbg_enable
F2FS_WBDBG_INO="${F2FS_WBDBG_INO:-0}"              # /sys/module/f2fs/parameters/wbdbg_ino
F2FS_WBDBG_WAIT_MS="${F2FS_WBDBG_WAIT_MS:-300}"    # /sys/module/f2fs/parameters/wbdbg_wait_ms
F2FS_WBDBG_SAMPLE_SHIFT="${F2FS_WBDBG_SAMPLE_SHIFT:-2}"  # /sys/module/f2fs/parameters/wbdbg_sample_shift

# SysRq dump (kernel task state)
SYSRQ_ENABLE="${SYSRQ_ENABLE:-1}"                  # 0=disabled, 1=enable loop
SYSRQ_INTERVAL_SEC="${SYSRQ_INTERVAL_SEC:-60}"      # interval between each 'w'
SYSRQ_T_EVERY_N="${SYSRQ_T_EVERY_N:-5}"           # every N times of 'w', also trigger 't'
SYSRQ_SET_ON_SESSION_START="${SYSRQ_SET_ON_SESSION_START:-1}"  # try: echo 1 > /proc/sys/kernel/sysrq

# New: auto-stop sysrq loop when sysrq w/t output is stable (unchanged) for N times
SYSRQ_STOP_ON_SAME="${SYSRQ_STOP_ON_SAME:-1}"      # 1=auto stop when stable, 0=disable
SYSRQ_STOP_SAME_N="${SYSRQ_STOP_SAME_N:-2}"        # consecutive stable times threshold
SYSRQ_CAPTURE_SLEEP_SEC="${SYSRQ_CAPTURE_SLEEP_SEC:-0.25}"     # sleep after trigger before capturing dmesg segment
SYSRQ_DMESG_TAIL_LINES="${SYSRQ_DMESG_TAIL_LINES:-12000}"      # dmesg tail lines to cover one dump

# Auto-discover when no serial args are provided
DISCOVER_N="${DISCOVER_N:-2}"                      # 2 by default; can be 1,3,... or 'all'

WORKER_PIDS=()

usage() {
  cat <<EOF
用法:
  $0 [-o OUT_ROOT] [SERIAL1 SERIAL2 ...]

说明:
  - 传序列号时：按传入序列号并行抓取
  - 不传序列号时：自动取当前在线的前 DISCOVER_N 台 device（默认 2；若不足则取全部；也可设为 all）

环境变量:
  STATE_INTERVAL_SEC   rapid_state 采样间隔，默认 0.10
  SCAN_INTERVAL_SEC    pstore/tombstones 扫描间隔，默认 0.25
  SU_FORCE             auto | su0 | suc
  BUGREPORT_AFTER_SEC  多少秒后抓 bugreport，0 表示禁用

  F2FS_WBDBG_APPLY         1=自动下发 f2fs wbdbg 参数（默认 0）
  F2FS_WBDBG_DELAY_SEC     设备上线后延迟多少秒再下发（默认 15）
  F2FS_WBDBG_ENABLE        wbdbg_enable（默认 1）
  F2FS_WBDBG_INO           wbdbg_ino（默认 0，表示不过滤 inode）
  F2FS_WBDBG_WAIT_MS       wbdbg_wait_ms（默认 300）
  F2FS_WBDBG_SAMPLE_SHIFT  wbdbg_sample_shift（默认 0，表示不采样）

  SYSRQ_ENABLE                1=在 session 内循环触发 sysrq 'w'/'t'（默认 1）
  SYSRQ_INTERVAL_SEC          sysrq 'w' 的触发间隔（默认 10 秒）
  SYSRQ_T_EVERY_N             每触发 N 次 'w' 再触发一次 't'（默认 2）
  SYSRQ_SET_ON_SESSION_START  1=尝试开启 /proc/sys/kernel/sysrq（默认 1）

  SYSRQ_STOP_ON_SAME          1=若 sysrq w/t 输出连续不变则自动停止（默认 1）
  SYSRQ_STOP_SAME_N           连续不变次数阈值（默认 2）
  SYSRQ_CAPTURE_SLEEP_SEC     trigger 后等待再抓 dmesg 的秒数（默认 0.25）
  SYSRQ_DMESG_TAIL_LINES      dmesg tail 行数（默认 12000）

  DISCOVER_N                  不传序列号时自动选的设备数量（默认 2；可为 all）
  ADB_BIN              adb 可执行文件路径，默认 adb

示例:
  $0 -o out_dir ABC123
  $0 ABC123 XYZ789
  DISCOVER_N=1 $0
  DISCOVER_N=all $0
  SYSRQ_ENABLE=1 SYSRQ_INTERVAL_SEC=1 SYSRQ_T_EVERY_N=10 $0 ABC123
  SYSRQ_ENABLE=1 SYSRQ_STOP_SAME_N=3 $0 ABC123
EOF
}

ts() { date '+%F %T'; }

main_log() {
  printf '[%s][main] %s
' "$(ts)" "$*"
}

sanitize_serial() {
  printf '%s' "$1" | sed 's/[^A-Za-z0-9._-]/_/g'
}

adb_cmd() {
  local serial="$1"
  shift
  "$ADB_BIN" -s "$serial" "$@"
}

discover_serials() {
  local n="${DISCOVER_N}"
  if [ "$n" = "all" ]; then
    mapfile -t SERIALS < <(
      "$ADB_BIN" devices | awk 'NR>1 && $2=="device" {print $1}'
    )
  else
    if ! printf '%s' "$n" | grep -Eq '^[0-9]+$'; then
      n=2
    fi
    mapfile -t SERIALS < <(
      "$ADB_BIN" devices | awk 'NR>1 && $2=="device" {print $1}' | head -n "$n"
    )
  fi
}

cleanup_workers() {
  local p
  for p in "${WORKER_PIDS[@]:-}"; do
    kill "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  WORKER_PIDS=()
}

trap 'cleanup_workers; exit 0' INT TERM EXIT

worker_log() {
  local line
  line="[$(ts)][${serial}] $*"
  printf '%s
' "$line" | tee -a "$dev_root/host.log"
}

worker_cleanup_bg() {
  local p
  for p in "${BG_PIDS[@]:-}"; do
    kill "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  BG_PIDS=()
}

worker_is_online() {
  adb_cmd "$serial" get-state 2>/dev/null | grep -qx device
}

worker_wait_online() {
  until worker_is_online; do
    adb_cmd "$serial" wait-for-device >/dev/null 2>&1 || true
    sleep 0.10
  done
}

worker_detect_su_mode() {
  if [ "$SU_FORCE" = "su0" ]; then
    SU_MODE="su0"
    return 0
  fi
  if [ "$SU_FORCE" = "suc" ]; then
    SU_MODE="suc"
    return 0
  fi

  if adb_cmd "$serial" shell 'su 0 sh -c id' 2>/dev/null | grep -q 'uid=0'; then
    SU_MODE="su0"
    return 0
  fi

  if adb_cmd "$serial" shell 'su -c id' 2>/dev/null | grep -q 'uid=0'; then
    SU_MODE="suc"
    return 0
  fi

  SU_MODE="none"
  return 1
}

worker_run_root() {
  local script="$1"
  case "${SU_MODE:-none}" in
    su0)
      printf '%s
' "$script" | adb_cmd "$serial" shell 'su 0 sh'
      ;;
    suc)
      printf '%s
' "$script" | adb_cmd "$serial" shell 'su -c sh'
      ;;
    *)
      printf '%s
' "$script" | adb_cmd "$serial" shell sh
      ;;
  esac
}

worker_run_root_cmd() {
  local cmd="$1"
  local out_file="${2:-}"
  local tag

  if [ "${SU_MODE:-none}" != "none" ]; then
    tag="$ADB_BIN -s $serial shell su -c \"$cmd\""
    worker_log "+ $tag"
    if [ -n "$out_file" ]; then
      {
        printf '$ %s\n' "$tag"
        adb_cmd "$serial" shell su -c "$cmd"
      } >> "$out_file" 2>&1
    else
      adb_cmd "$serial" shell su -c "$cmd"
    fi
    return 0
  fi

  tag="$ADB_BIN -s $serial shell $cmd"
  worker_log "+ $tag"
  if [ -n "$out_file" ]; then
    {
      printf '$ %s\n' "$tag"
      adb_cmd "$serial" shell "$cmd"
    } >> "$out_file" 2>&1
  else
    adb_cmd "$serial" shell "$cmd"
  fi
}

worker_schedule_wbdbg_params() {
  local sess="$1"
  local out="$sess/wbdbg_apply.log"
  local pbase="/sys/module/f2fs/parameters"

  [ "${F2FS_WBDBG_APPLY}" = "0" ] && return 0

  (
    sleep "${F2FS_WBDBG_DELAY_SEC}"
    if ! worker_is_online; then
      worker_log "skip wbdbg params: device offline"
      exit 0
    fi

    worker_log "applying wbdbg params after ${F2FS_WBDBG_DELAY_SEC}s delay"
    worker_run_root_cmd "echo ${F2FS_WBDBG_ENABLE} > ${pbase}/wbdbg_enable" "$out" || true
    worker_run_root_cmd "echo ${F2FS_WBDBG_INO} > ${pbase}/wbdbg_ino" "$out" || true
    worker_run_root_cmd "echo ${F2FS_WBDBG_WAIT_MS} > ${pbase}/wbdbg_wait_ms" "$out" || true
    worker_run_root_cmd "echo ${F2FS_WBDBG_SAMPLE_SHIFT} > ${pbase}/wbdbg_sample_shift" "$out" || true
    worker_run_root_cmd "cat ${pbase}/wbdbg_enable ${pbase}/wbdbg_ino ${pbase}/wbdbg_wait_ms ${pbase}/wbdbg_sample_shift" "$out" || true
  ) &
  BG_PIDS+=("$!")
}

worker_one_shot_collect() {
  local sess="$1"

  worker_run_root "$(cat <<'EOF'
echo "=== DATE ==="; date
echo "=== UPTIME ==="; uptime
echo "=== ID ==="; id 2>/dev/null
echo "=== GETPROP ==="; getprop
echo "=== MOUNT ==="; mount 2>/dev/null
echo "=== DF ==="; df -h 2>/dev/null
echo "=== PSTORE LS ==="; ls -l /sys/fs/pstore 2>&1
echo "=== LAST_KMSG ==="; ls -l /proc/last_kmsg 2>&1
echo "=== TOMBSTONES LS ==="; ls -l /data/tombstones 2>&1
echo "=== DROPBOX LS ==="; ls -l /data/system/dropbox 2>&1
echo "=== PACKAGES LS ==="; ls -l /data/system/packages* 2>&1
echo "=== USERS LS ==="; ls -l /data/system/users 2>&1
EOF
)" > "$sess/00_once.txt" 2>&1 || true

  worker_run_root "$(cat <<'EOF'
for f in /sys/fs/pstore/* /proc/last_kmsg; do
  [ -e "$f" ] || continue
  echo "===== $f ====="
  cat "$f" 2>&1 || true
  echo
done
EOF
)" > "$sess/pstore_once.txt" 2>&1 || true

  adb_cmd "$serial" pull /data/system/packages.xml "$sess/" >/dev/null 2>&1 || true
  adb_cmd "$serial" pull /data/system/packages.list "$sess/" >/dev/null 2>&1 || true
  adb_cmd "$serial" pull /data/system/packages-stopped.xml "$sess/" >/dev/null 2>&1 || true
  adb_cmd "$serial" pull /data/system/users "$sess/users" >/dev/null 2>&1 || true
  adb_cmd "$serial" pull /data/tombstones "$sess/tombstones_pull" >/dev/null 2>&1 || true
  adb_cmd "$serial" pull /data/system/dropbox "$sess/dropbox_pull" >/dev/null 2>&1 || true
}

worker_start_streams() {
  local sess="$1"

  worker_run_root "$(cat <<'EOF'
logcat -b all -v threadtime
EOF
)" > "$sess/logcat_all.txt" 2>&1 &
  BG_PIDS+=("$!")

  worker_run_root "$(cat <<'EOF'
cat /proc/kmsg 2>/dev/null ||
dmesg -w 2>/dev/null ||
logcat -b kernel -v threadtime 2>/dev/null ||
dmesg 2>/dev/null
EOF
)" > "$sess/kernel_stream.txt" 2>&1 &
  BG_PIDS+=("$!")

  # Optional: sysrq loop to dump task states into kernel log.
  # Note: sysrq output goes to kernel log (kernel_stream.txt), NOT sysrq_loop.txt.
  if [ "${SYSRQ_ENABLE}" != "0" ]; then
    local sysrq_script
    sysrq_script="$(cat <<'EOF'
set -u

SYSRQ_INTERVAL_SEC="__SYSRQ_INTERVAL_SEC__"
SYSRQ_T_EVERY_N="__SYSRQ_T_EVERY_N__"
SYSRQ_SET_ON_SESSION_START="__SYSRQ_SET_ON_SESSION_START__"

SYSRQ_STOP_ON_SAME="__SYSRQ_STOP_ON_SAME__"
SYSRQ_STOP_SAME_N="__SYSRQ_STOP_SAME_N__"
SYSRQ_CAPTURE_SLEEP_SEC="__SYSRQ_CAPTURE_SLEEP_SEC__"
SYSRQ_DMESG_TAIL_LINES="__SYSRQ_DMESG_TAIL_LINES__"

if [ "$SYSRQ_SET_ON_SESSION_START" != "0" ]; then
  echo 1 > /proc/sys/kernel/sysrq 2>/dev/null || true
fi

log_kmsg() {
  # best-effort: write marker into kernel log
  echo "<6>$*" > /dev/kmsg 2>/dev/null || true
}

capture_hash_between_markers() {
  # $1=seq  $2=type(w/t)
  local seq="$1"
  local typ="$2"
  local begin_pat end_pat dump hash
  begin_pat="[SYSRQ_LOOP] seq=${seq} type=${typ} BEGIN"
  end_pat="[SYSRQ_LOOP] seq=${seq} type=${typ} END"

  dump="$(
    dmesg 2>/dev/null | tail -n "${SYSRQ_DMESG_TAIL_LINES}" | awk -v b="$begin_pat" -v e="$end_pat" '
      index($0,b)>0 {cap=1; next}
      index($0,e)>0 {cap=0}
      cap {
        line=$0
        sub(/^\[[ 0-9.]+\][ 	]*/, "", line)  # strip dmesg timestamp
        sub(/^<[0-9]+>/, "", line)            # strip kmsg priority
        if (line ~ /^\[SYSRQ_LOOP\]/) next
        if (line ~ /^\[WBDBG\]/) next
        if (line ~ /^F2FS-fs /) next
        if (line ~ /^healthd:/) next
        if (line ~ /^s3c2410-wdt /) next
        if (line ~ /^google_(battery|charger):/) next
        if (line ~ /^max777x9-pmic /) next
        print line
      }'
  )"

  if [ -z "$dump" ]; then
    echo ""
    return 0
  fi

  hash="$(
    printf '%s
' "$dump" | (
      sha1sum 2>/dev/null || md5sum 2>/dev/null || cksum 2>/dev/null
    ) | awk '{
      # sha1/md5: "<hash> -"
      # cksum: "<crc> <bytes>"
      if (NF>=2 && $2 ~ /^[0-9]+$/) print $1 "-" $2; else print $1
    }'
  )"
  echo "$hash"
}

i=0
seq=0
prev_w_hash=""
prev_t_hash=""
w_streak=0
t_streak=0
t_seen=0

while true; do
  i=$((i+1))
  ts="$(date '+%F %T')"

  echo "[$ts] trigger sysrq: w"
  seq=$((seq+1))
  log_kmsg "[SYSRQ_LOOP] seq=${seq} type=w BEGIN"
  echo w > /proc/sysrq-trigger 2>/dev/null || echo "[$ts] WARNING: failed to write /proc/sysrq-trigger (need root?)"
  sleep "$SYSRQ_CAPTURE_SLEEP_SEC"
  log_kmsg "[SYSRQ_LOOP] seq=${seq} type=w END"

  w_hash="$(capture_hash_between_markers "$seq" "w")"
  if [ -n "$w_hash" ]; then
    if [ -z "$prev_w_hash" ]; then
      w_streak=1
    elif [ "$w_hash" = "$prev_w_hash" ]; then
      w_streak=$((w_streak+1))
    else
      w_streak=1
    fi
    prev_w_hash="$w_hash"
  else
    echo "[$ts] NOTE: failed to capture sysrq w dump (dmesg/marker missing?), skip stable-check"
  fi

  if [ "$SYSRQ_T_EVERY_N" -gt 0 ] && [ $((i % SYSRQ_T_EVERY_N)) -eq 0 ]; then
    echo "[$ts] trigger sysrq: t"
    seq=$((seq+1))
    log_kmsg "[SYSRQ_LOOP] seq=${seq} type=t BEGIN"
    echo t > /proc/sysrq-trigger 2>/dev/null || echo "[$ts] WARNING: failed to write /proc/sysrq-trigger (need root?)"
    sleep "$SYSRQ_CAPTURE_SLEEP_SEC"
    log_kmsg "[SYSRQ_LOOP] seq=${seq} type=t END"

    t_hash="$(capture_hash_between_markers "$seq" "t")"
    if [ -n "$t_hash" ]; then
      t_seen=1
      if [ -z "$prev_t_hash" ]; then
        t_streak=1
      elif [ "$t_hash" = "$prev_t_hash" ]; then
        t_streak=$((t_streak+1))
      else
        t_streak=1
      fi
      prev_t_hash="$t_hash"
    else
      echo "[$ts] NOTE: failed to capture sysrq t dump (dmesg/marker missing?), skip stable-check"
    fi
  fi

  # Stop when BOTH w and t dumps are stable (unchanged) for SYSRQ_STOP_SAME_N times.
  if [ "$SYSRQ_STOP_ON_SAME" != "0" ]; then
    if [ "$w_streak" -ge "$SYSRQ_STOP_SAME_N" ]; then
      if [ "$SYSRQ_T_EVERY_N" -le 0 ]; then
        echo "[$ts] STOP: sysrq w dump stable for ${w_streak} times (no t configured)"
        log_kmsg "[SYSRQ_LOOP] STOP: stable sysrq w for ${w_streak} times"
        break
      elif [ "$t_seen" -eq 1 ] && [ "$t_streak" -ge "$SYSRQ_STOP_SAME_N" ]; then
        echo "[$ts] STOP: sysrq w/t dumps stable (w_streak=${w_streak}, t_streak=${t_streak})"
        log_kmsg "[SYSRQ_LOOP] STOP: stable sysrq w/t (w=${w_streak}, t=${t_streak})"
        break
      fi
    fi
  fi

  sleep "$SYSRQ_INTERVAL_SEC"
done
EOF
)"
    sysrq_script="${sysrq_script/__SYSRQ_INTERVAL_SEC__/${SYSRQ_INTERVAL_SEC}}"
    sysrq_script="${sysrq_script/__SYSRQ_T_EVERY_N__/${SYSRQ_T_EVERY_N}}"
    sysrq_script="${sysrq_script/__SYSRQ_SET_ON_SESSION_START__/${SYSRQ_SET_ON_SESSION_START}}"

    sysrq_script="${sysrq_script/__SYSRQ_STOP_ON_SAME__/${SYSRQ_STOP_ON_SAME}}"
    sysrq_script="${sysrq_script/__SYSRQ_STOP_SAME_N__/${SYSRQ_STOP_SAME_N}}"
    sysrq_script="${sysrq_script/__SYSRQ_CAPTURE_SLEEP_SEC__/${SYSRQ_CAPTURE_SLEEP_SEC}}"
    sysrq_script="${sysrq_script/__SYSRQ_DMESG_TAIL_LINES__/${SYSRQ_DMESG_TAIL_LINES}}"

    worker_run_root "$sysrq_script" > "$sess/sysrq_loop.txt" 2>&1 &
    BG_PIDS+=("$!")
  fi

  worker_run_root "$(cat <<EOF
while true; do
  echo "===== SNAPSHOT ====="
  date
  echo "--- props ---"
  getprop sys.boot_completed 2>/dev/null
  getprop dev.bootcomplete 2>/dev/null
  getprop sys.user.0.ce_available 2>/dev/null
  getprop init.svc.zygote 2>/dev/null
  getprop init.svc.zygote64 2>/dev/null
  getprop init.svc.surfaceflinger 2>/dev/null
  getprop init.svc.servicemanager 2>/dev/null
  getprop init.svc.vold 2>/dev/null
  getprop init.svc.installd 2>/dev/null
  getprop init.svc.tombstoned 2>/dev/null
  getprop init.svc.bootanim 2>/dev/null
  echo "--- pidof ---"
  pidof system_server zygote zygote64 servicemanager surfaceflinger vold installd tombstoned netd apexd bootanimation 2>/dev/null || true
  echo "--- ps ---"
  ps -A 2>/dev/null | grep -E "system_server|zygote|surfaceflinger|servicemanager|vold|installd|tombstoned|netd|apexd|bootanimation|package|systemui" || true
  sleep ${STATE_INTERVAL_SEC}
done
EOF
)" > "$sess/rapid_state.txt" 2>&1 &
  BG_PIDS+=("$!")

  worker_run_root "$(cat <<EOF
while true; do
  echo "===== PSTORE_TOMBSTONES_SCAN ====="
  date
  for d in /sys/fs/pstore /data/tombstones; do
    [ -d "\$d" ] || continue
    echo "## \$d"
    ls -l "\$d" 2>/dev/null || true
    for f in "\$d"/*; do
      [ -f "\$f" ] || continue
      echo "---- \$f ----"
      cat "\$f" 2>/dev/null || true
      echo
    done
  done
  sleep ${SCAN_INTERVAL_SEC}
done
EOF
)" > "$sess/pstore_tombstones_loop.txt" 2>&1 &
  BG_PIDS+=("$!")

  if [ "$BUGREPORT_AFTER_SEC" != "0" ]; then
    (
      sleep "$BUGREPORT_AFTER_SEC"
      if worker_is_online; then
        adb_cmd "$serial" bugreport "$sess/bugreport" >/dev/null 2>&1 || true
      fi
    ) &
    BG_PIDS+=("$!")
  fi
}

device_worker() (
  serial="$1"
  dev_root="$2"
  SU_MODE="none"
  BG_PIDS=()

  mkdir -p "$dev_root"
  trap 'worker_cleanup_bg; exit 0' INT TERM EXIT

  worker_log "output dir: $dev_root"

  while true; do
    worker_log "waiting for device..."
    worker_wait_online

    sess="$dev_root/session_$(date +%F_%H%M%S)"
    mkdir -p "$sess"
    worker_log "device online, session=$sess"

    if worker_detect_su_mode; then
      worker_log "root shell mode: $SU_MODE"
    else
      worker_log "WARNING: su root failed, fallback to non-root shell"
      SU_MODE="none"
    fi

    worker_one_shot_collect "$sess"
    worker_start_streams "$sess"
    worker_schedule_wbdbg_params "$sess"

    while worker_is_online; do
      sleep 0.10
    done

    worker_log "device offline, ending current session"
    worker_cleanup_bg
  done
)

main() {
  local opt
  local -a SERIALS=()

  while getopts ":o:h" opt; do
    case "$opt" in
      o)
        OUT_ROOT="$OPTARG"
        ;;
      h)
        usage
        exit 0
        ;;
      \?)
        echo "未知参数: -$OPTARG" >&2
        usage >&2
        exit 1
        ;;
    esac
  done
  shift $((OPTIND - 1))

  if [ "$#" -ge 1 ]; then
    SERIALS=("$@")
  elif [ "$#" -eq 0 ]; then
    discover_serials
    if [ "${#SERIALS[@]}" -lt 1 ]; then
      echo "当前没有在线 device（adb devices 里状态为 device 的）" >&2
      exit 1
    fi
  fi

  mkdir -p "$OUT_ROOT"
  main_log "output root: $OUT_ROOT"
  main_log "serials: ${SERIALS[*]}"

  local serial serial_safe dev_root
  for serial in "${SERIALS[@]}"; do
    serial_safe="$(sanitize_serial "$serial")"
    dev_root="$OUT_ROOT/$serial_safe"
    mkdir -p "$dev_root"

    device_worker "$serial" "$dev_root" &
    WORKER_PIDS+=("$!")

    main_log "worker started: serial=$serial pid=$! dir=$dev_root"
  done

  wait
}

main "$@"
