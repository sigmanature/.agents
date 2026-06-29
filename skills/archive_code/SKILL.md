# archive_code — vma_debug: Enrich Kernel Trace Events with VMA Context

## Purpose

Teach AI agents how to add rich VMA debug context (name, process identity, vma range) to existing Linux kernel trace events. This skill documents a real, proven patch that was applied to the Android Common Kernel (Pixel tree) to aid debugging of deferred split / THP collapse issues by making tracepoints carry actionable VMA identity information.

## When to Use This Skill

Use when an AI agent needs to:
- Add process/VMA identity to existing kernel tracepoints for debugging
- Extend function signatures to carry debug context through the callchain to tracepoints
- Enrich `ftrace` / `perf` trace events with context that survives post-mortem analysis
- Understand the pattern of "propagate debug info → tracepoint" in the Linux kernel
- Reproduce a similar VMA-debugging instrumentation in another kernel subsystem

## Design Intent

### Problem Being Solved

When debugging deferred split / THP collapse issues on Android, the existing `mm_folio_deferred_split` and `mm_madvise_dontneed` trace events provided only the folio's PFN, order, and a reason code. This lacked critical context for post-mortem analysis:

1. **Which process triggered the split?** — PID, TGID, comm
2. **Which VMA was involved?** — vma range [start, end]
3. **What is the VMA's logical name?** — anon_vma_name (set via `prctl(PR_SET_VMA, ...)`)
4. **Was the reason propagated correctly?** — the old code hardcoded `reason=0` in the trace call regardless of the actual reason

### Solution Strategy: "Context Propagation Chain"

The core architecture is a three-layer pattern:

```
Layer 1: Call Site            Layer 2: Core Function          Layer 3: Tracepoint
─────────────                 ──────────────────              ─────────────────
passes reason + vma    →     carries them through     →     records them into
to deferred_split_folio()    to the tracepoint              the ftrace ring buffer
```

**Key insight**: The tracepoint was already there, but the context was thrown away at Layer 1. The fix is to plumb the context through Layer 2.

### What Each Change Does

| File | Change | Why |
|------|--------|-----|
| `include/linux/huge_mm.h` | Extend `deferred_split_folio()` signature with `reason` + `vma` | Core plumbing: carry context through |
| `include/trace/events/huge_memory.h` | Enhance 2 trace events with process + VMA identity fields | Output: what gets recorded |
| `mm/huge_memory.c` | Accept new params, pass `reason` to tracepoint | Implementation: stop hardcoding reason=0 |
| `mm/khugepaged.c` | Pass `DSR_KHUGEPAGED` + vma at call site | Layer 1: khugepaged collapse path |
| `mm/rmap.c` | Pass `DSR_PARTIALLY_MAPPED` + vma at call site | Layer 1: rmap unmap path |

### Design Principles for Similar Work

1. **Minimal footprint**: Only 5 files changed, all changes are additive to existing trace events — no new tracepoints, no new files
2. **No behavioral change**: The trace events are for debugging only; no logic is altered
3. **Safe to revert**: The changes are self-contained; reverting them has zero functional impact
4. **Use existing infrastructure**: `anon_vma_name()` is a standard kernel API; `TASK_COMM_LEN`, `current->pid`, etc. are always available
5. **VMA can be NULL**: All VMA access uses `vma ? vma->field : default` guards — the deferred split queue can be entered without a VMA context

---

## Full Code Diff (The Actual Patch)

### Patch Header

```
From: vma_debug instrumentation
Subject: [PATCH] enrich mm trace events with VMA debug context

Add process identity (pid/tgid/comm), VMA range, and VMA name to
mm_folio_deferred_split and mm_madvise_dontneed tracepoints for
post-mortem debugging of deferred split / THP collapse issues.

Extend deferred_split_folio() signature to carry reason code and
VMA pointer through to the tracepoint, replacing the hardcoded
reason=0 with the actual reason.
```

---

### File 1: `include/linux/huge_mm.h`

**Purpose**: Extend the `deferred_split_folio()` signature to carry debug context.

```diff
diff --git a/include/linux/huge_mm.h b/include/linux/huge_mm.h
--- a/include/linux/huge_mm.h
+++ b/include/linux/huge_mm.h
@@ -425,7 +425,8 @@ static inline int split_huge_page(struct page *page)
 {
 	return split_huge_page_to_list_to_order(page, NULL, 0);
 }
-void deferred_split_folio(struct folio *folio, bool partially_mapped);
+void deferred_split_folio(struct folio *folio, bool partially_mapped,
+			  unsigned int reason, struct vm_area_struct *vma);
```

**For the inline stub (NOMMU / CONFIG_TRANSPARENT_HUGEPAGE=n path):**

```diff
@@ -625,7 +626,9 @@ static inline int try_folio_split_to_order(struct folio *folio,
 
-static inline void deferred_split_folio(struct folio *folio, bool partially_mapped) {}
+static inline void deferred_split_folio(struct folio *folio, bool partially_mapped,
+					unsigned int reason,
+					struct vm_area_struct *vma) {}
```

**⚠️ Common pitfall**: You MUST update BOTH the declaration AND the inline stub, or you'll get linker errors on NOMMU builds.

---

### File 2: `include/trace/events/huge_memory.h`

**Purpose**: Enrich the trace events with VMA + process context.

Two trace events are modified:

#### 2a: `mm_folio_deferred_split`

```diff
 TRACE_EVENT(mm_folio_deferred_split,
-	TP_PROTO(struct folio *folio, unsigned int reason),
-	TP_ARGS(folio, reason),
+	TP_PROTO(struct folio *folio, unsigned int reason,
+		 struct vm_area_struct *vma),
+	TP_ARGS(folio, reason, vma),
 	TP_STRUCT__entry(
 		__field(unsigned long, pfn)
 		__field(unsigned int, order)
 		__field(unsigned int, reason)
+		__field(pid_t, pid)
+		__field(pid_t, tgid)
+		__array(char, comm, TASK_COMM_LEN)
+		__array(char, leader_comm, TASK_COMM_LEN)
+		__field(unsigned long, mm)
+		__field(unsigned long, vma_start)
+		__field(unsigned long, vma_end)
+		__string(vma_name, vma && anon_vma_name(vma) ?
+			 anon_vma_name(vma)->name : "")
 	),
 	TP_fast_assign(
 		__entry->pfn = folio_pfn(folio);
 		__entry->order = folio_order(folio);
 		__entry->reason = reason;
+		__entry->pid = current->pid;
+		__entry->tgid = current->tgid;
+		memcpy(__entry->comm, current->comm, TASK_COMM_LEN);
+		memcpy(__entry->leader_comm,
+		       current->group_leader ? current->group_leader->comm :
+		       current->comm, TASK_COMM_LEN);
+		__entry->mm = (unsigned long)(vma ? vma->vm_mm : current->mm);
+		__entry->vma_start = vma ? vma->vm_start : 0;
+		__entry->vma_end = vma ? vma->vm_end : 0;
+		__assign_str(vma_name);
 	),
-	TP_printk("pfn=0x%lx order=%u reason=%s",
+	TP_printk("pfn=0x%lx order=%u reason=%s pid=%d tgid=%d comm=%s leader_comm=%s mm=0x%lx vma=[0x%lx-0x%lx] vma_name=\"%s\"",
 		__entry->pfn, __entry->order,
 		__print_symbolic(__entry->reason,
 			{ 0, "PARTIALLY_MAPPED" }, { 1, "ZAP" },
-			{ 2, "KHUGEPAGED" }))
+			{ 2, "KHUGEPAGED" }),
+		__entry->pid, __entry->tgid, __entry->comm,
+		__entry->leader_comm, __entry->mm, __entry->vma_start,
+		__entry->vma_end, __get_str(vma_name))
 );
```

**Key patterns to note:**
- `__field` for scalar types (pid_t, unsigned long)
- `__array` for fixed-size char arrays (comm)
- `__string` for variable-length strings with inline conditional (vma_name)
- VMA access always guarded: `vma ? vma->field : fallback`
- `current->` is always available; VMA may not be

#### 2b: `mm_madvise_dontneed`

Same pattern, applied to a different existing trace event:

```diff
 TRACE_EVENT(mm_madvise_dontneed,
 		 unsigned long len),
 	TP_ARGS(vma, start, len),
 	TP_STRUCT__entry(
+		__field(pid_t, pid)
+		__field(pid_t, tgid)
+		__array(char, comm, TASK_COMM_LEN)
+		__array(char, leader_comm, TASK_COMM_LEN)
+		__field(unsigned long, mm)
 		__field(unsigned long, vma_start)
 		__field(unsigned long, vma_end)
 		__field(unsigned long, start)
 		__field(unsigned long, len)
+		__string(vma_name, vma && anon_vma_name(vma) ?
+			 anon_vma_name(vma)->name : "")
 	),
 	TP_fast_assign(
+		__entry->pid = current->pid;
+		__entry->tgid = current->tgid;
+		memcpy(__entry->comm, current->comm, TASK_COMM_LEN);
+		memcpy(__entry->leader_comm,
+		       current->group_leader ? current->group_leader->comm :
+		       current->comm, TASK_COMM_LEN);
+		__entry->mm = (unsigned long)(vma ? vma->vm_mm : current->mm);
 		__entry->vma_start = vma->vm_start;
 		__entry->vma_end = vma->vm_end;
 		__entry->start = start;
 		__entry->len = len;
+		__assign_str(vma_name);
 	),
-	TP_printk("vma=0x%lx-0x%lx start=0x%lx len=%lu",
+	TP_printk("pid=%d tgid=%d comm=%s leader_comm=%s mm=0x%lx vma=[0x%lx-0x%lx] start=0x%lx len=%lu end=0x%lx vma_name=\"%s\"",
+		__entry->pid, __entry->tgid, __entry->comm,
+		__entry->leader_comm, __entry->mm, __entry->vma_start,
+		__entry->vma_end, __entry->start, __entry->len,
+		__entry->start + __entry->len, __get_str(vma_name))
 );
```

**Note**: In this trace event, `vma` was already a parameter (unlike `mm_folio_deferred_split` which had no vma parameter at all), so we only add the process identity + vma_name fields here.

---

### File 3: `mm/huge_memory.c`

**Purpose**: Accept the new params in `deferred_split_folio()` and pass the real `reason` to the tracepoint.

```diff
--- a/mm/huge_memory.c
+++ b/mm/huge_memory.c
@@ -1280,7 +1280,7 @@ static vm_fault_t __do_huge_pmd_anonymous_page(struct vm_fault *vmf)
 		map_anon_folio_pmd(folio, vmf->pmd, vma, haddr);
 		mm_inc_nr_ptes(vma->vm_mm);
 		this_cpu_inc(deferred_split_reason_counts[DSR_ZAP]);
-		deferred_split_folio(folio, false);
+		deferred_split_folio(folio, false, DSR_ZAP, vma);
 		spin_unlock(vmf->ptl);
 	}
 
@@ -4055,7 +4055,8 @@ bool __folio_unqueue_deferred_split(struct folio *folio)
 }
 
-void deferred_split_folio(struct folio *folio, bool partially_mapped)
+void deferred_split_folio(struct folio *folio, bool partially_mapped,
+			  unsigned int reason, struct vm_area_struct *vma)
 {
 	struct deferred_split *ds_queue = get_deferred_split_queue(folio);
 	...
@@ -4100,7 +4101,7 @@ void deferred_split_folio(struct folio *folio, bool partially_mapped)
 	if (list_empty(&folio->_deferred_list)) {
 		list_add_tail(&folio->_deferred_list, &ds_queue->split_queue);
 		ds_queue->split_queue_len++;
-		trace_mm_folio_deferred_split(folio, 0);
+		trace_mm_folio_deferred_split(folio, reason, vma);
```

**The critical fix**: The old code always passed `reason=0` to the tracepoint, meaning every deferred split looked like a "PARTIALLY_MAPPED" case in traces regardless of the actual trigger. Now the real reason flows through.

---

### File 4: `mm/khugepaged.c`

**Purpose**: Update khugepaged collapse path to pass context.

```diff
--- a/mm/khugepaged.c
+++ b/mm/khugepaged.c
@@ -1237,7 +1237,7 @@ static int collapse_huge_page(struct mm_struct *mm, unsigned long address,
 	set_pmd_at(mm, address, pmd, _pmd);
 	update_mmu_cache_pmd(vma, address, pmd);
 	this_cpu_inc(deferred_split_reason_counts[DSR_KHUGEPAGED]);
-	deferred_split_folio(folio, false);
+	deferred_split_folio(folio, false, DSR_KHUGEPAGED, vma);
```

---

### File 5: `mm/rmap.c`

**Purpose**: Update rmap unmap path to pass context.

```diff
--- a/mm/rmap.c
+++ b/mm/rmap.c
@@ -1764,7 +1764,7 @@ static __always_inline void __folio_remove_rmap(struct folio *folio,
 	    !folio_test_partially_mapped(folio)) {
 		trace_mm_folio_partial_unmap(folio, page, nr, nr_pmdmapped);
 		this_cpu_inc(deferred_split_reason_counts[DSR_PARTIALLY_MAPPED]);
-		deferred_split_folio(folio, true);
+		deferred_split_folio(folio, true, DSR_PARTIALLY_MAPPED, vma);
 	}
```

**Note**: rmap.c already had `vma` in scope (it's a parameter of `__folio_remove_rmap`), so we just pass it through.

---

## How to Create a Similar Patch (AI Agent Guide)

### Step-by-Step Execution

When asked to add VMA debug context to kernel trace events, follow this checklist:

#### Phase 1: Identify the Tracepoints

1. Find the tracepoint(s) you want to enrich in `include/trace/events/`
2. Check what context is already available in `TP_PROTO` / `TP_ARGS`
3. Determine what's missing: pid/tgid/comm, VMA range, VMA name, mm pointer

#### Phase 2: Determine the Context Propagation Path

1. **If the tracepoint already receives a VMA**: Just add fields — no signature changes needed upstream
2. **If the tracepoint doesn't receive a VMA**: You need to plumb `vma` through the callchain
   - Trace the callers: who calls the function that triggers this tracepoint?
   - Do those callers have `vma` in scope?
   - If yes: extend the intermediate function signature
   - If no: you may need a larger refactor — reconsider scope

#### Phase 3: Add Fields to the Trace Event (3 sections to edit)

For each trace event, edit ALL THREE of these sections inside `TRACE_EVENT()`:

1. **`TP_STRUCT__entry`** — Declare storage fields
2. **`TP_fast_assign`** — Populate fields at trace time
3. **`TP_printk`** — Format output string

**Standard fields to add** (pick from this template):

```c
/* In TP_STRUCT__entry: */
__field(pid_t, pid)
__field(pid_t, tgid)
__array(char, comm, TASK_COMM_LEN)
__array(char, leader_comm, TASK_COMM_LEN)
__field(unsigned long, mm)
__field(unsigned long, vma_start)
__field(unsigned long, vma_end)
__string(vma_name, vma && anon_vma_name(vma) ?
         anon_vma_name(vma)->name : "")

/* In TP_fast_assign: */
__entry->pid = current->pid;
__entry->tgid = current->tgid;
memcpy(__entry->comm, current->comm, TASK_COMM_LEN);
memcpy(__entry->leader_comm,
       current->group_leader ? current->group_leader->comm :
       current->comm, TASK_COMM_LEN);
__entry->mm = (unsigned long)(vma ? vma->vm_mm : current->mm);
__entry->vma_start = vma ? vma->vm_start : 0;
__entry->vma_end = vma ? vma->vm_end : 0;
__assign_str(vma_name);

/* In TP_printk (add to format string): */
" pid=%d tgid=%d comm=%s leader_comm=%s mm=0x%lx vma=[0x%lx-0x%lx] vma_name=\"%s\"",
__entry->pid, __entry->tgid, __entry->comm,
__entry->leader_comm, __entry->mm, __entry->vma_start,
__entry->vma_end, __get_str(vma_name)
```

#### Phase 4: Plumb Parameters Through Intermediate Functions

If extending function signatures:

1. Add the parameter to the declaration in the `.h` file
2. Add it to the definition in the `.c` file
3. **⚠️ Check for inline stubs** — `static inline` no-op stubs in headers MUST be updated too, or you'll get linker errors
4. Update ALL call sites

#### Phase 5: Verify

1. Check all call sites compile (build the kernel)
2. Verify `perf list | grep <tracepoint>` shows the new fields
3. Test with `trace-cmd record -e <tracepoint>` and check output

### Common Pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Forgot the inline stub | Linker error: undefined reference to `deferred_split_folio` | Update the `static inline` no-op in the header too |
| NULL VMA deref | Kernel oops when trace fires | Always guard: `vma ? vma->field : fallback` |
| `__assign_str()` without `__string()` | Compile error | Match them 1:1 in `TP_STRUCT__entry` |
| `__get_str()` without `__assign_str()` | Empty string in output | Both are needed |
| Forgot to update `TP_printk` format | New fields exist but never printed | Count format specifiers vs arguments |
| `reason` hardcoded to 0 | All events show same reason | Pass the actual reason through the chain |

### Safety Properties of This Pattern

These changes are **safe to apply and safe to revert** because:

1. **Additive only** — no existing fields removed or reordered
2. **No logic changes** — tracepoints are no-op when tracing is off
3. **NULL-safe** — all VMA accesses check `vma != NULL`
4. **No ABI break** — tracepoint format is not a stable kernel ABI; tools parse by field name, not binary layout
5. **Self-contained** — the patch touches a closed set of 5 files with no cross-module dependencies

---

## References

- Linux kernel tracepoint documentation: `Documentation/trace/tracepoints.rst`
- `include/trace/events/huge_memory.h` — source trace event definitions
- `include/linux/mm_inline.h` — `anon_vma_name()` and friends
- `include/linux/mm_types.h` — `struct vm_area_struct` definition
- `mm/huge_memory.c` — `deferred_split_folio()` implementation
- `mm/rmap.c` — `__folio_remove_rmap()` — one of the call sites
- `mm/khugepaged.c` — `collapse_huge_page()` — another call site

## Archive Metadata

- **Date archived**: 2026-06-29
- **Source**: `~/learn_os/pixel/common_my_dec/`
- **Base kernel**: Android Common Kernel (Pixel tree), main branch
- **Files modified**: `include/linux/huge_mm.h`, `include/trace/events/huge_memory.h`, `mm/huge_memory.c`, `mm/khugepaged.c`, `mm/rmap.c`
- **Revert method**: `git checkout -- <files>` (safe, zero functional impact)

---

# archive_code — vma_anon_name: Auto-Annotate Anonymous VMAs with 16KB Alignment Debug Names

## Purpose

Teach AI agents how to instrument the Linux kernel's mmap/brk paths to automatically set `anon_vma_name` with 16KB alignment information on every anonymous VMA. This makes `/proc/pid/maps` and `PR_SET_VMA` tracepoints immediately show which VMAs are 16KB-aligned, without needing separate instrumentation. Combined with the tracepoint enrichment (vma_debug above), this enables end-to-end tracking of 16KB folio behavior from allocation through split/deferred-split.

**⚠️ PERFORMANCE NOTE**: This code sets `anon_vma_name` via `anon_vma_name_alloc()` (which does `kzalloc` + `kstrdup`) on every anonymous mmap and brk call — a hot allocation path. It was reverted due to performance concerns. Archive this for reference only; do NOT blindly apply to production kernels.

## Design Intent

### Problem Being Solved

When debugging 16KB large folio behavior on Android (Pixel), understanding whether a VMA is 16KB-aligned is critical for correlating folio allocation/split behavior with the virtual address layout. Without this, you need separate tracing or manual inspection to know alignment.

### Solution: Automatic anon_vma_name Annotation

Three injection points cover all anonymous VMA creation paths:

1. **`__mmap_new_vma()`** — mmap(MAP_ANONYMOUS) creates a new anonymous VMA
2. **`do_brk_flags()`** — brk() extends the heap
3. **`__split_vma()`** — mprotect/mremap/munmap partial operations split existing VMAs

Each sets `vma->anon_name` to a formatted string encoding:
- Whether the VMA came from MAP_FIXED or not
- Whether start, end, or both are 16KB-aligned
- Whether the VMA is a split fragment

### Format Convention

```
MAP_FIXED:16KB_aligned              — both start and end are 16KB-aligned
MAP_FIXED:start_16KB_aligned_end_not — only start is aligned
MAP_FIXED:not_16KB_aligned           — neither is aligned
non_MAP_FIXED:16KB_aligned          — same, but from non-MAP_FIXED mmap
non_MAP_FIXED:start_16KB_aligned_end_not
non_MAP_FIXED:not_16KB_aligned
brk:16KB_aligned                    — brk()-allocated, both aligned
brk:start_16KB_aligned_end_not
brk:not_16KB_aligned
MAP_FIXED:16KB_aligned_split         — split fragment of a previously-aligned VMA
```

### Name Preservation in `replace_anon_vma_name()`

When a user calls `prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME, ...)` on a VMA that already has a debug name, the code preserves the debug info by combining it with the user's name:

```
User sets name "foo" on MAP_FIXED:16KB_aligned
→ anon_name becomes "foo (MAP_FIXED:16KB_aligned)"
```

If the VMA already has a combined name (e.g., after a prior user set), the debug part is extracted and re-attached to the new user name.

## Full Code Diff (The Actual Patch)

### Commit Header

```
commit 73effccba8088e22bd245f54d0f71d982994e5e4
Author: Nanzhe <zhaonanzhe@xiaomi.com>
Date:   Fri Jun 26 23:36:18 2026 +0800

    将所有vma改动全部给提交

    Change-Id: Ia5094d3f7e2766684247cf9c32ad599ec55b1b7a
```

### File 1: `mm/vma.h` — Extend mmap_region() signature

**Purpose**: Plumb `flags` parameter through to eventually reach `__mmap_new_vma()` where MAP_FIXED detection happens.

```diff
diff --git a/mm/vma.h b/mm/vma.h
--- a/mm/vma.h
+++ b/mm/vma.h
@@ -332,7 +332,7 @@ void mm_drop_all_locks(struct mm_struct *mm);
 
 unsigned long mmap_region(struct file *file, unsigned long addr,
 		unsigned long len, vm_flags_t vm_flags, unsigned long pgoff,
-		struct list_head *uf);
+		struct list_head *uf, unsigned long flags);
```

### File 2: `mm/mmap.c` — Pass flags through to mmap_region

```diff
diff --git a/mm/mmap.c b/mm/mmap.c
--- a/mm/mmap.c
+++ b/mm/mmap.c
@@ -555,7 +555,7 @@ unsigned long do_mmap(struct file *file, unsigned long addr,
 
-	addr = mmap_region(file, addr, len, vm_flags, pgoff, uf);
+	addr = mmap_region(file, addr, len, vm_flags, pgoff, uf, flags);
```

### File 3: `mm/vma.c` — Core annotation logic

This is the bulk of the change with 4 sub-changes:

#### 3a: MMAP_STATE macro — add `flags` field

```diff
--- a/mm/vma.c
+++ b/mm/vma.c
@@ -16,6 +16,7 @@ struct mmap_state {
 	pgoff_t pgoff;
 	unsigned long pglen;
 	vm_flags_t vm_flags;
+	unsigned long flags;
 	struct file *file;
 	...
 };
 
-#define MMAP_STATE(name, mm_, vmi_, addr_, len_, pgoff_, vm_flags_, file_) \
+#define MMAP_STATE(name, mm_, vmi_, addr_, len_, pgoff_, vm_flags_, file_, flags_) \
 	struct mmap_state name = {					\
 		...
+		.flags = flags_,					\
 	}
```

#### 3b: `__mmap_new_vma()` — Set anon_name for anonymous VMAs

```diff
@@ -2476,8 +2503,36 @@ static int __mmap_new_vma(struct mmap_state *map, struct vm_area_struct **vmap)
 	else if (map->vm_flags & VM_SHARED)
 		error = shmem_zero_setup(vma);
-	else
+	else {
 		vma_set_anonymous(vma);
+		/*
+		 * Set anon_vma_name for anonymous VMAs with MAP_FIXED and
+		 * 16KB alignment information for debugging.
+		 */
+		if (map->flags & MAP_FIXED) {
+			if (IS_ALIGNED(map->addr, 16384) &&
+			    IS_ALIGNED(map->end, 16384))
+				vma->anon_name = anon_vma_name_alloc(
+					"MAP_FIXED:16KB_aligned");
+			else if (IS_ALIGNED(map->addr, 16384))
+				vma->anon_name = anon_vma_name_alloc(
+					"MAP_FIXED:start_16KB_aligned_end_not");
+			else
+				vma->anon_name = anon_vma_name_alloc(
+					"MAP_FIXED:not_16KB_aligned");
+		} else {
+			if (IS_ALIGNED(map->addr, 16384) &&
+			    IS_ALIGNED(map->end, 16384))
+				vma->anon_name = anon_vma_name_alloc(
+					"non_MAP_FIXED:16KB_aligned");
+			else if (IS_ALIGNED(map->addr, 16384))
+				vma->anon_name = anon_vma_name_alloc(
+					"non_MAP_FIXED:start_16KB_aligned_end_not");
+			else
+				vma->anon_name = anon_vma_name_alloc(
+					"non_MAP_FIXED:not_16KB_aligned");
+		}
+	}
```

**⚠️ PERFORMANCE ISSUE**: `anon_vma_name_alloc()` calls `kzalloc` + `kstrdup` — this is on every anonymous mmap call, which is a hot path. In production Android, this adds measurable overhead.

#### 3c: `__split_vma()` — Mark split fragments

```diff
@@ -564,6 +566,31 @@ __split_vma(struct vma_iterator *vmi, struct vm_area_struct *vma,
 		vma->vm_end = addr;
 	}
 
+	/*
+	 * Mark both fragments with "_split" suffix so we can distinguish
+	 * originally-misaligned VMAs from split fragments.
+	 */
+	if (vma->anon_name &&
+	    !strstr(vma->anon_name->name, "_split") &&
+	    (!strncmp(vma->anon_name->name, "MAP_FIXED:", 10) ||
+	     !strncmp(vma->anon_name->name, "non_MAP_FIXED:", 14) ||
+	     !strncmp(vma->anon_name->name, "brk:", 4))) {
+		char split_name[64];
+		struct anon_vma_name *split_anon;
+
+		snprintf(split_name, sizeof(split_name), "%s_split",
+			 vma->anon_name->name);
+		split_anon = anon_vma_name_alloc(split_name);
+		if (split_anon) {
+			struct anon_vma_name *old = vma->anon_name;
+
+			vma->anon_name = anon_vma_name_reuse(split_anon);
+			new->anon_name = anon_vma_name_reuse(split_anon);
+			anon_vma_name_put(old);
+			anon_vma_name_put(old);
+		}
+	}
+
 	/* vma_complete stores the new vma */
 	vma_complete(&vp, vmi, vma->vm_mm);
```

**Key pattern**: Uses `anon_vma_name_reuse()` to share the same allocation between both fragments (reference counting), rather than allocating twice.

#### 3d: `do_brk_flags()` — Annotate brk-created VMAs

```diff
@@ -2804,6 +2859,19 @@ int do_brk_flags(struct vma_iterator *vmi, struct vm_area_struct *vma,
 
 	vma_set_anonymous(vma);
+	/*
+	 * Set anon_vma_name for brk-created anonymous VMAs with
+	 * 16KB alignment information. brk() is never MAP_FIXED.
+	 */
+	if (IS_ALIGNED(addr, 16384) && IS_ALIGNED(addr + len, 16384))
+		vma->anon_name = anon_vma_name_alloc(
+			"brk:16KB_aligned");
+	else if (IS_ALIGNED(addr, 16384))
+		vma->anon_name = anon_vma_name_alloc(
+			"brk:start_16KB_aligned_end_not");
+	else
+		vma->anon_name = anon_vma_name_alloc(
+			"brk:not_16KB_aligned");
 	vma_set_range(vma, addr, addr + len, addr >> PAGE_SHIFT);
```

#### 3e: `mmap_region()` and `__mmap_region()` — Accept `flags`

```diff
@@ -2638,14 +2693,14 @@ static bool can_set_ksm_flags_early(struct mmap_state *map)
 
 static unsigned long __mmap_region(struct file *file, unsigned long addr,
 		unsigned long len, vm_flags_t vm_flags, unsigned long pgoff,
-		struct list_head *uf)
+		struct list_head *uf, unsigned long flags)
 {
 	...
-	MMAP_STATE(map, mm, &vmi, addr, len, pgoff, vm_flags, file);
+	MMAP_STATE(map, mm, &vmi, addr, len, pgoff, vm_flags, file, flags);
 	...
 }

 unsigned long mmap_region(struct file *file, unsigned long addr,
 			  unsigned long len, vm_flags_t vm_flags, unsigned long pgoff,
-			  struct list_head *uf)
+			  struct list_head *uf, unsigned long flags)
 {
 	...
-	ret = __mmap_region(file, addr, len, vm_flags, pgoff, uf);
+	ret = __mmap_region(file, addr, len, vm_flags, pgoff, uf, flags);
```

### File 4: `mm/madvise.c` — Preserve debug name on user-set name

```diff
diff --git a/mm/madvise.c b/mm/madvise.c
--- a/mm/madvise.c
+++ b/mm/madvise.c
@@ -133,6 +133,54 @@ static int replace_anon_vma_name(struct vm_area_struct *vma,
 	if (anon_vma_name_eq(orig_name, anon_name))
 		return 0;
 
+	/*
+	 * If the original name is our auto-generated debug name, preserve it
+	 * by appending to the new user-set name.
+	 * Format: "user_name (debug_info)".
+	 */
+	if (orig_name &&
+	    (!strncmp(orig_name->name, "MAP_FIXED:", 10) ||
+	     !strncmp(orig_name->name, "non_MAP_FIXED:", 14) ||
+	     !strncmp(orig_name->name, "brk:", 4))) {
+		char combined[128];
+		struct anon_vma_name *combined_name;
+
+		snprintf(combined, sizeof(combined), "%s (%s)",
+			 anon_name->name, orig_name->name);
+		combined_name = anon_vma_name_alloc(combined);
+		if (combined_name) {
+			vma->anon_name = combined_name;
+			anon_vma_name_put(orig_name);
+			return 0;
+		}
+	}
+
+	/*
+	 * If orig_name is already a combined name (user set "foo" on a
+	 * debug-named VMA), extract the debug part and re-attach it.
+	 */
+	if (orig_name) {
+		const char *debug = NULL;
+
+		debug = strstr(orig_name->name, " (MAP_FIXED:");
+		if (!debug)
+			debug = strstr(orig_name->name, " (non_MAP_FIXED:");
+		if (!debug)
+			debug = strstr(orig_name->name, " (brk:");
+		if (debug) {
+			char combined[128];
+			struct anon_vma_name *combined_name;
+
+			snprintf(combined, sizeof(combined), "%s%s",
+				 anon_name->name, debug);
+			combined_name = anon_vma_name_alloc(combined);
+			if (combined_name) {
+				vma->anon_name = combined_name;
+				anon_vma_name_put(orig_name);
+				return 0;
+			}
+		}
+	}
+
 	vma->anon_name = anon_vma_name_reuse(anon_name);
 	anon_vma_name_put(orig_name);
```

**Design rationale for `replace_anon_vma_name()`**: Android's ART runtime uses `prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME, ...)` to tag memory regions (e.g., "RegionSpace", "ZygoteSpace"). Without these preservation rules, ART's name setting would overwrite our debug alignment info. The two-pass approach handles:
1. **First pass**: Debug name is pure (e.g., `MAP_FIXED:16KB_aligned`), user sets "RegionSpace" → becomes `RegionSpace (MAP_FIXED:16KB_aligned)`
2. **Second pass**: Name is already combined, user sets "ZygoteSpace" → debug part extracted and re-attached → `ZygoteSpace (MAP_FIXED:16KB_aligned)`

## How to Replicate This Pattern (AI Agent Guide)

### When to Use This Pattern

Use auto-annotation when:
- You need VMA identity information visible in `/proc/pid/maps` and trace events
- The identity is derivable at VMA creation time (alignment, flags, caller context)
- You're willing to accept the allocation overhead on the creation hot path

### Do NOT Use When

- Performance is critical (every allocation on the mmap path adds up)
- You only need the information in specific trace events (use the tracepoint enrichment pattern instead — see vma_debug above)
- You can get the same information from existing tracepoints without modifying the creation path

### Step-by-Step Implementation

1. **Identify VMA creation points**: `__mmap_new_vma()`, `do_brk_flags()`, and split point `__split_vma()`
2. **Plumb any needed context**: If you need flags like `MAP_FIXED`, propagate them through the callchain (but measure the cost — adding parameters to hot-path functions may have CPU overhead even when unused)
3. **Set anon_name at creation**: Use `anon_vma_name_alloc()` for new names, `anon_vma_name_reuse()` for sharing between split fragments
4. **Handle name replacement**: Modify `replace_anon_vma_name()` in `mm/madvise.c` if you need debug names to survive user `prctl()` calls
5. **Add split handling**: If a VMA with a debug name is split, decide whether both fragments should keep the name or get a modified version

### Common Pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| `anon_vma_name_alloc()` on hot path | Perf regression, GC jank | Use a lighter annotation or make it conditional on a debug config |
| Forgot split handling | Split fragments lose debug name | Add `__split_vma()` hook |
| Name overwritten by `prctl()` | Debug names disappear after ART sets VMA names | Modify `replace_anon_vma_name()` |
| Memory leak on split | `anon_vma_name` refcount mismatch | Use `anon_vma_name_reuse()` (takes a ref) + `anon_vma_name_put()` for the old refs |
| `flags` not in scope | Can't check MAP_FIXED | Plumb `flags` through `mmap_region()` → `__mmap_region()` → `MMAP_STATE` → `__mmap_new_vma()` |

### Performance Mitigation Strategies (If You Must Use in Production)

1. **Gate behind a static key / debug config**: Only annotate when `CONFIG_VMA_DEBUG_NAMES=y`
2. **Use a pre-allocated pool**: Instead of `kzalloc` per VMA, pre-allocate common name strings
3. **Use fixed strings**: If possible, use `.rodata` string literals instead of `kasprintf`-style formatting
4. **Defer annotation**: Set the name lazily on first access instead of at creation time

## Archive Metadata

- **Date archived**: 2026-06-29
- **Source**: `~/learn_os/pixel/common_my_dec/`
- **Commit**: `73effccba8088e22bd245f54d0f71d982994e5e4`
- **Base kernel**: Android Common Kernel (Pixel tree), main branch
- **Files modified**: `mm/madvise.c`, `mm/mmap.c`, `mm/vma.c`, `mm/vma.h`
- **Revert method**: `git revert HEAD` (clean revert via `post-commit` hook propagation)
- **Reason for revert**: Performance — `anon_vma_name_alloc()` per anonymous VMA creation is too expensive on the hot path
- **Revert hook behavior**: `git revert` creates new commit → `post-commit` hook → `sync.sh --from HEAD~1 --push` → shadow repo updated → reverse hook `sync_back.sh` → NOOP (dest already matches)
