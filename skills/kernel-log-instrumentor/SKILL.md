---
name: kernel-log-instrumentor
description: "generate linux kernel debug instrumentation plans and patch-ready snippets for printk/pr_emerg, pr_debug with dynamic_debug, and tracepoints. use when asked to add kernel logs, track key state variables (loop-carried or shared), debug locks/condition variables, reduce printk noise by switching to pr_debug, upgrade hot-path logs to tracepoints, or turn detailed key=value logs into a queryable table for filtering by inode/pid/seq/shared-object ids. enforce: stage logs on a temporary git branch carried by a disposable worktree with one easy-to-revert commit; in learn_os-style kernel tasks build each worktree with its own O= dir and seed it from /home/nzzhao/learn_os/f2fs_upstream/.config; default to pr_debug plus dynamic_debug for temporary high-volume logs; printk uses kern_emerg only for low-frequency must-see events; all logs print __func__; concurrency logs include lock+ and lock- pairs with predicate fields and health rules."
---

# Kernel Log Instrumentor

## What to do
When asked to "add kernel logs" or "instrument" linux kernel code, produce:

1. **Reversible change plan** (temporary branch + worktree + rollback)
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
   - Do not ask the user to keep branch-switching in their main checkout just to add temporary logs.
   - Prefer a **temporary log branch in a disposable worktree**; in `learn_os`, use [references/worktree-kernel-build.md](references/worktree-kernel-build.md) and the helper `scripts/git_temp_log_worktree.sh`.
   - Provide commands to create the worktree, commit the logs as **one commit**, and remove/revert them later.
   - If the user explicitly wants same-checkout branch switching, keep it as an exception rather than the default.

3. **Prepare the build output when the logs will be compiled**
   - Give each temporary log worktree its own `O=` output directory.
   - In `~/learn_os`, default to copying the shared proven config from `/home/nzzhao/learn_os/f2fs_upstream/.config`.
   - Build with `/home/nzzhao/learn_os/myscripts/make_upstream.sh --src <worktree> --out <out> --config-seed /home/nzzhao/learn_os/f2fs_upstream/.config [targets...]`.
   - Do not rely on a fresh scratch `olddefconfig` alone when the debug task depends on options such as dynamic debug, fscrypt, or other instrumentation-related config.

4. **Choose instrumentation mode**
   - **Normal mode (default):** entry/exit + state changes + error paths + lock boundaries.
   - **Detail mode (trigger):** if the user requests "详细/详细模式/deep/trace everything" or the bug is still ambiguous after normal mode.
     - Add hierarchical logging across selected callees (child + grandchild) per [references/detail-mode.md](references/detail-mode.md).

5. **Choose mechanism**
   - **printk/pr_emerg** for low-frequency, high-signal events.
   - **tracepoint** for hot paths / loops / per-packet / per-irq / high-frequency logs (see [references/tracepoint-upgrade.md](references/tracepoint-upgrade.md)).

6. **Generate patch snippets**
   - Use the formatting rules in [references/log-format.md](references/log-format.md).
   - **Always** prefer `pr_debug()` plus dynamic_debug for temporary verbose logs.
   - If the user specifically needs unconditional visibility, or the running config lacks the needed debug support, generate `pr_emerg` / `KERN_EMERG` snippets instead.
   - Always include function name.

7. **If concurrency is involved, apply concurrency rules**
   - If the code uses locks/rwsem/atomics/wait queues/completions/rcu, follow [references/concurrency-logging.md](references/concurrency-logging.md).

8. **If the user wants to track one object across many threads, switch to table mode**
   - Design log lines so each line is a self-contained row with stable `k=v` fields.
   - Include both actor ids (`pid`, `comm`, `cpu`) and shared-object ids (`ino`, `index`, `folio`, `seq`, custom ids).
   - Explain how to query the resulting log with [references/log-table-workflow.md](references/log-table-workflow.md) and `scripts/kernel_log_kv_query.py`.
   - When using `pr_debug()`/dynamic_debug, provide a reproducible enable/disable recipe; for f2fs table-mode examples see `scripts/enable_f2fs_inode_kv_logs.sh`.
   - If the user explicitly wants “大胆打日志” (always-on visibility), prefer `pr_emerg` but **still keep an inode/range filter**; see [references/f2fs-writeback-kv-logs.md](references/f2fs-writeback-kv-logs.md).

## Output format
Unless the user asks otherwise, respond in this structure:

### 1) Temporary worktree and rollback
- Base branch, temporary branch name, worktree path, and `O=` output path suggestion
- Commands to create the worktree, build it, commit the log patch, and remove/revert it

### 2) Variables to track
- Extract key state variables from user intent (see [references/variable-selection.md](references/variable-selection.md))

### 3) Where to log
- Exact insertion points (line/statement anchors)
- Normal vs detail mode call-depth notes
- Think hard about the log granularity and necessity to minimize noise.
### 4) Patch-ready code
- Minimal macros + log lines (copy/paste)
- If high-frequency, also include tracepoint skeleton

### 5) How to read the logs
- Apply "healthy vs suspicious" rules (especially concurrency)
- When relevant, show one or two concrete query commands that treat the log as a table

## Non-negotiable rules
- **Temporary branch in a disposable worktree:** never suggest landing logs directly on main or repeatedly checking branches in the user's main checkout unless they explicitly ask for that.
- **Separate build output per worktree:** keep one `O=` directory per temporary log worktree.
- **Shared config seed in `learn_os`:** default to `/home/nzzhao/learn_os/f2fs_upstream/.config` when seeding scratch build outputs.
- **Default temporary debug mechanism:** use `pr_debug()` plus dynamic_debug for reversible verbose logging unless the user specifically needs unconditional console visibility.
- **printk level:** if `printk`/`pr_emerg` is chosen, use **KERN_EMERG** only for truly must-see events.
- **Always print function name:** every line includes `__func__` (directly or via macro).
- **Stable prefix:** every line starts with a short tag so it can be grepped.
- **Runtime control:** when using `pr_debug()`, provide exact dynamic_debug commands or a helper script path to enable the selected callsites before testing and disable them afterward.
- **Table-friendly rows for multi-thread debug:** if the user is tracking a shared object across threads, ensure each relevant log line contains the same queryable ids and avoids prose-only fields.

## References
- [references/worktree-kernel-build.md](references/worktree-kernel-build.md)
- [references/log-format.md](references/log-format.md)
- [references/variable-selection.md](references/variable-selection.md)
- [references/concurrency-logging.md](references/concurrency-logging.md)
- [references/detail-mode.md](references/detail-mode.md)
- [references/tracepoint-upgrade.md](references/tracepoint-upgrade.md)
- [references/log-table-workflow.md](references/log-table-workflow.md)
- [references/f2fs-writeback-kv-logs.md](references/f2fs-writeback-kv-logs.md)
- [references/f2fs-writeback-kv-filter.md](references/f2fs-writeback-kv-filter.md)
- [references/f2fs-write_cache_folios-eagain-propagation.md](references/f2fs-write_cache_folios-eagain-propagation.md)
- [references/f2fs-inode-centric-atomic-verity-large-folio.md](references/f2fs-inode-centric-atomic-verity-large-folio.md)
- [references/fscrypt_open_einval_pkgxml.md](references/fscrypt_open_einval_pkgxml.md) (recipe for `/data/system/packages.xml` `open failed: EINVAL`)
- [references/fsverity_open_einval_pkgxml.md](references/fsverity_open_einval_pkgxml.md) (recipe for `/data/system/packages.xml` `open failed: EINVAL` due to fs-verity)
- [references/pixel-kleaf-build-gotchas.md](references/pixel-kleaf-build-gotchas.md) (Bazel/Kleaf build pitfalls in local Pixel checkouts)

## Workflow Contract

### Main Workflow
1. Pin the failing symptom to one concrete outer return site and log line, such as `page_mkwrite_state ... err=-5 ret=0x2`.
2. Instrument the outer site first so the next repro distinguishes branch-local reasons instead of only restating the final errno.
3. Instrument the immediate upstream state setter that can force that branch, such as `CP_ERROR_FLAG` or `mapping_set_error(-EIO)`.
4. Build and run one repro with only those linked layers instrumented.
5. Update the bug matrix with the new causal chain before widening the log surface.

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Trace design | Filesystem `mmap` write reports `page_mkwrite ... err=-EIO` but inner cause is open | Add one log at the `page_mkwrite` early `-EIO` branch selector, one log at the first `mapping_set_error(-EIO)` producer for the same subsystem, and one log at every `CP_ERROR_FLAG` setter reachable from that producer | The next repro must show an ordered chain such as `write_end_io_eio` or `page_eio_cp_error` or `cp_error_set` before `page_mkwrite_eio_source source=cp_error` | If `page_mkwrite_eio_source` fires without any preceding upstream setter log, widen only from the hit branch's direct callers, not the whole writeback stack | replace ad-hoc EIO logging with a fixed three-layer tracing pattern |

### Output Contract
- phase reached:
- outer symptom pinned:
- branch selector sites added:
- upstream setter sites added:
- build verification:
- repro evidence chain:
- next widening step:
