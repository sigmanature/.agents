# Archived UFFD Order-2 Experiment

This archive records experiment state from the Pixel common mTHP/order-0 investigation. On this workstation the preserved 20260629 archive contains the UFFD trace/counter layer and notes about the order-2 fast path, but the original fast-path implementation body is not present in the archived patch files.

## UFFD anonymous mfill order-2 fast path

Status: archived notes only on this workstation; removed from the active Pixel common tree unless explicitly reconstructing the experiment.

Files involved:
- `mm/userfaultfd.c`
- `include/linux/mthp_experiment.h`
- `mm/mthp_experiment.c`

Experimental code shape:
- `mfill_atomic_order2_range_ok(dst_addr, src_addr, len)`
- `mfill_atomic_order2_pte_none(dst_pmd, dst_vma, dst_addr)`
- `mfill_atomic_install_anon_order2(...)`
- `mfill_atomic_pte_copy_order2(...)`
- debugfs switch `uffd_mfill_order2_copy_enable`

Observed issue:
- The workload frequently fell back before useful order-2 installs.
- Common blockers included short `len`, unaligned address, shared mappings, and copy-user fallback.
- The fast path did not materially reduce the dominant UFFD order-0 samples in the baseline run.

Active-tree policy:
- Keep UFFD total/order0/shmem/zeropage reason counters if useful.
- Do not keep UFFD order-2 attempt/success counters in the formal reason panel.
- Restore the fast path only for a targeted UFFD semantic experiment, then reboot-isolate the run.

Current active-tree cleanup:
- `mfill_atomic_order2_range_ok()`, `mfill_atomic_order2_pte_none()`,
  `mfill_atomic_install_anon_order2()`, and `mfill_atomic_pte_copy_order2()`
  have been removed from `mm/userfaultfd.c`.
- The debugfs switch `uffd_mfill_order2_copy_enable` has been removed.
- The formal counter table no longer contains UFFD order-2 attempt/success or
  order-2 fallback counters.
- Retained counters are limited to UFFD total/order0/zeropage/shmem/continue
  paths that explain remaining order-0 allocation volume.

Reconstructed implementation shape used for a targeted local experiment:
- Add a self-contained `uffd_mfill_order2` debugfs node in `mm/userfaultfd.c` with `enabled` and `stats` files.
- Default the fast path off and expose a bool core kernel parameter, e.g. `core_param(uffd_mfill_order2, uffd_mfill_order2_enabled, bool, 0600)`, so A/B boot identity is controlled before ART startup UFFD traffic.
- Gate the fast path to private anonymous `MFILL_ATOMIC_COPY` and `MFILL_ATOMIC_ZEROPAGE` only.
- Require `dst_addr` to be `PAGE_SIZE << 2` aligned, require `src_addr` to be aligned for COPY, and require at least one full 16KB window remaining in the ioctl range.
- Allocate `vma_alloc_folio(GFP_HIGHUSER_MOVABLE, 2, vma, dst_addr)`, copy or zero four base pages, charge memcg, add one large anonymous rmap with `folio_add_new_anon_rmap(..., RMAP_EXCLUSIVE)`, add LRU, then install four contiguous PTEs with `set_ptes()`.
- Keep normal order-0 fallback for short ranges, unaligned ranges, copy-user fallback, allocation failure, memcg failure, and existing destination PTEs.
- Validation command for the Pixel slider lane: `cd /home/nzzhao/learn_os/pixel && ./build_slider.sh --lane my_dec`.

## Swpin order-2 smoke counters

Archived/de-emphasized counters:
- `swpin_sync_order2_attempt`
- `swpin_sync_order2_success`
- `swpin_sync_order2_alloc_fail`
- `swpin_sync_order2_charge_fail`

Reason:
- They were smoke/validation counters, not formal fallback causes.
- Formal analysis should use:
  - `swpin_sync_fallback_no_orders_allowable`
  - `swpin_sync_fallback_no_orders_vma_suitable`
  - `swpin_sync_fallback_no_orders_swap_suitable`
  - refined `swpin_allowable_*`, `swpin_suitable_*`, and `swpin_swap_suitable_*` counters.

## UFFD EEXIST granularity rule for ART 16KB experiments

When ART is forced to use a 16KB logical `gPageSize` on an x86 Cuttlefish kernel
with 4KB base pages, never treat `UFFDIO_COPY`, `UFFDIO_ZEROPAGE`, or
`UFFDIO_MOVE` `-EEXIST` as proof that a whole 16KB ART page is already mapped.
The kernel UFFD API reports exact bytes completed, and `-EEXIST` denotes the
current kernel PTE/base-page conflict unless a higher-level path has already
returned a positive large-folio completion.

For the local order-2 `mfill_atomic_pte_order2()` fast path:
- order-2 success may return `UFFD_MFILL_ORDER2_SIZE` because all four PTEs were
  installed atomically with `set_ptes()`;
- order-2 `PTE_EXIST` must return `0`, not `-EEXIST`, so the normal order-0
  `mfill_atomic_pte()` fallback can identify the exact 4KB PTE conflict;
- userspace ART UFFD wrappers should internally advance by kernel base-page
  granularity on `EEXIST` and partial positive results, but only return complete
  ART logical-page multiples to callers that update 16KB `PageState` arrays.

Observed validation for this rule:
- before the fix, synthetic package installation on B stopped around p16 with a
  `system_server` tombstone in `ObjectArray::AssignableCheckingMemcpy()` after
  `UFFDIO_COPY EEXIST` on the same 16KB destination window;
- after changing kernel order-2 PTE_EXIST fallback and ART `CopyIoctl()` return
  accounting, B installed all 60 synthetic APKs with no tombstones and completed
  B/A all16K/all4K 120-cycle sampler cells.
