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
- Full high-overhead June 2026 experiment archive lives at `/home/nzzhao/.agents/skills/archive/android-mthp-experiment-20260629`; use it only when restoring the complete trace/counter/swap-birth-record instrumentation.

## Workflow Contract

### Main Workflow
1. Identify the userspace page-size override and kernel base-page/mTHP settings.
2. Search ART GC, Scudo, bionic allocator, and kernel VMA/mTHP callsites.
3. Classify each callsite by syscall alignment, allocator metadata, UFFD registration, or VMA merge dependency.
4. Validate VMA merge risk against kernel `vma_merge` predicates and `thp_vma_suitable_order()` boundaries.
5. Report the decision path, crash risk, and any fallback counters or tracepoints to collect next.

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| CVD x86 kernel build | Need to rebuild the x86_64 Cuttlefish kernel dist from `/home/nzzhao/learn_os/pixel/common_my_dec` | Run `/home/nzzhao/learn_os/pixel/build_x86_64.sh` (default lane `my_dec`, target `//common_my_dec:kernel_x86_64_dist`) | `out/kernel_x86_64/dist/{bzImage,vmlinux,boot.img,system_dlkm.erofs.img}` exist and `build_x86_64.sh status` records `exit_code=0` | Inspect `out/kernel_x86_64/logs/build_x86_64-*.log`; use `build_x86_64.sh stop` rather than `pkill -f` | replace handwritten Bazel command; then follow CVD custom-kernel/DLKM image pairing workflow |
| Preflight | Userspace `get_page_size` or equivalent is forced to 16KB on a 4KB base-page kernel | Run the search commands in `references/userspace-forced-16kb-page-size-audit.md` | Raw ART/scudo/bionic/kernel artifacts exist with file:line matches | Broaden search to `PAGE_SIZE`, `gPageSize`, `mmap`, `madvise`, `UFFD`, and `remap` | block runtime conclusion until callsites are classified |
| ART GC | Mark-compact uses partial UFFD registration or `MADV_DONTNEED` | Inspect `KernelPreparation()`, UFFD ioctl helpers, and page-status arrays | Ranges are multiples of forced 16KB and therefore kernel-page aligned | Trace `UFFDIO_*` errno and VMA split/unregister path | branch to UFFD/VMA split analysis |
| Scudo | Secondary allocator remaps or releases cached mappings | Inspect `mapSecondary()`, `MemMapLinux::remapImpl()`, and release paths | Remap address/size are 16KB aligned and no `MAP_FIXED` failure is observed | Capture failing address, size, flags, and neighboring VMAs | branch to fixed-address remap diagnosis |
| Bionic | Small-object allocator maps one allocator page per chunk | Inspect `BionicSmallObjectAllocator::alloc_page()` and `page_start()` metadata lookup | `page_size()`, `mmap`, `munmap`, and metadata lookup all use the same forced unit | Check invalid pointer/page signature failures | continue unless metadata lookup is inconsistent |
| VMA Merge | Concern that 16KB alignment changes neighboring VMA merge | Inspect `vma_merge` predicates and `thp_vma_suitable_order()` | Merge predicates fail/pass due to flags/file/policy/UFFD/name/anon_vma/pgoff, not userspace page-size alone | Add targeted tracepoint/log for prev/middle/next predicates | replace alignment-only explanation with predicate evidence |
| UFFD order-2 mfill | Restoring or changing the experimental UFFD anonymous order-2 fast path | Default the fast path off, expose a bool core kernel parameter with `core_param(uffd_mfill_order2, ..., bool, ...)`, keep debugfs `enabled`/`stats`, and enable B runs with kernel cmdline `uffd_mfill_order2=1` | `/sys/kernel/debug/uffd_mfill_order2/stats` reports `enabled 0` for A and `enabled 1` plus successes for B | If only debugfs is written after boot, restart the guest because startup ART UFFD events cannot be recovered | branch A/B runs by kernel parameter, not by post-boot mutation |
| COW order-2 mTHP | Changing or measuring the experimental `do_wp_page()` order-2 COW fast path | Default `mthp_cow_order2` off and expose it as a bool `core_param`; enable only target B/full16K boots with early kernel cmdline `mthp_cow_order2=1` | A boots show `/sys/module/kernel/parameters/mthp_cow_order2=N`; B/full16K boots show cmdline and parameter enabled before app startup; COW vmstat counters move only in enabled cells | If the parameter is written only after boot, treat startup COW attribution as contaminated and reboot with the intended cmdline | branch A/B runs by boot parameter, not by post-boot mutation |
| CVD Kernel Bring-Up | Kernel build prints `Kernel ABI header` warnings from `tools/perf` or `tools/objtool` | Treat as a tools header-sync warning, not Android GKI/KMI enforcement; separately check vendor ramdisk module ABI | `tools/perf/check-headers.sh` or `tools/objtool/sync-check.sh` emits the warning, and `modinfo` vermagic for ramdisk modules matches the booted kernel | If first-stage init reports `insmod ... Exec format error`, replace/rebuild vendor ramdisk modules for the new kernel | branch away from perf header sync; block only on module ABI/vermagic mismatch |
| CVD x86 mmap Alignment | Pixel arm64 `generic_get_unmapped_area()` 16KB patch works, but CVD x86_64 OAT BSS runtime address remains 4KB-skewed | Patch `arch/x86/kernel/sys_x86_64.c` because x86_64 defines `HAVE_ARCH_UNMAPPED_AREA`; apply the 16KB mask to bottom-up and topdown non-`MAP_FIXED` paths | `arch/x86/kernel/sys_x86_64.c` sets `info.align_mask` for both arch paths, kernel dist rebuilds, and OAT BSS `unaligned bss` disappears from logcat | If skew remains, inspect ART ELF `local_reservation.Begin()` and `/proc/<pid>/maps` for actual reservation base | replace generic-only kernel assumption for CVD x86 |
| CVD Custom Kernel Images | Booting a self-built CVD x86 kernel with changed module ABI | Rebuild first-stage vendor ramdisk modules, replace `system_dlkm` in super with matching kernel dist `system_dlkm.flatten.*.img`, and refresh `vbmeta_system_dlkm` | Kernel log loads `/lib/modules/*` and `/system/lib/modules/*` without `this_module section size` aborts | If `/vendor/bin/dlkm_loader` aborts on old virtual-device vendor modules, replace `vendor_dlkm` with matching modules or an empty experimental image plus refreshed `vbmeta_vendor_dlkm` | block ART validation until dlkm_loader no longer aborts |
| CVD SELinux Noise | Experimental empty or mismatched vendor_dlkm lets ART pass but SystemServer dies on ashmem/device SELinux denials | Relaunch only the validation run with `--extra_bootconfig_args='androidboot.selinux=permissive'` and clearly mark the result as permissive | logcat shows `permissive=1`, `BOOT_COMPLETED`, and no ART OAT/MarkCompact blockers | If boot still fails, treat the new fatal as a separate platform/policy issue, not an ART alignment blocker unless ART markers recur | branch runtime validation while preserving the ART alignment conclusion |
| do_wp_page mTHP COW | Designing or patching write-fault COW to copy an anonymous mTHP/order-2 window | Read `references/do-wp-page-mthp-cow-audit-20260710.md`, start with `FAULT_FLAG_WRITE` only, skip UFFD-armed/KSM/zero/special/non-present/pinned/partial-source-folio cases, and keep order-0 `wp_page_copy()` fallback | File:line audit covers `do_wp_page`, `wp_page_copy`, `alloc_anon_folio`, rmap/GUP/UFFD/KSM and test entry points; candidate patch validates all PTEs under PTL before clear/install | If any gate is not mechanically verifiable, do not batch COW; collect a fallback reason and return to current order-0 path | branch experimental patch behind a knob or tight order-2 gate before broad enablement |
| Pixel common object validation | Temporary `gki_defconfig` object build fails in `scripts/gendwarfksyms` with missing host `dwarf.h` before reaching the target object | For syntax-only local validation, use a temporary `O=` tree and disable `CONFIG_GENDWARFKSYMS`, `CONFIG_DEBUG_INFO_BTF`, and `CONFIG_DEBUG_INFO_BTF_MODULES` before `olddefconfig`; keep real release/KMI builds on the proper host dependency set | Target object such as `mm/memory.o` compiles in the temp tree and the config delta is isolated outside the source tree | If ABI/KMI, BTF, or release validation is required, install the missing DWARF/BTF host dependencies instead of disabling these configs | branch local syntax validation only; do not treat it as a release-build substitute |

### Output Contract
- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:
