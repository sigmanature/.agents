---
name: sqlite-wal-repro-suite
description: End-to-end suite for reproducing SQLite WAL + wal_checkpoint load (CLI or Instrumentation app) and capturing correlated evidence (logcat/dmesg/db snapshots/perfetto). Use whenever the user mentions SQLite WAL, wal_checkpoint, sqlite corruption/SQLITE_CORRUPT, fsync IO errors, or wants a repeatable Android storage write workload on Pixel or Cuttlefish.
---

# SQLite WAL Repro Suite

This skill consolidates the WAL repro materials (app + workloads + capture scripts) into one place.

## What you get

- **Instrumentation repro app** (fscrypt app-private path semantics)
- **SQLite CLI WAL workload** (fast /data/local/tmp repro)
- **Write-load generators** (SettingsProvider and Launcher monkey)
- **Evidence collection**: perfetto (when tracefs is usable), logcat, dmesg, and DB snapshots

## Layout

Skill root: `~/.agents/skills/sqlite-wal-repro-suite/`

- `assets/sqlite_wal_checkpoint_repro_app/` (Android project)
- `scripts/`
  - `adb_helpers.sh`
  - `sqlite_wal_checkpoint_repro_and_trace.sh` (device-side sqlite3 CLI WAL workload + perfetto)
  - `sqlite_write_load_settingsprovider.sh`
  - `sqlite_write_load_launcher_monkey.sh`
  - `cf_sqlite_wal_loop.sh` (Cuttlefish adb wrapper for sqlite3 loop)
  - `walrepro_plan2_capture_tracefs.sh` (app workload + tracefs capture on a rooted device)
  - `walrepro_loop_until_detect.sh` (repeat `pm clear + capture` until first `DETECT/FAIL`)
- `references/` (how-to + theory notes)

## Quick start (CLI workload + perfetto)

Run a WAL/checkpoint loop under `/data/local/tmp` and capture perfetto (if tracefs is available):

```bash
~/.agents/skills/sqlite-wal-repro-suite/scripts/sqlite_wal_checkpoint_repro_and_trace.sh \
  --seconds 300 \
  --checkpoint TRUNCATE \
  --sync FULL
```

Artifacts land in `./sqlite_wal_repro_YYYYmmdd_HHMMSS/` (current directory).

## Instrumentation repro app (fscrypt path)

### Build APKs (host)

```bash
cd ~/.agents/skills/sqlite-wal-repro-suite/assets/sqlite_wal_checkpoint_repro_app
./gradlew :app:assembleDebug :app:assembleAndroidTest
```

### Install + run (device)

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk

adb shell am instrument -w -r \
  -e seconds 300 \
  -e checkpoint TRUNCATE \
  -e synchronous FULL \
  -e blobBytes 4096 \
  -e updatesPerTxn 200 \
  -e rows 2048 \
  -e checkEvery 1 \
  com.learnos.sqlitewalrepro.test/androidx.test.runner.AndroidJUnitRunner
```

### Checkpoint-dense mode (dedicated checkpointer thread)

Use this when you want many more real checkpoint attempts and explicit `phase=CKPT`
log rows showing SQLite's checkpoint result tuple plus DB/WAL/SHM sizes before/after:

```bash
~/.agents/skills/sqlite-wal-repro-suite/scripts/walrepro_plan2_capture_tracefs.sh \
  --serial <SERIAL> \
  --seconds 600 \
  --writers 1 \
  --readers 0 \
  --updatesPerTxn 1 \
  --blobBytes 1024 \
  --rows 256 \
  --checkpoint TRUNCATE \
  --synchronous FULL \
  --checkEvery 200 \
  --checkpointThread 1 \
  --checkpointEveryIters 1 \
  --checkpointBurst 1 \
  --checkpointSleepMs 0
```

Key effect:

- the writer produces tiny committed transactions quickly;
- a separate `wal-checkpointer` thread uses its own SQLite connection;
- `phase=CKPT` rows report `busy`, `log_frames`, `checkpointed_frames`, and
  `db/wal/shm` sizes before/after each checkpoint attempt.

### Stop-on-first-hit loop

Use this when you want unattended repeated `pm clear + capture` attempts and to stop at the first real anomaly window:

```bash
~/.agents/skills/sqlite-wal-repro-suite/scripts/walrepro_loop_until_detect.sh \
  --serial <SERIAL> \
  --base-out walrepro_loop_$(date +%Y%m%d_%H%M%S) \
  --max-attempts 0 \
  -- \
  --seconds 180 \
  --writers 1 \
  --readers 0 \
  --updatesPerTxn 4 \
  --blobBytes 1024 \
  --rows 256 \
  --maxRows 8192 \
  --updatePct 50 \
  --insertPct 25 \
  --replacePct 25 \
  --checkpoint TRUNCATE \
  --synchronous FULL \
  --checkEvery 200 \
  --patternSample 10 \
  --checkpointThread 1 \
  --checkpointEveryIters 1 \
  --checkpointBurst 1 \
  --checkpointSleepMs 0 \
  --klogTarget wal
```

Artifacts:

- loop root contains `summary.tsv` (`attempt`, `status`, `run_dir`, `marker`)
- each attempt directory is a normal `walrepro_plan2_capture_tracefs.sh` run
- the loop stops on first `phase=DETECT`, `phase=THREAD_FAIL`, or `phase=FAIL`
- default behavior runs `pm clear com.learnos.sqlitewalrepro` before each attempt; use `--no-clear` to keep warm state

### Pull DB snapshots

```bash
adb pull /sdcard/Android/data/com.learnos.sqlitewalrepro/files/wal_repro_artifacts ./wal_repro_artifacts
```

Host-side inspection caveat:

- when checking a pulled `repro.db` snapshot on the host, prefer `sqlite3 -readonly` or `file:...?...immutable=1`;
- opening a WAL-mode DB read-write on the host can create synthetic local `repro.db-wal` / `repro.db-shm` files that were not part of the original device snapshot.

### Failure timestamp (T_detect vs T_report)

Logcat tag: `WalRepro`. Each line includes `ts_mono_ns` (since-boot monotonic nanoseconds), so it aligns well with tracefs/perfetto timelines.

- `phase=DETECT detector=quick_check qc=<...>` or `phase=DETECT detector=pattern detail=<...>`:
  - **T_detect** (closest “first detection” timestamp)
- `phase=FAIL ...`:
  - **T_report** (later; after stop/join)
- `phase=SNAPSHOT ...`:
  - boundary where snapshot/copy I/O begins (treat as post-failure noise)

## Cuttlefish notes (tracefs-friendly)

If you need syscall-level traces and your physical device blocks tracefs (e.g. Magisk `CapEff=0`), prefer running the workload on Cuttlefish where `adb root` works.

Use the Cuttlefish helper workload:

```bash
~/.agents/skills/sqlite-wal-repro-suite/scripts/cf_sqlite_wal_loop.sh --run-dir <RUN_DIR> --seconds 180
```

## Tracefs enable checklist (Pixel / Magisk gotcha)

You observed the common pitfall:

- This often fails even as root:
  - `adb shell su -c "echo 1 > /sys/kernel/tracing/events/.../enable"`
- Because `>` redirection is processed by the **outer** non-root shell, before `su` runs.

Two safe ways:

### A) Interactive root shell (manual)

```sh
adb shell
su
echo 1 > /sys/kernel/tracing/events/raw_syscalls/enable
echo 1 > /sys/kernel/tracing/events/raw_syscalls/sys_enter/enable
echo 1 > /sys/kernel/tracing/events/raw_syscalls/sys_exit/enable
```

### B) Non-interactive (recommended; correct quoting)

```bash
adb shell su -c 'sh -c "echo 1 > /sys/kernel/tracing/events/raw_syscalls/sys_enter/enable"'
```

### Suggested pre-run toggles (minimal set)

1) Disable tracing + clear buffer:

- `echo 0 > /sys/kernel/tracing/tracing_on`
- `: > /sys/kernel/tracing/trace`

2) Optional: timestamp clock:

- `echo mono > /sys/kernel/tracing/trace_clock` (if present)

3) Optional: filter to the workload PID (best when workload is an app process):

- `echo <PID> > /sys/kernel/tracing/set_event_pid`

Important detail:
- `set_event_pid` matches **Linux thread IDs (TIDs)**, not the process TGID.
- Java/SQLite I/O often runs on background threads (e.g. `wal-writer-0`), so filtering only by TGID can miss the real `fdatasync()/fsync()` failure.
- In the Plan-2 script we therefore write **all** tids under `/proc/<tgid>/task/*` into `set_event_pid`.

4) Enable syscall + f2fs events of interest:

- raw syscalls:
  - `events/raw_syscalls/sys_enter/enable`
  - `events/raw_syscalls/sys_exit/enable`
- f2fs:
  - `events/f2fs/f2fs_rename_start/enable`, `f2fs_rename_end`
  - `events/f2fs/f2fs_unlink_enter/enable`, `f2fs_unlink_exit`
  - `events/f2fs/f2fs_evict_inode/enable`, `f2fs_drop_inode`
  - `events/f2fs/f2fs_file_write_iter/enable`, `f2fs_do_write_data_page`
  - `events/f2fs/f2fs_sync_file_enter/enable`, `events/f2fs/f2fs_sync_file_exit/enable` (directly correlates with `fdatasync()/fsync()` return codes)
  - `events/f2fs/f2fs_sync_dirty_inodes_enter/enable`, `events/f2fs/f2fs_sync_dirty_inodes_exit/enable` (context around checkpoint/writeback)
  - `events/f2fs/f2fs_filemap_fault/enable`, `f2fs_vm_page_mkwrite`
  - `events/f2fs/f2fs_replace_atomic_write_block/enable` (atomic-related signal)

5) Re-enable tracing:

- `echo 1 > /sys/kernel/tracing/tracing_on`

### Host-side helper (automates the above)

Use:

```bash
~/.agents/skills/sqlite-wal-repro-suite/scripts/tracefs_prep_minimal.sh --serial <SERIAL> --pid <APP_PID>
```

`walrepro_plan2_capture_tracefs.sh` also now auto-resolves the current `repro.db` / `repro.db-wal` / `repro.db-shm` inode set from the T0 fd snapshot, then best-effort arms `/sys/fs/f2fs/<dev>/klog_wb_ino` before opening the start gate.

- default target is `--klogTarget wal`
- switch to `--klogTarget db` when you specifically want checkpoint-to-main-db writeback
- artifacts include `klog_target.txt` and `dmesg_f2fs_wb_filtered.txt` in the run directory

## References

- How to run the instrumentation app: `references/sqlite_wal_repro_app.md`
- SQLite WAL checkpoint vs f2fs atomic files: `references/sqlite-wal-checkpoint-vs-f2fs-atomic-file.md`
- Anomaly-window alignment method: `references/sqlite_wal_anomaly_window_analysis.md`
