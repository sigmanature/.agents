#!/bin/bash
set -e

SERIAL="${1:-}"
ADB="adb"
if [ -n "$SERIAL" ]; then
    ADB="adb -s $SERIAL"
fi

echo "=== Setting up mmap_lock tracepoints ==="
echo "Device: ${SERIAL:-default}"

$ADB shell "su -c 'mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null || true'"

events=(
    filemap_fault_begin
    filemap_fault_wait_start
    filemap_fault_wait_end
    filemap_fault_retry
    filemap_fault_end
    vma_start_write_begin
    vma_start_write_wait_start
    vma_start_write_wait_end
    vma_start_write_done
    mmap_lock_wait_start
    mmap_lock_wait_end
    mmap_lock_hold_start
    mmap_lock_hold_end
)

for evt in "${events[@]}"; do
    $ADB shell "su -c 'echo 1 > /sys/kernel/debug/tracing/events/$evt/enable 2>/dev/null || \
        echo 1 > /sys/kernel/debug/tracing/events/mmap_lock/$evt/enable 2>/dev/null || \
        echo 1 > /sys/kernel/debug/tracing/events/filemap/$evt/enable 2>/dev/null || \
        echo 1 > /sys/kernel/debug/tracing/events/mm/$evt/enable 2>/dev/null || true'"
done

$ADB shell "su -c 'echo 0 > /sys/kernel/debug/tracing/events/sched/sched_switch/enable 2>/dev/null || true'"

$ADB shell "su -c 'echo 32768 > /sys/kernel/debug/tracing/buffer_size_kb'"

$ADB shell "su -c 'echo > /sys/kernel/debug/tracing/trace'"

$ADB shell "su -c 'echo 1 > /sys/kernel/debug/tracing/tracing_on'"

# Enable global stacktrace (per-event stacktrace files may not exist on all kernels)
$ADB shell "su -c 'echo 1 > /sys/kernel/debug/tracing/options/stacktrace 2>/dev/null || true'"

echo "=== Tracepoints enabled ==="
$ADB shell "su -c 'cat /sys/kernel/debug/tracing/trace_clock'"
$ADB shell "su -c 'cat /sys/kernel/debug/tracing/tracing_on'"

echo "=== Enabling per-event kernel stacktrace where available ==="

# Events for which we want stacktraces enabled (fallback to group paths)
stack_events=(
    vma_start_write_begin
    vma_start_write_wait_start
    vma_start_write_wait_end
    vma_start_write_done
    filemap_fault_begin
    filemap_fault_wait_start
    filemap_fault_wait_end
    filemap_fault_retry
    filemap_fault_end
    mmap_lock_wait_start
    mmap_lock_wait_end
)

for evt in "${stack_events[@]}"; do
    # Try multiple group paths like the enable loop; ignore failures if file missing
    $ADB shell "su -c 'echo 1 > /sys/kernel/debug/tracing/events/$evt/stacktrace 2>/dev/null || \
        echo 1 > /sys/kernel/debug/tracing/events/mmap_lock/$evt/stacktrace 2>/dev/null || \
        echo 1 > /sys/kernel/debug/tracing/events/filemap/$evt/stacktrace 2>/dev/null || \
        echo 1 > /sys/kernel/debug/tracing/events/mm/$evt/stacktrace 2>/dev/null || true'"
done

echo "=== Stacktrace enable attempted for key events (missing files are ignored) ==="
