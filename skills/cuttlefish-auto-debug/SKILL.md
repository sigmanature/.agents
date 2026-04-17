---
name: cuttlefish-auto-debug
description: orchestrate reproducible Android Cuttlefish debugging with adb-root, tracefs validation, perfetto/ftrace syscall capture, f2fs/block tracepoints, workload execution, and evidence bundling. Use when an agent needs to boot Cuttlefish from cvd-host_package plus aosp_cf images, validate kernel tracing support, run Android storage/SQLite workloads, and capture pftrace/logcat/dmesg/getprop artifacts.
---

# Cuttlefish Auto Debug Skill

Follow this skill when debugging Android storage/F2FS/SQLite workloads on **Cuttlefish** and you need reliable observability through **adb + tracefs + perfetto/ftrace**.

This skill is designed for the current Pixel 6 / Android 16 / F2FS large-folio investigation where the physical device can reproduce `SQLITE_IOERR_FSYNC`, but tracing on the phone is blocked by missing capabilities and possibly missing tracefs/tracing kernel config.

## Core behavior

- Prefer reproducible scripted operations over ad-hoc commands.
- Never claim "syscall trace captured" unless all of the following are true:
  1. `/sys/kernel/tracing` or `/sys/kernel/debug/tracing` exists and is readable.
  2. The requested events exist under `events/<group>/<event>/enable`.
  3. At least one requested event is enabled or accepted by perfetto.
  4. A non-empty `*.pftrace` file is pulled to the host.
- Treat Cuttlefish as an observability and iteration environment first. Do not assume it exactly matches Pixel 6 storage hardware, dm stack, inlinecrypt hardware, or f2fs mount behavior.
- Do not try to boot a Pixel 6/oriole kernel directly in x86_64 Cuttlefish. Pixel kernel artifacts and Cuttlefish kernel artifacts are different targets unless the user explicitly has an ARM64 Cuttlefish setup.
- If tracing fails, classify the failure as one of:
  - missing tracefs/tracing kernel config,
  - tracefs not mounted,
  - event absent,
  - adb/root/SELinux/capability failure,
  - perfetto/traced service failure.
- Always collect evidence alongside traces: `getprop`, `/data` mount line, `id`, `CapEff`, `dmesg`, `logcat`, tracefs probes, and the exact perfetto config used.

## Current investigation assumptions

- Physical repro device: Pixel 6 (`oriole`), Android 16 / SDK 36, user/release-keys.
- Physical `/data`: f2fs with `inlinecrypt`, `fsync_mode=nobarrier`, dm device.
- Workload: Android instrumentation app that creates high-frequency SQLite WAL transactions and `PRAGMA wal_checkpoint(TRUNCATE)` in an app-private encrypted path.
- Failure: `SQLiteDiskIOException`, `SQLITE_IOERR_FSYNC`, usually from `SQLiteDatabase.endTransaction()` on writer thread.
- Physical tracing blocker: `su` may be uid 0 but `CapEff=0000000000000000`, causing tracefs/sysfs writes and perfetto ftrace setup to fail.

## Workflow decision tree

1. Need to boot or reset Cuttlefish? Use **Boot policy**.
2. Need adb/root readiness? Use **ADB policy**.
3. Need syscall/f2fs traces? Use **Tracefs readiness** then **Perfetto capture policy**.
4. Need to run SQLite/instrumentation workload? Use **Workload policy**.
5. Need to compare with Pixel? Use **Pixel-vs-Cuttlefish caveats**.
6. Need to report completion? Use **Evidence bundle contract**.

## Boot policy

### Artifact discovery

Look for these files in the user's Android platform `out/dist` or supplied artifact directory:

- `cvd-host_package.tar.gz`
- `aosp_cf_*_img*.zip`

Use a dedicated run directory so state is isolated and reproducible:

```bash
RUN_ROOT=${RUN_ROOT:-$HOME/cf_runs}
RUN_NAME=${RUN_NAME:-pixel-fsync-$(date +%Y%m%d_%H%M%S)}
RUN_DIR="$RUN_ROOT/$RUN_NAME"
mkdir -p "$RUN_DIR"
```

### Start from local dist artifacts

```bash
cd "$RUN_DIR"
tar -xvf /path/to/cvd-host_package.tar.gz
unzip /path/to/aosp_cf_*_img*.zip
HOME="$RUN_DIR" ./bin/launch_cvd --daemon --resume=false
HOME="$RUN_DIR" ./bin/adb wait-for-device
HOME="$RUN_DIR" ./bin/adb devices
```

For the first tracing pipeline, prefer `aosp_cf_x86_64*phone*userdebug` images on an x86_64 Linux host with KVM. Only use ARM64 Cuttlefish if the user explicitly has an ARM64 Cuttlefish image and matching kernel.

### Stop and reset

```bash
HOME="$RUN_DIR" ./bin/stop_cvd || true
```

For a clean state on the next launch, use the same launch flags plus `--resume=false`.

Do not claim stop succeeded unless Cuttlefish processes for that run are gone or the next `launch_cvd --resume=false` succeeds.

## ADB policy

Prefer the adb binary from the Cuttlefish host package:

```bash
ADB="$RUN_DIR/bin/adb"
"$ADB" wait-for-device
"$ADB" root
"$ADB" wait-for-device
"$ADB" shell id
"$ADB" shell 'cat /proc/self/status | grep CapEff || true'
"$ADB" shell getprop ro.build.type
```

If multiple devices are connected, select the Cuttlefish serial explicitly:

```bash
SERIAL=$("$ADB" devices | awk '/^cvd-|^0\.0\.0\.0:|^localhost:/{print $1; exit}')
ADB_S="$ADB -s $SERIAL"
```

Never run a host-side command inside `adb shell`. Examples:

- Host-side: `adb pull`, `adb push`, `adb logcat`, `adb install`, `adb shell`.
- Device-side: `adb shell pm ...`, `adb shell am instrument ...`, `adb shell perfetto ...`.

## Tracefs readiness

### Mandatory gate before perfetto

Run:

```bash
"$ADB" shell 'ls -ld /sys/kernel/tracing /sys/kernel/debug/tracing 2>/dev/null || true'
"$ADB" shell 'mount -t tracefs nodev /sys/kernel/tracing 2>/dev/null || true'
"$ADB" shell 'ls /sys/kernel/tracing/events 2>/dev/null | head || true'
"$ADB" shell 'cat /sys/kernel/tracing/available_events 2>/dev/null | head || true'
```

Classify results:

- `/sys/kernel/tracing` missing and mount fails: likely kernel config missing, not a perfetto problem.
- `events` missing: likely `CONFIG_EVENT_TRACING`/ftrace event support missing.
- `raw_syscalls` missing but `syscalls` exists: use specific syscall events.
- paths exist but writes fail: adb/root/SELinux/capability problem.

### Required event probes

Use the helper script when available:

```bash
scripts/cf_probe_tracefs.sh --run-dir "$RUN_DIR"
```

Manual probes:

```bash
"$ADB" shell 'test -e /sys/kernel/tracing/events/raw_syscalls/sys_enter/enable && echo raw_enter=OK || echo raw_enter=NO'
"$ADB" shell 'test -e /sys/kernel/tracing/events/raw_syscalls/sys_exit/enable && echo raw_exit=OK || echo raw_exit=NO'
"$ADB" shell 'test -e /sys/kernel/tracing/events/sched/sched_switch/enable && echo sched_switch=OK || echo sched_switch=NO'
"$ADB" shell 'ls /sys/kernel/tracing/events/f2fs 2>/dev/null | head || true'
"$ADB" shell 'ls /sys/kernel/tracing/events/block 2>/dev/null | head || true'
```

## Perfetto capture policy

### Correct perfetto pattern

Use a text protobuf config on the host, stream it to device perfetto, write output on device, then pull it:

```bash
TRACE_DEV=/data/misc/perfetto-traces/cf_trace_$(date +%Y%m%d_%H%M%S).pftrace
"$ADB" shell "perfetto -c - --txt -o $TRACE_DEV" < perfetto_cfg.txt
"$ADB" pull "$TRACE_DEV" ./cf_trace.pftrace
```

### Recommended event set

Prefer:

- `sched/sched_switch`
- `raw_syscalls/sys_enter`
- `raw_syscalls/sys_exit`

Add if present:

- `syscalls/sys_enter_openat`
- `syscalls/sys_enter_write`
- `syscalls/sys_enter_pwrite64`
- `syscalls/sys_enter_fsync`
- `syscalls/sys_enter_fdatasync`
- `syscalls/sys_enter_ftruncate`
- `syscalls/sys_enter_renameat2`
- `syscalls/sys_enter_unlinkat`
- `block/block_rq_issue`
- `block/block_rq_complete`
- `f2fs/f2fs_file_write_iter`
- `f2fs/f2fs_do_write_data_page`
- `f2fs/f2fs_sync_file_enter`
- `f2fs/f2fs_sync_file_exit`
- `f2fs/f2fs_replace_atomic_write_block`

Do not include nonexistent events in the final config when using strict perfetto capture. Generate the config by probing tracefs first.

### Helper usage

```bash
scripts/cf_capture_perfetto.sh --run-dir "$RUN_DIR" --seconds 120 --tag sqlite_fsync
```

To run a workload while perfetto is recording:

```bash
scripts/cf_capture_perfetto.sh --run-dir "$RUN_DIR" --seconds 180 --tag wal_checkpoint -- \
  "$RUN_DIR/bin/adb" shell 'am instrument -w <package>/<runner>'
```

## Workload policy

### Instrumentation app workload

Use the existing Android instrumentation workload if available. Agent should not invent package names. Discover or accept from user:

```bash
"$ADB" shell pm list instrumentation
"$ADB" shell am instrument -w <target.package>/<runner.class>
```

If APK is provided:

```bash
"$ADB" install -r -g /path/to/repro.apk
"$ADB" shell pm list instrumentation | grep -i '<package-or-keyword>'
```

### SQLite CLI fallback workload

If no instrumentation APK is available and `sqlite3` exists on device, use a CLI stress workload under `/data/local/tmp`. This does not perfectly match app-private fscrypt behavior, but is useful to verify tracing and syscall sequence capture.

Use the consolidated suite: `sqlite-wal-repro-suite` (preferred).

## Evidence bundle contract

Always write artifacts into a host run output directory:

```bash
OUT="$RUN_DIR/evidence_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"
```

Collect:

```bash
"$ADB" shell getprop > "$OUT/getprop.txt"
"$ADB" shell id > "$OUT/adb_id.txt" || true
"$ADB" shell 'cat /proc/self/status | grep CapEff || true' > "$OUT/capeff.txt" || true
"$ADB" shell 'mount | grep " /data " || true' > "$OUT/mount_data.txt"
"$ADB" shell 'ls -ld /sys/kernel/tracing /sys/kernel/debug/tracing 2>/dev/null || true' > "$OUT/tracefs_paths.txt"
"$ADB" shell 'cat /sys/kernel/tracing/available_events 2>/dev/null || true' > "$OUT/available_events.txt"
"$ADB" shell 'dmesg -T 2>/dev/null || dmesg 2>/dev/null || true' > "$OUT/dmesg.txt"
"$ADB" logcat -b all -v threadtime -d > "$OUT/logcat_all_threadtime.txt" || true
```

Use helper:

```bash
scripts/cf_collect_evidence.sh --run-dir "$RUN_DIR" --tag after_repro
```

## Kernel config guidance

For syscall tracing through perfetto/ftrace, verify these are enabled in the target kernel config:

```text
CONFIG_TRACEPOINTS=y
CONFIG_TRACING=y
CONFIG_FTRACE=y
CONFIG_EVENT_TRACING=y
CONFIG_FTRACE_SYSCALLS=y
CONFIG_TRACEFS_FS=y
CONFIG_DYNAMIC_FTRACE=y
```

Useful optional debug knobs:

```text
CONFIG_KPROBES=y
CONFIG_KPROBE_EVENTS=y
CONFIG_FUNCTION_TRACER=y
CONFIG_FUNCTION_GRAPH_TRACER=y
CONFIG_STACKTRACE=y
```

For the current Pixel physical device: if the running kernel lacks tracefs/event tracing, then fixing Magisk/CapEff alone is insufficient; syscall perfetto capture still cannot work. For Pixel, add these configs to a Pixel kernel fragment. For Cuttlefish, add them to the Cuttlefish kernel target, not to the Pixel/oriole target.

## Pixel-vs-Cuttlefish caveats

Cuttlefish is ideal to recover observability and iterate quickly, but it may not reproduce a Pixel-only storage failure because:

- Pixel `/data` uses physical storage, dm, f2fs, inlinecrypt, and vendor kernel/device stack.
- Cuttlefish block devices and encryption support can differ. If inline encryption hardware is absent but the kernel has blk-crypto fallback enabled, the block layer may use software crypto fallback.
- Fallback can change I/O shape because encrypted writes may use bounce pages and different bio splitting/merging behavior.

Use Cuttlefish in two phases:

1. Capture the upper-layer syscall and scheduling sequence for the SQLite WAL/checkpoint workload.
2. If needed, customize Cuttlefish kernel/storage/mount config to approximate Pixel conditions, then compare against Pixel logs.

## Included scripts

- `scripts/cf_boot_from_dist.sh`: boot Cuttlefish from local host package and image zip.
- `scripts/cf_stop.sh`: stop one Cuttlefish run directory.
- `scripts/cf_probe_tracefs.sh`: classify tracefs/event/root readiness.
- `scripts/cf_capture_perfetto.sh`: generate event-filtered perfetto config, run capture, optionally run workload.
- `scripts/cf_collect_evidence.sh`: collect getprop/mount/dmesg/logcat/tracefs evidence.
- `scripts/cf_run_instrumentation.sh`: install optional APK and run Android instrumentation under capture-friendly logging.

References:

- `references/deployment-current-pixel-fsync.md`
- `references/perfetto-tracefs-runbook.md`
- `references/tracefs-kernel-config.md`
- `references/adb-cuttlefish-rules.md`
- `references/pixel-vs-cuttlefish-storage-caveats.md`
