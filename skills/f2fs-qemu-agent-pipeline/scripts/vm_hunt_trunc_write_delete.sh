#!/bin/bash
set -euo pipefail
cd /root/shared_with_host/test

LOG=/tmp/hunt/hunt_trunc_write_delete.log
: > "$LOG"

BASE_SIZE=65537
WRITE_OFF=65537
WRITE_SIZE=64k
WORKERS=24
ROUNDS=1000000

{
  echo "[sysctl-before]"
  sysctl vm.dirty_background_bytes vm.dirty_bytes vm.dirty_writeback_centisecs vm.dirty_expire_centisecs vm.dirtytime_expire_seconds || true
} >> "$LOG" 2>&1

sysctl -w vm.dirty_background_bytes=$((4*1024*1024)) >> "$LOG" 2>&1 || true
sysctl -w vm.dirty_bytes=$((16*1024*1024)) >> "$LOG" 2>&1 || true
sysctl -w vm.dirty_writeback_centisecs=10 >> "$LOG" 2>&1 || true
sysctl -w vm.dirty_expire_centisecs=20 >> "$LOG" 2>&1 || true
sysctl -w vm.dirtytime_expire_seconds=5 >> "$LOG" 2>&1 || true

{
  echo "[sysctl-after]"
  sysctl vm.dirty_background_bytes vm.dirty_bytes vm.dirty_writeback_centisecs vm.dirty_expire_centisecs vm.dirtytime_expire_seconds || true
  echo "[mode] truncate(base_size) -> buffered_write(unaligned,no-fsync) -> delete(loop)"
  echo "[start] $(date)"
  echo "[config] BASE_SIZE=$BASE_SIZE WRITE_OFF=$WRITE_OFF WRITE_SIZE=$WRITE_SIZE WORKERS=$WORKERS ROUNDS=$ROUNDS"
} >> "$LOG" 2>&1

worker() {
  local wid="$1"
  local i f rc
  for i in $(seq 1 "$ROUNDS"); do
    f="/mnt/f2fs/enc_test/hunt_twd_w${wid}_${i}.bin"
    truncate -s "$BASE_SIZE" "$f"

    rc=0
    python3 /root/shared_with_host/test/rw_test.py \
      w "$WRITE_OFF" "$WRITE_SIZE" \
      -f "$f" \
      --no-fsync \
      --verify-mode cache \
      --pattern-mode filepos \
      --token PyWrtDta \
      --seed 0 \
      --pattern-gen stream \
      --chunk 1m >> "$LOG" 2>&1 || rc=$?

    if [ "$rc" -ne 0 ]; then
      echo "[worker=$wid iter=$i] rw_test rc=$rc file=$f" >> "$LOG"
      rc=0
    fi

    rm -f "$f"

    if (( i % 200 == 0 )); then
      echo "[worker=$wid] checkpoint i=$i $(date)" >> "$LOG"
    fi
  done
}

for w in $(seq 0 $((WORKERS-1))); do
  worker "$w" &
  echo "[spawn] worker=$w pid=$!" >> "$LOG"
done

wait
