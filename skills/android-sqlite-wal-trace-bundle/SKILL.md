---
name: android-sqlite-wal-trace-bundle
description: Use when reproducing SQLite WAL + explicit checkpoint I/O via an Android instrumentation app and capturing aligned perfetto/logcat/dmesg plus db/wal/shm snapshots on a Pixel device or Android Cuttlefish.
---

# Android SQLite WAL Trace Bundle

Run the **SQLite WAL + explicit checkpoint** instrumentation repro workload (app sandbox / fscrypt path) and collect a time-aligned evidence bundle:

- Perfetto trace (`*.pftrace`) with syscall + f2fs + block events (best-effort; depends on root/capabilities)
- Logcat (`threadtime`)
- Dmesg snapshots and optional streaming (`dmesg -w`)
- DB inode snapshots for correlation (`ls -li` sampling of the app’s `databases/`)
- App-exported DB/WAL/SHM snapshots on first corruption (pulled from external storage)

This skill is intended to work on:
- **Pixel** (often `su`/Magisk root; `adb root` usually unavailable)
- **Cuttlefish** (use `adb root`; `su` often absent)

## When to Use

Use this skill when you need **one command** that:
- runs the repro app through `am instrument`
- produces a single timestamped output folder
- captures artifacts suitable for “what happened right before fsync/checkpoint?”

Do **not** use this skill when:
- you need to boot/launch Cuttlefish images (use `cuttlefish-auto-debug`)
- you need a general ADB toolbox (use `android-adb-workflows`)

## Workload Contract (defaults)

This skill assumes the debuggable instrumentation repro app:
- app package: `com.learnos.sqlitewalrepro`
- instrumentation runner: `com.learnos.sqlitewalrepro.test/androidx.test.runner.AndroidJUnitRunner`
- app-exported artifacts on device (pullable without root):
  - `/sdcard/Android/data/com.learnos.sqlitewalrepro/files/wal_repro_artifacts/`

If you have a fork with different package/runner, pass `--pkg` / `--runner` to the script.

## Quick Start

### 0) Pick device

If multiple devices are connected:

```bash
adb devices
SERIAL=<put-serial-here>
```

### 1) (Cuttlefish only) enable root adbd

```bash
adb -s "$SERIAL" root
adb -s "$SERIAL" wait-for-device
```

### 1.5) Ensure the repro app is installed (once per device)

If you have the repo checked out at `~/learn_os/android/sqlite_wal_checkpoint_repro_app/`:

```bash
cd /home/nzzhao/learn_os/android/sqlite_wal_checkpoint_repro_app
./gradlew :app:assembleDebug :app:assembleAndroidTest

adb -s "$SERIAL" install -r app/build/outputs/apk/debug/app-debug.apk
adb -s "$SERIAL" install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb -s "$SERIAL" shell pm list instrumentation | grep -i sqlitewalrepro || true
```

### 2) Run capture suite

Baseline (single writer, checkpoint every loop):

```bash
chmod +x scripts/walrepro_capture_suite.sh
./scripts/walrepro_capture_suite.sh --serial "$SERIAL" --seconds 300
```

More stress:

```bash
./scripts/walrepro_capture_suite.sh --serial "$SERIAL" \
  --seconds 600 \
  --writers 2 \
  --readers 2 \
  --checkpoint TRUNCATE \
  --synchronous FULL \
  --check-every 1
```

If you don’t have root (or perfetto fails due to capabilities), keep the run but disable perfetto/dmesg:

```bash
./scripts/walrepro_capture_suite.sh --serial "$SERIAL" --seconds 300 --no-perfetto --no-dmesg-stream --no-db-poll
```

## Outputs (evidence bundle contract)

Each run creates a host folder:

`walrepro_capture_YYYYmmdd_HHMMSS/`

Key files:
- `instrument_stdout.txt`, `instrument_stderr.txt`
- `logcat_threadtime.txt`
- `dmesg_before.txt`, `dmesg_after.txt`, `dmesg_stream.txt` (if enabled and root works)
- `db_inode.txt` and `db_dir_lsli_samples.txt` (if root works)
- `perfetto_cfg.txt` and `perfetto.pftrace` (if enabled and perfetto succeeds)
- `wal_repro_artifacts/` (if the app snapshots DB/WAL/SHM)

## How to Correlate

- Use logcat tag `WalRepro` lines containing `ts_mono_ns=...` as your “time anchor”.
- Perfetto ftrace timestamps are since-boot; `elapsedRealtimeNanos()` is also since-boot, so they align well.
- For kernel printk that references inode numbers, use `db_inode.txt` or `db_dir_lsli_samples.txt` to map inode → `repro.db` / `repro.db-wal` / `repro.db-shm`.

## Root Modes (Pixel vs Cuttlefish)

The script auto-detects a “root method”:
- `adbd` (when `adb shell id -u` is `0`, typical after `adb root` on Cuttlefish/userdebug)
- `su` (when `su -c id -u` works, typical on Pixel with Magisk)
- `none` (logcat + app artifacts only)

You can force it:

```bash
./scripts/walrepro_capture_suite.sh --serial "$SERIAL" --root-method su
./scripts/walrepro_capture_suite.sh --serial "$SERIAL" --root-method adbd
./scripts/walrepro_capture_suite.sh --serial "$SERIAL" --root-method none
```

## Troubleshooting

- Perfetto file missing: check `perfetto_stdout.txt` / `perfetto_stderr.txt`.
  - On Pixel user builds, pulling `/data/misc/perfetto-traces/...` usually needs `su` and `adb exec-out`.
  - If your root has `CapEff=0`, ftrace enable may fail; keep logcat/dmesg and consider switching to a root method with capabilities.
- Dmesg empty or permission denied: root is required (`su` or `adb root`).
- `pm clear` fails: verify `--pkg` is correct and the app is installed.
- Instrumentation can’t be found: run `adb shell pm list instrumentation | grep -i wal` and update `--runner`.

## Included scripts

- `scripts/walrepro_capture_suite.sh`: orchestrates perfetto/logcat/dmesg/inode polling + `am instrument` + artifact pulls.
