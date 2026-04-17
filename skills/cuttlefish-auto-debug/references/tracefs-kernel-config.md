# Tracefs / ftrace kernel config checklist

For Android perfetto syscall tracing through `linux.ftrace`, the target kernel needs tracing infrastructure, event tracing, syscall events, and a mounted tracefs path.

Recommended minimal config:

```text
CONFIG_TRACEPOINTS=y
CONFIG_TRACING=y
CONFIG_FTRACE=y
CONFIG_EVENT_TRACING=y
CONFIG_FTRACE_SYSCALLS=y
CONFIG_TRACEFS_FS=y
CONFIG_DYNAMIC_FTRACE=y
```

Optional but useful:

```text
CONFIG_KPROBES=y
CONFIG_KPROBE_EVENTS=y
CONFIG_FUNCTION_TRACER=y
CONFIG_FUNCTION_GRAPH_TRACER=y
CONFIG_STACKTRACE=y
```

Runtime checks:

```bash
adb shell 'zcat /proc/config.gz 2>/dev/null | egrep "CONFIG_(TRACEFS_FS|FTRACE|FTRACE_SYSCALLS|EVENT_TRACING|TRACING|TRACEPOINTS)=" || true'
adb shell 'ls -ld /sys/kernel/tracing /sys/kernel/debug/tracing 2>/dev/null || true'
adb shell 'cat /sys/kernel/tracing/available_events 2>/dev/null | head || true'
```

Important: if the running Pixel kernel lacks these configs, fixing `su`/capabilities alone will not make syscall perfetto traces work.
