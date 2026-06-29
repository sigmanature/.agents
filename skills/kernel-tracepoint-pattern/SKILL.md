---
name: kernel-tracepoint-pattern
description: Use when adding or fixing Linux kernel ftrace tracepoints, especially TRACE_EVENT definitions, CREATE_TRACE_POINTS placement, caller includes, and common link or compile failures around trace event wiring.
---

# Kernel Tracepoint Pattern

## When To Use

Adding a new ftrace tracepoint to Linux kernel code. Use this skill whenever you need to instrument kernel code with a standard trace event (not `trace_printk`).

## Three-Layer Architecture

```
1. include/trace/events/<subsystem>.h  — TRACE_EVENT definition
2. <one> .c file with CREATE_TRACE_POINTS — instantiation (once per subsystem)
3. Any other .c files that call the tracepoint — declaration include
```

### Layer 1: Header (tracepoint definition)

```c
/* SPDX-License-Identifier: GPL-2.0 */
#undef TRACE_SYSTEM
#define TRACE_SYSTEM mysubsystem

#if !defined(_TRACE_MYSUBSYSTEM_H) || defined(TRACE_HEADER_MULTI_READ)
#define _TRACE_MYSUBSYSTEM_H

#include <linux/tracepoint.h>

TRACE_EVENT(my_event_name,

    TP_PROTO(type1 arg1, type2 arg2),
    TP_ARGS(arg1, arg2),

    TP_STRUCT__entry(
        __field(type1, field1)
        __field(type2, field2)
        __field(pid_t, pid)
        __array(char, comm, TASK_COMM_LEN)
    ),

    TP_fast_assign(
        __entry->field1 = arg1;
        __entry->field2 = arg2;
        __entry->pid    = current->pid;
        memcpy(__entry->comm, current->comm, TASK_COMM_LEN);
    ),

    TP_printk("field1=%d field2=%lx pid=%d comm=%s",
        __entry->field1, __entry->field2,
        __entry->pid, __entry->comm)
);

#endif /* _TRACE_MYSUBSYSTEM_H */

/* This part must be outside protection */
#include <trace/define_trace.h>
```

Key rules:
- `#undef TRACE_SYSTEM` before setting — prev include may have set it
- `#define TRACE_SYSTEM` must match one word (no hyphens)
- `#if !defined(…)) || defined(TRACE_HEADER_MULTI_READ)` — standard guard
- `#include <trace/define_trace.h>` at end — REQUIRED, outside the guard
- Do NOT depend on filesystem-specific types unless the calling .c file includes them

### Layer 2: Instantiation (CREATE_TRACE_POINTS)

Exactly **one** .c file per TRACE_SYSTEM must define and include:

```c
#define CREATE_TRACE_POINTS
#include <trace/events/mysubsystem.h>
```

This generates the actual `__tracepoint_xxx` and `__traceiter_xxx` symbols.

If that .c file also needs to *call* the tracepoint, it must include the header **twice**:
1. Once without `CREATE_TRACE_POINTS` (in the normal include block) — for the declaration/stub
2. Once with `CREATE_TRACE_POINTS` (at file end or separate block) — for the definition

### Layer 3: Caller files (declaration only)

Any .c file that calls `trace_my_event_name(...)` must include:

```c
#include <trace/events/mysubsystem.h>   // NO CREATE_TRACE_POINTS
```

## Common Pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Missing `#include <trace/define_trace.h>` | tracepoint not found at link | Add it at end of header |
| Multiple CREATE_TRACE_POINTS | duplicate symbol errors | Only one .c file defines it |
| Missing declaration include | `trace_xxx` undefined | Add `#include <trace/events/xxx.h>` to caller |
| F2fs types in mm trace header | `nid_t` unknown when compiled from mm/ | Use only generic types in cross-subsystem headers |
| CREATE_TRACE_POINTS before header includes types | Missing kernel types | Define CREATE_TRACE_POINTS after all kernel includes |
| Defining enums inside trace event header | `redefinition of 'xxx'` because trace headers are included twice | Prefer integer `#define` constants + `__print_symbolic` directly in the trace header; if you must use enum, guard it with `#ifndef __XXX_DECLARE_TRACE_ENUMS_ONCE_ONLY` (see `include/trace/events/afs.h`) or define it in a lightweight kernel header |
| Including heavy kernel headers (e.g. `<linux/mm.h>`) in trace event header | Boot/runtime failures or circular include issues | Keep trace headers minimal; use integer constants or forward declarations instead of pulling in heavy headers |

## Reference: mmap_lock

- `include/trace/events/mmap_lock.h` — header (Layer 1)
- `mm/mmap_lock.c` — instantiation (Layer 2), line 2-3
- `include/linux/mmap_lock.h` — wrapper functions for callers (Layer 3)

## Checklist

- [ ] Header: `#undef TRACE_SYSTEM` + `#define TRACE_SYSTEM`
- [ ] Header: guard with `TRACE_HEADER_MULTI_READ`
- [ ] Header: `#include <trace/define_trace.h>` at end
- [ ] Header: only generic types (no subsystem-specific typedefs)
- [ ] One .c: `#define CREATE_TRACE_POINTS` + include
- [ ] All callers: include header without CREATE_TRACE_POINTS
