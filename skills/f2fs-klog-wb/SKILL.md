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

## Workflow Contract

### Main Workflow

1. Identify the target F2FS mount, suspect path, inode, size, and whether the path is mutable or atomic-rewritten.
2. If the suspect is mutable under `/data`, preserve the current inode before analysis.
3. Configure klog filters against the preserved inode/path or live suffix, depending on the question.
4. Trigger the read/write workload and capture dmesg with a marker.
5. Parse logical page to physical block deltas and report `+1`, local `+2`, holes, and extent jumps.
6. If a preserved encrypted artifact has `+2` plus high-entropy pages, optionally test the orphan-original-block hypothesis with the raw pblk inline-crypto probe.
7. If raw pblk and neighbor-DUN probes do not recover normal plaintext, move to the `+2` high-entropy corruption matrix and plan write-time provenance instrumentation.
8. Report / handoff with artifact paths and whether preservation succeeded.

### Decision Table

| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Crash hit | any native crash, SIGSEGV, SIGILL, SIGABRT, or ART abort appears during pressure | stop the pressure loop immediately; archive tombstone/logcat; preserve `/data` files named by tombstone maps, memory-near sections, `artd` logs, or package compiled artifacts; then launch or otherwise reproduce the crashing component against the current live files before block/entropy analysis | tombstone/logcat is archived, each suspect has a preserve manifest with matching inode/sha256, and live recheck evidence says whether the current component still crashes and whether the live path still references the preserved inode | if the component still crashes, treat the preserved current inode as the active suspect and analyze it before any next pressure cycle; if the current component no longer crashes, or inode/mtime is after the crash time, report only that the original sample is likely rewritten and keep the current file as comparison evidence | block next pressure cycle |
| Preflight | suspect file is mutable `/data` state or app artifact, or may be replaced by atomic rename | run `scripts/adb_preserve_mutable_suspect_file.sh` before parsing/klog; use neutral preserve names that do not match the original basename, `.tmp`, or `.reservecopy` targets | manifest shows original inode and preserved hardlink inode match or records same-directory hardlink fallback; host archive exists | if `/data/local/tmp` hardlink fails, immediately try a neutral hidden hardlink in the original directory, then copy from the pinned hardlink; if hardlink still fails, copy/pull immediately and report failure | block klog/pattern analysis until preservation is done or failure is explicit |
| Root shell | Magisk `su` reports `option requires an argument -- c` or `/system/bin/sh: 1: parameter not set` | use `su -c '<one command string>'`; do not use `su 0 sh -c ...` in preserve scripts unless that exact device accepts it | smoke-test preserve against one small file and confirm manifest/sha256 | fall back to direct `su -c` tar/cp/pull for current suspect files | replace |
| Klog filter | live path may be rewritten after stat | prefer preserved hardlink inode for old sample; use suffix-only or restat immediately for live replacement tracking | dmesg contains marker and `F2FS_WB` rows for expected inode/path | if no rows, check whether the inode changed and rerun against preserved hardlink/current inode | branch |
| Dmesg capture | `adb shell 'su 0 dmesg'` returns empty | use `adb shell 'su 0 sh -c "dmesg"'` | dmesg file has marker and nonzero bytes | use `dmesg -w` capture or `/proc/kmsg` if available | replace |
| Orphan pblk probe | preserved encrypted `/data` artifact has local `+2` block deltas and high-entropy pages, and you need to test whether the expected `+1` physical block still contains the original ciphertext | use `/sys/fs/f2fs/<dev>/klog_wb_raw_read_{ino,lblk,pblk,radius,run}` on a kernel containing the raw-read probe; set inode, target file logical block, center pblk, and radius, then write `1` to `run` | dmesg has `F2FS_WB RAW_READ` rows with `needs_crypt=1 has_key=1 inline=1 bio_crypt=1`, plus `crc32`, byte counts, and `first32` for each candidate pblk | if `has_key=0` or `bio_crypt=0`, unlock and read/open the preserved hardlink to instantiate fscrypt info, then rerun; if still unavailable, do not interpret raw ciphertext as decrypted plaintext | branch |
| Neighbor-DUN probe | current mapped bad pblk still looks high entropy and you suspect it contains nearby page data decrypted with the wrong page index / DUN | fix the bad pblk and sweep nearby `klog_wb_raw_read_lblk` values with radius `0`; compare `max` byte count, entropy if available, and first bytes against a good reference page shape | one swept `lblk` recovers a low-entropy / structured page shape, or all swept values remain high-entropy-shaped | if all nearby DUNs remain high entropy, record that hypothesis as not supported for this sample and move to the corruption matrix | branch |
| Hypothesis matrix | orphan pblk and neighbor-DUN probes fail to recover normal plaintext from a preserved `+2` high-entropy bad region | read `references/plus2-high-entropy-corruption-matrix.md`; classify next work under page-writeback, bio-submission, or buffered-write/writeback concurrency; instrument write-time provenance before another raw-decrypt sweep | next plan names the layer, suspect ID, needed evidence, and exclusion criteria | if the evidence does not explain local `+2`, do not promote it as a leading cause; keep it as weakened/open until write-time logs support it | continue |

### Output Contract

- phase reached:
- decision path taken:
- preservation evidence:
- klog marker:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## Workflow: Raw Pblk Inline-Crypto Probe

Use this only after the suspect inode has been preserved and a map+entropy scan has identified candidate bad pages.  It is designed for inline-encrypted `/data` files: the probe submits a direct 4KB READ bio for candidate physical blocks while attaching the preserved inode's fscrypt inline crypto context for a selected file logical block (`lblk`).  It does **not** print or extract keys.

Example for Gmail VDEX page 52, where the current mapped pblk is `4536060` and the expected `+1` orphan candidate is near `4536059`:

```bash
SER=18281FDF6007HB
DEV=dm-49
SYS=/sys/fs/f2fs/$DEV

adb -s "$SER" shell 'su -c "dd if=/data/local/tmp/f2fs_preserve_18281FDF6007HB_20260428_210954/preserved_inode_24220.hardlink of=/dev/null bs=4096 skip=52 count=1 2>/dev/null || true"'

adb -s "$SER" shell 'su -c "
dmesg -C || true
SYS=/sys/fs/f2fs/dm-49
echo 1 > \$SYS/klog_wb_enable
echo 24220 > \$SYS/klog_wb_raw_read_ino
echo 52 > \$SYS/klog_wb_raw_read_lblk
echo 4536060 > \$SYS/klog_wb_raw_read_pblk
echo 3 > \$SYS/klog_wb_raw_read_radius
echo 1 > \$SYS/klog_wb_raw_read_run
sleep 0.2
dmesg
"'
```

Interpretation:

- `bio_crypt=1` means the probe attached an inline crypto context to the raw pblk READ bio.
- The center pblk should reproduce the current preserved file page when `lblk` matches the page index; this validates the probe path.
- If the expected `+1` orphan pblk, read with the same `lblk`, looks structurally valid while the current mapped pblk looks like high-entropy garbage, the orphan-original-block hypothesis gains support.
- If both current and expected/neighborhood pblks remain high-entropy or structurally invalid under the same `lblk`, there is no local evidence that the expected original plaintext is still recoverable there.

If a fixed bad pblk remains high-entropy when swept across nearby `lblk` values,
the simple wrong-DUN / neighboring-page-index hypothesis is not supported for
that sample. Move to the `+2` high-entropy corruption matrix:

- [references/plus2-high-entropy-corruption-matrix.md](references/plus2-high-entropy-corruption-matrix.md)

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
