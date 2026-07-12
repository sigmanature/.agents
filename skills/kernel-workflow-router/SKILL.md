---
name: kernel-workflow-router
description: Use for Linux kernel development workflows that may involve multiple related skills, especially when the user mentions QEMU or guest VMs, top-down analysis, tracepoints, large log search with rg, git bisect, long-running experiment capture, or handoff-style reports. Read this router first, then choose the relevant sub-router or workflow.
---

# Kernel Workflow Router

Use this skill as the first routing layer for the user's common kernel workflow stack.

## Routing Rule

If the task matches one of the categories below, read the listed sub-router or workflow next before doing detailed work.

## Skill Map

### 1. VM / guest / QEMU workflow

Use `kernel-vm-router` next when the user asks to:

- start or reuse a QEMU VM
- run commands in a guest
- use QGA or guest SSH
- reproduce a bug inside a VM
- run xfstests or fsstress in a guest

Primary downstream skills:

- `kernel-vm-router`
- `f2fs-qemu-agent-pipeline`
- `kernel-debug-orchestrator`
- `xfstests-qga-ubuntu`
- `fsstress-qga-deploy`

### 2. Analysis / trace / large logs

Use `kernel-analysis-router` next when the user asks to:

- do top-down analysis
- classify runtime paths or dominant callers
- add or fix kernel tracepoints
- analyze traces or capture call stacks
- search large logs with `rg`
- run or interpret `git bisect`

Primary downstream skills:

- `kernel-analysis-router`
- `topdown-kernel-diagnosis`
- `kernel-tracepoint-pattern`
- `kernel-trace-analysis-pipeline`
- `kernel-log-instrumentor`

### 3. Reports / handoff documents

Go directly to the reporting skills when the user asks to:

- write an experiment report
- prepare a handoff document
- turn technical results into recipient-facing output

Primary downstream skills:

- `handoff-doc-style`
- `experiment-report-writer`

## Selection Notes

- Prefer the VM route for anything that starts with runtime bring-up, guest access, or reproduction in QEMU.
- Prefer the analysis route for anything that starts with classification, instrumentation, trace capture, or large-log inspection.
- If the task spans VM bring-up and analysis, read `kernel-vm-router` first, then switch to `kernel-analysis-router` once the guest runtime is ready.
- If the task ends in a recipient-facing summary or report, apply `handoff-doc-style` before drafting final output.
