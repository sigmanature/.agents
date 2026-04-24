# Tracefs syscall capture scope for oat rewrite windows

Use this note when someone expects the tracefs capture to contain only storage-facing syscalls such
as `read`/`write`/`mmap`/`rename`/`truncate`/`unlink`, but the trace shows entries like `futex`.

## What the shipped capture script enables today

`scripts/adb_oat_rewrite_capture.sh --tracefs` currently enables:

- `sched/sched_process_fork`
- `sched/sched_process_exec`
- `raw_syscalls/sys_enter`
- `raw_syscalls/sys_exit`
- `f2fs/f2fs_rename_start`
- `f2fs/f2fs_rename_end`
- `f2fs/f2fs_unlink_enter`
- `f2fs/f2fs_unlink_exit`
- `f2fs/f2fs_sync_file_enter`
- `f2fs/f2fs_sync_file_exit`

The important point is that `raw_syscalls/sys_enter` and `raw_syscalls/sys_exit` are **whole-event**
tracepoints. Once they are enabled, every syscall issued by the traced task can appear in the trace.

## Why `futex` appears

The script narrows trace volume with `set_event_pid`, which filters by the currently interesting
task ids (dex2oat first, then the app process when retargeting succeeds). That narrows **which
task** is recorded, but it does **not** narrow **which syscall families** from that task are kept.

So if dex2oat or the app process executes:

- `futex`
- signal-related syscalls
- binder-adjacent waits
- `openat`, `mmap`, `read`, `write`, `fsync`, `renameat2`, `unlinkat`

all of those can legitimately appear in the raw trace as long as they come from the traced task.

## What is not implemented today

The shipped workflow does **not** currently install a syscall-number filter such as:

- only `read` / `write`
- only `mmap`
- only `renameat(2)` / `unlinkat`
- only `ftruncate` / `fdatasync`

That means the default trace should be interpreted as:

- pid/tid-focused
- time-window-focused
- not syscall-family-curated

## Practical guidance

- Do not treat the presence of `futex` alone as evidence of the storage fault you are hunting.
- Use `tracefs_syscall_decode.py --json` plus the timeline merge UI to make the mixed syscall stream
  readable before inferring causality.
- When you need a storage-only narrative, treat syscall-family filtering as a separate experiment
  axis. The current shipped baseline favors broader causality preservation over aggressive syscall
  reduction.

## If you want narrower syscall capture later

Some kernels expose filter files on `events/raw_syscalls/sys_enter/filter` and
`events/raw_syscalls/sys_exit/filter` that can potentially be used to filter by syscall id. That is
not part of the current shipped workflow yet, because device/kernel support and the exact id list
need to be validated per target environment before making it a reusable default.
