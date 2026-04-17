# f2fs inode-centric k=v logging plan (large folio + fs-verity + atomic write)

This reference is a reusable *instrumentation plan* for debugging suspected data corruption involving:
- large folios (CONFIG_F2FS_LARGE_FOLIO / mapping large-folio support)
- fs-verity enable flow (FI_VERITY_IN_PROGRESS / fsverity_active)
- atomic write / COW commit (FI_ATOMIC_* + cow_inode)

It is designed for **table-style querying**: every log line is a stable row with consistent `k=v` fields.

## Scope (Pixel common kernel, f2fs)
- `fs/f2fs/data.c`: `f2fs_write_cache_folios`, `f2fs_write_single_data_folio`, `f2fs_write_end_io`, `prepare_large_folio_atomic_write_begin`
- `fs/f2fs/inode.c`: `f2fs_iget` large-folio mapping decision
- `fs/f2fs/file.c`: atomic-write ioctls + verity ioctls wrapper
  - Optional but highly relevant:
  - `fs/f2fs/verity.c`: set/clear `FI_VERITY_IN_PROGRESS`
  - `fs/f2fs/segment.c`: `f2fs_inplace_write_data`, `f2fs_outplace_write_data` (blkaddr visibility)

## Why fs-verity can still implicate write paths

Even though a verity-protected inode is effectively read-only *after verity is enabled*,
the **enable/build** flow itself performs writes:

- build Merkle tree + descriptor (often beyond `i_size`) via page cache
- force persistence (e.g. `filemap_write_and_wait`)
- update xattrs / inode flags to “turn verity on”

On Android, some update flows also create a new inode, write it fully, then enable
verity on the new file. So verity-heavy workloads can still stress:
- writeback
- truncate/beyond-EOF handling
- metadata persistence

## Integrity gotchas (large-folio + writeback_iter + ffs)

These are easy to miss when porting page-based writeback code to folio/subpage tracking:

1) **`writeback_iter()` clears DIRTY-for-IO**
   - `mm/page-writeback.c:folio_prepare_writeback()` calls `folio_clear_dirty_for_io(folio)` before returning a locked folio to `->writepages`.
   - Consequence: inside `f2fs_write_cache_folios()` the folio will very often have `folio_test_dirty(folio) == false`, even though *subpage dirty work remains* (tracked out-of-band in `ffs`).
   - Rule of thumb: once you adopt `ffs` for subpage dirty tracking, **never gate correctness on `folio_test_dirty()` inside the writeback loop** unless you *explicitly* re-dirty it.

2) **On retry / early-fail, you must re-dirty**
   - If you fail to submit IO for dirty data after `folio_clear_dirty_for_io()`, the only way that data gets retried is if the filesystem calls `folio_redirty_for_writepage(wbc, folio)` (or equivalent) and preserves `ffs` dirty state.
   - Missing re-dirty can become **silent data loss**: pagecache has the bytes, but reclaim can drop them because the folio is now “clean”.

3) **Do not `break` out of a `writeback_iter()` loop**
   - `writeback_iter()`’s API contract is: keep calling it until it returns `NULL`, so it can account `nr_to_write`, stash the first error for WB_SYNC_ALL, and release internal batches.
   - If the filesystem uses `break`, you can get incorrect error propagation and/or incomplete integrity writeback.

4) **`ffs` allocation failure is correctness-sensitive**
   - For `folio_nr_pages(folio) > 1` data writeback, `f2fs_write_end_io()` uses `ffs->write_pages_pending` to decide when to call `folio_end_writeback()`.
   - If a large folio enters writeback without `ffs` attached (allocation failure, unexpected code path), end-io accounting can become “best effort” and may end writeback too early or not at all.

## Quick “are we hitting the bug?” probes

These probes are designed to be low-effort and high-signal; they intentionally trade some extra CPU for clarity.

### Probe A: Atomic large-folio RMW single-page partial-tail case

Target: `fs/f2fs/data.c:prepare_large_folio_atomic_write_begin()`.

Goal: catch the case “start aligned, end unaligned, within a single 4K subpage” and verify whether Stage B is zero-filling when the original block is non-hole.

Suggested probe:
- Before Stage B loop, detect the single-page partial-tail condition and log:
  - `ori_off`, `len`, `head_index`, `tail_index`, `head_blk_addr`, `tail_blk_addr`
  - `FI_ATOMIC_REPLACE`
- Optionally (debug-only), call `__find_data_block(inode, head_index, &ori)` and warn if `ori != NULL_ADDR` but `head_blk_addr == NULL_ADDR`.

### Probe B: Retry path uses folio dirty bit (writeback_iter interaction)

Target: `fs/f2fs/data.c:f2fs_write_cache_folios()`, right before:
`if (retry && !folio_test_dirty(folio)) goto out;`

Goal: see if the function is skipping writeback even when `ffs` still reports dirty subpages.

Suggested probe:
- If `retry && !folio_test_dirty(folio) && folio_has_ffs(folio)`:
  - compute a local `pos0/end0` and call `ffs_find_dirty_range(folio, &pos0, end_pos)`
  - `WARN_ON_ONCE(r_len0 != 0)` and dump `ino`, `folio_index`, `end_pos`, `r_len0`, and `err`.

### Probe C: Error exit must re-dirty (or you drop data)

Target: `fs/f2fs/data.c:f2fs_write_cache_folios()`, at the `out:` label.

Goal: catch cases where `err != 0` and we are about to unlock/end-writeback without redirtying, leaving modified bytes clean.

Suggested probe:
- If `err != 0` and `folio_has_ffs(folio)`:
  - warn if there exists any dirty subpage (`ffs_find_dirty_range(...) != 0`) while `folio_test_dirty(folio) == false`
  - log `err`, `folio_submitted`, and whether `op_lock_held` was taken.

## Stable k=v schema (recommended keys)

Every line should include (even if some values are `-1`):
- Identity: `event`, `func`, `ino`, `pid`, `cpu`, `comm`
- Verity: `verity_active`, `verity_ip` (in progress)
- Atomic: `atomic_file`, `atomic_commit`, `atomic_committed`, `atomic_replace`, `cow_ino`
- Mapping/folios: `map_large`, `map_min_order`, `map_max_order`, `folio_index`, `folio_order`, `folio_nr_pages`, `subidx`, `data_idx`
- Writeback range: `wbc_sync`, `wbc_start`, `wbc_end`, `wbc_nr_to_write`, `wbc_pages_skipped`
- Addresses: `old_blkaddr`, `new_blkaddr`, `sector`
- Status: `err`, `bi_status`, `errno`

### Notes on field sources
- `verity_active`: `fsverity_active(inode)`
- `verity_ip`: `f2fs_verity_in_progress(inode)` (f2fs-specific flag)
- `atomic_file`: `f2fs_is_atomic_file(inode)`
- `atomic_committed`: `is_inode_flag_set(inode, FI_ATOMIC_COMMITTED)`
- `atomic_replace`: `is_inode_flag_set(inode, FI_ATOMIC_REPLACE)`
- `atomic_commit` (folio-level): `f2fs_is_atomic_file(inode) && folio_test_f2fs_atomic(folio)`
- `map_large`: `mapping_large_folio_support(inode->i_mapping)`
- `map_min_order` / `map_max_order`: `mapping_min_folio_order()` / `mapping_max_folio_order()`

## Suggested log prefix
- Use a fixed tag that is grep-friendly: `f2fs_kv`
- Include `func=%s` with `__func__` on every line.

Example prefix:
`pr_debug("f2fs_kv func=%s event=... ...\\n", __func__, ...);`

## Patch-ready log line templates (k=v)

These are templates (format strings + field ordering) intended to be copy/pasted
into `pr_debug()` callsites. They assume you already have `struct inode *inode`
and/or `struct folio *folio` in scope.

### 0) Common field blocks

**Inode identity + state (always include):**
```
"ino=%lu pid=%d cpu=%d comm=%s "
"verity_active=%d verity_ip=%d "
"atomic_file=%d atomic_commit=%d atomic_committed=%d atomic_replace=%d cow_ino=%lu "
"map_large=%d map_min_order=%u map_max_order=%u "
```

Suggested arguments:
```
inode->i_ino,
current->pid, raw_smp_processor_id(), current->comm,
fsverity_active(inode), f2fs_verity_in_progress(inode),
f2fs_is_atomic_file(inode),
/* atomic_commit: caller-defined (often folio-level) */ atomic_commit,
is_inode_flag_set(inode, FI_ATOMIC_COMMITTED),
is_inode_flag_set(inode, FI_ATOMIC_REPLACE),
F2FS_I(inode)->cow_inode ? F2FS_I(inode)->cow_inode->i_ino : 0UL,
mapping_large_folio_support(inode->i_mapping),
mapping_min_folio_order(inode->i_mapping),
mapping_max_folio_order(inode->i_mapping)
```

**Folio identity (include when a folio is involved):**
```
"folio_index=%lu folio_order=%u folio_nr_pages=%u "
```
Arguments:
```
folio->index, folio_order(folio), folio_nr_pages(folio)
```

**Writeback control (include in writeback functions):**
```
"wbc_sync=%d wbc_start=%lld wbc_end=%lld wbc_nr_to_write=%ld wbc_pages_skipped=%lu "
```
Arguments:
```
wbc->sync_mode, (long long)wbc->range_start, (long long)wbc->range_end,
wbc->nr_to_write, wbc->pages_skipped
```

**Addresses and status:**
```
"old_blkaddr=0x%llx new_blkaddr=0x%llx sector=%llu bi_status=%d errno=%d err=%d "
```
Arguments:
```
(unsigned long long)old_blkaddr,
(unsigned long long)new_blkaddr,
(unsigned long long)sector,
bi_status, errno, err
```

### 1) data.c: f2fs_write_cache_folios()

ENTER:
```
pr_debug("f2fs_kv func=%s event=write_cache_folios_enter "
         "ino=%lu pid=%d cpu=%d comm=%s "
         "verity_active=%d verity_ip=%d "
         "atomic_file=%d atomic_committed=%d atomic_replace=%d cow_ino=%lu "
         "map_large=%d map_min_order=%u map_max_order=%u "
         "wbc_sync=%d wbc_start=%lld wbc_end=%lld wbc_nr_to_write=%ld wbc_pages_skipped=%lu\n",
         __func__, inode->i_ino,
         current->pid, raw_smp_processor_id(), current->comm,
         fsverity_active(inode), f2fs_verity_in_progress(inode),
         f2fs_is_atomic_file(inode),
         is_inode_flag_set(inode, FI_ATOMIC_COMMITTED),
         is_inode_flag_set(inode, FI_ATOMIC_REPLACE),
         F2FS_I(inode)->cow_inode ? F2FS_I(inode)->cow_inode->i_ino : 0UL,
         mapping_large_folio_support(inode->i_mapping),
         mapping_min_folio_order(inode->i_mapping),
         mapping_max_folio_order(inode->i_mapping),
         wbc->sync_mode, (long long)wbc->range_start, (long long)wbc->range_end,
         wbc->nr_to_write, wbc->pages_skipped);
```

RANGE (before calling `f2fs_write_single_data_folio()`):
```
pr_debug("f2fs_kv func=%s event=write_cache_folios_range "
         "ino=%lu pid=%d cpu=%d comm=%s "
         "verity_active=%d verity_ip=%d atomic_file=%d atomic_committed=%d atomic_replace=%d cow_ino=%lu "
         "map_large=%d map_min_order=%u map_max_order=%u "
         "folio_index=%lu folio_order=%u folio_nr_pages=%u "
         "pos=%llu end=%llu r_len=%u op_lock_held=%d\n",
         __func__, inode->i_ino,
         current->pid, raw_smp_processor_id(), current->comm,
         fsverity_active(inode), f2fs_verity_in_progress(inode),
         f2fs_is_atomic_file(inode),
         is_inode_flag_set(inode, FI_ATOMIC_COMMITTED),
         is_inode_flag_set(inode, FI_ATOMIC_REPLACE),
         F2FS_I(inode)->cow_inode ? F2FS_I(inode)->cow_inode->i_ino : 0UL,
         mapping_large_folio_support(inode->i_mapping),
         mapping_min_folio_order(inode->i_mapping),
         mapping_max_folio_order(inode->i_mapping),
         folio->index, folio_order(folio), folio_nr_pages(folio),
         pos, pos + r_len, r_len, op_lock_held);
```

RANGE result (after the call):
```
pr_debug("f2fs_kv func=%s event=write_cache_folios_range_done "
         "ino=%lu pid=%d cpu=%d comm=%s "
         "folio_index=%lu pos=%llu end=%llu r_len=%u submitted=%d op_lock_held=%d err=%d\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         folio->index, pos, pos + r_len, r_len, submitted, op_lock_held, err);
```

EXIT:
```
pr_debug("f2fs_kv func=%s event=write_cache_folios_exit "
         "ino=%lu pid=%d cpu=%d comm=%s err=%d nwritten=%d wbc_pages_skipped=%lu wbc_nr_to_write=%ld\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         err, nwritten, wbc->pages_skipped, wbc->nr_to_write);
```

### 2) data.c: f2fs_write_single_data_folio()

ENTER:
```
pr_debug("f2fs_kv func=%s event=write_single_data_folio_enter "
         "ino=%lu pid=%d cpu=%d comm=%s "
         "atomic_file=%d atomic_commit=%d cow_ino=%lu "
         "folio_index=%lu folio_order=%u folio_nr_pages=%u "
         "start=%llu end=%llu\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         f2fs_is_atomic_file(inode), atomic_commit,
         F2FS_I(inode)->cow_inode ? F2FS_I(inode)->cow_inode->i_ino : 0UL,
         folio->index, folio_order(folio), folio_nr_pages(folio),
         start, end);
```

SUBPAGE decision (Detail mode; place after `fio.old_blkaddr` is known, before submit):
```
pr_debug("f2fs_kv func=%s event=write_single_data_folio_subpage "
         "ino=%lu pid=%d cpu=%d comm=%s "
         "folio_index=%lu subidx=%lu data_idx=%lu "
         "path=%s ipu_force=%d meta_gc=%d "
         "old_blkaddr=0x%llx atomic_commit=%d\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         folio->index, (unsigned long)i, (unsigned long)data_idx,
         path_str, ipu_force, fio.meta_gc,
         (unsigned long long)fio.old_blkaddr, atomic_commit);
```

SUBPAGE submit result:
```
pr_debug("f2fs_kv func=%s event=write_single_data_folio_submit "
         "ino=%lu pid=%d cpu=%d comm=%s "
         "folio_index=%lu subidx=%lu data_idx=%lu submit=%s "
         "old_blkaddr=0x%llx new_blkaddr=0x%llx submitted=%u err=%d\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         folio->index, (unsigned long)i, (unsigned long)data_idx, submit_str,
         (unsigned long long)fio.old_blkaddr, (unsigned long long)fio.new_blkaddr,
         fio.submitted, err);
```

EXIT:
```
pr_debug("f2fs_kv func=%s event=write_single_data_folio_exit "
         "ino=%lu pid=%d cpu=%d comm=%s folio_index=%lu submitted=%d err=%d\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         folio->index, local_submitted, err);
```

### 3) data.c: f2fs_write_end_io()

BIO summary:
```
pr_debug("f2fs_kv func=%s event=write_end_io "
         "pid=%d cpu=%d comm=%s "
         "bio_sector=%llu bio_size=%u bi_status=%d errno=%d\n",
         __func__, current->pid, raw_smp_processor_id(), current->comm,
         (unsigned long long)bio->bi_iter.bi_sector, bio->bi_iter.bi_size,
         bio->bi_status, blk_status_to_errno(bio->bi_status));
```

Per-folio completion (Normal: only on error; Detail: always):
```
pr_debug("f2fs_kv func=%s event=write_end_io_folio "
         "ino=%lu pid=%d cpu=%d comm=%s "
         "folio_index=%lu folio_order=%u folio_nr_pages=%u "
         "type=%d finished=%d bi_status=%d errno=%d\n",
         __func__, folio->mapping->host->i_ino,
         current->pid, raw_smp_processor_id(), current->comm,
         folio->index, folio_order(folio), folio_nr_pages(folio),
         type, finished, bio->bi_status, blk_status_to_errno(bio->bi_status));
```

### 4) data.c: prepare_large_folio_atomic_write_begin()

ENTER:
```
pr_debug("f2fs_kv func=%s event=prepare_large_folio_atomic_wb_enter "
         "ino=%lu pid=%d cpu=%d comm=%s cow_ino=%lu "
         "folio_index=%lu folio_order=%u folio_nr_pages=%u "
         "pos=%llu len=%u ori_off=%zu "
         "start_index=%lu end_index=%lu partial_head=%d partial_tail=%d\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         cow_inode ? cow_inode->i_ino : 0UL,
         folio->index, folio_order(folio), folio_nr_pages(folio),
         (unsigned long long)pos, len, ori_off,
         (unsigned long)start_index, (unsigned long)end_index,
         has_partial_head, has_partial_tail);
```

Stage B read-before-write row:
```
pr_debug("f2fs_kv func=%s event=prepare_large_folio_atomic_rbw "
         "ino=%lu pid=%d cpu=%d comm=%s cow_ino=%lu "
         "folio_index=%lu need_off=%zu is_head=%d use_cow=%d "
         "blkaddr=0x%llx sector=%llu submit_err=%d\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         cow_inode ? cow_inode->i_ino : 0UL,
         folio->index, need_off, is_head, use_cow,
         (unsigned long long)blkaddr, (unsigned long long)sector, err);
```

EXIT:
```
pr_debug("f2fs_kv func=%s event=prepare_large_folio_atomic_wb_exit "
         "ino=%lu pid=%d cpu=%d comm=%s err=%d node_changed=%d\n",
         __func__, inode->i_ino, current->pid, raw_smp_processor_id(), current->comm,
         err, need_balance);
```


## Normal mode (minimal noise, highest signal)

Normal mode logs:
- entry/exit of the target functions
- *state transitions* (flag set/clear, lock acquired/released, retry)
- *error paths* (EIO/EFSCORRUPTED, bio failure, mapping mismatch)
- one row per folio/range **only when** verity/atomic/large-folio conditions apply

### data.c: f2fs_write_cache_folios()
Logpoints:
- ENTER: after `trace_pkgxml` block (near function start) with wbc + mapping info.
- FOLIO_START: right after `folio_start_writeback(folio)` with folio + inode state.
- SKIP reasons:
  - `folio->mapping != mapping`
  - i_size bounds skip (when `!verity_in_progress` and folio beyond `end_index`)
  - end_pos truncated/zeroed due to i_size
- RANGE row: just before calling `f2fs_write_single_data_folio()` (once per dirty range).
- RANGE result: immediately after call (err/submitted/op_lock_held).
- RETRY row: on `err == -EAGAIN ... goto retry;`
- EXIT: just before `return err;`

### data.c: f2fs_write_single_data_folio()
Logpoints:
- ENTER: at function top with `start/end` range, folio state, `atomic_commit`.
- PATH decision: per-subpage only when `(atomic_commit || verity_ip || folio_order>0)` or on errors:
  - extent cache hit path vs dnode lookup path
  - op-lock acquisition failure (`-EAGAIN`)
  - blkaddr validation failure (`-EFSCORRUPTED`)
- SUBMIT result:
  - after `f2fs_inplace_write_data(&fio)` (IPU)
  - after `f2fs_outplace_write_data(&dn, &fio)` (OPU)
  Include `old_blkaddr/new_blkaddr`, `fio.submitted`, `err`.
- EXIT: at function bottom with `local_submitted` and `err`

### data.c: f2fs_write_end_io()
Logpoints:
- ERROR-only (Normal mode): if `bio->bi_status != BLK_STS_OK`
  - include `bi_status`, `errno=blk_status_to_errno()`
  - per-folio row for each folio in the bio: `folio_index/order/nr_pages`, `type`, `finished`
- Optional: a low-rate summary per bio: total folios, sector/size, status

### data.c: prepare_large_folio_atomic_write_begin()
Logpoints:
- ENTER: after len clamp, include `pos,len,ori_off`, `start_index/end_index`,
  `has_partial_head/tail`, `cow_ino`.
- Stage A summary (Normal mode): after map unlock:
  - `need_balance` (node_changed)
  - `head_blkaddr/tail_blkaddr`, `head_use_cow/tail_use_cow`
- Stage B only on read/zero and errors:
  - for each subpage read-before-write: `need_off`, `blkaddr`, `use_cow`,
    `sector`, `err`
- EXIT: return code + whether Stage B ran

### inode.c: f2fs_iget() mapping large-folio decision
Logpoints:
- Only when `S_ISREG(inode->i_mode)` path and decision happens:
  - before calling `mapping_set_folio_order_range(inode->i_mapping, 0, 2)`
  - include `may_large=f2fs_inode_may_use_large_folio(inode)`,
    `inline/compressed/verity_active/quota` sub-causes if desired
  - include resulting `map_min_order/map_max_order`

### file.c: atomic-write & verity ioctls
Logpoints:
- `f2fs_ioc_start_atomic_write()`:
  - ENTER with `truncate`, basic inode flags, `cow_ino` if exists
  - after `filemap_write_and_wait_range()` (ret)
  - after cow inode create/reuse (cow_ino, truncate to 0)
  - FLAG transition rows: FI_ATOMIC_FILE set, FI_ATOMIC_REPLACE set
  - EXIT with `ret`, `atomic_write_task` set/unset
- `f2fs_ioc_commit_atomic_write()`:
  - ENTER, and EXIT with `ret`, whether `f2fs_commit_atomic_write()` was called
- `f2fs_ioc_abort_atomic_write()`:
  - ENTER/EXIT + `clean` semantics (true)
- `f2fs_ioc_enable_verity()` wrapper:
  - log that ioctl started and returned (actual FI_VERITY_IN_PROGRESS toggles in verity.c)

## Detail mode (deep, high-volume)

Detail mode adds:
- per-subpage rows inside `f2fs_write_single_data_folio()` loop:
  - `data_idx`, `path=extent|dnode`, `ipu_force`, `old_blkaddr`, `meta_gc`
  - `op_lock=trylock` result
  - `encrypt=...` return code
  - `submit=ipu|opu` return code + `new_blkaddr`
- per-iteration rows in `prepare_large_folio_atomic_write_begin()` Stage A:
  - `cur` (page index), `cow_has_mapping` (dn.data != NULL_ADDR),
    `reserved=1` and resulting `cow_blkaddr` after reserve
- always-on `f2fs_write_end_io()` per-folio completion rows

For detail mode, prefer **tracepoints** (see below) for hot loops.

## Tracepoint upgrade candidate (if printk/pr_debug too noisy)

Candidate: add a new tracepoint for the **per-subpage write decision** in
`f2fs_write_single_data_folio()` (inside the `for (i=...)` loop).

Rationale:
- This loop is naturally high-frequency.
- `TRACE_EVENT` gives structured fields, tracefs filtering, and avoids dmesg spam.

Suggested event name:
- `f2fs_large_folio_subpage_write`

Suggested fields:
- `ino`, `folio_index`, `folio_order`, `subidx`, `data_idx`
- `atomic_file`, `atomic_commit`, `atomic_replace`, `cow_ino`
- `verity_active`, `verity_ip`
- `path` (`extent_hit`, `dnode_lookup`)
- `old_blkaddr`, `new_blkaddr`
- `err`

Alternatives before adding a new tracepoint:
- enable existing `f2fs_submit_folio_write` tracepoint (has `ino`, `folio_index`, `oldaddr`, `newaddr`, `op_flags`)
- enable existing `f2fs_replace_atomic_write_block` tracepoint (commit mapping)

## Health rules (suspicious patterns)

Treat these as **corruption risk signals** worth correlating with on-disk failures:

1) **fs-verity flag ordering**
- `verity_ip=1` should span all writeback of pages beyond i_size needed for Merkle+desc.
- If you see `verity_ip=0` while `write_cache_folios` still writes beyond i_size, flag it.

2) **Atomic + large folio consistency**
- In `write_single_data_folio`, `atomic_file=1` and `atomic_commit=1` implies `dn_inode` is `cow_inode`.
  - Any write using `dn_inode=inode` while `atomic_commit=1` is suspicious.
- `cow_ino` should be stable across the atomic session (start→commit/abort).
  Frequent changes or NULL during commit implies races or teardown.

3) **Read-before-write correctness in prepare_large_folio_atomic_write_begin**
- For partial head/tail, if `use_cow=1`, Stage B reads should target `cow_inode`.
  If reads target original inode while `use_cow=1`, risk of stale data merge.
- `blkaddr` validity failures (`-EFSCORRUPTED`) in Stage B are high-signal.

4) **Block address validation failures**
- Any `err=-EFSCORRUPTED` from `f2fs_is_valid_blkaddr()` in these paths is high priority.
- Repeated failures on the same `ino` or same `data_idx` are stronger evidence than one-off.

5) **End-IO errors**
- `write_end_io` with `bi_status!=OK` on data writes involving
  `atomic_file=1` or `verity_ip=1` raises the risk of partial updates.

6) **Mapping feature contradictions**
- `f2fs_inode_may_use_large_folio()` returns false for `fsverity_active=1`.
  If you ever observe a “set large folio range” decision while `verity_active=1`, that’s a correctness bug.
