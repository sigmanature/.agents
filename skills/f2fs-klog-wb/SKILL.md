---
name: f2fs-klog-wb
description: "Enable and operate the custom F2FS writeback klog system (F2FS_WB_KLOG) found in vendor-modified kernel trees. Covers the 7 sysfs nodes, detail levels, filtering by inode/suffix/index, correlation via klog_wb_seq, and how to read dmesg output. Use when asked to enable F2FS writeback tracing on a Pixel/vendor kernel, to add F2FS_WB_KLOG call sites, to correlate userspace syscalls with writeback folios, or to use the klog_wb_* sysfs interface."
---

# F2FS Writeback Klog (Custom Vendor Klog)

## Scope

This skill covers the **custom F2FS writeback klog system** (a vendor patch, NOT upstream F2FS) that allows targeted, filterable dmesg logging of writeback and read/write folio operations. It uses a `KERN_EMERG` printk path to bypass normal loglevel gating, and is controlled entirely via sysfs at runtime -- no kernel recompilation needed.

Code lives in vendor kernel trees such as:
- `$PIXEL/common/fs/f2fs/` (Pixel common kernel)

Key source locations:
- Macro: `f2fs.h` line ~53: `F2FS_WB_KLOG(sbi, subtag, fmt, ...)`
- Struct fields: `f2fs.h` lines ~1936-1943: `klog_wb_*` members in `struct f2fs_sb_info`
- Match helpers: `f2fs.h` lines ~1981-2059: `f2fs_klog_wb_match_*()` inline functions
- Sysfs attrs: `sysfs.c` lines ~185-327: show/store handlers
- Call sites: `data.c`, `file.c`, `inode.c`, `sysfs.c`

## Workflow: Enable + Observe

### Step 1: Find the sysfs mount point

```bash
ls /sys/fs/f2fs/
# Output example: dm-0  mmcblk0p3
DEV="<device_shown_above>"
```

### Step 2: Set filters (optional, narrow the scope first)

```bash
# Filter by inode number (0 = all inodes)
echo 12345 > /sys/fs/f2fs/$DEV/klog_wb_ino

# Filter by dentry suffix (empty = no filter)
echo ".vdex" > /sys/fs/f2fs/$DEV/klog_wb_suffix

# Filter by page index range (0 = disabled)
echo 0 > /sys/fs/f2fs/$DEV/klog_wb_idx_lo
echo 100 > /sys/fs/f2fs/$DEV/klog_wb_idx_hi
```

### Step 3: Choose detail level

```bash
echo 1 > /sys/fs/f2fs/$DEV/klog_wb_enable      # Turn on

# 0 = only error-class logs (when enable is set)
# 1 = + function enter/exit logs
# 2 = + per-folio sampled logs (requires klog_wb_sample > 0 for sampling)
echo 1 > /sys/fs/f2fs/$DEV/klog_wb_detail
```

### Step 4: Start capturing

```bash
dmesg -w | grep 'F2FS_WB'
```

### Step 5: Disable when done

```bash
echo 0 > /sys/fs/f2fs/$DEV/klog_wb_enable
# Reset all filters to defaults
echo "" > /sys/fs/f2fs/$DEV/klog_wb_suffix
echo 0 > /sys/fs/f2fs/$DEV/klog_wb_ino
echo 0 > /sys/fs/f2fs/$DEV/klog_wb_idx_lo
echo 0 > /sys/fs/f2fs/$DEV/klog_wb_idx_hi
```

## sysfs Nodes Reference

| Node | Type | Default | Range | Description |
|---|---|---|---|---|
| `klog_wb_enable` | u32 | 0 | 0/1 | Master switch. Must be 1 for any klog to emit. |
| `klog_wb_detail` | u32 | 0 | 0-2 | Detail tier: 0=errors, 1=+enter/exit, 2=+sampled folios. |
| `klog_wb_sample` | u32 | 0 | >=0 | Sample rate for per-folio logs. 0 disables sampling. |
| `klog_wb_ino` | u64 | 0 | any | Inode filter. 0 matches all; non-zero matches exactly this inode. |
| `klog_wb_suffix` | str | "" | <32B | Dentry name suffix filter (e.g. ".vdex"). Empty = no filter. |
| `klog_wb_idx_lo` | u64 | 0 | any | Page index lower bound. 0 = disabled. |
| `klog_wb_idx_hi` | u64 | 0 | any | Page index upper bound. 0 = disabled. |

## Output Format

Logs are emitted via `f2fs_printk()` with `KERN_EMERG` level. Format:

```
F2FS-fs (<device>): KERN_EMERG F2FS_WB <subtag> fn=<function> cpu=<cpu> pid=<pid> comm=<comm> <custom_fields>
```

Example:
```
F2FS-fs (dm-0): KERN_EMERG F2FS_WB ENTER fn=f2fs_write_cache_folios cpu=3 pid=142 comm=kworker/u8:3 ino=12345 index=0..7
```

## Using F2FS_WB_KLOG in code

Add custom log lines in vendor f2fs code:

```c
F2FS_WB_KLOG(sbi, "MY_TAG", "some_field=%d ino=%lu\n", some_value, inode->i_ino);
```

The macro auto-appends: `fn=<func> cpu=<n> pid=<pid> comm=<comm>`

### Matching logic in code

Before logging, code checks:

1. `READ_ONCE(sbi->klog_wb_enable)` must be non-zero
2. Some lines also check `klog_wb_detail >= N` (N depends on the call site)
3. `f2fs_klog_wb_match_ino(sbi, inode)` filters by inode + suffix
4. `f2fs_klog_wb_match_index(sbi, pgoff)` filters by single page index
5. `f2fs_klog_wb_match_index_range(sbi, start, nrpages)` filters by range

When reading code, look for the pattern `klog_on = READ_ONCE(sbi->klog_wb_enable) && ...` to see which conditions a particular log site requires.

## Detail Level Semantics

Detail level gates differ across call sites. In general:

- **detail >= 0**: Error-class logs always emit when `klog_wb_enable=1`
- **detail >= 1**: Function enter/exit, inode lifecycle events (write_inode, evict_inode), fsync/ftruncate syscall correlation
- **detail >= 2**: Per-folio I/O detail (write_cache_folios, readpage, readahead, write_begin/end, mmap fault, f2fs_migrate_page)

### Sampling (klog_wb_sample)

When `klog_wb_sample > 0` and `klog_wb_detail >= 2`, per-folio logs may be rate-limited by a sequence counter (`klog_wb_seq`). This prevents log flooding during high-throughput writeback while still capturing a representative sample.

### Correlation

`atomic64_t klog_wb_seq` in `sbi` provides a monotonically increasing sequence number (via `atomic64_inc_return`) for correlating related log lines across multiple function calls for the same writeback event.

## Key Call Sites by File

### data.c
- `f2fs_readpage()` / `f2fs_readpages()` -- read I/O entry
- `f2fs_write_cache_folios()` -- writeback I/O core, folio-level detail
- `f2fs_write_begin()` / `f2fs_write_end()` -- write path enter/exit
- `f2fs_writepages()` -- writeback entry
- `f2fs_readahead` -- readahead I/O
- `f2fs_migrate_page()` -- page migration
- `f2fs_do_write_data_page()` -- data page write

### file.c
- `f2fs_file_write_iter()` -- write syscall correlation
- `f2fs_fsync()` -- fsync syscall correlation + index filter
- `f2fs_file_fallocate()` -- fallocate correlation
- `f2fs_vm_page_mkwrite()` -- mmap write fault
- `f2fs_truncate()` -- truncate correlation

### inode.c
- `f2fs_write_inode()` -- write_inode correlation
- `f2fs_evict_inode()` -- inode eviction

## Troubleshooting

### No logs appear
1. Verify `klog_wb_enable` is 1: `cat /sys/fs/f2fs/$DEV/klog_wb_enable`
2. Check if filters are too restrictive: reset all to defaults (0 or empty)
3. Verify the device is actually mounted as f2fs: `mount | grep f2fs`
4. Check dmesg buffer isn't wrapping: `dmesg -T | tail -50`

### Log flooding
- Lower `klog_wb_detail` to 0 or 1
- Set `klog_wb_ino` to a specific inode
- Set `klog_wb_suffix` to target specific file types
- Set `klog_wb_sample` to limit per-folio log rate

### "Invalid argument" on store
- `klog_wb_detail` must be 0, 1, or 2
- `klog_wb_suffix` must be < 32 characters
