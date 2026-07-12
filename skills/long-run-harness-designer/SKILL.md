---
name: long-run-harness-designer
description: Design task-specific harnesses for long-running experiments, benchmarks, boot loops, stress tests, samplers, or agent-run automation. Use when Codex is about to create or run a long-run script, synthetic workload, Cuttlefish/QEMU/device experiment, A/B benchmark, sampling loop, or any workflow where early bad signals should stop the run instead of wasting time. The skill teaches Codex to derive minimal control parameters, reject unjustified sleeps/intervals/touch strides, add task-local self-check hooks, define early sanity gates, and provide a concrete implementation example without hardcoding one domain.
---

# Long-Run Harness Designer

This is a meta-skill. Do not treat it as a fixed hook library. Treat it like an abstract class for designing a task-specific harness before running any long-duration experiment.

## Core Rule

Before running a long task, define what must be true early. If early evidence contradicts the intended workload, stop and debug the harness. Do not keep running just because the process is making progress.

## Abstract Harness Contract

For every long-run task, instantiate these parts for the current domain:

1. **Intent**: State what pressure, behavior, or system path the run is supposed to exercise.
2. **Minimal Controls**: List only variables required by the intent. Prefer deleting extra knobs over adding optional knobs.
3. **Unjustified-Knob Audit**: Reject new sleeps, intervals, strides, retry loops, touch gaps, random delays, and background churn unless the user requested them or evidence justifies them.
4. **Launch Evidence**: Define task-local proof that the workload actually started, not just that a command was issued.
5. **Workload Self-Evidence**: Define task-local proof that the intended inner operation happened.
6. **Early Metric Gate**: Choose cheap counters/logs to inspect after the first small slice of runtime.
7. **Abort Rule**: Define exact conditions that discard the run and stop the long task.
8. **Resume/Checkpoint Rule**: Write the active plan, expected evidence, and known bad signatures to a durable checkpoint file.
9. **Output Contract**: Report whether the run reached launch, self-evidence, early metric gate, full run, or abort.

## Minimal-Control Discipline

When adding code or scripts:

- Default to fewer variables.
- Delete knobs whose only valid value is always zero.
- Do not add generic `*_interval_ms`, `*_stride`, `*_sleep`, `touch_every_n`, or background loops unless they map to the user's stated experiment variable.
- If a delay is needed only after a first burst, put it after the first burst and name it as a steady-state parameter.
- If a parameter exists only for future flexibility, do not add it yet.
- If a workload is synthetic, use direct semantic units like `cow_pages_per_child`, `resident_mb`, `filemap_mb`, `process_count`; avoid indirect pacing controls unless pacing itself is the experiment.

## Unjustified-Knob Audit Pattern

Before accepting a diff, scan for these patterns in new or modified lines:

```text
sleep, usleep, nanosleep, Thread.sleep, time.sleep
interval, delay, throttle, backoff, retry_sleep
stride, step, sample_every, touch_every
while true background loops
random jitter, fixed wait before first action
```

For each hit, require one of:

- explicitly requested by the user;
- copied from existing workflow semantics without changing behavior;
- proven by measured real workload behavior;
- after-first-burst steady-state behavior that cannot suppress the early gate.

If none applies, remove it.

## Launch Evidence Pattern

Do not trust command success alone. Define evidence at the boundary where the target system accepts and begins the workload.

Examples:

- Android app: `am start` has no negative result code, target activity is not shell-expanded/truncated, app process exists, and app log marker appears.
- Kernel/QEMU/CVD: guest reaches a named boot phase, expected serial/ADB/SSH endpoint is ready, and expected kernel/build hash appears.
- Benchmark: child process emits its own initialized marker and reports the parsed config.
- Sampler: first sample includes all required counters and no missing/zero schema error.

## Workload Self-Evidence Pattern

Define proof for the inner operation, not merely the wrapper.

Examples:

- COW workload: child reports actual pages written, and parent records child count and per-round pages.
- Filemap workload: process reports bytes/pages read and map count.
- Allocator workload: process reports live bytes, allocation count, and touched bytes.
- GC workload: process reports GC trigger count or observed runtime event.
- Network workload: server reports accepted requests and payload bytes, not just client started.

## Early Metric Gate Pattern

Run a short pilot slice before the full run. The gate must be cheap, deterministic, and domain-specific.

Define:

- `gate_duration` or `gate_cycles`;
- required launch evidence;
- required self-evidence;
- minimum/maximum sanity thresholds;
- bad signatures that immediately discard the cell;
- whether the full run can continue automatically after passing.

If an early metric is unexpectedly low, stop and inspect harness correctness first. Do not continue to the full run hoping it improves.

## Checkpoint Pattern

Write a task-local checkpoint before a long run starts. Include:

```markdown
# Active Long-Run Checkpoint
- objective:
- expected workload behavior:
- minimal control parameters:
- forbidden invented knobs:
- launch evidence:
- self-evidence:
- early metric gate:
- abort signatures:
- current artifacts:
- resume command:
```

On context resume, read this checkpoint before touching scripts or interpreting results.

## Task-Local Hook Script Pattern

For each concrete task, write small validation scripts near the artifacts or in `.worklog/` first. Promote only reusable patterns to the owning skill.

Recommended scripts:

- `validate_diff_knobs.py`: fail on unjustified new sleeps/intervals/strides.
- `validate_launch_evidence.py`: fail if target accepted no real workload.
- `early_metric_gate.py`: parse first samples/logs and stop/discard if sanity fails.
- `summarize_cell.py`: report evidence before metrics.

Rules for long-run scripts:

- Do not use `set -e` in the main long-run orchestrator.
- Use lock/PID/PGID/state files for single-instance control.
- Persist logs, heartbeat, status, exit code, and stop reason.
- Readiness must be evidence-based, not just “process started”.

## Report Template

Always report:

- phase reached: launch / self-evidence / early gate / full run / abort
- minimal controls used:
- invented knobs removed:
- launch evidence:
- self-evidence:
- early gate result:
- discarded artifacts:
- next action:

## Concrete Example

For a full example based on Android Cuttlefish synthetic mTHP workloads, read `references/cvd_mthp_synthetic_example.md`.
