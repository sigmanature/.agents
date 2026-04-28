---
name: android-adb-workflows
description: generate correct, runnable adb commands and host-side bash scripts for android devices (pixel and others), including monkey automation, package discovery, crash artifact collection, and Android ART odex/vdex triage. use when a user asks for adb/adb shell commands, dexopt/oat/vdex/oatdump/vdexdump help, ClassNotFoundException or NoClassDefFoundError triage, package names, pulling crash artifacts, or avoiding common root/quoting/host-vs-device pitfalls.
---

# Android ADB Workflows

This skill is a **router + toolkit**:

- **General ADB execution rules** (host vs device, `su -c`, quoting/redirection, root-only artifacts): see `references/adb_execution_reference.md`.
- **Monkey automation + log collection** (repeatable run, timestamped folder): use `scripts/run_monkey_and_collect_logs.sh`.
- **Package name discovery** (list/search/current foreground app): use `scripts/adb_pkg.sh`.

## Core rule (do not violate)
Do **not** blindly wrap everything in `adb shell`. Pick the correct layer:

- **Host-side adb subcommands**: `adb pull/push`, `adb logcat`, `adb bugreport`, `adb exec-out`, `adb install`, `adb uninstall`.
- **Device-side shell commands**: `adb shell pm ...`, `adb shell dumpsys ...`, `adb shell monkey ...`, `adb shell getprop ...`.
- **Root on device**: only when needed, use `adb shell su -c '<device command>'` (and `su -c 'sh -c "..."'` for pipes/redirection/globs).

When the user’s question touches root or complex quoting, **load** `references/adb_execution_reference.md` before responding.

## Output contract (what to send in chat)
Always provide:

1) A **ready-to-run** command (or short script snippet) the user can copy/paste.
2) Any **defaults** you assumed (events/throttle/seed/etc.) spelled out explicitly.
3) Where outputs land (files/folders) and what to inspect.

Avoid long explanations unless the user asks.

## Routing guide

### A) User asks to run Monkey / fuzz UI / random clicks / stress test
Use the shipped script:

```bash
chmod +x scripts/run_monkey_and_collect_logs.sh
./scripts/run_monkey_and_collect_logs.sh --package <pkg.name>
```
If one device,directly use the only device.
If multi-device, include `--serial <SERIAL>`.

**Defaults (if user didn’t specify):**
- `--events 50000`
- `--throttle 75`
- `--seed <generated and printed by script>`
- native crashes are ignored by default so monkey keeps running; pass `--abort-on-native-crash` only when you explicitly want crash-stop semantics

After generating the command, list key output files:
- `logcat_all_threadtime.txt`
- `monkey_stdout.txt`, `monkey_stderr.txt`
- `dumpsys_activity_start.txt`, `dumpsys_activity_end.txt`
- `dumpsys_meminfo_start.txt`, `dumpsys_meminfo_end.txt`
- `dumpsys_dropbox_print.txt`
- `device_artifacts/` (only if `su` available)
- `summary.txt`

If the user asks to stop monkey quickly, use the shipped stop script:

```bash
chmod +x scripts/stop_monkey_now.sh
./scripts/stop_monkey_now.sh --serial <SERIAL>
```

Notes to state explicitly:
- monkey runs on the device; unplugging USB does not reliably stop it
- `stop_monkey_now.sh` kills `com.android.commands.monkey` on device and sends `KEYCODE_HOME`
- if the user also launched a host wrapper such as `run_experiment.py`, killing device monkey stops the workload but the host wrapper may still continue sampling until its own exit logic runs

### B) User asks “how do I quickly get package names?” or run in try mode
Use `scripts/adb_pkg.sh`.

Examples:

```bash
chmod +x scripts/adb_pkg.sh
./scripts/adb_pkg.sh list
./scripts/adb_pkg.sh --filter wechat list
./scripts/adb_pkg.sh current
```
- User may explictly ask to run in try mode.
### C) User asks about pulling tombstones / ANR traces / dropbox, or “root pitfalls”
Load `references/adb_execution_reference.md` and follow its recommended patterns (especially `su -c` + `sh -c` nesting and safe artifact pulling).

### D) User asks to uninstall then reinstall an app (APK in hand)
Use the shipped reinstall helper:

```bash
chmod +x scripts/adb_reinstall_apk.sh
./scripts/adb_reinstall_apk.sh --serial <SERIAL> --package <pkg.name> --apk <path.apk> --launch
```

If the user doesn’t know `<pkg.name>`, use `scripts/adb_pkg.sh` to discover it first.

If the user asks to reinstall the common 4-app set (WeChat/UC/Douyin/Huoshan), use:

```bash
chmod +x scripts/adb_reinstall_wechat_douyin_uc_huoshan.sh
./scripts/adb_reinstall_wechat_douyin_uc_huoshan.sh --serial <SERIAL> --base-dir <DIR_WITH_APKS> --allow-downgrade --grant --launch
```

### E) User asks to locate app SQLite DB files / WAL/SHM / “where is the database?”
Use the consolidated suite: `sqlite-wal-repro-suite` (preferred).

### F) User asks what UI actions can trigger SQLite writes in third-party apps
Use the consolidated suite: `sqlite-wal-repro-suite` (preferred).

### G) User asks for a script to generate SQLite write load (Settings / Launcher)

Use the consolidated suite: `sqlite-wal-repro-suite` (preferred).

### H) User asks to reproduce SQLite WAL + checkpoint IO and capture syscall/fs traces

Use the consolidated suite: `sqlite-wal-repro-suite` (preferred).

### I) User asks to reproduce WAL corruption inside fscrypt app sandbox

Use the consolidated suite: `sqlite-wal-repro-suite` (preferred).

### J) User asks to force repeated dexopt/oat/vdex regeneration (no reboot)

Use the shipped loop script (and the reference note for caveats):

```bash
chmod +x scripts/adb_dexopt_regen_loop.sh
./scripts/adb_dexopt_regen_loop.sh --package <pkg.name> --iters 5
```

If the user doesn’t know `<pkg.name>`, discover it first with `scripts/adb_pkg.sh`:

```bash
chmod +x scripts/adb_pkg.sh
./scripts/adb_pkg.sh --filter aweme listf
./scripts/adb_pkg.sh current
```

Then point them to `references/dexopt_oat_vdex_regen.md` for:
- forcing recompiles (`pm delete-dexopt`, `pm compile -f`, `pm compile --reset`)
- stable repro defaults (`speed-profile` only) versus explicit multi-filter experiments
- profile control (`pm art clear-app-profiles`, `pm snapshot-profile`, `pm dump-profiles`)
- triggering background dexopt (`pm bg-dexopt-job`, `pm art dexopt-packages`)

### J1) User asks to triage ART ClassNotFoundException / NoClassDefFoundError from current odex/vdex artifacts

When the APK is already assumed intact, use the artifact-first triage flow:

```bash
chmod +x scripts/adb_cnfe_odex_vdex_triage.sh
./scripts/adb_cnfe_odex_vdex_triage.sh \
  --serial <SERIAL> \
  --package <pkg.name> \
  --class '<missing.class.Name>' \
  --class '<related.anchor.Class>'
```

Read `references/dex_cnfe_odex_vdex_forensics.md` for the interpretation matrix:
- `oatdump --header-only` failure or `vdexdump_min.py --json --strict` failure points to header / top-level structure breakage
- successful class probes plus page-sized zero runs in the pulled artifact point to payload zeroing
- successful headers plus non-zero mixed corruption near the suspect region point to torn / partial rewrite
- use the same reference for the `drop_caches` differential when the user wants to distinguish page-cache-sensitive failure from a stable live-artifact failure
- use the same reference for the tail-page `dd + drop_caches + F2FS klog` differential when the user needs to tell `mapped zero bytes` apart from `read-side hole zero-fill`, and to further split the hole case into `NULL_ADDR` vs `NEW_ADDR` with focused kernel logs
- use the same reference for live-artifact isolation when the user wants to rename aside the current dalvik-cache pair and see whether the package cleanly falls back to `run-from-apk`
- use the same reference for mixed old/new pair testing when the user wants to swap only one side (`dex` or `vdex`) and narrow the suspect file
- use the same reference for old/new VDEX section diffing when mixed-pair results already isolate the VDEX side; compare checksum, `verifier_deps`, and `type_lookup_table` before claiming corruption

If the user still needs to prove the APK really contains the class, start with:
- `references/dex_classnotfound_storage_triage.md`

Useful building blocks in this path:
- `scripts/extract_cnfe_classes.py` to normalize CNFE/NCDFE class names from logcat
- `scripts/run_device_oatdump.sh` for package-scoped or explicit odex/oat probes
- `scripts/vdexdump_min.py` for strict VDEX structural validation
- `scripts/page_semantics_scan.py` for page-level section mapping, sparse-page clustering, and executable-page AArch64 anomaly scans on pulled ELF artifacts
- `scripts/adb_watch_target_am_crash_vdex_tail.py` when you need a host-side watcher that waits for target-package `am_crash`, resolves the current live `base.vdex`, and reports whether the final file-valid partial page is all zero
- `xxd` or `hexdump` on the pulled artifact for raw zero-page confirmation

Escalate to the rewrite-window flow in `references/dexopt_oat_vdex_regen.md` only when the corruption seems timing-sensitive during install or compile.

### K) User asks to reproduce package oat/vdex rewrite windows and capture the syscall sequence

Use the shipped capture script:

```bash
chmod +x scripts/adb_oat_rewrite_capture.sh
./scripts/adb_oat_rewrite_capture.sh --serial <SERIAL> --package <pkg.name> --apk <path.apk>
```

Add `--tracefs` only after root is proven usable from `adb shell`:

```bash
adb shell 'su -c id'
./scripts/adb_oat_rewrite_capture.sh --serial <SERIAL> --package <pkg.name> --tracefs --post-start-open-window-sec 3
```

What it does:
- installs or updates the target APK if `--apk` is provided
- freezes one-shot invariant inputs by default via `scripts/adb_oat_invariant_freeze.sh`
- captures `pm art dump` before and after each compile
- summarizes effective filter/reason from `pm art dump`; treat this as source-of-truth for what ART actually kept
- captures artifact snapshots by default at four key phases:
  - `S0_initial_state`
  - `S1_post_compile_return`
  - `S2_settled_post_compile`
  - `S3_crash_edge_*` on the first detected package crash marker within the open-window logic
- each artifact snapshot stores the current `pm art dump`, discovered oat/vdex paths, per-file metadata, device-side `oatdump --header-only` output for `.odex/.oat`, strict host-side `vdexdump_min.py --json --strict` output for `.vdex`, and best-effort `invariant_manifest_v1.json` plus its rc/stderr sidecars
- each artifact snapshot also derives probe classes from seeded defaults plus normalized CNFE/NCDFE classes extracted from `logcat_all_threadtime.txt`, merges/dedupes them into `probe_classes_runtime.json`, `probe_classes_runtime.txt`, and `probe_classes_all.txt`, then runs `oatdump --list-classes --class-filter ... --require-match` against each captured `.odex/.oat`
- class-probe outputs are persisted per artifact as `*.class_probes.tsv` plus per-class stdout/stderr sidecars, so you can see whether a crash-signature class is still visible in the compiled artifact at `S0/S1/S2/S3`
- stable repro defaults are the historically proven Huoshan path: `speed-profile`, `reason=cmdline`, `launch_interval=3`, `foreground_hold=5`, post-compile cold starts at `0,5`
- when the goal is “launch inside a fresh compile window”, treat `--delete-dexopt` as part of the per-iteration invariant rather than an optional speed-up; otherwise a cold start can land on pre-existing oat/vdex instead of a true rewrite window
- multi-filter rewrite-window experiments still work, but are now explicit opt-in via `--filters`
- force-stops the app before each launch cycle by default, so launch pressure becomes true cold-start consumption instead of warm reuse
- relaunches the app while compile is running, then runs post-compile delayed cold starts, so you can study both rewrite and post-commit consumption windows
- when root is usable from `adb shell`, also captures top-level `dmesg_stream.txt` during the whole run plus `dmesg_after.txt` at the end, so kernel `mmap`/readahead/writeback evidence is not lost outside `trace_pipe`
- if `--tracefs` is enabled, captures `trace_pipe` across compile and post-compile cold starts; it narrows `set_event_pid` to dex2oat first and then uses best-effort app retargeting
- current trace retargeting now uses root-backed PID/TID discovery when `--tracefs` is on, because some builds allow root `tracefs` writes but hide target PIDs/TIDs from non-root `pidof` / `/proc`; if retarget still fails, the script leaves an explicit `trace_scope_warning=still_unfiltered` breadcrumb in the per-iter logs instead of silently staying at `<all>`
- the shipped tracefs preset currently enables `raw_syscalls/sys_enter`, `raw_syscalls/sys_exit`, and a small f2fs rename/unlink/sync set; narrowing is pid/tid-based via `set_event_pid`, not syscall-family based, so traced tasks can legitimately show `futex` and other non-filesystem syscalls
- the shipped script does not currently install a read/write/mmap/rename/truncate/unlink-only syscall-number filter; if you need that distinction, document it as a separate experiment axis and see `references/tracefs_syscall_scope.md`
- add `--post-start-open-window-sec <sec>` when the app crashes too fast for stable PID retargeting; this keeps the first post-launch seconds fully open and can stop tracing early on package crash markers
- use `--artifact-settle-sec <sec>` to control how long the script waits before taking the settled `S2` snapshot; pass `--no-artifact-snapshots` to disable the extra artifact dumps when you only want trace/log pressure

If `su` exists but `adb shell su -c id` fails with `Permission denied`, stop and read:
- `references/tracefs_root_diagnostics.md`
- `references/tracefs_syscall_scope.md`
- `references/oat_compile_window_coldstart.md`

### L) User asks to align offline logcat, dmesg, and tracefs timelines

Use the shipped offline merger when the user already has captured text files and needs one readable three-column forensic table (`logcat | dmesg | syscall`) over the same wall-clock window:

```bash
python3 scripts/android_timeline_merge.py \
  --logcat <logcat_threadtime.txt> \
  --dmesg <dmesg.txt> \
  --trace <trace_decoded.json> \
  --trace-anchor-monotonic <sec> \
  --trace-anchor-wall <YYYY-MM-DDTHH:MM:SS> \
  --window-start <YYYY-MM-DDTHH:MM:SS> \
  --window-end <YYYY-MM-DDTHH:MM:SS> \
  --pid <PID> \
  --path-substr <PATH_FRAGMENT>
```

Notes to state explicitly:
- trace input is aligned by one user-supplied monotonic-to-wall anchor
- `--trace` prefers `tracefs_syscall_decode.py --json` output; raw trace text is accepted but has weaker filtering fidelity
- if `--logcat` is provided without `--trace-anchor-wall`, also pass `--year <YYYY>` because threadtime lines do not include a year
- raw layer and UI layer are both available now:
  - `--raw-json-out <file>` writes the source-faithful merged dataset, including normalized fields plus preserved `raw_line` / `source_raw`
  - `--html-out <file>` writes an interactive three-column UI with per-event expand/collapse and full event JSON in each detail pane
  - stdout/table mode remains available for quick terminal inspection
- filtering is union-style across categories: for example `--pid 7029 --path-substr base.vdex` keeps pid-matched logcat rows and path-matched dmesg/syscall rows in the same aligned table
- `--pid` primarily matches pid fields; for sources that only expose thread ids in the captured record, it can also match that thread id. Use `--tid` when you need thread-only narrowing.
- use `--bucket-ms <ms>` to control row granularity; default `1000` works best when bracketed dmesg only has second precision

### M) User asks to decode offline tracefs raw syscall lines from Android arm64 traces

Use the shipped decoder when the user already has `raw_syscalls/sys_enter` / `sys_exit` text and wants readable syscall names, errno decoding, flag decoding, or best-effort fd-to-path correlation:

```bash
python3 scripts/tracefs_syscall_decode.py trace_pipe.txt
python3 scripts/tracefs_syscall_decode.py --json trace_pipe.txt
```

Notes to state explicitly:
- this is offline-only; it decodes existing trace text and does not capture new traces
- path correlation is best-effort and depends on pathname hints being present in the same trace stream
- `--json` preserves structured fields for downstream tooling

### N) User asks why Android is black-screened and suspects the display service never started

Start with `references/display_black_screen_sf_alive.md`.

Use this path when the device is black but you need to separate:
- `SurfaceFlinger` / composer never started
- panel or compositor hardware path is broken
- SurfaceFlinger is alive but composing a black frame
- `system_server` or `/data` I/O trouble left `ColorFade` / brightness stuck

State these checkpoints explicitly:
- verify `surfaceflinger`, composer, and `service check display` before claiming the display stack is down
- always take a `screencap` to distinguish “panel-only black” from “SurfaceFlinger output black”
- if `screencap` is black too, inspect `dumpsys SurfaceFlinger` for a full-screen `ColorFade` layer and `BrightnessController`
- if `dumpsys activity` / `display` hang or watchdog appears in logcat, pivot immediately to `system_server` + `dropbox` + `dmesg` and look for `FileDescriptor.sync` / `PackageInstallerService.writeSessions` / F2FS writeback stalls
- only pursue binary corruption after pulling the display ELF files and checking for full-page zero-fill or other obvious artifact damage

## Included resources
- `scripts/run_monkey_and_collect_logs.sh`
- `scripts/stop_monkey_now.sh`
- `scripts/adb_pkg.sh`
- `scripts/adb_dexopt_regen_loop.sh` (force repeated dexopt/oat/vdex regeneration without reboot)
- `scripts/adb_oat_rewrite_capture.sh` (package install + launch pressure + dexopt loop + optional tracefs capture)
- `scripts/adb_oat_tmp_watcher.sh` (watch current package install dir for `.tmp`/`.backup` and optional final oat/vdex files)
- `scripts/adb_oat_invariant_freeze.sh` (freeze invariant inputs: package path, art dump summary, selected props, APK/profile metadata)
- `scripts/adb_cnfe_odex_vdex_triage.sh` (pull live package odex/vdex, run oatdump/vdexdump probes, and scan for zero-page damage)
- `scripts/adb_watch_target_am_crash_vdex_tail.py` (watch target-package `am_crash`, resolve live `base.vdex`, and inspect whether the final file-valid partial page is all zero)
- `scripts/run_device_oatdump.sh` (device-side ART `oatdump` wrapper for a package or explicit oat path; supports `--header-only` and `--mode list-classes` with `--class-filter` / `--require-match`)
- `scripts/extract_cnfe_classes.py` (offline: extract + normalize CNFE/NCDFE class names from Android logcat for ART class-probe workflows)
- `scripts/vdexdump_min.py` (minimal host-side VDEX structural parser for strict CNFE odex/vdex triage: sections, checksums, embedded dex list)
- `scripts/pm_art_dump_summary.py` (summarize effective filter/reason/location from captured `pm art dump`)
- `scripts/oat_artifact_manifest.py` (build `invariant_manifest_v1.json` from one artifact snapshot; emphasizes stable anchors over whole-file hashes)
- `scripts/adb_reinstall_apk.sh` (uninstall + reinstall 1 APK)
- `scripts/adb_reinstall_wechat_douyin_uc_huoshan.sh` (uninstall + reinstall WeChat/UC/Douyin/Huoshan)
- `scripts/logcat_sig_diff.py` (offline: compare 2 `logcat_all.txt` files)
- `scripts/logcat_storage_triage.py` (offline: scan 1 `logcat_all.txt` for storage-ish signals + top crashes)
- `scripts/lf_unaligned_rw_smoke.sh` (large-folio + encrypted dir unaligned R/W smoke test)
- `scripts/pkgxml_install_reboot_capture.sh` (install 1 APK -> reboot -> capture pm_critical packages.xml EINVAL evidence)
- `scripts/android_timeline_merge.py` (offline: render one aligned `logcat | dmesg | syscall` table window with pid/tid/inode/path filters)
- `scripts/tracefs_syscall_decode.py` (offline: decode Android arm64 raw_syscalls enter/exit traces with errno/flag decoding + best-effort fd mapping)
- `scripts/adb_helpers.sh` (optional helpers for scripts; safe quoting patterns)
- `references/adb_execution_reference.md`
- `references/monkey_flags.md`
- `references/pm_critical_packages_xml_einval.md` (PackageManager `packages.xml` `EINVAL` triage)
- `references/large_folio_unaligned_rw_smoke.md` (how to run the unaligned R/W test)
- `references/logcat_offline_compare.md` (offline: diff two devices’ logcat captures)
- `references/dex_cnfe_odex_vdex_forensics.md` (artifact-first ART CNFE/NCDFE triage when APK integrity is already assumed)
- `references/logcat_storage_triage.md` (offline: decide “storage regression vs app/dex/classloader” quickly)
- `references/dex_classnotfound_storage_triage.md` (CNFE widespread after reboot: map crash->apk->oat/vdex + storage signals + safe recovery)
- `references/native_lib_file_inspect.md` (inspect `/data/app/.../lib/arm64/*.so` via adb/su)
- `references/app_reinstall_workflow.md` (quick uninstall/reinstall checklist)
- `references/dexopt_oat_vdex_regen.md` (deterministic dexopt/profile control + caveats)
- `references/tracefs_root_diagnostics.md` (when `su` exists but `adb shell su -c id` still fails)
- `references/tracefs_syscall_scope.md` (current tracefs event set, why `futex` appears, and what is not filtered today)
- `references/oat_compile_window_coldstart.md` (fixed `delete-dexopt -> compile -> cold-start during compile` workflow with watcher-first evidence)
