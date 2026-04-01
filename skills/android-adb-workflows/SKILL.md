---
name: android-adb-workflows
description: generate correct, runnable adb commands and host-side bash scripts for android devices (pixel and others), including monkey automation with repeatable parameters, fast package name discovery, and log/artifact collection (logcat, dumpsys, dropbox, bugreport, tombstones/anr) with root-aware handling (su -c, magisk). use when a user asks for adb/adb shell commands, android monkey tests, package names, pulling crash artifacts, or avoiding common root/quoting/host-vs-device pitfalls.
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

## Included resources
- `scripts/run_monkey_and_collect_logs.sh`
- `scripts/stop_monkey_now.sh`
- `scripts/adb_pkg.sh`
- `scripts/adb_helpers.sh` (optional helpers for scripts; safe quoting patterns)
- `references/adb_execution_reference.md`
- `references/monkey_flags.md`
