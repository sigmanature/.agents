# F2FS writeback KV logs (inode/range filtered) for corruption debugging

Use this when you need to correlate:
- userspace IO sequences (`pwrite64`, `fsync`, `fdatasync`, `ftruncate`, `renameat2`)
- with kernel writeback selection (`write_cache_folios` / `write_cache_pages`)

Goal: **high signal, low noise** logs that can survive concurrent system load.

## Design rules

- Prefer **table-friendly** single-line `k=v` logs with a stable tag prefix.
- Always include:
  - actor: `pid`, `comm`, `cpu`
  - object id: `ino` (and `index` for folio/page-level)
  - correlation: `seq` (monotonic per-sbi counter) and `fn`
- Avoid log explosion:
  - log entry/exit only by default
  - log per-folio only with sampling and index-range filters
  - print only on “skip/error” paths first

## Pixel common kernel integration (recommended knobs)

When instrumenting Pixel common `fs/f2fs/data.c:f2fs_write_cache_folios`, add:
- entry/exit summary logs (once per call)
- logs on skip paths:
  - `mapping_mismatch` (truncate / inode switch / invalidation races)
  - `retry_clean` (writeback retry but folio already clean)
  - `beyond_eof` (writeback sees folio beyond `i_size`)
- logs on error paths:
  - `write_single` returned error

Prefer **runtime sysfs filters** on `struct f2fs_sb_info`, exposed under:
`/sys/fs/f2fs/<s_id>/...`

Suggested sysfs controls:
- `klog_wb_enable` (0/1)
- `klog_wb_detail` (0 errors-only, 1 + enter/exit, 2 + sampled folios)
- `klog_wb_sample` (N; 0 disables per-folio logs)
- `klog_wb_ino` (0=all, else only this inode)
- `klog_wb_idx_lo`, `klog_wb_idx_hi` (0/0 disables range filter)

## Usage (device-side)

1) Find your F2FS instance id (example: `userdata`):

```sh
adb shell cat /proc/mounts | grep -E ' /data '
```

2) Configure filters (example: only one DB inode, only page indexes 10000..12000):

```sh
S_ID=userdata
INO=40974
adb shell su -c "echo 1 > /sys/fs/f2fs/$S_ID/klog_wb_enable"
adb shell su -c "echo 1 > /sys/fs/f2fs/$S_ID/klog_wb_detail"
adb shell su -c "echo $INO > /sys/fs/f2fs/$S_ID/klog_wb_ino"
adb shell su -c "echo 10000 > /sys/fs/f2fs/$S_ID/klog_wb_idx_lo"
adb shell su -c "echo 12000 > /sys/fs/f2fs/$S_ID/klog_wb_idx_hi"
```

Or use the helper script:

```sh
./scripts/set_f2fs_wb_klog_filters.sh --sid userdata --enable 1 --detail 1 --ino 40974 --idx-lo 10000 --idx-hi 12000 --print-adb
```

3) Grep logs as a table:

```sh
adb shell su -c 'dmesg -T | grep -F "F2FS_WB" | tail -n 200'
```

If you saved logs to a file, parse with the shipped query helper:
- `scripts/kernel_log_kv_query.py` (in this skill)

## Health rules (interpretation)

Healthy:
- `ENTER`/`EXIT` pairs have matching `seq` for the same inode.
- `skip_*` counts are near zero under steady-state writeback.
- `write_single_err` remains zero.

Suspicious:
- many `mapping_mismatch` or `beyond_eof` skips correlated with checkpoint/truncate load
- `write_single` errors (especially `-EFSCORRUPTED`, `-EIO`)
- lots of `retry_clean` (timing/race signals) combined with corruption onset
