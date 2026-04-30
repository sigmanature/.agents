# Kernel Stream Capture for High-Volume F2FS Klogs

## Purpose

High-volume `F2FS_WB` provenance logs can overflow the kernel ring buffer across
multiple reboot/compile pressure iterations.  Do not rely on post-hit `dmesg`
for these runs.  Start a host-side kernel stream before enabling broad suffix
or unfiltered F2FS logging.

Use:

```text
scripts/adb_grab_su.sh
```

This script keeps reconnecting across device reboots and writes one session
directory per online interval.  The kernel log stream is:

```text
<out>/<serial>/session_<timestamp>/kernel_stream.txt
```

It uses root when available and streams:

```text
cat /proc/kmsg || dmesg -w || logcat -b kernel || dmesg
```

## Capture Pattern

Example for one device:

```bash
SER=18281FDF6007HB
OUT=output/f2fs_wb_stream_$(date +%Y%m%d_%H%M%S)

SYSRQ_ENABLE=0 \
/home/nzzhao/.agents/skills/f2fs-klog-wb/scripts/adb_grab_su.sh \
  -o "$OUT" "$SER"
```

For ART pressure, start this stream first, then start the pressure loop in a
separate process.  Leave the stream running until the crash watcher has stopped
the pressure loop and the suspect files have been preserved.

## Filtering After a Hit

When a crash hit identifies a suspect file, preserve it first, then get its
inode.  Filter the streamed kernel log by that inode:

```bash
STREAM=output/f2fs_wb_stream_.../18281FDF6007HB/session_.../kernel_stream.txt
INO=24220

grep -F "ino=$INO" "$STREAM" | grep -F 'F2FS_WB' > "ino_${INO}_f2fs_wb.log"
```

Useful narrower filters:

```bash
# Only submit/write provenance rows once those stages exist.
grep -F "ino=$INO" "$STREAM" | grep -F 'stage=submit'
grep -F "ino=$INO" "$STREAM" | grep -F 'stage=node_update'

# Search for a known bad pblk neighborhood.
grep -F "ino=$INO" "$STREAM" | grep -E 'new_blkaddr=45360(58|60|62)'

# Extract a kernel timestamp range, when needed.
awk '
  match($0, /^\<[0-9]+\>\[[[:space:]]*([0-9]+\.[0-9]+)\]/, m) {
    ts = m[1] + 0
    if (ts >= 2445.0 && ts <= 2450.0) print
  }
' "$STREAM" | grep -F "ino=$INO"
```

## Workflow Notes

- For broad provenance logging, suffix filters such as `.dex` or `.vdex` may
  still be very noisy during ART full compile pressure.  Expect large streams.
- The crash-time suspect inode is known only after preservation.  Therefore the
  default flow is stream broadly, preserve immediately on hit, then filter by
  `ino=` in the saved stream.
- If the target file is hardlink-preserved, use the preserved inode for log
  filtering.  The live path may be atomically replaced after the hit.
- `dmesg -C` should not be used once the stream is running unless the experiment
  explicitly wants to discard earlier context.
