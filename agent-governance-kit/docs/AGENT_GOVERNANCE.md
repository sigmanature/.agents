# Agent governance policy (enforced by tooling)

This document explains the **intent** behind the tooling in `tools/agentctl.py`.

## Worklog is a staging queue

The worklog exists to prevent context evaporation. It is **not** the final landing zone.

Every worklog item must end in exactly one terminal state:

- discarded
- deferred (with reason)
- promoted (record which file(s) it was promoted to)

Promotion targets should be one of:
- `references/` (reusable caveats, environment pitfalls, error signatures)
- `scripts/` (repeatable deterministic procedures)
- `skills/` or your skill docs (changes to triggers, decision points, troubleshooting)

## Job registry

Any long-running/background process must be started via `agentctl start`.

A background job has 3 phases:

1) `running` — started and tracked
2) `finished` — exit code known, logs available
3) `consumed` — someone extracted meaning and recorded next actions

The close gate **fails** if any job is:
- still running, or
- finished but not consumed

This prevents the common failure mode: start a job, forget it existed, and stop prematurely.

## Close gate

The close gate is a *hard stop* against premature completion.

It fails when:

- worklog has open/triaged items not moved to a terminal state
- job registry has running jobs or finished-but-unconsumed jobs

In other words: if the agent wants to stop, it must **close the loop**.

## Narrow auditor

The auditor is intentionally narrow:
- It does not read hidden reasoning.
- It does not modify code.
- It only inspects **observable artifacts**: worklog, job registry, and state.

Output is a short verdict with concrete next actions. This can be pasted to the main agent.

