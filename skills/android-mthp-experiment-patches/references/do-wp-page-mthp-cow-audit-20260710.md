# do_wp_page mTHP COW audit (2026-07-10)

Scope: `/home/nzzhao/learn_os/pixel/common`, Linux `6.18.0`, branch `debug/pkgxml_einval_trace_20260408`.
Goal: evaluate a patch that lets a write fault in `do_wp_page()` copy an eligible anonymous mTHP window, e.g. order-2 / 16KB, instead of only the faulting base page.

## Current facts

- Existing `wp_page_copy()` is strictly order-0: `folio_prealloc()` allocates `order=0`, `__wp_page_copy_user()` copies one base page, notifier/TLB/rmap/accounting ranges are exactly one page.
- Existing anonymous first-touch already allocates mTHP in `alloc_anon_folio()`, validates VMA/order suitability, checks that all target PTEs are none, charges the full folio, adds `nr_pages - 1` references, and installs all PTEs with `set_ptes()`.
- Existing fork path already batches PTE-mapped large folios in `copy_present_ptes()`, but it shares the old folio and clears `PageAnonExclusive` on subpages. If pinned, it immediately copies only the single pinned PTE for the child.
- Existing upstream history includes `1da190f4d0a6 mm: Copy-on-Write (COW) reuse support for PTE-mapped THP`; its commit message explicitly says future work could “fault around” surrounding PTEs but latency needs investigation. Follow-up fixes `8bdea2fce980` and `4b7c0857f87a` show mapcount/refcount races are real under swap/unmap stress.

## Code anchors

| Domain | File:line | Important current behavior |
|---|---:|---|
| fork present PTE batching | `mm/memory.c:1112` | `copy_present_ptes()` detects consecutive PTEs in a large folio and calls `folio_try_dup_anon_rmap_ptes()` |
| COW copy allocation | `mm/memory.c:1185` | `folio_prealloc()` allocates only order-0 and charges one folio |
| COW copy main | `mm/memory.c:3659` | `wp_page_copy()` handles one page, one notifier range, one rmap remove |
| COW reuse main | `mm/memory.c:3987` | `wp_can_reuse_anon_folio()` includes large-folio reuse gate |
| write-protect fault entry | `mm/memory.c:4050` | `do_wp_page()` handles UFFD-WP, shared mappings, reuse, and copy fallback |
| anon first-touch mTHP alloc | `mm/memory.c:5045` | `alloc_anon_folio()` chooses enabled/suitable mTHP orders and checks `pte_range_none()` |
| anon first-touch mTHP map | `mm/memory.c:5135` | `do_anonymous_page()` installs `nr_pages` PTEs and charges `MM_ANONPAGES` by `nr_pages` |
| mTHP VMA policy | `include/linux/huge_mm.h:288` | `thp_vma_allowable_orders()` applies sysfs/madvise/THP-disabled policy |
| mTHP boundary check | `include/linux/huge_mm.h:220` | `thp_vma_suitable_order()` rejects VMA-left/right boundary crossing |
| rmap new large anon | `mm/rmap.c:1538` | `folio_add_new_anon_rmap()` sets mapcount and `PageAnonExclusive` per subpage for non-PMD-mappable large folios |
| rmap remove large PTEs | `mm/rmap.c:1672` | `folio_remove_rmap_ptes()` handles large-folio partial mapping and deferred split |
| dup anon rmap | `include/linux/rmap.h:633` | `__folio_try_dup_anon_rmap()` clears `PageAnonExclusive` and refuses pinned folios |
| share anon rmap | `include/linux/rmap.h:774` | `__folio_try_share_anon_rmap()` requires cleared PTE + memory barriers for GUP-fast |
| GUP anon pin rule | `mm/gup.c:849` | read-only FOLL_PIN on non-exclusive anon page returns `-EMLINK` to force unshare |
| GUP unshare predicate | `mm/internal.h:1471` | `gup_must_unshare()` is per-subpage and checks `PageAnonExclusive(page)` |
| PMD THP COW behavior | `mm/huge_memory.c:1891` | ordinary shared PMD THP write fault falls back to split, not huge-copy |
| PMD huge zero COW | `mm/huge_memory.c:1859` | huge zero PMD COW allocates a full PMD folio and clears/flushes PMD before mapping |
| selftest COW matrix | `tools/testing/selftests/mm/cow.c:1204` | `cow.c` already runs anon COW tests over base pages, THP sizes, PTE-mapped THP, single-PTE THP, partial mremap/shared |

## Current order-0 COW sequence

Relevant code shape from `mm/memory.c:3659`:

```c
new_folio = folio_prealloc(mm, vma, vmf->address, pfn_is_zero);
err = __wp_page_copy_user(&new_folio->page, vmf->page, vmf);
mmu_notifier_range_init(&range, MMU_NOTIFY_CLEAR, 0, mm,
        vmf->address & PAGE_MASK,
        (vmf->address & PAGE_MASK) + PAGE_SIZE);
...
ptep_clear_flush(vma, vmf->address, vmf->pte);
folio_add_new_anon_rmap(new_folio, vma, vmf->address, RMAP_EXCLUSIVE);
folio_add_lru_vma(new_folio, vma);
set_pte_at(mm, vmf->address, vmf->pte, entry);
update_mmu_cache_range(vmf, vma, vmf->address, vmf->pte, 1);
folio_remove_rmap_pte(old_folio, vmf->page, vma);
```

For an order-2 COW copy, every one-page operation above needs an explicit range/nr_pages equivalent. The most important ordering invariant is preserved in the existing comment at `mm/memory.c:3740`: clear + flush old PTE before installing the new PTE, then remove old rmap, so other processes cannot reuse/write the old page while this CPU still has a stale TLB entry.

## Baseline mTHP allocation/map sequence to reuse

Relevant code shape from `mm/memory.c:5045` and `mm/memory.c:5135`:

```c
orders = thp_vma_allowable_orders(vma, vma->vm_flags, TVA_PAGEFAULT,
                                  BIT(PMD_ORDER) - 1);
orders = thp_vma_suitable_orders(vma, vmf->address, orders);
...
addr = ALIGN_DOWN(vmf->address, PAGE_SIZE << order);
if (pte_range_none(pte + pte_index(addr), 1 << order))
        break;
...
folio = vma_alloc_folio(gfp, order, vma, addr);
mem_cgroup_charge(folio, vma->vm_mm, gfp);
folio_ref_add(folio, nr_pages - 1);
add_mm_counter(vma->vm_mm, MM_ANONPAGES, nr_pages);
folio_add_new_anon_rmap(folio, vma, addr, RMAP_EXCLUSIVE);
folio_add_lru_vma(folio, vma);
set_ptes(vma->vm_mm, addr, vmf->pte, entry, nr_pages);
update_mmu_cache_range(vmf, vma, addr, vmf->pte, nr_pages);
```

For COW, the destination side can reuse the charge/ref/rmap/install pattern, but the source side must additionally validate and remove the old PTE/rmap range.

## Required COW mTHP eligibility gates

A conservative first patch should pass all gates below or fallback to current `wp_page_copy()`.

1. Fault type gate:
   - Require `FAULT_FLAG_WRITE`.
   - Reject `FAULT_FLAG_UNSHARE`: GUP/KSM unshare is semantically per-subpage today.
   - Reject shared mappings; `do_wp_page()` already routes `VM_SHARED|VM_MAYSHARE` to `wp_page_shared()`/`wp_pfn_shared()`.

2. VMA/order policy gate:
   - Use `thp_vma_allowable_orders(vma, vma->vm_flags, TVA_PAGEFAULT, BIT(PMD_ORDER) - 1)` so sysfs/madvise/prctl/THP-disabled policy matches `alloc_anon_folio()`.
   - Use `thp_vma_suitable_orders(vma, vmf->address, orders)` so the aligned COW window does not cross VMA boundaries.
   - Prefer limiting first patch to one configured target order, e.g. `order=2`, or highest order not larger than `folio_order(old_folio)`.

3. PTE page-table gate:
   - The full target window must stay within one PTE page/PMD.
   - The PTE pointer should be obtained for `cow_addr = ALIGN_DOWN(vmf->address, PAGE_SIZE << order)`, not only the fault address.
   - Revalidate the fault PTE and all window PTEs after reacquiring PTL; any mismatch falls back/returns 0 like `wp_page_copy()`.

4. Source folio gate:
   - Require `vm_normal_page()` on every PTE in the window.
   - Require all PTEs map consecutive pages in the same anonymous large folio.
   - Require source range corresponds to a valid subrange of `old_folio`: `page_idx + nr_pages <= folio_nr_pages(old_folio)`.
   - For initial patch, strongly prefer `folio_order(old_folio) == order` to avoid creating partial old-folio mappings. Supporting subrange COW from a larger folio is possible but must accept deferred split/partial-map side effects.

5. PTE bit gate:
   - Reject non-present PTEs: swap/migration/device-private/device-exclusive entries are handled by existing per-PTE code.
   - Reject `pte_protnone()`: NUMA hinting has a separate path.
   - Reject any UFFD-WP PTE and probably reject `userfaultfd_armed(vma)` entirely in the first patch; `alloc_anon_folio()` already falls back when UFFD is armed because it needs per-page fault fidelity.
   - Decide explicitly whether dirty/young/soft-dirty bits must be identical across all PTEs. If not identical, either preserve per-PTE bits via per-PTE install or fallback.

6. Pin/GUP gate:
   - If `folio_maybe_dma_pinned(old_folio)`, fallback. Pincount is folio-wide and `folio_try_dup_anon_rmap_*()` already treats pinned large folios conservatively.
   - If `MMF_HAS_PINNED` and source folio may be pinned, fallback rather than clearing or changing exclusivity across the window.

7. KSM/zero/special gate:
   - Reject `folio_test_ksm(old_folio)`. KSM is small-folio oriented and `wp_can_reuse_anon_folio()` already rejects it.
   - Reject zero-PFN source for first patch unless all PTEs in the window are already present zero PTEs. Mapping over `pte_none` neighbors would change UFFD-missing/mincore/RSS semantics.
   - Reject PFNMAP/MIXEDMAP/special PTEs; current `__wp_page_copy_user()` has a best-effort one-page fallback for PFN mappings.

8. Accounting gate:
   - Add `MM_ANONPAGES` by `nr_pages` for the new folio.
   - If replacing anon with anon, do not net-change RSS except the existing old rmap removal/new rmap addition pattern should balance mapcounts; if replacing zero page, increment by `nr_pages` only for actually replaced present zero PTEs.
   - For a large new folio mapped by PTEs, call `folio_ref_add(new_folio, nr_pages - 1)` before install.

9. MMU notifier/TLB/cache gate:
   - Notifier range must be `[cow_addr, cow_addr + PAGE_SIZE * nr_pages)`.
   - Clear old PTEs and flush the whole range before installing new PTEs, or use a proven equivalent sequence.
   - Existing batch API: `get_and_clear_ptes(mm, cow_addr, ptep, nr_pages)` clears present PTEs mapping consecutive pages of same folio and merges dirty/young bits; pair with `flush_tlb_range(vma, cow_addr, cow_addr + size)` if accessible.
   - `ptep_clear_flush()` has only single-PTE API; looping it is correct but more expensive.
   - On arm64, `set_ptes()` may fold contiguous PTEs through `contpte_set_ptes()`; if per-PTE attributes differ, use per-PTE `set_pte_at()`/`set_ptes(...,1)` and accept no contiguous-PTE fold.

10. rmap ordering gate:
   - Install new PTE(s) only after clear+flush of old PTE(s).
   - Remove old rmap after new PTE install, same ordering principle as current `wp_page_copy()`.
   - Use `folio_remove_rmap_ptes(old_folio, old_first_page, nr_pages, vma)` only if all old PTEs were actually replaced.
   - Partial old-folio unmap will queue deferred split in `__folio_remove_rmap()` when the old large folio becomes partially mapped.

## Semantic changes to decide explicitly

### 1. Dirty bit amplification

If one write fault maps all 4 PTEs writable+dirty, the other 3 pages become dirty before userspace writes them. This mirrors `do_anonymous_page()` mTHP first-touch, but differs from current fork COW, where only the faulting base page becomes dirty. It may affect `/proc/pagemap`, `clear_refs`, page idle, and dirty/young tracking tests.

Mitigations:
- conservative: preserve each original PTE's young/dirty/soft-dirty and make only the faulting PTE writable+dirty;
- aggressive: require all PTEs in the window have homogeneous allowed bits and accept dirty amplification as a documented performance tradeoff;
- hybrid: only batch-map writable+dirty when soft-dirty tracking is not active and PTE bits are homogeneous.

### 2. Soft-dirty semantics

Current `wp_page_copy()` explicitly preserves soft-dirty/uffd-wp only for `FAULT_FLAG_UNSHARE`; ordinary write COW constructs `maybe_mkwrite(pte_mkdirty(entry), vma)`. A batch COW that writes all PTEs can mark neighboring pages soft-dirty or dirty depending on architecture/config. Existing `mprotect.c:216` says large-folio batches cannot infer all subpage exclusivity from the first page; the same applies to soft-dirty and write upgrades.

Mitigation: reject batching when `vma_soft_dirty_enabled(vma)` or when any PTE bit differs, until a per-PTE preservation path is implemented.

### 3. UFFD fidelity

`alloc_anon_folio()` skips mTHP when `userfaultfd_armed(vma)` because missing/wp events need per-page fidelity. A COW mTHP path that clears or replaces 4 PTEs can suppress expected UFFD-WP events on neighboring pages. First patch should skip all UFFD-armed VMAs.

### 4. Zero-page COW

Writing a zero page currently allocates one private base page. Allocating a 16KB folio and installing PTEs over not-yet-faulted neighbors would change population/RSS/mincore/UFFD-missing behavior. First patch should skip zero PFN, or only batch when every PTE in the window is an already-present zero PTE.

### 5. Partial source folio COW

If source folio is larger than target order, replacing one order-2 subrange makes the old folio partially mapped in this mm and triggers deferred split pressure. This may be fine but changes reclaim behavior and mTHP split counters. First patch should use `folio_order(old_folio) == order` unless the goal explicitly includes subrange COW from larger THP.

### 6. GUP/pin visibility

Pinned folios cannot be randomly replaced behind the pin. Fork-time rmap code forces early copy when pinned. A write-fault-time batch copy must never replace neighboring subpages that are pinned or might need unshare semantics. Since pincount is folio-wide, fallback on any `folio_maybe_dma_pinned(old_folio)`.

### 7. Mlocked VMA

`folio_add_new_anon_rmap()` only calls `mlock_vma_folio()` when the folio is fully mapped to the VMA. A batch COW of the full mTHP window is okay; a partial subrange from a larger folio can leave mlock/reclaim behavior different for old and new folios. Test `VM_LOCKED` separately.

### 8. NUMA placement and base address

`folio_prealloc()` and `vma_alloc_folio()` follow VMA placement rules. The COW mTHP allocation should use `cow_addr`, not `vmf->address`, so interleave/bind placement covers the whole new folio consistently.

### 9. Concurrent faults in same window

Two threads can fault different subpages of the same old mTHP. The losing thread must observe PTE mismatch after PTL reacquire and return 0, not remove rmap twice. This requires validating all PTEs in the window under PTL just before clear/install.

### 10. MMU notifier consumers

Secondary MMUs rely on `MMU_NOTIFY_CLEAR` range. Expanding only the PTE clear without expanding the notifier range is a correctness bug. Keep notifier range exactly equal to replaced PTE window.

## Patch design candidates

### Candidate A: conservative full-copy, per-PTE permission preservation

- Allocate/copy an order-2 folio.
- Replace all 4 PTEs, but preserve per-PTE young/dirty/soft-dirty as much as possible.
- Only the faulting PTE becomes writable+dirty; neighbors remain read-only/exclusive, so later writes take cheap reuse faults instead of copy faults.
- Pros: minimal semantic drift.
- Cons: more code; cannot use a single homogeneous `set_ptes()` for all entries; less contpte benefit.

### Candidate B: homogeneous full-copy, all writable+dirty

- Allocate/copy an order-2 folio.
- Require all source PTEs have homogeneous allowed bits and no soft-dirty/UFFD complications.
- Install all 4 PTEs writable+dirty using `set_ptes()`.
- Pros: simple, maximizes “one COW fault covers one mTHP” goal.
- Cons: dirty/write fault amplification; must be justified and tested.

### Candidate C: order-2-only experimental path behind knob

- Implement Candidate B or A but gated by a boot/debug knob, e.g. `mthp_cow_order2=1` or debugfs.
- Add counters/tracepoints focused on fallback reasons, not smoke-only attempts/successes in formal reason tables.
- Best first experimental path in Android/Pixels because rollback and A/B comparison are easy.

## Suggested first implementation skeleton

```c
static vm_fault_t wp_page_copy_mthp(struct vm_fault *vmf, struct folio *old_folio)
{
        order = choose_order2_or_allowed_order(vmf, old_folio);
        nr = 1U << order;
        cow_addr = ALIGN_DOWN(vmf->address, PAGE_SIZE << order);
        old_first = vmf->page - ((vmf->address - cow_addr) >> PAGE_SHIFT);

        if (!eligible_vma_order(vmf->vma, cow_addr, order))
                return VM_FAULT_FALLBACK;
        if (vmf->flags & FAULT_FLAG_UNSHARE)
                return VM_FAULT_FALLBACK;
        if (userfaultfd_armed(vmf->vma))
                return VM_FAULT_FALLBACK;
        if (folio_maybe_dma_pinned(old_folio) || folio_test_ksm(old_folio))
                return VM_FAULT_FALLBACK;
        if (folio_order(old_folio) != order)
                return VM_FAULT_FALLBACK;

        new = vma_alloc_folio(vma_thp_gfp_mask(vma), order, vma, cow_addr);
        charge/copy all nr pages; __folio_mark_uptodate(new);

        mmu_notifier_range_init(&range, MMU_NOTIFY_CLEAR, 0, mm, cow_addr,
                                cow_addr + (PAGE_SIZE << order));
        mmu_notifier_invalidate_range_start(&range);
        pte = pte_offset_map_lock(mm, vmf->pmd, cow_addr, &ptl);
        if (!all nr PTEs still match expected old folio/PTE bits)
                release;
        clear+flush nr old PTEs;
        folio_ref_add(new, nr - 1);
        add_mm_counter(mm, MM_ANONPAGES, nr);
        folio_add_new_anon_rmap(new, vma, cow_addr, RMAP_EXCLUSIVE);
        folio_add_lru_vma(new, vma);
        install nr new PTEs;
        update_mmu_cache_range(vmf, vma, cow_addr, pte, nr);
        folio_remove_rmap_ptes(old_folio, old_first, nr, vma);
        unlock/end notifier/drop refs;
}
```

The skeleton is intentionally conservative; actual code should use existing helpers where possible and should not duplicate `wp_page_copy()` logic without preserving its clear/flush/rmap ordering.

## Test plan

### Unit/selftest layer

1. Extend `tools/testing/selftests/mm/cow.c`:
   - Add a dedicated mTHP COW allocation test that enables one non-PMD order (prefer order-2 / 16KB when available), populates an mTHP, forks, child writes one subpage, then verifies:
     - parent content unchanged;
     - child content correct for all subpages;
     - all subpages remain populated;
     - old parent does not observe child writes.
   - Reuse `detect_thp_sizes()` and `thp_push_settings()` at `cow.c:1204`.

2. Add semantic tests:
   - write fault on first/middle/last subpage of the mTHP window;
   - VMA-left/right boundary cases where `ALIGN_DOWN()` window crosses `vm_start`/`vm_end`, expecting fallback order-0;
   - partial old folio via `MADV_DONTNEED`, expecting fallback;
   - PTE-mapped THP via `mprotect()` at `cow.c:873`, expecting either batch copy or safe fallback;
   - soft-dirty after `clear_refs`: verify only intended pages become soft-dirty, or document all-window dirtying if Candidate B chosen;
   - UFFD-WP armed range: expect no mTHP batch COW and no lost UFFD events;
   - GUP pin before fork / child write: expect no replacement of pinned data;
   - mlock range: no reclaim/swap surprise and no crash.

3. Add observability:
   - Count `anon_fault_alloc` for the chosen mTHP size before/after if using existing stats is acceptable;
   - For COW-specific behavior, prefer temporary debugfs/tracepoint fallback reason buckets during experiment, not permanent smoke counters.

### Stress layer

1. Fork/write storm:
   - parent allocates many order-2 mTHPs;
   - N children write random subpages and exit;
   - parent verifies checksums after each child.

2. Race stress:
   - concurrent child COW, `mprotect(PROT_READ/WRITE)`, `madvise(MADV_DONTNEED)`, `mremap`, and `MADV_PAGEOUT` on neighboring subranges.

3. Pin stress:
   - combine `gup_test` / io_uring fixed buffer patterns from `cow.c` with child writes to random subpages.

4. UFFD stress:
   - `uffd-wp` registered on the range, then fork and write; verify event counts and no neighbor event loss.

5. Reclaim/swap stress:
   - enable swap, repeatedly `MADV_PAGEOUT`, child COW, and validate no WARNs from mapcount/refcount paths.

## Recommended first patch scope

- Implement behind an experimental knob first.
- Only anonymous private mappings.
- Only `FAULT_FLAG_WRITE`, not `FAULT_FLAG_UNSHARE`.
- Skip `userfaultfd_armed(vma)`.
- Skip zero PFN, KSM, special, non-present, migration/swap/device entries.
- Skip pinned folios.
- Start with `folio_order(old_folio) == target_order == 2`.
- Preserve current `wp_page_copy()` fallback for every rejected case.
- Add minimal fallback reason trace/debug counters during experiment.

## Promotion notes

This reference promotes `.worklog/opencode-mthp-cow-selftests.md` and `.worklog/opencode-mthp-cow-rmap-specials.md` into the owning mTHP skill. The raw `.worklog` reports can be deleted after the goal is complete; they are no longer the sole source of reusable knowledge.
