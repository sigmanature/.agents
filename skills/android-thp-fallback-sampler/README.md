# Android THP 16KB Fallback Reproduction Experiment

This project runs a real-world Android app launch/background workload to compare the **16KB THP enabled** case against the **4K baseline (THP disabled)**. It measures how often the kernel falls back from anonymous 16KB hugepages to 4KB pages.

The main output is `summary.md`, which gives you directly:

- Number of successful 16KB anonymous hugepage allocations
- Number of fallbacks to 4KB pages
- Fallback ratio
- Memory allocation stall and compaction event counts

## What you need

- An Android device connected via adb
- A rooted device (the script wraps root commands with `su -c` by default; if you already ran `adb root`, use `--no-use-su` instead)
- Python 3.10 or newer
- Some apps installed on the device that you want to test (uninstalled apps will be skipped automatically)

## Two-step reproduction

### 1) 4K baseline (THP disabled)

```bash
adb shell "su -c 'echo none > /sys/kernel/mm/transparent_hugepage/hugepages-16kB/enabled'"
python3 scripts/run_memstress_and_collect_logs.py \
  --serial <YOUR_DEVICE_SERIAL> \
  --from-manifest config/default_memstress_manifest.json \
  --out-dir ./output/baseline_4k
```

### 2) 16K THP (THP enabled)

```bash
adb shell "su -c 'echo always > /sys/kernel/mm/transparent_hugepage/hugepages-16kB/enabled'"
python3 scripts/run_memstress_and_collect_logs.py \
  --serial <YOUR_DEVICE_SERIAL> \
  --from-manifest config/default_memstress_manifest.json \
  --out-dir ./output/thp_16k
```

Replace `<YOUR_DEVICE_SERIAL>` with your device's adb serial, which you can get from `adb devices`.


## How to adapt it to your own device

### Replace the package list

The `packages` field in `config/default_memstress_manifest.json` is an example list. Replace it with apps actually installed on your device.

Quick way to get installed package names:

```bash
adb shell pm list packages | sed 's/package://' > my_packages.txt
```

Then modify the `packages` field in `config/default_memstress_manifest.json`, or create a new manifest file.

### Package and share the workload apps with someone else

If you want someone else to run the exact same workload, you need to give them both the scripts and the APK files for the apps listed in `config/default_memstress_manifest.json`.

1. Put all APK files into one directory, for example `apks/`.
2. Do not include internal download links or credentials in the shared package. Remove any URLs from the manifest or source files.
3. Package the directory as a zip or tar file and share it with the recipient.
4. On the recipient's side, install the APKs before running the experiment. For example, with a loop:

```bash
for apk in apks/*.apk; do
  adb install -g "$apk"
done
```

Or use `adb install-multi-package` if the Android version supports it.

> Note: Only share apps you have permission to distribute. Proprietary or third-party apps may have copyright or license restrictions.

### If you are using adb root

If you already ran `adb root`, add `--no-use-su` to skip the `su -c` wrapper:

```bash
python3 scripts/run_memstress_and_collect_logs.py \
  --serial <YOUR_DEVICE_SERIAL> \
  --from-manifest config/default_memstress_manifest.json \
  --out-dir ./output/thp_16k \
  --no-use-su
```

### Change a single parameter

For example, to use a different random seed or output directory:

```bash
python3 scripts/run_memstress_and_collect_logs.py \
  --serial <YOUR_DEVICE_SERIAL> \
  --from-manifest config/default_memstress_manifest.json \
  --seed 20260618 \
  --out-dir ./output/run_001
```

### Manifest parameters explained

`config/default_memstress_manifest.json` is the default configuration file. You normally do not need to change it, but if you want to tune the experiment, these are the fields you can edit:

| Field | Meaning |
|---|---|
| `serial` | The adb serial of the target device. The command-line `--serial` overrides this value. |
| `status` | Internal state (`pending`, `running`, `finished`). You can leave it as `pending`. |
| `config.counters` | The kernel THP stats counters to sample. The default set is `anon_fault_alloc`, `anon_fault_fallback`, `anon_fault_fallback_charge`, `split`, `swpin`, `swpout`, `zswpout`. |
| `config.interval_s` | How often, in seconds, the script reads the THP stats. Default is 60 seconds. |
| `config.use_su` | Whether to wrap root commands with `su -c`. Default is `true`. Set to `false` if you already ran `adb root`. |
| `config.no_network_check` | Whether to skip the network connectivity check. Default is `true`, so the network check is skipped. |
| `config.max_cycles` | Total number of memstress cycles. In each cycle the script launches a burst of apps. Default is 120. |
| `config.memstress.packages` | The list of package names that the workload will launch. This is the most common field to change for your own device. |
| `config.memstress.burst_size` | How many apps to launch in each cycle. Default is 4. |
| `config.memstress.hold_ms` | How long, in milliseconds, each app stays in the foreground before going home. Default is 15. |
| `config.memstress.launch_gap_ms` | Delay in milliseconds between app launches within the same cycle. Default is 15. |
| `config.memstress.cycle_sleep_ms` | Delay in milliseconds between the end of one cycle and the start of the next. Default is 1000. |
| `config.memstress.seed` | Random seed for shuffling the app order. Fixing this makes the experiment reproducible. Default is 20260617. |
| `config.memstress.mode` | `launch_only` (default) launches and holds apps. `interactive` adds a touch step; you usually do not need it. |
| `config.memstress.clear_logcat` | Whether to clear the logcat buffer before the workload starts. Default is `true`. |
| `config.buddyinfo_interval_s` | How often to sample `/proc/buddyinfo`. Set to 0 to disable. Default is 0. |
| `config.vmstat_interval_s` | How often to sample `/proc/vmstat`. Set to 0 to disable. Default is 10. |
| `packages_resolved` | Filled in automatically by the script. Maps package names to their launcher activities. |
| `samples` | Filled in automatically. Number of successful THP stats samples taken. |
| `sample_errors` | Filled in automatically. Number of failed THP stats samples. |

Full list of command-line parameters:

```bash
python3 scripts/run_memstress_and_collect_logs.py --help
```

## What output you will get

Each `--out-dir` will contain:

- `raw_samples.csv`: raw cumulative kernel counter values
- `derived.csv`: per-window deltas and the fallback ratio
- `summary.md`: the final summary report
- `run_manifest.json`: the complete parameters and runtime record of this run
- `vmstat_start.json` / `vmstat_end.json`: snapshots of `/proc/vmstat` at the start and end of the experiment
- `memstress/cycle_log.jsonl`: which apps were launched in each cycle

## Metric explanations

`summary.md` contains these metrics:

| Metric | Meaning |
|---|---|
| **anon_alloc** | Total number of successful 16KB anonymous hugepage allocations during the experiment. |
| **anon_fallback** | Total number of 16KB anonymous hugepage allocations that failed and fell back to 4KB pages. |
| **fallback_ratio** | Fallback ratio: `anon_fallback / (anon_alloc + anon_fallback)`. A higher value means large-page allocations are more likely to fail. |
| **alloc_stall** | Total number of times memory allocation stalled because of memory pressure (`allocstall_normal + allocstall_movable`). |
| **compact_stall** | Total number of times memory allocation triggered synchronous compaction to free large contiguous blocks. |


## Project file layout

```text
.
├── README.md                           # this file
├── config/
│   └── default_memstress_manifest.json # default experiment configuration
└── scripts/
    ├── run_memstress_and_collect_logs.py   # main script
    ├── derive_metrics.py                   # generates derived.csv and summary.md
    └── utils/                              # modules required by the main script
```

`tests/test_crash_signature.py` is a unit test for crash detection, and the `references/` directory contains supplementary reference documents. Neither is required to run the experiment.
