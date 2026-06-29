---
name: android-mthp-experiment-patches
description: Use when working in the Pixel common kernel tree on Android mTHP/order-0/deferred-split experiments, especially to distinguish active reason counters from archived smoke counters or to restore the archived UFFD order-2 mfill experiment.
---

# Android mTHP Experiment Patches

Use this skill for `/home/nzzhao/learn_os/pixel/common` mTHP instrumentation work.

Core policy:
- Keep active debugfs counters focused on **fallback reasons**.
- Do not keep smoke-only `*_attempt` or `*_success` counters in the formal reason table.
- Prefer cheap counters first, then targeted VMA tracepoints for hot reasons.
- For VMA-driven anon/swap fallback, trace `pid/tgid/comm/leader_comm/mm/vma_start/vma_end/vma_name/vm_flags` and the relevant order masks.
- For order-2 VMA suitability counters, keep only the mutually exclusive primary reasons: `*_vma_too_small`, `*_left_boundary`, and `*_right_boundary`. Retire/delete `*_vma_start_unaligned` and `*_vma_end_unaligned` counters from the formal reason table; alignment should be derived from the trace fields `vma_start`, `vma_end`, `addr`, `haddr`, and `vma_name`.
- `left_boundary` means `ALIGN_DOWN(fault_addr, 16KB) < vma->vm_start`: the 16KB folio window containing the fault address would extend before the VMA. `right_boundary` means `aligned_addr + 16KB > vma->vm_end`: the 16KB folio window would extend past the VMA end.
- For one-shot attribution of `thp_vma_suitable_orders()` failures, the most important signal is the anon/swapin fallback tracepoint, not vm-event counters. Counters provide closed totals; tracepoint records provide the per-event join keys needed for `subreason x vma_name x comm/leader_comm x mm` bucketing. Report VMA suitability as primary-reason percentages plus VMA-name buckets, not as summed alignment-tag counters.
- For report structure, reuse the first baseline experiment for the broad order-0 split: first split moveable vs unmoveable, then fully bucket moveable. Treat `uffd`, `swapin`, and anonymous fault fallback as the three major explainable heads; keep slab/page-table/mmu-gather and similar residual heads as proportional context unless they become the focus of a later round.
- For UFFD, bucket by process/thread first, then trace back the dominant user-space handler source. For deferred split, keep the existing plan: attribute true deferred-split events by stack plus VMA name and correlate with `madvise`/`munmap` only where needed.

Archived experiment details:
- Read [references/archived-uffd-order2.md](references/archived-uffd-order2.md) before restoring the UFFD anonymous mfill order-2 fast path.
