# Perfetto + tracefs runbook

## Goal

Capture a timestamp-aligned syscall, scheduler, block, and f2fs event trace from Android/Cuttlefish.

## Gate checks

```bash
adb root
adb shell 'id; cat /proc/self/status | grep CapEff || true'
adb shell 'mount -t tracefs nodev /sys/kernel/tracing 2>/dev/null || true'
adb shell 'cat /sys/kernel/tracing/available_events | head'
adb shell 'test -e /sys/kernel/tracing/events/raw_syscalls/sys_enter/enable && echo OK || echo NO'
```

## Perfetto command pattern

```bash
adb shell 'perfetto -c - --txt -o /data/misc/perfetto-traces/out.pftrace' < perfetto_cfg.txt
adb pull /data/misc/perfetto-traces/out.pftrace ./out.pftrace
```

## Minimal config

```textproto
buffers: { size_kb: 131072 fill_policy: RING_BUFFER }
data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "raw_syscalls/sys_enter"
      ftrace_events: "raw_syscalls/sys_exit"
    }
  }
}
duration_ms: 120000
```

## Failure classification

- `disabled ftrace` in perfetto logs: check tracefs and event existence first, then permissions/capabilities.
- `Permission denied` writing enable files: root/SELinux/capability problem.
- no `/sys/kernel/tracing/events`: kernel config or mount problem.
- event absent: use alternate event names or rebuild kernel with the missing tracing feature.
