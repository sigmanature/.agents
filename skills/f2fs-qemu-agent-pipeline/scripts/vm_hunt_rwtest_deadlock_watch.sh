#!/bin/bash
set -euo pipefail

QGA=(python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py --timeout 40)
OUTLOG=/tmp/host_hunt_watch.log
GUEST_HUNT_LOG=/tmp/hunt/hunt_trunc_write_delete.log

STREAK_NEED=2
VERIFY_GAP_SEC=10
MAX_TICKS=14400

: > "$OUTLOG"
echo "[host-watch] start $(date) rule=rw_test.py-only + stable-stack-check" >> "$OUTLOG"

get_rwtest_d() {
  local dout target
  dout="$(${QGA[@]} "ps -eo pid,stat,comm,args | awk '\$2 ~ /^D/ {print}'" || true)"
  if [ -z "$dout" ]; then
    return 0
  fi
  target="$(printf '%s\n' "$dout" | grep -E 'rw_test.py' || true)"
  printf '%s\n' "$target"
}

guest_log_lines() {
  local n
  n="$(${QGA[@]} "wc -l ${GUEST_HUNT_LOG} 2>/dev/null | awk '{print \$1}'" || true)"
  if [ -z "$n" ]; then
    echo 0
  else
    echo "$n"
  fi
}

capture_snapshot() {
  local ts="$1"
  ${QGA[@]} "echo w > /proc/sysrq-trigger; sleep 1; echo t > /proc/sysrq-trigger; sleep 1; echo l > /proc/sysrq-trigger; sleep 1; dmesg > /tmp/hunt/dmesg_${ts}.txt; ps -eo pid,stat,comm,args > /tmp/hunt/ps_${ts}.txt"
  ${QGA[@]} "cat /tmp/hunt/dmesg_${ts}.txt" > "/tmp/hunt_dmesg_${ts}.host.txt"
  ${QGA[@]} "cat /tmp/hunt/ps_${ts}.txt" > "/tmp/hunt_ps_${ts}.host.txt"
  ${QGA[@]} "cat ${GUEST_HUNT_LOG} 2>/dev/null || true" > "/tmp/hunt_guest_log_${ts}.host.txt"
  /home/nzzhao/.agents/tools/stack_trace_md "/tmp/hunt_dmesg_${ts}.host.txt" -o "/tmp/hunt_stack_${ts}.md"
}

stack_signature() {
  local f="$1"
  rg -n 'task:python3|rw_test.py|prepare_write_begin|f2fs_get_inode_folio|folio_wait_bit_common|f2fs_write_cache_folios|truncate_inode_pages_final|f2fs_evict_inode|do_unlinkat|io_schedule' "$f" \
    | sed 's/^[0-9]\+://' \
    | sha256sum \
    | awk '{print $1}'
}

streak=0
for i in $(seq 1 "$MAX_TICKS"); do
  TARGET="$(get_rwtest_d)"

  if [ -n "$TARGET" ]; then
    streak=$((streak + 1))
    {
      echo "[host-watch] tick=$i streak=$streak rw_test_D_present"
      printf '%s\n' "$TARGET"
    } >> "$OUTLOG"

    if (( streak >= STREAK_NEED )); then
      ts1=$(date +%Y%m%d_%H%M%S)
      lines1=$(guest_log_lines)
      {
        echo "[host-watch] phase1 trigger ts=$ts1 streak=$streak lines=$lines1"
        printf '%s\n' "$TARGET"
      } | tee -a "$OUTLOG"
      capture_snapshot "$ts1"
      sig1=$(stack_signature "/tmp/hunt_dmesg_${ts1}.host.txt")

      sleep "$VERIFY_GAP_SEC"

      TARGET2="$(get_rwtest_d)"
      if [ -z "$TARGET2" ]; then
        echo "[host-watch] phase2 abort: rw_test.py D-state disappeared (transient block)" | tee -a "$OUTLOG"
        streak=0
        continue
      fi

      ts2=$(date +%Y%m%d_%H%M%S)
      lines2=$(guest_log_lines)
      {
        echo "[host-watch] phase2 confirm ts=$ts2 lines=$lines2"
        printf '%s\n' "$TARGET2"
      } | tee -a "$OUTLOG"
      capture_snapshot "$ts2"
      sig2=$(stack_signature "/tmp/hunt_dmesg_${ts2}.host.txt")

      same_stack=0
      no_progress=0
      [ "$sig1" = "$sig2" ] && same_stack=1
      [ "$lines1" = "$lines2" ] && no_progress=1

      {
        echo "[host-watch] decision sig1=$sig1 sig2=$sig2 same_stack=$same_stack lines1=$lines1 lines2=$lines2 no_progress=$no_progress"
        echo "CAP1_DMESG=/tmp/hunt_dmesg_${ts1}.host.txt"
        echo "CAP1_PS=/tmp/hunt_ps_${ts1}.host.txt"
        echo "CAP1_STACK=/tmp/hunt_stack_${ts1}.md"
        echo "CAP2_DMESG=/tmp/hunt_dmesg_${ts2}.host.txt"
        echo "CAP2_PS=/tmp/hunt_ps_${ts2}.host.txt"
        echo "CAP2_STACK=/tmp/hunt_stack_${ts2}.md"
      } | tee -a "$OUTLOG"

      if (( same_stack == 1 && no_progress == 1 )); then
        echo "[host-watch] DEADLOCK_SUSPECT=1 (stable stack + no progress)" | tee -a "$OUTLOG"
        exit 0
      fi

      echo "[host-watch] transient/slow-path block, continue hunting" | tee -a "$OUTLOG"
      streak=0
    fi
  else
    if (( streak > 0 )); then
      echo "[host-watch] tick=$i streak reset from $streak" >> "$OUTLOG"
    elif (( i % 30 == 0 )); then
      echo "[host-watch] tick=$i no rw_test D-state" >> "$OUTLOG"
    fi
    streak=0
  fi

  sleep 1
done

echo "[host-watch] timeout: no sustained rw_test D-state" | tee -a "$OUTLOG"
