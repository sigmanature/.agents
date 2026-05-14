---
title: Refault - Pressure-Only SOP
summary: SOP for generating realistic refaults using only black-box app pressure and normal user navigation. No victim instrumentation or synthetic revisit helpers.
---

# Refault (Pressure-Only) SOP

Purpose
-------

Provide a reusable, upstream-defensible procedure to generate and prove refault behavior using only pressure from other apps (churn), without adding instrumentation or synthetic page-revisit helpers inside the victim app. This SOP is intended for use with the mmap-lock-contention-analysis skill and tracepoint collection workflows.

Hard constraints
----------------

- No custom victim app instrumentation. Do not add code, hooks, or deliberate revisit helpers to the victim.
- Do not craft synthetic page revisit behavior inside the victim process. The victim must behave exactly as in normal use.
- The workload must rely on black-box pressure: launching/rotating heavy apps, memory-hungry foreground tasks, or other system-level pressure generators.
- Avoid relying on frequent drop_caches as the primary mechanism for causing refaults; drop_caches can be used sparsely only for comparison.

Core model (high-level)
-----------------------

1. Choose a stable victim app and a repeatable restore path (see "Victim selection").
2. Prime the victim once: navigate it to the business page of interest, then return to HOME (or background it) without killing the process.
3. Create churn by repeatedly launching/rotating other heavy apps. The victim must be excluded from the churn set.
4. After each churn period, revisit the victim using the same normal user path (prefer recent-task restore; fallback to the same launcher entry if that path is stable).
5. Collect kernel and user-aware traces for each phase and prove refaults post-hoc using (tgid/mm, ino, pgoff) overlap and filemap_fault evidence.

Important clarification
---------------------

When we say “定点回访同一批页” under this SOP, we mean: after the fact, show that the revisit windows produce a highly overlapping set of (ino, pgoff) entries (same file inode and page offset) associated with the victim's mm/tgid, not that we pre-target specific virtual addresses. The burden is on trace evidence, not on synthetic targeting.

Why drop_caches is weak
------------------------

- drop_caches is global and non-targeted; it evicts broadly and can reheat victim pages indirectly when the victim is accidentally revisited.
- It does not reproduce natural working-set eviction created by app pressure and can create artifacts that are not representative of realistic user behavior.
- Use sparse, controlled drop_caches only for comparison experiments, not as the main mechanism.

Workload groups
---------------

- Primary: pressure-only churn (recommended). No drop_caches. Use heavy app rotation, memory-hungry foreground workloads, or automated app launches to cause memory pressure while excluding the victim.
- Auxiliary: pressure + sparse drop_caches (comparison only). Introduce infrequent drop_caches to compare behavior; avoid using this as the primary evidence for refaults.

Victim selection criteria & anti-patterns
---------------------------------------

Good victim characteristics:
- Stable restore path (recents, consistent launcher entry) that reproduces the same business page.
- Long-lived process: the app stays resident when backgrounded (not fully killed by OS under normal conditions).
- File-backed VMA footprint for the business page (files/so mapping) so (ino, pgoff) overlap is meaningful.

Anti-patterns / avoid:
- Victims that dynamically rebase, re-map, or recreate pages on each resume (unstable VMAs).
- Victims with non-deterministic startup flows that make revisit paths variable.
- Victims whose business pages are purely anonymous heap with frequent COW-on-write changes that make (ino, pgoff) overlap irrelevant.

Churn selection & dwell/gap philosophy
-------------------------------------

- Churn should exclude the victim and consist of a mix of heavy foreground apps and background memory pressure generators (e.g., large-browser tabs, large-game launches, media players).
- Dwell: run each churn element long enough to produce measurable pressure (tunable; start with conservative windows and tune empirically).
- Gap: after churn, allow a short gap for system stabilization before revisiting the victim (to avoid transient launcher artifacts). Both dwell and gap are tunable starting points—do not hardcode numbers in the SOP.

Revisit path priority
---------------------

1. Recents-task restore (preferred): brings the exact task back into foreground using system task restore mechanisms.
2. Stable launcher entry (fallback): open the app via the launcher shortcut that deterministically lands on the same business flow.

Evidence requirements (phased)
-----------------------------

Phase 1 — Victim priming & revisit path proof
- Prove the victim was primed into the intended business page and that revisit returned to the same business page.
- Evidence: logcat screenshots, UI-visible markers, activity/component names, or simpleperf traces that map to the same activity/entry point across prime and revisit.

Phase 2 — Prove refault occurred
- Acceptable proof:
  - Trace evidence showing filemap_fault events tied to the victim's tgid/mm during revisits.
  - Repeated or overlapping sets of (ino, pgoff) appearing in fault events across revisit windows for the same tgid/mm.
  - Demonstrate that these (ino,pgoff) entries were not newly created by the revisit (i.e., they belong to file-backed mappings associated with the victim's mm at prime time).
- Do NOT claim identical virtual addresses are required. Instead, require same mm/tgid and overlapping (ino,pgoff) evidence across revisit windows.

Phase 3 — Prove filemap_fault_wait growth
- Show that filemap_fault_wait (counts/duration) increased under pressure vs baseline for the victim's mm/tgid during revisit windows.
- Use baseline traces collected with minimal churn and compare counts, median/percentile wait durations, and retry rates.

Phase 4 — Evaluate overlap with vma_start_write
- After Phases 1–3, examine whether refaults temporally overlap with vma_start_write or mmap_lock wait events.
- This phase requires careful correlation by timestamp and pid/mm, and is conditional on Phase 3 showing wait growth.

Notes on Phase 2 specifics
-------------------------

- Acceptable metrics for Phase 2 proof:
  - filemap_fault_begin / filemap_fault_wait_start / filemap_fault_wait_end entries for the victim's tgid/mm during revisit windows.
  - Repeated occurrences of the same (ino, pgoff) across distinct revisit windows. Use set-overlap statistics (Jaccard or simple intersection counts) to quantify overlap.
  - Correlate any user-space stack traces (simpleperf/perfetto) to confirm the faults originate from the victim process.

Special-case note
-----------------

This SOP focuses on pressure-only refault behavior. If the investigation requires same-process writer overlap (e.g., checking whether a writer in the same process holds mmap_lock), that is out of scope for this SOP and needs separate evidence paths — which may require natural occurrences of mprotect/munmap/mremap/split_vma present in the victim's real workload.

Workflow Contract
-----------------

### Main Workflow

1. Select victim and verify stable restore path.
2. Prime victim into business page; collect baseline traces (trace_pipe, simpleperf, logcat, perfetto as needed).
3. Background victim (HOME or recents); confirm process remains resident.
4. Run churn (pressure-only) excluding victim; collect trace_pipe and perfetto across the churn window.
5. Revisit victim via recents or launcher; collect revisit traces and UI evidence.
6. Repeat churn + revisit cycles (N > 2) to build repeated windows.
7. Analyze:
   - Phase 1 checks: revisit path correctness.
   - Phase 2 checks: (ino,pgoff) overlap and filemap_faults for victim mm.
   - Phase 3 checks: wait count/duration growth vs baseline.
   - Phase 4 checks: temporal overlap with vma_start_write/mmap_lock events.
8. Produce report including Output Contract fields (below).

### Decision Table

| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Victim path unstable | Victim does not land on the business page reliably | Choose alternate victim or improve revisit path (use launcher deep link if stable) | UI evidence or activity names match | Abort SOP for this victim and pick new victim | Block: restart at Main Workflow step 1 |
| Victim included in churn | Victim process observed in churn launches or killed during churn | Remove victim from churn set; re-run churn | Process id/tgid excluded in memstress logs | Redo churn with corrected set | Continue from churn step |
| Revisit returns wrong page/task | Revisit lands on welcome/login or other unrelated page | Improve restore path or extend prime sequence | UI evidence mismatch | Consider per-app stabilization or pick different victim | Block until resolved |
| No repeated (ino,pgoff) across revisits | Overlap metric below threshold | Increase churn cycles; verify priming correctness; collect more windows | Jaccard or intersection metrics computed | If still low, victim may not have stable file-backed mappings — pick new victim | Branch: consider auxiliary drop_caches for contrast only |
| Pressure too weak | No increase in filemap_fault_wait or counts vs baseline | Increase dwell per churn item or add heavier churn elements | Compare baseline vs pressure windows | If can't show growth, escalate to more aggressive pressure or abort | Continue experiments after tuning |
| drop_caches dominates behavior | Sparse drop_caches shows stronger effect than churn | Flag experiment as drop_caches-dominated; treat as comparison-only | Compare traces with/without drop_caches | Require re-design using pressure-only or document as drop_caches artifact | Mark as comparison run only |

### Output Contract

- victim and revisit path (human-readable description and instrumented evidence)
- churn set summary (packages used, exclusion of victim confirmed)
- whether victim was excluded from churn (yes/no, with evidence)
- baseline vs pressure comparison method (how baselines were collected and compared)
- repeated file-page set evidence (intersection counts, Jaccard index, example (ino,pgoff) samples)
- refault evidence status (Phase 2): PASS/FAIL with supporting traces and counts
- wait-growth evidence status (Phase 3): PASS/FAIL with statistics (counts, median, p50/p90/p95 wait durations)
- overlap analysis readiness (Phase 4): READY/NOT_READY and why (e.g., sufficient wait growth to warrant vma_start_write correlation)
- fallback used / blocker (if any): description and next action

Validation & Reporting
----------------------

- Always include raw trace snippets and summary JSON extracted from existing analysis scripts.
- Quantify overlap with simple metrics (intersection size, Jaccard index) and include example (ino,pgoff) tuples with timestamps and pid/mm.
- For wait-growth, report counts, median, p50/p90/p95, and retry rates.

Appendix: Suggested data collection checklist
------------------------------------------

- trace_pipe capture for whole window
- perfetto trace for broader context and user stacks
- simpleperf or perf user-stack sampling (60s windows around revisit)
- logcat screenshots or UI markers for revisit-path confirmation
- memstress/mem-rotation logs enumerating churn package start/stop and exit reasons
- a small manifest that lists churn packages and confirms victim exclusion

Notes
-----

This SOP is intentionally prescriptive about evidence and conservative about mechanisms. It aims to produce refault signals that are defensible upstream by relying on realistic app pressure and post-hoc trace evidence rather than synthetic victim targeting.
