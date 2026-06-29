# Archived UFFD Order-2 Experiment

This archive records experiment state from the Pixel common mTHP/order-0 investigation.

## UFFD anonymous mfill order-2 fast path

Status: archived and removed from the active Pixel common tree unless explicitly restoring the experiment.

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
