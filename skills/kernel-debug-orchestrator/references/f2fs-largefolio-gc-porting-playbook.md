# Playbook: Port F2FS Large-Folio GC Fixes Between Trees (No Instrumentation)

## Scope
This note captures the minimal, functional touch points to port F2FS **large folio**
GC correctness changes between kernel trees (e.g. upstream-ish `f2fs` into
Android `pixel/common`), while explicitly excluding any instrumentation noise.

Hard exclusions (do not port):
- debug logging (printk/pr_* / trace_* additions)
- `__attribute__((optimize("O0")))` or any build/attribute hacks

## Minimal Touch Points (Typical)

### `fs/f2fs/data.c`
- `f2fs_submit_page_read()`
  - Must submit reads for a large folio with:
    - `bio_add_folio(bio, folio, PAGE_SIZE, offset_in_folio(folio, ...))`
  - Must bump `ffs->read_pages_pending` for each 4K subpage read so that
    read completion only ends the folio when all subpages finish.
- `f2fs_get_read_data_folio()`
  - Must not reject large folios.
  - Must use per-4K-subpage uptodate checks for large folios (not just `folio_test_uptodate()`).
  - For `NEW_ADDR`, must zero only the 4K subrange and mark only that subrange uptodate.
- Supporting helpers often needed when the target tree is missing them:
  - `ffs_test_blk_dirty()` (per-subpage dirty snapshot)
  - `ffs_clear_subrange_dirty_and_test()` (clear a 4K dirty range and report whether
    other dirty subpages remain)

### `fs/f2fs/gc.c`
- `move_data_page()`
  - For large folios, compute `foff = offset_in_folio(folio, bidx<<PAGE_SHIFT)`.
  - BG_GC: mark the 4K subrange dirty before `folio_mark_dirty()`.
  - FG_GC:
    - Set `fio.idx = bidx - folio->index` and `fio.cnt = 1` so writeback is per-subpage.
    - Preserve per-subpage dirty bits across `folio_clear_dirty_for_io()`
      (re-dirty the folio if other dirty subpages remain).
    - Maintain `ffs->write_pages_pending` bookkeeping if the tree uses it.

### `fs/f2fs/f2fs.h`
- Export prototypes for any newly added `ffs_*` helpers used cross-file.

### Corner Case: `move_data_block()` + `f2fs_submit_page_bio()`
Some trees have a `move_data_block()` path (meta-inode GC / encrypted-page staging) that
calls `f2fs_submit_page_bio()` with `fio->encrypted_page` set. This has an important
constraint when large folios are in play:

- `f2fs_submit_page_bio()` uses `fio->idx` for both:
  - crypto logical index: `fio_lblk = fio_folio->index + fio->idx`
  - bio offset into the submitted folio: `fio->idx << PAGE_SHIFT`
- When `fio->encrypted_page` is set, the submitted `data_folio` becomes the folio backing
  that page (often an order-0 meta-mapping folio). In that case `fio->idx` must remain 0,
  otherwise the bio offset will be wrong.

Implication: if you reproduce issues in `move_data_block()` with large folios present in the
file's mapping, "just set idx/cnt like `move_data_page()`" is not necessarily safe. The safer
fix is usually:
- ensure `move_data_block()` never operates on a large `fio->folio` (retry with order-0), or
- refactor I/O to decouple crypto index from folio offset.

## Minimal Verification
If a full Android build is too heavy, at least do an object-level compile in the target
tree to catch signature drift:

```bash
cd <kernel-tree-root>
make O=/tmp/<build> ARCH=x86_64 defconfig
make O=/tmp/<build> ARCH=x86_64 fs/f2fs/data.o fs/f2fs/gc.o -j$(nproc)
```
