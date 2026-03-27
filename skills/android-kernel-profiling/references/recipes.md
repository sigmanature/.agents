# Command recipes and pitfalls

## Host vs device

- Run Python scripts and `report_html.py` on the computer connected to the phone.
- Run the ARM64 `simpleperf` binary on the phone.
- Use `run_simpleperf_on_device.py` if you want a host-side wrapper that pushes and runs the device binary for you.

## Quick setup paths

### From AOSP

```text
system/extras/simpleperf/scripts/
```

### From NDK

```text
<android-sdk>/ndk/<version>/simpleperf/
```

## System-wide kernel sampling

```bash
adb shell su 0 /data/local/tmp/simpleperf record \
  -a -g -e cpu-clock:k --duration 10 \
  -o /data/local/tmp/perf.data
adb pull /data/local/tmp/perf.data .
```

## Process-specific kernel sampling

```bash
PID=$(adb shell su 0 pidof <process_name> | tr -d '\r')
adb shell su 0 /data/local/tmp/simpleperf record \
  -p "$PID" -g -e cpu-clock:k --duration 10 \
  -o /data/local/tmp/perf.data
adb pull /data/local/tmp/perf.data .
```

## User vs kernel split

```bash
adb shell su 0 /data/local/tmp/simpleperf stat \
  -a -e task-clock:u,task-clock:k --duration 10
```

Fallbacks:

```bash
adb shell su 0 /data/local/tmp/simpleperf stat \
  -a -e cpu-clock:u,cpu-clock:k --duration 10

adb shell su 0 /data/local/tmp/simpleperf stat \
  -a -e cpu-cycles:u,cpu-cycles:k --duration 10
```

## Common pitfalls

### PMU multiplexing

Too many hardware events at once can produce misleading counts. Prefer a small event set or use `--group`.

### Lost samples and truncated stacks

If recording output reports lost samples or truncated stacks, increase `-m` or `--user-buffer-size`, or reduce `-f`.

### Wrong binary set

If `binary_cache/` was built from the wrong kernel build, source and disassembly can look plausible but still be wrong.

### SELinux surprises

Root is necessary for many platform-wide cases, but some workflows can still fail due to SELinux policy or context.

### Simultaneous two-device runs

Useful for operator convenience, but not required. Prefer repeated runs per device with controlled thermal state and compare median or p95.
