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

### Pull DB snapshots

```bash
adb pull /sdcard/Android/data/com.learnos.sqlitewalrepro/files/wal_repro_artifacts ./wal_repro_artifacts
```

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

4) Enable syscall + f2fs events of interest:

- raw syscalls:
  - `events/raw_syscalls/sys_enter/enable`
  - `events/raw_syscalls/sys_exit/enable`
- f2fs:
  - `events/f2fs/f2fs_rename_start/enable`, `f2fs_rename_end`
  - `events/f2fs/f2fs_unlink_enter/enable`, `f2fs_unlink_exit`
  - `events/f2fs/f2fs_evict_inode/enable`, `f2fs_drop_inode`
  - `events/f2fs/f2fs_file_write_iter/enable`, `f2fs_do_write_data_page`
  - `events/f2fs/f2fs_filemap_fault/enable`, `f2fs_vm_page_mkwrite`
  - `events/f2fs/f2fs_replace_atomic_write_block/enable` (atomic-related signal)

5) Re-enable tracing:

- `echo 1 > /sys/kernel/tracing/tracing_on`

### Host-side helper (automates the above)

Use:

```bash
~/.agents/skills/sqlite-wal-repro-suite/scripts/tracefs_prep_minimal.sh --serial <SERIAL> --pid <APP_PID>
```

## References

- How to run the instrumentation app: `references/sqlite_wal_repro_app.md`
- SQLite WAL checkpoint vs f2fs atomic files: `references/sqlite-wal-checkpoint-vs-f2fs-atomic-file.md`
