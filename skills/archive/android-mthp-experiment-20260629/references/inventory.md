# Archived mTHP Experiment Inventory

This archive contains the full high-overhead instrumentation used for the Pixel 6 16KB mTHP order-0 attribution work.

## Full Committed Stack

Patch:

```text
patches/0000-full-committed-mthp-experiment-stack.patch
```

Original range:

```text
dd369db1d7f5..700e6147efec
```

Main content:
- F2FS/EXT4 file-page order-2 experiments and tracepoints.
- mmap/non-MAP_FIXED alignment changes and VMA anon-name debug strings.
- zram large compression experiment.
- VMA anonymous-name enrichment for alignment and mapping provenance.
- `madvise(DONTNEED)` tracepoint fields: current pid/tgid/comm, leader comm, `mm`, VMA range, and `vma_name`.
- Deferred split tracepoint fields: folio pfn/order/reason, task info, `mm`, VMA range, and `vma_name`.
- Basic direct reclaim failure reason counters in `mm/page_alloc.c`.
- Initial VMA/mmap alignment trace support.

## Final Commit Only

Patch:

```text
patches/0001-head-mthp-experiment-commit.patch
```

Original commit:

```text
700e6147efec 增加全部改动
```

This is kept for bisect-style restoration of only the last committed layer. Most full restores should use `0000-full-committed-mthp-experiment-stack.patch`.

## Dirty Worktree Layer

Patch:

```text
patches/0002-dirty-mthp-experiment-worktree.patch
```

Files modified:

```text
include/trace/events/huge_memory.h
mm/Makefile
mm/huge_memory.c
mm/memory.c
mm/mremap.c
mm/page_alloc.c
mm/rmap.c
mm/shmem.c
mm/swap_state.c
mm/swapfile.c
mm/userfaultfd.c
mm/vmscan.c
include/linux/mthp_experiment.h
mm/mthp_experiment.c
```

Main content:
- `include/linux/mthp_experiment.h` enum tables, trace info structs, swap birth-record structs, helper declarations.
- `mm/mthp_experiment.c` debugfs counter implementation and swap birth-record table.
- Anon fault fallback counters and tracepoint:
  - UFFD armed fallback.
  - no allowable order.
  - VMA suitability fallback.
  - PTE range occupied.
  - allocation/charge failures.
- VMA suitability subreasons:
  - VMA too small.
  - left boundary.
  - right boundary.
- Swapin fallback counters and tracepoints:
  - synchronous/readahead path split.
  - swapcache hit.
  - UFFD fallback.
  - no orders from allowable/VMA suitable/swap suitable.
  - swap offset mismatch.
- Swap birth-record tracking:
  - records whether faulting swap entry came from order-0 or order-2 folio at swapout time.
  - records birth PFN, subindex, and phase data used for the VPN/SWP mod4 phase evidence.
- UFFD mfill counters and tracepoint:
  - copy, zeropage, continue, shmem-related accounting.
  - thread/process/VMA attribution for `mfill_atomic_pte_copy()`.
- Shmem and swapin reason counters.
- Page allocator order-0 migratetype counters.
- Deferred shrinker no-split switch and counters.

## Why Archived

This code is useful for attribution, but it is not suitable for routine performance runs:
- tracepoint payloads are large;
- counters touch hot paths;
- swap birth-record tracking adds work to swapout/swapin paths;
- page allocator and fault path instrumentation changes the exact workload being measured.

Keep it out of the active tree unless the experiment specifically needs these signals.
