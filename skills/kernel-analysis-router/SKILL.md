---
name: kernel-analysis-router
description: Use when a Linux kernel task is primarily about top-down analysis, path classification, tracepoint work, trace capture, large-log search with rg, or git bisect. This router chooses the correct analysis workflow before detailed code or log analysis starts.
---

# Kernel Analysis Router

Use this router after `kernel-workflow-router` when the task is analysis-first.

## Choose The Next Skill

- Use `topdown-kernel-diagnosis` for top-down recursive analysis where the goal is to classify dominant runtime paths cheaply first, then add targeted stack capture only for hot reasons.
- Use `kernel-tracepoint-pattern` when the main task is adding or fixing Linux kernel tracepoints, including `TRACE_EVENT`, `CREATE_TRACE_POINTS`, and trace event wiring.
- Use `kernel-trace-analysis-pipeline` when the task spans instrumentation, trace collection, trace analysis, and final reporting as one end-to-end pipeline.
- Use `kernel-log-instrumentor` when the main gap is insufficient kernel logging and the next step is to add or refine structured logs before broader analysis.

## Search Rules

- For large logs, use `rg` as the default search tool.
- If the user asks for path attribution or dominant callers, prefer `topdown-kernel-diagnosis` before high-overhead tracing.
- If the user already has a trace design and just needs wiring or compile fixes, go directly to `kernel-tracepoint-pattern`.

## Handoff Rules

- If the task requires booting or controlling a VM before analysis can proceed, switch to `kernel-vm-router` first.
- If the task ends in a report or handoff document, apply `handoff-doc-style` before final drafting and use `experiment-report-writer` when the output is leadership-facing or office-style.
