# Upgrade frequent logs to tracepoints

## When to use tracepoints
Prefer tracepoints over printk when the log site is:
- inside a tight loop / per-packet / per-irq / per-page
- inside a spinlock or irq-disabled region
- expected to fire thousands of times per second
- needed for performance analysis (timing, histograms)

## Tracepoint formatting rules
1. **Stable event name**
   - Use `subsys_action` or `subsys_state` naming.
   - Keep it stable across iterations so tooling scripts don't break.

2. **Typed fields, not ad-hoc printf**
   - Put data in `__field()` / `__array()` / `__string()`.
   - Use `TP_printk()` only for rendering; the structured fields are the real API.

3. **Key=value print format**
   - Keep field order stable.
   - Prefer `k=v` pairs.

4. **No expensive work**
   - Do not allocate memory or walk long lists to build trace strings.

## Minimal TRACE_EVENT template
Create a new trace event header, usually:
- `include/trace/events/<subsys>.h`

Example:

```c
#undef TRACE_SYSTEM
#define TRACE_SYSTEM klog

#if !defined(_TRACE_KLOG_H) || defined(TRACE_HEADER_MULTI_READ)
#define _TRACE_KLOG_H

#include <linux/tracepoint.h>

TRACE_EVENT(klog_state,
	TP_PROTO(int cpu, pid_t pid, int state, int count, int ret),
	TP_ARGS(cpu, pid, state, count, ret),
	TP_STRUCT__entry(
		__field(int, cpu)
		__field(pid_t, pid)
		__field(int, state)
		__field(int, count)
		__field(int, ret)
	),
	TP_fast_assign(
		__entry->cpu = cpu;
		__entry->pid = pid;
		__entry->state = state;
		__entry->count = count;
		__entry->ret = ret;
	),
	TP_printk("cpu=%d pid=%d state=%d count=%d ret=%d",
		__entry->cpu, __entry->pid, __entry->state, __entry->count, __entry->ret)
);

#endif /* _TRACE_KLOG_H */

#include <trace/define_trace.h>
```

## Calling a tracepoint
Include the header and call it at the log site:

```c
#include <trace/events/klog.h>

trace_klog_state(raw_smp_processor_id(), task_pid_nr(current), ctx->state, ctx->count, ret);
```

## Bridging printk and tracepoints
If the user insists on printk but the site is hot:
- suggest: keep **one** printk (e.g., only on error/first occurrence)
- move the high-rate telemetry to a tracepoint.

## Tracepoint vs trace_printk
- `trace_printk()` is useful for quick experiments but is not a stable interface.
- For anything you might keep for more than a single debug session, use `TRACE_EVENT`.
