---
name: kernel-log-instrumentor
description: "Use when adding or restructuring Linux kernel debug logs, especially in Pixel/common where existing structured klog should be the default and pr_debug should only fill a specific missing edge."
---

# Kernel Log Instrumentor

## Core Rule

- `klog-first, pr_debug-for-gaps`
- On Pixel/common F2FS, default to `F2FS_WB_KLOG` plus runtime filters.
- Use `pr_debug` only when klog cannot express one exact branch, caller, or state transition.
- Do not invent new macro families, tag systems, or artifact basenames per session.

## Main Workflow

1. Pick the lane.
   - Default to the long-lived debug branch/worktree for temporary logs.
   - Promote only proven fixes back to the development branch.
2. Pin the target.
   - Name the file, function, symptom, and stable ids such as `ino`, `index`, `folio`, `seq`, or `pid`.
3. Start with klog.
   - If the subsystem already has structured runtime-filtered logs, use that first.
   - Narrow first by detail, inode, suffix, range, or equivalent filters.
4. Add only the missing edge.
   - If one exact causal edge is still ambiguous, add the smallest `pr_debug` gap-fill around that edge.
   - Do not build a second parallel logging system.
5. Read narrowly first.
   - Query by stable ids before broad grep.
   - Packetize the result when that will save future context.

## Hard Rules

- **Klog first:** on Pixel/common F2FS, do not start with `pr_debug` if `F2FS_WB_KLOG` can answer the question.
- **`pr_debug` is a gap-filler:** it bridges one missing edge; it is not the main workflow.
- **Stable macro family:** reuse the existing family.
- **Stable tags:** keep to a small reused set such as `ENTER`, `EXIT`, `STATE`, `ERR`, `WAIT`, `LOCK`, `PROV`, `WBIT`.
- **Stable filenames:** let the run directory vary, but keep basenames stable.
- **Always print function name:** every row includes `__func__` directly or through the macro.
- **Runtime control is mandatory:** if you add `pr_debug`, include the exact enable/disable recipe.

## Naming Contract

- Do not create per-session macro names such as `MAY12_DEBUG_X` or `PKGXML_TRACE_V7`.
- Prefer `k=v` rows over prose.
- Keep these artifact basenames stable:
  - `dmesg_stream.txt`
  - `dmesg_after.txt`
  - `controls_applied.txt`
  - `query_packet.json`
  - `notes.md`

## Triggered Escalations

- **Mutable `/data` suspect:** preserve first before broad pressure or post-hit parsing.
  - use `scripts/adb_preserve_mutable_suspect_file.sh`
- **Reboot pressure or very high-volume klog:** stream capture first, do not rely on post-hit `dmesg`.
  - see [references/kernel-stream-capture.md](references/kernel-stream-capture.md)
  - use `scripts/adb_grab_su.sh`
- **Preserved sample shows local `+2` plus high entropy:** jump to the matrix reference instead of widening ad hoc.
  - see [references/plus2-high-entropy-corruption-matrix.md](references/plus2-high-entropy-corruption-matrix.md)

## F2FS Default Tools

- `scripts/set_f2fs_wb_klog_filters.sh`
- `scripts/adb_grab_su.sh`
- `scripts/adb_preserve_mutable_suspect_file.sh`
- `scripts/adb_f2fs_map_entropy_scan.py`
- backend details: [references/f2fs-wb-klog-backend.md](references/f2fs-wb-klog-backend.md)

## `pr_debug` Gap-Fill

- Add one small cluster of callsites around the missing edge.
- Provide the matching dynamic_debug recipe, or point to:
  - `scripts/enable_f2fs_inode_kv_logs.sh`
- If the gap is in a hot loop and `pr_debug` would flood, replace that edge with a tracepoint.

## Output Format

### 1) Lane and target
- lane used
- file, function, symptom, stable ids

### 2) Method
- `klog`
- or `klog + pr_debug gap fill`
- or `tracepoint` for one hot edge

### 3) Runtime controls
- exact filter or dynamic_debug commands
- minimal repro scope

### 4) Patch and readback
- patch-ready snippets
- narrow query plan
- expected healthy versus suspicious transitions

## Decision Table

| Trigger | Action | Verify | On Failure |
|---|---|---|---|
| existing klog can express the chain | use klog only | required rows appear under narrow filters | if one edge is still ambiguous, add one precise gap-fill |
| klog identifies the object but not one exact transition | add minimal `pr_debug` around that edge | the new line bridges the missing edge | if it floods, replace that edge with a tracepoint |
| mutable `/data` file may be replaced | preserve first | preserve manifest records inode and copy status | report sample retention as blocked |
| `page_mkwrite ... err=-EIO` but inner cause is open | log outer selector plus first upstream setter chain | next repro shows an ordered chain | widen only from the hit branch's direct callers |

## References

- [references/worktree-kernel-build.md](references/worktree-kernel-build.md)
- [references/log-format.md](references/log-format.md)
- [references/log-table-workflow.md](references/log-table-workflow.md)
- [references/concurrency-logging.md](references/concurrency-logging.md)
- [references/detail-mode.md](references/detail-mode.md)
- [references/tracepoint-upgrade.md](references/tracepoint-upgrade.md)
- [references/f2fs-wb-klog-backend.md](references/f2fs-wb-klog-backend.md)
- [references/kernel-stream-capture.md](references/kernel-stream-capture.md)
- [references/plus2-high-entropy-corruption-matrix.md](references/plus2-high-entropy-corruption-matrix.md)
- [references/pixel-kleaf-build-gotchas.md](references/pixel-kleaf-build-gotchas.md)
