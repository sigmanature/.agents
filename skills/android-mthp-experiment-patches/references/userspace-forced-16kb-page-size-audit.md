# Userspace Forced-16KB Page-Size Audit

Use this reference when userspace page-size APIs are forced to report 16KB while the kernel still has 4KB base pages and mTHP/file-folio experiments are enabled.

## Scope

Search these domains before judging crash or VMA-merge risk:

- ART GC and `MemMap`: `art/runtime/gc`, `art/runtime/base/gc_visited_arena_pool.*`, `art/libartbase/base/mem_map.*`.
- Scudo allocator: `external/scudo/standalone`, `external/compiler-rt/lib/scudo`, `external/compiler-rt/lib/sanitizer_common`.
- Bionic internal allocator: `bionic/libc/bionic/bionic_allocator.cpp`, `bionic/libc/private/bionic_allocator.h`, `bionic/libc/platform/bionic/page.h`.
- Kernel VMA/mTHP logic: `mm/vma.c`, `mm/vma.h`, `include/linux/userfaultfd_k.h`, `include/linux/huge_mm.h`, `mm/memory.c`, `mm/shmem.c`.

## Reusable Search

Run from the Android tree and adjust the kernel path if needed:

```bash
mkdir -p /tmp/android_pagesize_scan
rg -n -S "GetPageSize\\(|get_page_size\\(|page_size\\(|PageSize\\(|gPageSize|DivideByPageSize|madvise|MADV_DONTNEED|userfaultfd|UFFD" \
  art/runtime/gc art/runtime/base/gc_visited_arena_pool.* art/libartbase/base/mem_map.* \
  > /tmp/android_pagesize_scan/art_gc_precise.txt
rg -n -S "getPageSizeCached\\(|getPageSize\\(|getPageSizeSlow\\(|mmapWrapper|remap|mmap|munmap|madvise|MADV_DONTNEED|releaseAndZeroPagesToOS" \
  external/scudo/standalone external/compiler-rt/lib/scudo external/compiler-rt/lib/sanitizer_common \
  > /tmp/android_pagesize_scan/scudo_precise.txt
rg -n -S "page_size\\(|page_start\\(|page_end\\(|mmap\\(|munmap\\(|BionicSmallObjectAllocator|alloc_mmap|kSmallObject" \
  bionic/libc/bionic/bionic_allocator.cpp bionic/libc/private/bionic_allocator.h bionic/libc/platform/bionic/page.h \
  > /tmp/android_pagesize_scan/bionic_alloc_precise.txt
rg -n -S "vma_merge|can_vma_merge|is_mergeable|anon_vma|vm_userfaultfd_ctx|thp_vma_suitable_orders|userfaultfd_armed|large folio|folio_order" \
  /home/nzzhao/learn_os/pixel/common_my_dec/mm /home/nzzhao/learn_os/pixel/common_my_dec/include/linux \
  /home/nzzhao/learn_os/pixel/common_my_dec/fs/f2fs /home/nzzhao/learn_os/pixel/common_my_dec/fs/ext4 \
  > /tmp/android_pagesize_scan/kernel_vma_mthp.txt
```

## Interpretation Notes

- ART `MemMap::GetPageSize()` and `gPageSize` become the unit for GC page-status arrays, `madvise(MADV_DONTNEED)`, and UFFD copy/move/zeropage ranges. Treat mismatched runtime/kernel page size as legal only if every syscall range remains aligned to the real kernel page size; 16KB satisfies that on 4KB kernels.
- ART mark-compact UFFD single-page paths pass `gPageSize` to `UFFDIO_COPY`, `UFFDIO_MOVE`, and `UFFDIO_ZEROPAGE`; forcing `gPageSize` to 16KB therefore makes those requests 16KB. Some optimized paths intentionally batch contiguous pages and pass `N * gPageSize`; keep this if the kernel fast path accepts 16KB-aligned multiples, or split the userspace ioctl loop into 16KB chunks only when a targeted order-2 experiment requires exactly one 16KB folio per ioctl.
- ART mark-compact has an explicit UFFD split workaround: it faults a page before registering a partial moving-space range so split VMAs get compatible `anon_vma` state and can merge after unregister.
- Scudo standalone remap uses fixed-address `mmap(MAP_FIXED)` through `MemMapLinux::remapImpl`; forcing 16KB mainly changes guard/remap/release granularity and can increase virtual/RSS retention, but fixed 16KB-aligned ranges are also 4KB-aligned.
- In the Pixel `common_my_dec` branch, non-`MAP_FIXED` `generic_get_unmapped_area()`/topdown already use a `PAGE_SIZE << 2` alignment mask for returned addresses. Bionic small-object anonymous `mmap(NULL, ...)` therefore does not need userspace overmap-and-trim for the VMA start; it still needs to pass a 16KB length and use the same 16KB unit for metadata lookup/free.
- Bionic internal small-object allocator maps one allocator-page chunk per small-object page and locates metadata with `page_start()`. If forced to 16KB consistently for this allocator, metadata lookup remains self-consistent; the allocator does not require one VMA per small-object page.
- Scudo fixed-address remap remains the special case: `MAP_FIXED` returns the requested address unchanged and only traces unaligned fixed anonymous/shmem mappings in `common_my_dec`, so Scudo must make the reserved base, remap address, and remap size 16KB aligned before calling `MemMapLinux::remapImpl()`.
- Kernel `vma_merge` does not inspect userspace page-size API results. It requires exact adjacency plus compatible flags, file, policy, UFFD context, anon VMA name, anon_vma, and pgoff continuity.
- `thp_vma_suitable_order()` refuses an order when `ALIGN_DOWN(addr, PAGE_SIZE << order)` would extend outside the VMA, so mTHP/order-2 folios fallback instead of crossing into a neighboring VMA.
