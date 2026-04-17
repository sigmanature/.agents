# Proving whether F2FS atomic-write ioctls were triggered (Android / Pixel)

Goal: produce **device-side evidence** that the F2FS atomic-write ioctl path ran:
- `F2FS_IOC_START_ATOMIC_WRITE`
- `F2FS_IOC_COMMIT_ATOMIC_WRITE`
- `F2FS_IOC_ABORT_ATOMIC_WRITE`

This is **opt-in behavior**: unless user space calls these ioctls, “normal writes” will not magically use them.

Assumptions:
- You have root on device (Magisk `su`).
- DB/files live on an F2FS mount (often `/data`).
- You can run commands via `adb`.

## 0) Preflight checks (fast)

### Confirm `/data` is F2FS

```bash
adb shell cat /proc/mounts | grep -E ' /data '
```

You want to see `f2fs` as the filesystem type for `/data`.

### Confirm root works

```bash
adb shell 'command -v su && su -c id'
```

### Confirm tracefs exists

```bash
adb shell su -c 'ls -ld /sys/kernel/tracing || ls -ld /sys/kernel/debug/tracing'
```

If neither exists, most of the tracing paths below won’t work (fallback to strace / kernel instrumentation).

## A) Best case: kernel has F2FS tracepoints

### A1) List available f2fs events

```bash
adb shell su -c 'ls -1 /sys/kernel/tracing/events/f2fs 2>/dev/null || echo "no f2fs events"'
```

### A2) Find atomic/ioctl-related events (names vary by kernel)

```bash
adb shell su -c 'sh -c "grep -R -n -E \"atomic|ioc|ioctl\" /sys/kernel/tracing/events/f2fs/*/name 2>/dev/null || true"'
```

If you see events that look like:
- `...ioc_start_atomic_write...`
- `...ioc_commit_atomic_write...`
- `...ioc_abort_atomic_write...`

then you can enable them directly.

### A3) Enable the events and capture

Replace `<EVT>` with the exact event dir name you found (under `events/f2fs/`).

```bash
adb shell su -c 'sh -c "
set -eu
T=/sys/kernel/tracing
echo 0 > $T/tracing_on
echo > $T/trace
echo 0 > $T/events/enable

# enable selected events (repeat as needed)
echo 1 > $T/events/f2fs/<EVT>/enable

echo 1 > $T/tracing_on
"'
```

In another terminal, stream the trace to host while you run your SQLite workload:

```bash
adb shell su -c 'cat /sys/kernel/tracing/trace_pipe' | tee trace_f2fs.txt
```

Stop capture with Ctrl-C, then grep:

```bash
grep -n -E 'atomic|ioc_.*atomic|ioctl' trace_f2fs.txt | head
```

Tip: the trace line already includes `comm` and `pid`, so you can usually identify which process triggered it.

## B) No F2FS tracepoints: use kprobes (still via tracefs)

This works even when `events/f2fs` is empty, as long as `kprobe_events` is writable and kallsyms are visible.

### B1) Discover candidate symbol names

```bash
adb shell su -c 'sh -c "grep -E \" f2fs_.*atomic| f2fs_ioctl$\" /proc/kallsyms | head -n 50 || true"'
```

Common targets (kernel-version dependent):
- `f2fs_ioctl` (top-level ioctl handler; best “catch-all”)
- `f2fs_ioc_start_atomic_write`
- `f2fs_ioc_commit_atomic_write`
- `f2fs_ioc_abort_atomic_write`

If you can’t see `/proc/kallsyms`, skip to **C) strace** or **D) kernel instrumentation**.

### B2) Register probes (minimum viable, no args)

```bash
adb shell su -c 'sh -c "
set -eu
T=/sys/kernel/tracing
echo 0 > $T/tracing_on
echo > $T/trace

# delete old probes if they exist (ignore errors)
echo \"-:f2fs_ioctl\"  >> $T/kprobe_events 2>/dev/null || true
echo \"-:f2fs_aw_start\" >> $T/kprobe_events 2>/dev/null || true
echo \"-:f2fs_aw_commit\" >> $T/kprobe_events 2>/dev/null || true
echo \"-:f2fs_aw_abort\"  >> $T/kprobe_events 2>/dev/null || true

# probe the top-level ioctl handler
echo \"p:f2fs_ioctl f2fs_ioctl\" >> $T/kprobe_events

# probe atomic helpers if symbols exist on this kernel (optional; may fail if missing)
echo \"p:f2fs_aw_start f2fs_ioc_start_atomic_write\"   >> $T/kprobe_events 2>/dev/null || true
echo \"p:f2fs_aw_commit f2fs_ioc_commit_atomic_write\" >> $T/kprobe_events 2>/dev/null || true
echo \"p:f2fs_aw_abort f2fs_ioc_abort_atomic_write\"   >> $T/kprobe_events 2>/dev/null || true

echo 1 > $T/events/kprobes/f2fs_ioctl/enable
echo 1 > $T/tracing_on
"'
```

Capture:

```bash
adb shell su -c 'cat /sys/kernel/tracing/trace_pipe' | tee trace_kprobes.txt
```

Interpretation:
- If you see `kprobes:f2fs_aw_start` / `...commit` / `...abort` in the trace, you’ve proven those paths ran.
- If you only see `kprobes:f2fs_ioctl`, you’ve proven “some f2fs ioctl ran”; you still need to identify *which* `cmd` (see next).

## Bonus: map `ino=<N>` from kernel logs to a file path on device

If your printk/kmsg includes `ino=<number>` (inode number), you can often resolve it to a file path:

```bash
INO=<number>
adb shell su -c "find /data -xdev -inum $INO -print -quit 2>/dev/null || echo '<not found>'"
```

Notes:
- Prefer searching under `/data` (not only `/data/user/0/...`) because SELinux may block directory traversal when you narrow the start path.
- If it returns `<not found>`, the file may have already been unlinked (deleted/renamed) and the inode number can be stale by the time you search.

### B3) (Better) Capture ioctl `cmd` from `f2fs_ioctl` (arm64: arg in x1)

This lets you map which ioctls were invoked even when the per-ioctl helper symbols aren’t visible.

```bash
adb shell su -c 'sh -c "
set -eu
T=/sys/kernel/tracing
echo 0 > $T/tracing_on
echo > $T/trace
echo \"-:f2fs_ioctl_cmd\" >> $T/kprobe_events 2>/dev/null || true
echo \"p:f2fs_ioctl_cmd f2fs_ioctl cmd=%x1\" >> $T/kprobe_events
echo 1 > $T/events/kprobes/f2fs_ioctl_cmd/enable
echo 1 > $T/tracing_on
"'
```

Then parse `cmd=0x...` values out of the trace and compare to your kernel’s `include/uapi/linux/f2fs.h`
(`F2FS_IOC_START_ATOMIC_WRITE` etc.).

## C) User-space proof: strace `ioctl()` (if available)

This proves whether the process issued the ioctl, independent of kernel tracing.

1) Find target PID (example uses a package name):

```bash
adb shell pidof <your.package.name>
```

2) Attach strace (if present on the device):

```bash
adb shell su -c 'strace -ff -tt -s 128 -e trace=ioctl -p <PID> -o /data/local/tmp/strace_ioctl'
```

3) Run your SQLite workload, then Ctrl-C and pull results:

```bash
adb pull /data/local/tmp/strace_ioctl* .
```

If your `strace` build understands F2FS ioctls, it may print symbolic names; otherwise it prints ioctl numbers, which you can map using `include/uapi/linux/f2fs.h` from the matching kernel source.

## D) Last resort: minimal kernel log instrumentation

When:
- tracefs/kprobes are disabled in the shipping kernel, or
- symbol visibility is blocked, or
- you want undeniable evidence tied to the exact handler path.

Minimal approach:
- Add `pr_debug()` (preferred) or `pr_info()` in `fs/f2fs/ioctl.c`:
  - in the `case F2FS_IOC_START_ATOMIC_WRITE:` handler (or the helper it calls)
  - and the commit/abort handlers
- Use dynamic_debug to enable only those callsites at runtime:

```bash
adb shell su -c 'mount -t debugfs none /sys/kernel/debug 2>/dev/null || true'
adb shell su -c 'sh -c "echo \"file fs/f2fs/ioctl.c +p\" > /sys/kernel/debug/dynamic_debug/control"'
```

Then reproduce and read:

```bash
adb shell su -c dmesg | tail -n 200
```

## E) SELinux blocks tracefs writes: use Perfetto (recommended on Pixel user builds)

On some production builds (Pixel user builds are a common case), even with Magisk `su` you may see:

- `/sys/kernel/tracing/kprobe_events`: `Permission denied`
- `/sys/kernel/tracing/events/.../enable`: `Permission denied`
- `getenforce` shows `Enforcing`
- `/sys/kernel/tracing` owned by `root:readtracefs`

In this situation, direct `echo 1 > .../enable` and kprobes are effectively blocked.
However, the device-side `perfetto` service can still capture ftrace events.

### E1) Capture an f2fs-focused trace while running your workload

Example: capture the key f2fs events needed to answer “did we hit atomic write internals?”:

```bash
SERIAL=<SERIAL>
TS=$(date +%Y%m%d_%H%M%S)
TRACE_DEV=/data/local/tmp/f2fs_atomic_${TS}.perfetto-trace

adb -s "$SERIAL" shell su -c "perfetto --background-wait -t 70s -b 64mb -o $TRACE_DEV \
  f2fs/f2fs_write_begin f2fs/f2fs_write_end \
  f2fs/f2fs_sync_file_enter f2fs/f2fs_sync_file_exit \
  f2fs/f2fs_replace_atomic_write_block"
```

Then, in the same window, run your workload (e.g., SettingsProvider writes / app SQLite churn).

Finally, pull the trace (use `exec-out` + `su` to avoid permission issues):

```bash
OUT=./output/f2fs_atomic_${TS}
mkdir -p "$OUT"
adb -s "$SERIAL" exec-out su -c "cat $TRACE_DEV" > "$OUT/trace.perfetto-trace"
```

### E2) Analyze on host with `trace_processor`

Download the host-side analyzer once:

```bash
curl -fsSL -o /tmp/trace_processor https://get.perfetto.dev/trace_processor
chmod +x /tmp/trace_processor
```

Check which f2fs events were captured:

```bash
TRACE=./output/f2fs_atomic_${TS}/trace.perfetto-trace
/tmp/trace_processor "$TRACE" -Q "select name, count(*) cnt from ftrace_event where name like 'f2fs_%' group by name order by cnt desc;"
```

Key decision query:

```bash
/tmp/trace_processor "$TRACE" -Q "select count(*) as cnt from ftrace_event where name='f2fs_replace_atomic_write_block';"
```

Interpretation:

- `cnt > 0`: strong evidence you exercised the **atomic-write replace/commit path** in f2fs.
- `cnt == 0`: strong evidence you **did not** commit atomic-write blocks during this run (i.e., very likely no `F2FS_IOC_*_ATOMIC_WRITE` commit happened).

Notes:
- Not all kernels expose syscall-level `sys_enter_ioctl` tracepoints; check:
  - `adb shell su -c "cat /sys/kernel/tracing/available_events | grep -i ioctl"`
  - On some builds you may only see `binder:binder_ioctl*`.
