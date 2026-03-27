# AOSP and Android kernel layout

## Mental model

Separate the Android platform tree from the kernel source checkout.

- The Android platform tree contains prebuilt kernel binaries, not the kernel source.
- The kernel source checkout contains the actual kernel source, build scripts, and Bazel / Kleaf tooling.

If the user says "I built from AOSP" but also mentions `common/`, interpret that as:

- platform AOSP tree for the Android image
- separate kernel checkout for `common` or Pixel kernel work

## Common places to look

### Simpleperf

If using AOSP source instead of an installed NDK, look under:

```text
system/extras/simpleperf/scripts/
```

Useful contents:

```text
system/extras/simpleperf/scripts/bin/android/arm64/simpleperf
system/extras/simpleperf/scripts/run_simpleperf_on_device.py
system/extras/simpleperf/scripts/binary_cache_builder.py
system/extras/simpleperf/scripts/report_html.py
```

### NDK install

If using Android Studio's NDK, look under:

```text
<android-sdk>/ndk/<version>/simpleperf/
```

## Kernel build outputs

### Kleaf / Bazel branches

Typical workflow:

```bash
tools/bazel run //common:kernel_aarch64_dist -- --destdir=$DIST_DIR
```

Typical expectation:

- final artifacts land in `$DIST_DIR`
- `vmlinux` is usually one of the produced debug-capable artifacts for the kernel image targets
- bootable images and modules are also placed in the distribution directory

### Legacy `build.sh` branches

Typical workflow:

```bash
BUILD_CONFIG=common/build.config.gki.aarch64 build/build.sh
```

Typical expectation:

- build output base defaults to `out/<branch>`
- distribution output defaults to `out/<branch>/dist`
- the kernel is built under a deeper out directory internally
- `DIST_DIR` is the place to check first for `Image`, `boot.img`, `vmlinux`, module archives, and optional unstripped-module output

## Module artifacts

If the target code is modular, keep the exact unstripped `.ko` files from the same build.

If the branch uses `UNSTRIPPED_MODULES`, those files may be copied into:

```text
<DIST_DIR>/unstripped/
```

This is often the easiest place to point `binary_cache_builder.py -lib`.

## Practical search checklist

When the user wants `vmlinux` or `f2fs.ko`, tell them to check in this order:

1. `$DIST_DIR/vmlinux`
2. `$DIST_DIR/unstripped/`
3. branch-specific module staging or module private output directories
4. the final installed modules directory only if they still have not found an unstripped copy

## Warning signs

- `vmlinux` exists but doesn't match the flashed kernel build -> bad symbolization
- only stripped installed modules are available -> function names may be partial and source/disassembly may be poor
- source tree path doesn't match the checked out revision that built the binaries -> source annotation may be missing or line mapping may look wrong
