---
name: kernel-log-instrumentor
description: "generate linux kernel debug instrumentation plans and patch-ready snippets for printk/pr_emerg, pr_debug with dynamic_debug, and tracepoints. use when asked to add kernel logs, track key state variables (loop-carried or shared), debug locks/condition variables, reduce printk noise by switching to pr_debug, upgrade hot-path logs to tracepoints, or turn detailed key=value logs into a queryable table for filtering by inode/pid/seq/shared-object ids. enforce: logs go on a temporary git branch with one easy-to-revert commit; default to pr_debug plus dynamic_debug for temporary high-volume logs; printk uses kern_emerg only for low-frequency must-see events; all logs print __func__; concurrency logs include lock+ and lock- pairs with predicate fields and health rules."
---

# Kernel Log Instrumentor

## What to do
When asked to "add kernel logs" or "instrument" linux kernel code, produce:

1. **Reversible change plan** (temporary branch + rollback)
2. **Logging plan** (what to log, where, and why)
3. **Patch-ready snippets** (printk/pr_emerg or tracepoint templates)
4. **Log health rules** (especially for concurrency)
5. **Table-read workflow** when the user needs to filter logs by ids across threads

Keep output actionable: commands, exact insertion points, and consistent log format.

## Workflow
Follow this decision flow.

1. **Confirm the target**
   - Identify: file path(s), function(s), and the bug symptom the user is chasing.
   - Maybe good to refer to the user's newest git commit.
   - If missing, ask for the smallest needed context: the target function body and the surrounding lock/loop region.

2. **Create a reversible staging plan (always)**
   - Ensure user's current workspace is clean and ready for the add-log change.
Otherwise, either mind the user or stash users' changes.
   - Provide git commands to create a **temporary log branch** and to **drop/revert** it.
   - Require that all logging changes are isolated in **one commit** (easy revert).

3. **Choose instrumentation mode**
   - **Normal mode (default):** entry/exit + state changes + error paths + lock boundaries.
   - **Detail mode (trigger):** if the user requests "详细/详细模式/deep/trace everything" or the bug is still ambiguous after normal mode.
     - Add hierarchical logging across selected callees (child + grandchild) per [references/detail-mode.md](references/detail-mode.md).

4. **Choose mechanism**
   - **printk/pr_emerg** for low-frequency, high-signal events.
   - **tracepoint** for hot paths / loops / per-packet / per-irq / high-frequency logs (see [references/tracepoint-upgrade.md](references/tracepoint-upgrade.md)).

5. **Generate patch snippets**
   - Use the formatting rules in [references/log-format.md](references/log-format.md).
   - Always include function name and KERN_EMERG severity.

6. **If concurrency is involved, apply concurrency rules**
   - If the code uses locks/rwsem/atomics/wait queues/completions/rcu, follow [references/concurrency-logging.md](references/concurrency-logging.md).

7. **If the user wants to track one object across many threads, switch to table mode**
   - Design log lines so each line is a self-contained row with stable `k=v` fields.
   - Include both actor ids (`pid`, `comm`, `cpu`) and shared-object ids (`ino`, `index`, `folio`, `seq`, custom ids).
   - Explain how to query the resulting log with [references/log-table-workflow.md](references/log-table-workflow.md) and `scripts/kernel_log_kv_query.py`.

## Output format
Unless the user asks otherwise, respond in this structure:

### 1) Temporary branch and rollback
- Branch name suggestion
- Commands to create branch, commit, and revert/drop

### 2) Variables to track
- Extract key state variables from user intent (see [references/variable-selection.md](references/variable-selection.md))

### 3) Where to log
- Exact insertion points (line/statement anchors)
- Normal vs detail mode call-depth notes
- Think hard about the log granularity,necessity to minimize noise.
### 4) Patch-ready code
- Minimal macros + log lines (copy/paste)
- If high-frequency, also include tracepoint skeleton

### 5) How to read the logs
- Apply "healthy vs suspicious" rules (especially concurrency)
- When relevant, show one or two concrete query commands that treat the log as a table

## Non-negotiable rules
- **Temporary branch, reversible:** never suggest landing logs directly on main.
- **Default temporary debug mechanism:** use `pr_debug()` plus dynamic_debug for reversible verbose logging unless the user specifically needs unconditional console visibility.
- **printk level:** if `printk`/`pr_emerg` is chosen, use **KERN_EMERG** only for truly must-see events.
- **Always print function name:** every line includes `__func__` (directly or via macro).
- **Stable prefix:** every line starts with a short tag so it can be grepped.
- **Runtime control:** when using `pr_debug()`, provide exact dynamic_debug commands or a helper script path to enable the selected callsites before testing and disable them afterward.
- **Table-friendly rows for multi-thread debug:** if the user is tracking a shared object across threads, ensure each relevant log line contains the same queryable ids and avoids prose-only fields.

## References
- [references/log-format.md](references/log-format.md)
- [references/variable-selection.md](references/variable-selection.md)
- [references/concurrency-logging.md](references/concurrency-logging.md)
- [references/detail-mode.md](references/detail-mode.md)
- [references/tracepoint-upgrade.md](references/tracepoint-upgrade.md)
- [references/log-table-workflow.md](references/log-table-workflow.md)
