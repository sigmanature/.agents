# OAT/VDEX write+rename syscall capture (Android 16, low-noise)

Goal: capture the **syscall-level commit pattern** around ART compilation outputs (e.g. `.oat`, `.vdex`, `.odex`, temp files) with enough context to answer:

- Which process/thread did the writes/renames/unlinks?
- What *ordering* occurred around `write/pwrite`, `fsync/fdatasync`, `renameat2`, `unlinkat`, `close`?
- Which paths under:
  - `/data/app/*/*/oat/` (app-local oat dirs)
  - `/data/dalvik-cache/` (global cache)
- Optional: inode correlation when paths are not visible in every event.

Principles:

1) **Keep the event set tiny** (syscalls + fork/exec).  
2) Use **PID/TID filtering** early (tracefs `set_event_pid`) to reduce overhead.  
3) Use **path-prefix filtering** only where the kernel can see the user-string (`openat*`, `renameat*`, `unlinkat`).  
4) For `write/fsync` where only fd is visible, either:
   - accept limited noise and correlate via adjacent `openat/close`, or
   - use eBPF to maintain an `fd -> inode/path` map.

---

## A. Tracefs/ftrace plan (rooted device)

### A1) Choose the capture target process

Common candidates during install/dexopt:

- `dex2oat64` / `dex2oat32`
- `artd` (and its children)
- `installd`
- `odrefresh` (boot-time refresh)

### A2) Correct PID filtering: `set_event_pid` uses **TIDs**

Tracefs “pid” filters match the kernel thread id (TID), not “process id” (TGID).

- If you only echo the TGID, you capture **only the main thread**.
- To capture a whole process, you must list **all** entries in `/proc/<TGID>/task/` (each is a TID).
- If the process forks/execs children, you must also add those children’s TIDs.

Practical rule: start with “TGID + all current TIDs”, and enable `sched_process_fork` so you can notice new PIDs to add.

### A3) Minimal event set for syscall ordering

Start with:

- Process lifecycle:
  - `sched/sched_process_fork`
  - `sched/sched_process_exec`
  - `sched/sched_process_exit` (optional)
- Syscalls that matter for oat/vdex commit semantics:
  - Open/create: `syscalls/sys_enter_openat`, `syscalls/sys_exit_openat` (plus `openat2` if present)
  - Directory setup: `syscalls/sys_enter_mkdirat`
  - Writes: `syscalls/sys_enter_write`, `syscalls/sys_enter_pwrite64`
  - Sync: `syscalls/sys_enter_fsync`, `syscalls/sys_enter_fdatasync`
  - Metadata: `syscalls/sys_enter_ftruncate`, `syscalls/sys_enter_fallocate` (optional)
  - Commit/replace: `syscalls/sys_enter_renameat`, `syscalls/sys_enter_renameat2`
  - Cleanup: `syscalls/sys_enter_unlinkat`, `syscalls/sys_enter_close`

Optional (only if available; varies by kernel):

- VFS tracepoints (provide inode info; lower-level than syscalls):
  - `fs/vfs_rename`, `fs/vfs_unlink`, `fs/vfs_write`, `fs/vfs_fsync`
- F2FS tracepoints (if `/data` is F2FS; names vary by branch):
  - `f2fs/f2fs_rename`, `f2fs/f2fs_unlink`, `f2fs/f2fs_fsync`

### A4) Example tracefs setup (device-side)

Assumes a rooted shell (`adb shell` then `su -c ...`).

```sh
# 0) sanity: which events exist?
ls /sys/kernel/tracing/events/syscalls | head
ls /sys/kernel/tracing/events/sched | head

# 1) reset tracer + buffer (bigger buffer -> fewer drops)
su -c 'echo 0 > /sys/kernel/tracing/tracing_on'
su -c 'echo nop > /sys/kernel/tracing/current_tracer'
su -c 'echo 32768 > /sys/kernel/tracing/buffer_size_kb'
su -c 'echo > /sys/kernel/tracing/trace'

# 2) enable the minimal event set
for ev in \
  sched/sched_process_fork \
  sched/sched_process_exec \
  sched/sched_process_exit \
  syscalls/sys_enter_openat \
  syscalls/sys_exit_openat \
  syscalls/sys_enter_openat2 \
  syscalls/sys_enter_mkdirat \
  syscalls/sys_enter_write \
  syscalls/sys_enter_pwrite64 \
  syscalls/sys_enter_fsync \
  syscalls/sys_enter_fdatasync \
  syscalls/sys_enter_ftruncate \
  syscalls/sys_enter_fallocate \
  syscalls/sys_enter_renameat \
  syscalls/sys_enter_renameat2 \
  syscalls/sys_enter_unlinkat \
  syscalls/sys_enter_close
do
  su -c "test -d /sys/kernel/tracing/events/${ev%/*}/${ev#*/} && echo 1 > /sys/kernel/tracing/events/$ev/enable"
done

# 3) set PID filter (TIDs) for a known TGID (example: dex2oat64)
PID="$(pidof dex2oat64 2>/dev/null | awk '{print $1}')"
echo "PID=$PID"
TIDS="$(ls /proc/$PID/task | tr '\n' ' ')"
su -c "echo $TIDS > /sys/kernel/tracing/set_event_pid"

# 4) start capture
su -c 'echo 1 > /sys/kernel/tracing/tracing_on'

# 5) stream in another terminal:
su -c 'cat /sys/kernel/tracing/trace_pipe'
```

Notes:

- Some syscalls may not exist on your kernel (`openat2`, `renameat2`, `fallocate`). The loop’s `test -d ...` keeps it resilient.
- If `dex2oat64` isn’t started yet, leave pid filtering empty initially, then set it when the process appears.

### A5) Updating PID filter for forks/execs (manual loop)

If you trace `sched_process_fork`, you can watch for new child pids, then “refresh” `set_event_pid`.

Simple refresh loop:

```sh
ROOT_PID="$PID"   # TGID of dex2oat64 or artd
while true; do
  # capture all descendants (toybox ps supports -o pid,ppid on most builds)
  ALL_PIDS="$(ps -A -o PID,PPID | awk -v root="$ROOT_PID" '
    $1==root {seen[$1]=1}
    {pid=$1; ppid=$2; parent[pid]=ppid; all[pid]=1}
    END{
      # naive: include any pid whose ancestor chain reaches root
      for (p in all) {
        x=p
        while (x in parent) {
          if (x==root || parent[x]==root) {print p; break}
          x=parent[x]
        }
      }
    }' | tr '\n' ' ')"

  TIDS=""
  for p in $ALL_PIDS; do
    test -d "/proc/$p/task" || continue
    TIDS="$TIDS $(ls /proc/$p/task)"
  done
  su -c "echo $TIDS > /sys/kernel/tracing/set_event_pid"
  sleep 1
done
```

This is intentionally “dumb but effective”. If you need something cleaner, prefer the eBPF approach below.

---

## B. Perfetto plan (timeline-friendly)

Perfetto is great when you want to:

- correlate syscalls with `sched_switch`/CPU time,
- view “timeline” ordering and gaps,
- query with `trace_processor`.

For low-noise: keep the exact same minimal ftrace event list. If PID filtering can’t be applied at collection time, do it at query time.

Example “tiny syscalls trace” config (30s):

```txt
buffers: { size_kb: 16384 fill_policy: RING_BUFFER }

data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_process_fork"
      ftrace_events: "sched/sched_process_exec"
      ftrace_events: "sched/sched_process_exit"
      ftrace_events: "syscalls/sys_enter_openat"
      ftrace_events: "syscalls/sys_exit_openat"
      ftrace_events: "syscalls/sys_enter_mkdirat"
      ftrace_events: "syscalls/sys_enter_write"
      ftrace_events: "syscalls/sys_enter_pwrite64"
      ftrace_events: "syscalls/sys_enter_fsync"
      ftrace_events: "syscalls/sys_enter_fdatasync"
      ftrace_events: "syscalls/sys_enter_renameat2"
      ftrace_events: "syscalls/sys_enter_unlinkat"
      ftrace_events: "syscalls/sys_enter_close"
    }
  }
}

data_sources: {
  config {
    name: "linux.process_stats"
    process_stats_config {
      scan_all_processes_on_start: true
      record_thread_names: true
    }
  }
}

duration_ms: 30000
```

Run:

```sh
adb shell perfetto -c - --txt -o /data/misc/perfetto-traces/oat_syscalls.pb <<'EOF'
(paste config)
EOF
adb pull /data/misc/perfetto-traces/oat_syscalls.pb .
```

---

## C. Optional eBPF / bpftrace (path-prefix filters, very low noise)

Best-effort approach if your build supports it:

- Use tracepoints `sys_enter_openat/openat2`, `sys_enter_renameat2`, `sys_enter_unlinkat`.
- Read the user pointer strings (`str(args->filename)`) and filter on prefixes:
  - `/data/app/`
  - `/data/dalvik-cache/`
- Print only matching events, plus `pid/tid/comm`.

Example bpftrace one-liner (paths only; easiest win):

```sh
bpftrace -e '
tracepoint:syscalls:sys_enter_openat
{
  $p = str(args->filename);
  if ($p ~ "^/data/app/.*/oat/" || $p ~ "^/data/dalvik-cache/") {
    printf("%-16s pid=%d tid=%d openat  %s flags=0x%x mode=0%o\\n",
           comm, pid, tid, $p, args->flags, args->mode);
  }
}

tracepoint:syscalls:sys_enter_renameat2
{
  $old = str(args->oldname);
  $new = str(args->newname);
  if ($old ~ "^/data/app/.*/oat/" || $old ~ "^/data/dalvik-cache/" ||
      $new ~ "^/data/app/.*/oat/" || $new ~ "^/data/dalvik-cache/") {
    printf("%-16s pid=%d tid=%d rename  %s -> %s flags=0x%x\\n",
           comm, pid, tid, $old, $new, args->flags);
  }
}

tracepoint:syscalls:sys_enter_unlinkat
{
  $p = str(args->pathname);
  if ($p ~ "^/data/app/.*/oat/" || $p ~ "^/data/dalvik-cache/") {
    printf("%-16s pid=%d tid=%d unlink  %s dirfd=%d\\n",
           comm, pid, tid, $p, args->dirfd);
  }
}
'
```

If you need `write/fsync` too, you typically have to correlate by fd/inode; that requires more involved eBPF (mapping `fd -> struct file -> inode`). Prefer starting with the path-visible syscalls first.

---

## D. strace fallback (last resort, simplest)

If you can run `strace` (static binary) on-device, it’s the quickest way to see:

- actual user-space paths for `openat/renameat2/unlinkat`,
- ordering around `fsync` and `close`,
- per-thread split with `-ff`.

Example (attach to a running dex2oat):

```sh
PID="$(pidof dex2oat64 | awk '{print $1}')"
su -c "strace -ff -ttt -T -o /data/local/tmp/dex2oat.strace \\
  -p $PID \\
  -s 256 \\
  -e trace=openat,openat2,mkdirat,renameat,renameat2,unlinkat,linkat,write,pwrite64,fsync,fdatasync,close,ftruncate,fallocate"
adb pull /data/local/tmp/dex2oat.strace* .
```

If your strace supports it, add:

- `-y` (print fd paths where possible)
- `-yy` (more fd decoding)

Limitations:

- Higher overhead than tracepoints/eBPF.
- Needs the binary present and allowed by SELinux/policies (root helps).

