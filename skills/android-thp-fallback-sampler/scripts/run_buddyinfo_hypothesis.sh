#!/usr/bin/env bash
# Each device configured independently as soon as it comes online.
# Captures initial buddyinfo at first adb contact.
# Barrier: both "ready" → simultaneously start memstress + trace.
#
# Usage: PKG_FILE=/path/to/packages.txt bash run_buddyinfo_hypothesis.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="${SCRIPT_DIR}/.."
MEMSTRESS="${SKILL_DIR}/scripts/run_memstress_and_collect_logs.py"
TRACER="${SKILL_DIR}/scripts/trace_page_alloc.py"

SERIAL_1A="${SERIAL_1A:-1A071FDF600053}"
SERIAL_21="${SERIAL_21:-21121FDF600C4G}"
CYCLES="${CYCLES:-100}"
SEED="${SEED:-12345}"
TRACE_DURATION="${TRACE_DURATION:-900}"
OUT_DIR="${OUT_DIR:-/home/nzzhao/learn_os/output/buddyinfo_split_$(date +%Y%m%d_%H%M%S)}"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -n "${PKG_FILE:-}" ] || die "PKG_FILE not set"
mkdir -p "$OUT_DIR"

READY_1A="$OUT_DIR/.ready_1A"
READY_21="$OUT_DIR/.ready_21"
rm -f "$READY_1A" "$READY_21"

echo "============================================================"
echo " buddyinfo hypothesis experiment (interactive + trace)"
echo " 1A=$SERIAL_1A  (readahead=0 ext4=0 f2fs=0)"
echo " 21=$SERIAL_21  (readahead=2 ext4=2 f2fs=2)"
echo " cycles=$CYCLES  seed=$SEED  trace=$TRACE_DURATION"s""
echo " out=$OUT_DIR"
echo "============================================================"

echo "[phase0] rebooting both devices..."
adb -s "$SERIAL_1A" reboot || true
adb -s "$SERIAL_21" reboot || true
echo "[phase0] done"

echo "[phase1] waiting for each device, configuring independently..."

snapshot_buddyinfo() {
    local serial="$1" label="$2"
    local ts out
    ts=$(date +%s)
    out="$OUT_DIR/buddyinfo_initial_${label}_${ts}.txt"
    adb -s "$serial" shell "su -c 'cat /proc/buddyinfo'" 2>/dev/null > "$out"
    if [ ! -s "$out" ]; then
        adb -s "$serial" shell "cat /proc/buddyinfo" 2>/dev/null > "$out"
    fi
    if [ -s "$out" ]; then
        echo "  [$label] initial buddyinfo saved ($(wc -l < "$out") lines)"
    else
        echo "  [$label] WARNING: could not read /proc/buddyinfo"
    fi
}

prepare_device() {
    local serial="$1" label="$2" ready_flag="$3"
    shift 3
    local sysfs_cmds=("$@")

    echo "  [$label] waiting for device..."
    adb -s "$serial" wait-for-device 2>/dev/null

    echo "  [$label] waiting for boot..."
    while true; do
        local b
        b=$(adb -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n' || echo "0")
        [ "$b" = "1" ] && break
        sleep 2
    done
    echo "  [$label] booted"

    snapshot_buddyinfo "$serial" "$label"

    echo "  [$label] sysfs config..."
    for cmd in "${sysfs_cmds[@]}"; do
        adb -s "$serial" shell "su -c '$cmd'" 2>/dev/null || true
    done
    echo "  [$label] sysfs done (${#sysfs_cmds[@]} commands)"

    echo "  [$label] waiting for network..."
    while true; do
        local r
        r=$(adb -s "$serial" shell "ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1 && echo online || echo offline" 2>/dev/null)
        [ "$r" = "online" ] && break
        sleep 3
    done
    echo "  [$label] network OK"

    touch "$ready_flag"
    echo "  [$label] READY"
}

SYSFS_1A=("echo 0 > /sys/kernel/mm/readahead/min_order")
SYSFS_1A+=("for p in /sys/fs/ext4/*/min_folio_order_cap; do echo 0 > \$p; done")
SYSFS_1A+=("for p in /sys/fs/ext4/*/max_folio_order_cap; do echo 0 > \$p; done")
SYSFS_1A+=("for p in /sys/fs/f2fs/*/min_folio_order_cap; do echo 0 > \$p; done")
SYSFS_1A+=("for p in /sys/fs/f2fs/*/max_folio_order_cap; do echo 0 > \$p; done")

prepare_device "$SERIAL_1A" "1A" "$READY_1A" "${SYSFS_1A[@]}" &
PID_1A=$!

SYSFS_21=("echo 2 > /sys/kernel/mm/readahead/min_order")
SYSFS_21+=("for p in /sys/fs/ext4/*/min_folio_order_cap; do echo 2 > \$p; done")
SYSFS_21+=("for p in /sys/fs/ext4/*/max_folio_order_cap; do echo 2 > \$p; done")
SYSFS_21+=("for p in /sys/fs/f2fs/*/min_folio_order_cap; do echo 2 > \$p; done")
SYSFS_21+=("for p in /sys/fs/f2fs/*/max_folio_order_cap; do echo 2 > \$p; done")

prepare_device "$SERIAL_21" "21" "$READY_21" "${SYSFS_21[@]}" &
PID_21=$!

echo "[phase2] waiting for both devices READY..."
while [ ! -f "$READY_1A" ] || [ ! -f "$READY_21" ]; do
    sleep 2
done
wait $PID_1A $PID_21
echo "[phase2] both devices READY — starting memstress + trace simultaneously!"

run_memstress() {
    local serial="$1" out_sub="$2"; shift 2
    python3 "$MEMSTRESS" \
        --serial "$serial" --out-dir "$OUT_DIR/$out_sub" \
        --max-cycles "$CYCLES" --mode interactive --no-crash-detect \
        --package-file "$PKG_FILE" --seed "$SEED" \
        --hold-ms 30 --launch-gap-ms 100 --cycle-sleep-ms 200 \
        --buddyinfo-interval-s 5 --buddyinfo-thp-counters split,anon_fault_alloc --vmstat-interval-s 30 --interval-s 60 \
        "$@"
}

start_trace() {
    local serial="$1" label="$2"
    python3 "$TRACER" \
        --serial "$serial" --min-order 1 --duration-s "$TRACE_DURATION" \
        --out-dir "$OUT_DIR/trace_$label" \
        > "$OUT_DIR/trace_${label}_stdout.log" 2> "$OUT_DIR/trace_${label}_stderr.log"
}

run_memstress "$SERIAL_1A" 1A \
    --readahead-min-order 0 --ext4-folio-order 0 --f2fs-max-folio-order 0 \
    > "$OUT_DIR/1A_stdout.log" 2> "$OUT_DIR/1A_stderr.log" &
PID_M1=$!

run_memstress "$SERIAL_21" 21 \
    --readahead-min-order 2 --ext4-folio-order 2 --f2fs-max-folio-order 2 \
    > "$OUT_DIR/21_stdout.log" 2> "$OUT_DIR/21_stderr.log" &
PID_M2=$!

start_trace "$SERIAL_1A" "1A" &
PID_T1=$!
start_trace "$SERIAL_21" "21" &
PID_T2=$!

echo "  memstress: 1A=$PID_M1  21=$PID_M2"
echo "  trace:     1A=$PID_T1  21=$PID_T2"
echo "[phase3] waiting for workload..."
wait $PID_M1; RC1=$?
wait $PID_M2; RC2=$?
wait $PID_T1 $PID_T2 2>/dev/null || true

echo ""
echo "============================================================"
echo " DONE  1A rc=$RC1  21 rc=$RC2"
echo " Results: $OUT_DIR"
echo "============================================================"
echo ""
echo "--- vmstat ---"
python3 -c "
import csv
for label, path in [('1A','$OUT_DIR/1A/vmstat_derived.csv'), ('21','$OUT_DIR/21/vmstat_derived.csv')]:
    try:
        with open(path) as f: rows=list(csv.DictReader(f))
        t={}
        for r in rows:
            for k,v in r.items():
                if k.startswith('d_') and v: t[k]=t.get(k,0)+int(v)
        a=t.get('d_allocstall_movable',0); p=t.get('d_pgscan_direct',0); c=t.get('d_compact_stall',0)
        print(f'  {label}: allocstall={a}  pgscan_direct={p}  compact={c}')
    except Exception as e: print(f'  {label}: {e}')
" 2>/dev/null
echo ""
echo "--- buddyinfo ---"
python3 -c "
import csv
for label, path in [('1A','$OUT_DIR/1A/buddyinfo_samples.csv'), ('21','$OUT_DIR/21/buddyinfo_samples.csv')]:
    try:
        with open(path) as f: rows=list(csv.DictReader(f))
        f,l=rows[0],rows[-1]
        print(f'  {label}: o0={f.get(\"Normal_order_0\",\"?\")}->{l.get(\"Normal_order_0\",\"?\")}  o1={f.get(\"Normal_order_1\",\"?\")}->{l.get(\"Normal_order_1\",\"?\")}  o2={f.get(\"Normal_order_2\",\"?\")}->{l.get(\"Normal_order_2\",\"?\")}')
    except Exception as e: print(f'  {label}: {e}')
" 2>/dev/null
echo ""
echo "--- initial buddyinfo ---"
for f in "$OUT_DIR"/buddyinfo_initial_*.txt; do
    [ -f "$f" ] && echo "$(basename "$f"):" && cat "$f" && echo
done
echo ""
echo "--- trace driver hits ---"
for label in 1A 21; do
    f="$OUT_DIR/trace_$label/analysis.txt"
    if [ -f "$f" ]; then
        echo "  [$label]:"
        grep -E "dma-heap|Page cache|WiFi|Slab|Video|USB|Order distribution" "$f" || true
    else
        echo "  [$label]: trace not found"
    fi
done
