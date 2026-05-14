# F2FS Writeback Klog (Custom Vendor Klog)

## Role

This is a backend reference for `kernel-log-instrumentor`, not a separate skill.

Default reading order:

1. decide in `kernel-log-instrumentor` whether the case is `klog-first`
2. if the chosen method is F2FS vendor klog, use this document for the backend details

## Scope

This reference covers the **custom F2FS writeback klog system** (a vendor patch, NOT upstream F2FS) that allows targeted, filterable dmesg logging of writeback and read/write folio operations. It uses a `KERN_EMERG` printk path to bypass normal loglevel gating, and is controlled entirely via sysfs at runtime -- no kernel recompilation needed.

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
2. Before long-running pressure, prelink likely mutable suspects by hardlinking current fs-verity and APK/DM/VDEX/ODEX/ART inodes, then repeat the prelink scan after boot/unlock, after full compile, and at crash-hit time.
3. If a specific suspect is mutable under `/data`, preserve the current inode before analysis.
4. Configure klog filters against the preserved inode/path or live suffix, depending on the question.
5. Trigger the read/write workload and capture dmesg with a marker; when chasing fs-verity state-file corruption, run the live inode watcher against the dmesg stream so visible `/data` inodes are preserved before atomic unlink/rename can hide them.
6. Parse logical page to physical block deltas and report `+1`, local `+2`, holes, and extent jumps.
7. If a preserved encrypted artifact has `+2` plus high-entropy pages, optionally test the orphan-original-block hypothesis with the raw pblk inline-crypto probe.
8. If raw pblk and neighbor-DUN probes do not recover normal plaintext, move to the `+2` high-entropy corruption matrix and plan write-time provenance instrumentation.
9. Report / handoff with artifact paths and whether preservation succeeded.

### Decision Table

| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Post-flash smoke | you rebuilt/flashed a new kernel and plan to use `WBIT` / `writeback_iter` logs as evidence | before pressure or deadlock analysis, configure broad filters (`ino=0`, empty suffix, `idx_lo=0`, `idx_hi=0`, `detail>=1`, `enable=1`) and trigger a small dirty+fsync/msync workload on `/data`; if the run depends on a newly added stage or callsite, smoke-test that exact stage string, not just generic `WBIT` emission | current run's `dmesg` contains `F2FS_WB WBIT`, and when a new stage is required the smoke workload also emits that exact stage string under the boot being analyzed | if earlier runs on the same harness emitted generic `WBIT` but the current run emits zero rows for the newly added stage while adjacent old `WBIT` stages still appear, do not treat the absence as path evidence; first suspect a flashed-kernel mismatch or a build missing that exact callsite in the running image | block / replace |
| Reboot-pressure dmesg capture | a reconnecting `dmesg -w` watcher is used across reboot loops and later analysis needs exact deadlock / WBIT alignment | write a host boot marker containing `boot_id` and current iteration before each `dmesg -w` session, and keep a per-boot stream file under a stable directory such as `dmesg_boots/`; analyze deadlock windows only within one boot slice, not the undifferentiated aggregate stream | aggregate stream shows explicit host boot markers, `dmesg_boots/` contains a file for the boot under review, and the deadlock / `WBIT` evidence compared in analysis comes from the same `boot_id` | if deadlock stacks and `WBIT` rows are only available in mixed multi-boot aggregate logs, treat the comparison as invalid until the run is re-sliced by boot boundary | block |
| Crash hit | any native crash, SIGSEGV, SIGILL, SIGABRT, or ART abort appears during pressure | stop the pressure loop immediately; archive tombstone/logcat; preserve `/data` files named by tombstone maps, memory-near sections, `artd` logs, or package compiled artifacts; then launch or otherwise reproduce the crashing component against the current live files before block/entropy analysis | tombstone/logcat is archived, each suspect has a preserve manifest with matching inode/sha256, and live recheck evidence says whether the current component still crashes and whether the live path still references the preserved inode | if the component still crashes, treat the preserved current inode as the active suspect and analyze it before any next pressure cycle; if the current component no longer crashes, or inode/mtime is after the crash time, report only that the original sample is likely rewritten and keep the current file as comparison evidence | block next pressure cycle |
| Compile/sync timeout under pressure | `cmd package compile ...`, package-private oat rebuild, or `sync` exceeds the harness timeout during reboot pressure | treat timeout as a deadlock hit, not as a soft failure; stop extra churn, snapshot `dmesg`, trigger `sysrq w` and `sysrq t`, dump `logcat` and `ps`, record `boot_id`, and do not issue the next reboot before these artifacts are written | run output contains a deadlock-hit record, a dedicated artifact directory for that timeout label, and bounded `dmesg_after_timeout` plus `dmesg_after_sysrq_{w,t}` snapshots from the same boot | if the harness merely logs `timeout` and continues to the next reboot, the run is workflow-invalid for deadlock analysis because it destroyed the live hang context | block next reboot |
| Pre-pressure preserve | reboot/dexopt/fs-verity pressure may atomically replace or delete suspect files before crash-time preservation can run | run `scripts/adb_prelink_suspect_files.sh --serial <SERIAL> --phase <PHASE> --out <OUT>` before delete/reboot, after boot/unlock, after full app compile, and immediately on crash hit; keep this as device-side hardlinks only, not full host copies | `prelink_manifest.tsv` lists candidate path, inode, size, attr, and either `central_status=ok` or `parent_status=ok`; compare later tombstone/logcat live path inode against the prelink manifest | if only a same-directory parent hardlink is possible, record that it may still be removed by recursive package deletion; if a path's inode changes after atomic rename, require the next phase's prelink manifest before claiming the new bad inode was preserved | block / branch |
| Bootloop fs-verity | device stays at boot animation with adb available, `sys.boot_completed` empty, and logcat shows `system_server` EIO while dmesg reports `fs-verity ... FILE CORRUPTED` | stop pressure; archive logcat/dmesg; run `lsattr` on `/data/system` and the dmesg-mapped inode paths; map dmesg inode numbers with `find /data -xdev -inum`; immediately preserve the mapped files and their `.reservecopy` partners by hardlink before reboot/recovery | `lsattr` shows `V`; dmesg inode maps to a concrete path; `dd` of the path and preserved hardlink fails at the same `pos`/page with `EIO`; preserve manifest shows the original inode and hardlink inode match | if current visible `/data/system` V files read cleanly, do not clear the incident; continue inode mapping because the corrupted file may be under `/data/misc/apexdata` or another system-server state directory | block next pressure cycle |
| Preflight | suspect file is mutable `/data` state or app artifact, or may be replaced by atomic rename | run `scripts/adb_preserve_mutable_suspect_file.sh` before parsing/klog; use neutral preserve names that do not match the original basename, `.tmp`, or `.reservecopy` targets | manifest shows original inode and preserved hardlink inode match or records same-directory hardlink fallback; host archive exists | if `/data/local/tmp` hardlink fails, immediately try a neutral hidden hardlink in the original directory, then copy from the pinned hardlink; if hardlink still fails, copy/pull immediately and report failure | block klog/pattern analysis until preservation is done or failure is explicit |
| Preserve EIO | preserving a corrupted fs-verity file causes `tar: short read: I/O error`, `dd` stops at the corruption offset, or host pull cannot read the full file | keep the device-side hardlink as the primary preservation artifact; package only metadata, stderr files, and any partial blob for host-side records; do not make host archive success a prerequisite for inode preservation | device-side `ls -li` shows link count increased and hardlink inode equals the original inode; host manifest records `host_archive_excludes_hardlink` and, if applicable, `copy_size_status=partial_or_short`; focused `dd skip=<bad_page>` on the hardlink reproduces EIO | if hardlink creation failed, record preservation failure and avoid further reboot/pressure until a direct same-directory hardlink or raw block method is attempted | replace |
| Batch preserve | a host script reads suspect paths with `while read ... < suspect_paths.txt` and calls `adb shell`, `adb pull`, or a preserve helper inside the loop | redirect every nested adb/preserve command's stdin from `/dev/null`, and make preserve keep names unique beyond second-resolution timestamps | preserve status contains one explicit `ok`, `failed`, or `skip_non_file` row for every suspect path; repeated fast preserves create distinct device keep directories | if only the first suspect is processed, assume adb consumed the loop stdin; fix the loop before the next pressure cycle and manually preserve the current suspects sequentially | replace |
| Root shell | Magisk `su` reports `option requires an argument -- c` or `/system/bin/sh: 1: parameter not set` | use `su -c '<one command string>'`; do not use `su 0 sh -c ...` in preserve scripts unless that exact device accepts it | smoke-test preserve against one small file and confirm manifest/sha256 | fall back to direct `su -c` tar/cp/pull for current suspect files | replace |
| Klog filter | live path may be rewritten after stat | prefer preserved hardlink inode for old sample; use suffix-only or restat immediately for live replacement tracking | dmesg contains marker and `F2FS_WB` rows for expected inode/path | if no rows, check whether the inode changed and rerun against preserved hardlink/current inode | branch |
| Dmesg capture | F2FS_WB is enabled, or pressure artifacts need writeback provenance | start `dmesg -w` or `/proc/kmsg` capture before triggering the workload, and also archive a hit-time `dmesg` snapshot; do not rely on logcat for `F2FS_WB` rows | artifact directory contains `dmesg_stream.txt` or equivalent plus `dmesg_after.txt`, and grep finds the marker/filter rows or an explicit empty result | if `adb shell 'su -c "dmesg"'` returns empty, try `adb shell 'su -c "sh -c \"dmesg\""'`; if streaming exits across reboot, use a reconnecting host loop | block evidence claims until dmesg/kmsg capture exists |
| Live fs-verity inode tracking | dmesg stream records `fs-verity ... inode N`, `FILE CORRUPTED`, `Unrecognized descriptor`, or `F2FS_VERITY ... ino=N` during pressure | run `scripts/adb_watch_fsverity_inodes.sh --serial <SERIAL> --dmesg-log <dmesg_stream.txt> --out <watcher_out> --scan-existing` or use a pressure script that starts it automatically; it maps each new inode with `find /data -xdev -inum` and preserves any live regular file by hardlink | watcher status has `map_start/map_done`, `preserve.status.txt` has `ok`, `skip_non_file`, or `no_live_path` for every inode, and successful preserve dirs contain `preserve_manifest.txt` | if old inodes have no live path, record `no_live_path` and keep pressure running; do not claim a preserved corrupt sample until a hardlink manifest exists | branch |
| Orphan pblk probe | preserved encrypted `/data` artifact has local `+2` block deltas and high-entropy pages, and you need to test whether the expected `+1` physical block still contains the original ciphertext | use `/sys/fs/f2fs/<dev>/klog_wb_raw_read_{ino,lblk,pblk,radius,run}` on a kernel containing the raw-read probe; set inode, target file logical block, center pblk, and radius, then write `1` to `run` | dmesg has `F2FS_WB RAW_READ` rows with `needs_crypt=1 has_key=1 inline=1 bio_crypt=1`, plus `crc32`, byte counts, and `first32` for each candidate pblk | if `has_key=0` or `bio_crypt=0`, unlock and read/open the preserved hardlink to instantiate fscrypt info, then rerun; if still unavailable, do not interpret raw ciphertext as decrypted plaintext | branch |
| Neighbor-DUN probe | current mapped bad pblk still looks high entropy and you suspect it contains nearby page data decrypted with the wrong page index / DUN | fix the bad pblk and sweep nearby `klog_wb_raw_read_lblk` values with radius `0`; compare `max` byte count, entropy if available, and first bytes against a good reference page shape | one swept `lblk` recovers a low-entropy / structured page shape, or all swept values remain high-entropy-shaped | if all nearby DUNs remain high entropy, record that hypothesis as not supported for this sample and move to the corruption matrix | branch |
| Hypothesis matrix | orphan pblk and neighbor-DUN probes fail to recover normal plaintext from a preserved `+2` high-entropy bad region | read `references/plus2-high-entropy-corruption-matrix.md`; classify next work under page-writeback, bio-submission, or buffered-write/writeback concurrency; instrument write-time provenance before another raw-decrypt sweep | next plan names the layer, suspect ID, needed evidence, and exclusion criteria | if the evidence does not explain local `+2`, do not promote it as a leading cause; keep it as weakened/open until write-time logs support it | continue |

### Output Contract

- phase reached:
- decision path taken:
- boot boundary evidence:
- prelink evidence:
- preservation evidence:
- klog marker:
- deadlock evidence:
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
adb shell 'su -c "dmesg -w"' | grep 'F2FS_WB'
```

For reboot pressure, use a reconnecting host-side loop and archive both the
stream and a final snapshot in the hit artifacts. `F2FS_WB` is a kernel printk
path; logcat alone is not sufficient evidence capture.

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
