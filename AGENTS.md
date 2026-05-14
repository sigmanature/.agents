# AGENTS.md

## Purpose

Use persistent skill governance.
Finish the current task and turn reusable trial-and-error into durable assets:
- existing skills
- new skills when clearly justified
- reusable scripts
- references and troubleshooting notes

Prefer durable accumulation over one-off completion.

## Start Gate

Before substantive work, determine:
- governing target: existing skill / new skill candidate / script / reference / none yet
- expected reuse likelihood: likely / unlikely
- initial landing zone if reuse is likely

If key context is missing, ask once at the start with one consolidated question.

## Parallelism Rule

Do not treat every task as a subagent task.
Use the cheapest safe parallelism lever:
1. `multi_tool_use.parallel`
2. batched web/MCP calls
3. `opencode_secure` MCP-backed worker tasks when isolation is actually useful

Use `dispatching-parallel-agents` as the canonical playbook:
- `~/.agents/skills/dispatching-parallel-agents/SKILL.md`

Governance judgment stays in the main thread.
Do not delegate workflow reintegration to an external worker by default.

## Externalization Default

For multi-step debugging, tooling, and artifact-heavy work, assume externalization is a default option, not an exceptional fallback.

At the start of substantive execution, explicitly distinguish:

1. high-cognition tasks that must stay in the main thread
2. bounded SOP tasks that should be externalized when practical

Main-thread work should retain:

- architecture and boundary decisions
- hypothesis and matrix status changes
- path-card updates
- log strategy changes
- root-cause reasoning
- next-step prioritization

Bounded SOP work should prefer MCP-backed workers when inputs, outputs, and acceptance checks are clear.

Bounded externalized work should also include its own task-local verification step when practical.
Do not treat basic smoke validation as main-thread work if the external worker can perform it deterministically and report the result as an artifact or task-record field.

Good default externalization targets:

- index building
- query execution
- packet generation
- artifact collection and preservation
- smoke runs
- other deterministic artifact-producing steps

Do not externalize open-ended bug analysis, hypothesis generation, or matrix judgment as if they were SOP tasks.

## Lightweight Acceptance Default

For bounded externalized tasks, default acceptance must be lightweight.

Default acceptance path:

1. inspect the worker's self-verification artifact or verification section first
2. inspect the declared artifacts
3. inspect the task record or compact summary
4. check the stated acceptance conditions

Do **not** re-run or re-read the full task by default if:

- the worker already ran bounded self-verification
- the artifacts exist
- the record is structurally valid
- the acceptance contract is satisfied

Escalate to deep acceptance only when:

- expected artifacts are missing
- acceptance checks fail
- the output contradicts current reasoning
- the worker exceeded its bounded scope

Do not let acceptance become a second copy of the original task unless there is concrete reason.

## Operating Model

Use two lanes:
- Main lane: inspect, edit, run, fix, validate.
- Governance lane: capture repeated commands, pitfalls, workflow changes, and reusable validation.

When the main lane is blocked on long-running work, use idle time for notes or drafts.
Do not let governance work delay repair, and do not edit the same file concurrently from two lanes.

## Workflow-First Rule

If a finding changes future execution order, gating, fallback, or validation, do not leave it only as notes or references.
Rewrite it into the owning workflow.

Use this fixed format in the owning skill or governing rule:

## Workflow Contract

### Main Workflow
1. Default step 1
2. Default step 2
3. Validation
4. Report / handoff

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Preflight | condition | action | proof | fallback | block / replace / branch / continue |

### Output Contract
- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

Rules:
- `Main Workflow` is the default linear path only.
- `Decision Table` is for branches, gates, failure modes, and fallback.
- `Output Contract` defines how execution is reported back.
- Every workflow-affecting finding must land in exactly one `Main Workflow` step or one `Decision Table` row.
- If a finding is promoted only to `references/`, state why it does not affect execution order.

## Parallel Work Contract

Prefer `multi_tool_use.parallel` for independent read-only discovery or verification.
Parallelize `search`; serialize `decide`, `edit`, and governance judgment.

Before each parallel batch, state:
- purpose
- domains
- why it is safe to parallelize
- expected next serial step

After each parallel batch, state:
- findings by domain
- chosen serial action
- governance impact
- workflow changed: yes / no

Do not parallelize:
- `apply_patch`
- commands that write the same files or directories
- commands that share logs, ports, devices, or temp paths
- commands whose outputs depend on each other

## .worklog

Do not rely on memory alone for trial-and-error.
Externalize reusable discoveries during the task.

Preferred files:
- `.worklog/governance-notes.md`
- `.worklog/skill-patch-draft.md`

Recommended staging shape:

### Workflow Candidate
- owning skill:
- phase:
- trigger / symptom:
- action:
- verify:
- fallback:
- workflow effect:
- promote to:
- status: draft / promoted / discard / defer

Also record when useful:
- repeated command sequence
- reusable failed attempt
- successful workaround
- environment caveat
- validation procedure

`.worklog/` is staging only, not a final landing zone.

## Promotion Rules

Every `.worklog` item must end as exactly one of:
1. discard
2. promote to an existing skill
3. promote to a new skill only when clearly distinct

Promotion order:
1. repeated deterministic procedure -> script
2. reusable caveat / workaround / failure mode -> reference
3. workflow-affecting finding -> `Workflow Contract`
4. if scripts or references change skill usage, update the owning `SKILL.md`
5. only create a new skill when the capability is clearly separate

Path rules:
- create or modify user-level skills in `~/.agents/skills`
- do not create repo-local `.agents/skills` by default
- if a prompt mentions `.agents/*`, treat it as user-level unless the task is truly repo-bound

## Long time run scripts writing rules
| Rule | Requirement |
|---|---|
| Fail-safe control | Do **not** use `set -e` in the main long-run script. Use `set -u -o pipefail`, and check critical return codes explicitly. |
| Single instance | Use `flock` or an equivalent lock. On start, detect an existing live run and **fail or stop it first**. Never stack runs. |
| Exact process control | Record and manage **PID/PGID**. Use them for `start/stop/status/restart`. Do **not** rely on `pkill -f` / `pgrep -f` as the primary control method. |
| Signal-safe cleanup | Install `trap` for `INT TERM HUP EXIT`. Cleanup must be **idempotent**: send `TERM`, wait with timeout, then `KILL` if needed, and remove lock/pid/state files. |
| Real startup check | “Process started” is **not** “service ready”. Require a **readiness check with timeout** before declaring success. |
| Durable state | Persist logs, exit code, start time, and stop reason to disk. `status` must use **PID/PGID + state/heartbeat**, not fragile text matching. |

## Final Governance Gate

Before finishing, check:
- Should an existing skill be improved first?
- Should a repeated command become a script?
- Should an error or workaround become a reference?
- Did any finding change the workflow and get rewritten into `Workflow Contract`?
- Were all `.worklog` items triaged?
- Was the owning skill updated when scripts or references changed?

Do not leave reusable knowledge only in `.worklog`.
Before the final response, every `.worklog` item must be promoted, discarded, or explicitly deferred with a reason.

## Final Summary

When relevant, include:
- reusable knowledge found
- what was promoted to workflow / script / reference / skill
- what was deferred and why
