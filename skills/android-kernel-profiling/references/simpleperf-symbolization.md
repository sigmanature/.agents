# Feeding `vmlinux` and `f2fs` symbols into `report_html.py`

## Core rule

`report_html.py` does not directly take `vmlinux` and `.ko` on the command line.
It primarily consumes:

1. `perf.data`
2. a `binary_cache/` directory containing unstripped binaries with symbols and debug info
3. optional `--source_dirs` for source annotation

So the normal workflow is:

```text
perf.data -> binary_cache_builder.py -> binary_cache/ -> report_html.py
```

## What `binary_cache_builder.py` wants

Point `-lib` at directories on the host that contain the exact unstripped artifacts from the same build:

- directory containing `vmlinux`
- directory containing `f2fs.ko` if `f2fs` is modular
- optionally other module or symbol directories from the same kernel build

It will combine artifacts pulled from the device with debug-capable binaries found on the host and populate `binary_cache/`.

## Minimal host workflow

### 1. Record on the device

Use either the AOSP simpleperf scripts or the NDK simpleperf directory on your computer.

```bash
adb shell su 0 /data/local/tmp/simpleperf record \
  -a -g -e cpu-clock:k --duration 10 \
  -o /data/local/tmp/perf.data
adb pull /data/local/tmp/perf.data .
```

### 2. Build `binary_cache/`

Assume:

- `VMLINUX_DIR` contains `vmlinux`
- `MODULE_DIR` contains `f2fs.ko`

```bash
python3 binary_cache_builder.py -i perf.data -lib "$VMLINUX_DIR,$MODULE_DIR"
```

You can pass more than one host directory in `-lib` as a comma-separated list.

If your unstripped modules were copied into `DIST_DIR/unstripped`, a common pattern is:

```bash
python3 binary_cache_builder.py -i perf.data -lib "$DIST_DIR,$DIST_DIR/unstripped"
```

### 3. Generate HTML with source and disassembly

Assume your kernel source checkout root is `KERNEL_TOP`.

```bash
python3 report_html.py \
  -i perf.data \
  --add_source_code \
  --source_dirs "$KERNEL_TOP" \
  --add_disassembly \
  --binary_filter vmlinux f2fs.ko
```

Use `--binary_filter` to keep disassembly generation focused. Without it, `--add_disassembly` can be slow.

## If `f2fs` is built into the kernel

Then there may be no `f2fs.ko`. In that case:

- only `vmlinux` is needed for kernel and built-in `f2fs` functions
- source annotation should still work if `--source_dirs` points at the kernel source tree that built `vmlinux`

Recommended command:

```bash
python3 binary_cache_builder.py -i perf.data -lib "$DIST_DIR"
python3 report_html.py \
  -i perf.data \
  --add_source_code \
  --source_dirs "$KERNEL_TOP" \
  --add_disassembly \
  --binary_filter vmlinux
```

## If `f2fs` is modular

Then use both:

- `vmlinux`
- exact `f2fs.ko`

Recommended command:

```bash
python3 binary_cache_builder.py -i perf.data -lib "$DIST_DIR,$DIST_DIR/unstripped,$MODULE_DIR"
python3 report_html.py \
  -i perf.data \
  --add_source_code \
  --source_dirs "$KERNEL_TOP" \
  --add_disassembly \
  --binary_filter vmlinux f2fs.ko
```

## What "stripped" means in practice

A stripped binary may still run perfectly, but it may be missing some or all of:

- full symbol table
- DWARF debug info
- `.debug_frame`
- line table data needed for source annotation

That means you may see:

- many `unknown` symbols
- call chains stopping at C functions
- no source tab or empty source annotation
- disassembly without useful symbol names or line mapping

## High-value checks before blaming the tools

### Check 1: exact build match

Make sure the device is running the exact kernel build that produced the `vmlinux` and `.ko` files you are feeding to `binary_cache_builder.py`.

### Check 2: `binary_cache/` exists

`report_html.py` expects a `binary_cache/` directory in the current working directory when adding source or disassembly.

### Check 3: source path is correct

`--source_dirs` should point to the kernel source checkout root, not just one leaf directory.

### Check 4: built-in vs module

If samples hit built-in `f2fs` code, filtering only on `f2fs.ko` will miss it.
If samples hit module code, filtering only on `vmlinux` will miss it.
Use both when in doubt.

### Check 5: C frame unwind issues

If call chains stop around plain C functions, suspect stripped `.debug_frame` or incomplete unwind info. On ARM64 native code, try `--call-graph fp` as a comparison run.

## A robust end-to-end example

```bash
# host side, inside simpleperf directory
adb shell su 0 /data/local/tmp/simpleperf record \
  -a -g -e cpu-clock:k --duration 15 \
  -o /data/local/tmp/perf.data

adb pull /data/local/tmp/perf.data .

DIST_DIR=/path/to/kernel/out/branch/dist
KERNEL_TOP=/path/to/kernel/checkout
MODULE_DIR=$DIST_DIR/unstripped

python3 binary_cache_builder.py -i perf.data -lib "$DIST_DIR,$MODULE_DIR"

python3 report_html.py \
  -i perf.data \
  --add_source_code \
  --source_dirs "$KERNEL_TOP" \
  --add_disassembly \
  --binary_filter vmlinux f2fs.ko
```

If `f2fs` is built-in, drop `f2fs.ko` from the filter.
