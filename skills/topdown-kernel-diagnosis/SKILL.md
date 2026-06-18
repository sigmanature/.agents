---
name: topdown-kernel-diagnosis
description: Top-down recursive kernel diagnosis using percpu arrays for coarse-grained counting then tracepoints for fine-grained stack capture. Use when you need to classify and quantify a multi-path kernel code flow (e.g., deferred split reasons, page fault types, reclaim types) and want minimal overhead for routine counts while retaining the ability to drill into hot paths with full stack traces.
---

# Top-Down Kernel Diagnosis

## Problem

Kernel paths that converge to a single function (e.g., `deferred_split_folio()`) but originate from
dozens of callers. You need to answer:

1. "Which caller path dominates in production?"
2. "When path X spikes, what's the exact stack trace?"

Counting every call with tracepoints costs too much. Guessing from code review misses runtime reality.

## Solution: Two-tier diagnosis

```
Tier 1 (always on):  percpu array counters → debugfs  ← cheap, log-free
Tier 2 (on demand):  tracepoint at hot reason site   ← capture stack when needed
```

### Tier 1: Percpu reason array

Define an enum of reasons, an array indexed by reason, increment in the common function.

```c
// huge_mm.h
enum deferred_split_reason {
    DSR_PARTIALLY_MAPPED = 0,
    DSR_ZAP,
    DSR_KHUGEPAGED,
    DSR_NR,  // sentinel, = number of reasons
};

DECLARE_PER_CPU(unsigned long, deferred_split_reason_counts[DSR_NR]);
```

```c
// huge_memory.c
DEFINE_PER_CPU(unsigned long, deferred_split_reason_counts[DSR_NR]);

void deferred_split_folio(struct folio *folio, bool partially_mapped,
                          enum deferred_split_reason reason)
{
    ...
    if (list_empty(&folio->_deferred_list)) {
        list_add_tail(...);
        this_cpu_inc(deferred_split_reason_counts[reason]);  // ← percpu, no lock
        trace_mm_folio_deferred_split(folio, reason);        // ← reason as param
    }
    ...
}
```

Each caller passes its reason:

```c
// Callers
deferred_split_folio(folio, true,  DSR_PARTIALLY_MAPPED);  // rmap.c
deferred_split_folio(folio, false, DSR_ZAP);               // huge_memory.c
deferred_split_folio(folio, false, DSR_KHUGEPAGED);        // khugepaged.c
```

**Why percpu array is correct**: `this_cpu_inc` operates on the current CPU's copy. Even if preempted and migrated between CPUs, the sum across all CPUs is invariant. No locks needed.

### Tier 1: debugfs readback

```c
static int deferred_split_reason_show(struct seq_file *m, void *v)
{
    unsigned long totals[DSR_NR] = {0};
    int cpu, i;

    for_each_possible_cpu(cpu)
        for (i = 0; i < DSR_NR; i++)
            totals[i] += per_cpu(deferred_split_reason_counts[i], cpu);

    seq_printf(m, "PARTIALLY_MAPPED: %lu\n", totals[DSR_PARTIALLY_MAPPED]);
    seq_printf(m, "ZAP:              %lu\n", totals[DSR_ZAP]);
    seq_printf(m, "KHUGEPAGED:       %lu\n", totals[DSR_KHUGEPAGED]);
    return 0;
}

// Register in init:
debugfs_create_file("deferred_split_reasons", 0400, NULL, NULL,
                    &deferred_split_reason_fops);
```

Usage: `cat /sys/kernel/debug/deferred_split_reasons`

Counters are monotonic (never reset). Watch the **ratio** between reasons, not absolute values.

### Tier 2: Targeted tracepoint

When debugfs shows reason X dominates, add a tracepoint **inside the `if (reason == X)` branch** in the callee, or better, at the exact caller site. Then capture stack traces.

```c
// Option A: inside deferred_split_folio, conditional
if (reason == DSR_ZAP)
    trace_deferred_split_zap_stack(folio);

// Option B: define a new trace event with save_stack_trace
TRACE_EVENT(deferred_split_zap,
    TP_PROTO(struct folio *folio),
    TP_ARGS(folio),
    TP_STRUCT__entry(
        __field(unsigned long, pfn)
        __array(unsigned long, stack, 16)
    ),
    TP_fast_assign(
        __entry->pfn = folio_pfn(folio);
        stack_trace_save(__entry->stack, 16, 1);  // skip 1 (this function)
    ),
    TP_printk("pfn=0x%lx stack=%pS %pS %pS ...",
        __entry->pfn,
        (void *)__entry->stack[0],
        (void *)__entry->stack[1],
        (void *)__entry->stack[2])
);
```

## Workflow Contract

### Main Workflow

1. Identify the convergent kernel path and all its caller categories.
2. Define `enum reason { ..., NR_REASONS }` covering each semantically distinct path.
3. Add `this_cpu_inc(reason_counts[reason])` at the convergence point, with `reason` as a function parameter.
4. Change all callers to pass their reason.
5. Register a read-only debugfs file summing across all CPUs.
6. Deploy, observe ratios.
7. If a reason spikes, add targeted tracepoint with `stack_trace_save()` at that reason's site.
8. Capture stacks, analyze, fix.
9. Optionally keep or remove the targeted tracepoint after debugging.

### Decision Table

| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Percpu counter | Need per-reason counts | Define enum + percpu array | `cat debugfs` shows non-zero | Check caller passes reason | continue |
| Hot reason found | 80%+ of counts in one reason | Add targeted tracepoint | Trace shows expected stacks | Double-check reason assignment | branch to targeted trace |
| Tracepoint noisy | Too many events | Use `trace_printk` with `ratelimit` or sampling | Spot-check a few stacks | Increase filter | replace targeted trace |
| Fix applied | Root cause identified | Observe debugfs ratio change | Hot reason ratio drops | Re-investigate | complete |

### Output Contract

- phase reached: percpu counting | targeted tracepoint | fix verified
- reason distribution: ratio table from debugfs
- hot reason identified: which reason, what %
- targeted trace evidence: sample stacks captured
- fix applied: commit or config change

## Key Design Rules

1. **Reason is a function parameter, never a percpu scratch variable.** Avoids preemption window, interrupt races, and stale values.

2. **Percpu array for counting, tracepoint for stack capture.** Counting is essentially free (one `inc` per event). Stack capture is expensive (serializes call chain), so only enable it on the hot reason.

3. **Enum size = NR_REASONS.** Always add a sentinel. Prevents array size drift.

4. **debugfs read sums all CPUs.** Percpu values are independently incremented; summing is correct because `this_cpu_inc` is atomic with respect to other CPUs (each CPU touches its own memory).

## Limitations

- Counters never reset (monotonic). Use rate of change (Δ over time), not absolute values.
- Preemption between CPUs means individual percpu slot values are unstable, but the sum is always correct.
- `stack_trace_save()` is not NMI-safe; don't use in NMI context.
- This pattern works best for **infrequent events** (deferred split, OOM, stall detection). For extremely hot paths (thousands/sec), even percpu inc can cause cacheline bouncing.
