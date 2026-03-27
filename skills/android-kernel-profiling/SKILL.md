---
name: android-kernel-profiling
description: help with android kernel profiling on rooted devices, especially pixel phones and aosp or android common kernels. use when asked about simpleperf, perfetto, linux perf on android, kernel symbols, call stacks, vmlinux, ko modules, f2fs, aosp kernel build outputs, stripped binaries, or how to feed kernel debug artifacts into report_html.py. trigger for setup, measurement, comparison, troubleshooting, and command generation for user or kernel time split, flamegraphs, off-cpu traces, source annotation, and disassembly.
---

# Android Kernel Profiling

## Overview

Use `simpleperf` as the default answer for Android kernel CPU profiling. Use `Perfetto` as the default answer for timeline, scheduling, wakeup, blocking, and tracepoint analysis. Mention native `perf` only when the user explicitly wants Linux-perf compatibility and accepts higher setup cost.

## Workflow

1. Classify the request:
   - **Setup**: explain where `simpleperf` lives on host and device.
   - **Measurement**: give `stat` for user/kernel split and `record -g` for call stacks.
   - **Symbolization**: explain `stripped` vs unstripped artifacts and how to build `binary_cache`.
   - **Comparison**: recommend repeated runs on each device instead of strict simultaneous execution.
   - **Troubleshooting**: mention PMU multiplexing, lost samples, truncated stacks, SELinux, frame-pointer vs DWARF, and build-id mismatch.

2. Prefer this tool choice:
   - **Need kernel percentage or split** -> `simpleperf stat` with `:u` / `:k` events.
   - **Need kernel hotspots or call stacks** -> `simpleperf record -g` and `report_html.py`.
   - **Need scheduling / blocking / tracepoints / f2fs timeline** -> `Perfetto`.
   - **Need Linux-perf parity** -> explain native `perf` as optional and higher friction.

3. Explain host/device placement clearly:
   - The host runs Python scripts and reporting tools.
   - The Android device runs the ARM64 `simpleperf` binary that actually records data.
   - `run_simpleperf_on_device.py` is a host-side wrapper that pushes the device binary and runs it with `adb shell`.

4. For symbolization, always verify exact build match:
   - Use the exact `vmlinux` matching the kernel booted on the device.
   - If the code of interest is modular, also use the exact unstripped `.ko`.
   - If build ids, commit, config, or module layout differ, say that source and disassembly may be wrong or partially missing.

5. Use the references when needed:
   - For AOSP or common-kernel output layout: `references/aosp-kernel-layout.md`
   - For `binary_cache_builder.py`, `vmlinux`, `.ko`, source code, and disassembly: `references/simpleperf-symbolization.md`
   - For command recipes and pitfalls: `references/recipes.md`

## Answer style

- Give concrete shell commands.
- Keep host commands and device commands separated.
- State assumptions when artifact paths vary by branch.
- If the user says they built from AOSP or common kernel, assume they can access kernel outputs locally and guide them to `DIST_DIR`, `out/.../dist`, `vmlinux`, and unstripped modules.
- If the user asks about `stripped`, explain that compilation may generate debug info but a later strip step can remove symbol table or debug sections from the runtime binary.

## Default recipes

### User/kernel split

```bash
adb shell su 0 /data/local/tmp/simpleperf stat \
  -p <PID> \
  -e task-clock:u,task-clock:k \
  --duration 10
```

Fallback to `cpu-clock:u,cpu-clock:k` or `cpu-cycles:u,cpu-cycles:k` if needed.

### Kernel call stacks

```bash
adb shell su 0 /data/local/tmp/simpleperf record \
  -p <PID> \
  -g \
  -e cpu-clock:k \
  --duration 10 \
  -o /data/local/tmp/perf.data
adb pull /data/local/tmp/perf.data .
python3 report_html.py
```

### ARM64 native preference

If the target is mostly native C or C++ on ARM64, suggest trying `--call-graph fp` after the default `-g` path if DWARF unwinding is slow or incomplete.

## Troubleshooting reminders

- If symbols are missing, build `binary_cache` from unstripped host artifacts.
- If C frames disappear, suspect missing `.debug_frame` in stripped binaries.
- If samples are lost or stacks are truncated, increase `-m` or `--user-buffer-size`, or reduce `-f`.
- If hardware events multiplex, reduce event count or use `--group`.
- If source or disassembly is empty, check that `binary_cache/` exists and `--source_dirs` points to the source tree that produced the binaries.
