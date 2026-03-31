# F2FS PID+INO Correlation Playbook

Use this playbook when `sysrq w/t` repeatedly shows blocked tasks in:

- `folio_wait_writeback`
- `truncate_inode_pages_final`
- `f2fs_evict_inode`

and you already have `[WBDBG]` logs containing `pid=`, `comm=`, `ino=`.

## Goal

Connect two views of the same incident:

1. `sysrq` blocked-stack view: "who is blocked now" (`pid/tgid/comm` + stack)
2. `[WBDBG]` writeback view: "which inode/path was active" (`pid/comm/ino`)

## Fast workflow

1. Collect logs with `adb_grab_su.sh`.
2. Run:

```bash
/home/nzzhao/learn_os/scripts/f2fs_pid_ino_correlate.sh \
  /path/to/kernel_stream.txt 3 40
```

3. Read report in this order:
   - `blocked pid summary`
   - `blocked comm clusters`
   - `per blocked record`
   - `all blocked windows comm clusters (all pids)`
   - `focus clusters (all blocked windows)`
   - `global pid->ino`

## Interpretation rules

1. `same-pid WBDBG > 0` near blocked timestamp:
   - strong signal that blocked thread and logged write path are directly related.
2. `same-pid WBDBG == 0` for app thread:
   - common in Android; writeback may be in another thread/pool/kworker.
   - keep inode-level and stack-level evidence; do not force same-pid conclusion.
3. Thread-cluster signal is often stronger than strict same-pid:
   - for package install/update cases, watch `PackageManager*`, `android.bg`, `android.io` clusters.
   - if these clusters dominate blocked windows while blocked stack stays stable, treat as high-confidence writeback-path contention.
4. Repeated blocked stacks with identical call chain over many sysrq rounds:
   - suspect stuck writeback bit or completion gap for specific folio/inode path.

## Known pitfalls and fixes

1. `sysrq_loop.txt` shows `awk` syntax errors:
   - old script used `in` as awk variable name, which is not portable on toybox awk.
   - fix by renaming to `cap`.
2. Early-stop hash never stabilizes due high-volume noise:
   - filter `[WBDBG]` and other noisy heartbeat lines before hashing sysrq dump segments.
3. `wbdbg` parameters seem configured but behavior unchanged:
   - check `wbdbg_apply.log`; `su -c "echo ... > /sys/module/f2fs/parameters/..."` may fail with permission denied.
   - verify effective values via `cat /sys/module/f2fs/parameters/wbdbg_*`.

## Minimum evidence to keep per incident

- one `kernel_stream.txt` with `sysrq` markers
- corresponding `sysrq_loop.txt`
- `wbdbg_apply.log`
- correlation report output from `f2fs_pid_ino_correlate.sh`
