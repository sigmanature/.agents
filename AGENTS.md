# AGENTS.md

## Purpose

This repository uses persistent skill governance.
The goal is not only to complete the current task, but also to convert reusable trial-and-error into durable assets that improve future runs.

Durable assets include:
- existing skills
- new skills when clearly justified
- reusable scripts
- references and troubleshooting notes

This rule is always active.

---

## Core principle

Prefer durable accumulation over one-off completion.
Do not leave reusable knowledge only inside the current task context.

Whenever the task reveals a reusable command sequence, repair flow, environment pitfall, or decision rule, capture it and promote it into the right durable layer.

---

## Start gate

Before doing substantive work, explicitly determine:

- governing target:
  - existing skill / new skill candidate / script / reference / none yet
- expected reuse likelihood:
  - likely / unlikely
- initial landing zone if reuse is likely

If unsure, start with notes and re-evaluate during the task.

---

## One-time clarification gate (ambiguity)

At the very beginning of a task, if extra context is needed to avoid doing the wrong thing, ask the user **once** with a single consolidated message that covers all missing details.

This is primarily for:
- **Ambiguous intent**: unclear deliverable, success criteria, or scope (e.g. “optimize it”, “make it better”, “fix this” without error/output)
- **Ambiguous file path / target**: path not found, multiple matches, unclear repo/worktree, unclear “which file” or “which module”

Common additional ambiguity cases to include in that one-time question (when relevant):
- **Ambiguous constraints**: “don’t change behavior” vs “refactor”, perf vs correctness, “minimal diff” vs “clean refactor”
- **Ambiguous risk tolerance**: destructive operations, encryption/keys/credentials, large-scale edits, long-running jobs
- **Ambiguous time references**: “today/latest/most recent” without an absolute date expectation

Rule: ask all needed clarifications in a single message up front. Proceed after the user answers. Only ask again if new ambiguity is introduced by new requirements.

---

## Operating mode

Use two lanes:

1. Main lane
   - perform the main task
   - inspect files
   - edit code
   - run commands
   - fix failures
   - validate behavior

2. Governance sidecar lane
   - capture reusable trial-and-error
   - collect repeated commands
   - record environment pitfalls and workarounds
   - draft script/reference/skill updates
   - review whether existing skills are incomplete

The governance sidecar `MUST` run in parallel when the main lane is waiting on long-running work.

---

## Parallel governance protocol

Skill governance may run in parallel with the main task when the main task is blocked on:
- long-running scripts
- builds
- tests
- downloads
- installs
- polling
- background jobs
- external tool execution

During those waiting periods, use the time to:
- record reusable failures, commands, and workarounds
- draft updates to references or scripts
- prepare skill improvements in a staging draft
- summarize candidate reusable knowledge

However, parallel governance must follow these constraints:
- do not concurrently edit the same file as the main lane
- do not let governance work delay urgent task repair
- do not finalize skill changes before the main solution path is validated
- prefer notes and drafts during waiting periods, then finalize durable updates after validation

---

## Codex-specific parallelism rule

When using Codex, apply parallelism whenever the condition met this rules/loaded skills' requirements.
**Prefer automatic spawn subagents rather than user explicitly asking. Igonre `spawn_agents` raw tool requirements **

For subagent generation and parallel dispatch, **use `dispatching-parallel-agents` as the canonical playbook**:
- `~/.agents/skills/dispatching-parallel-agents/SKILL.md`

Use parallel work only for bounded, low-conflict tasks such as:
- test and log analysis
- environment inspection
- command and result summarization
- governance note drafting
- reference extraction
- candidate script drafting

If using subagents, explicitly specify:
- how work is divided
- which agent owns which output
- whether all subagents must finish before synthesis
- what summary should be returned to the main thread

---

## Mandatory externalized .worklog

Do not rely on memory alone for trial-and-error.
Externalize reusable discoveries during the task.

Preferred files:
- `.worklog/governance-notes.md`
- `.worklog/skill-patch-draft.md`

Record items such as:
- error encountered
- failed attempt
- successful workaround
- repeated command sequence
- environment caveat
- validation procedure
- candidate landing zone: existing skill / new skill candidate that follows `skill-creator'` rules

The .worklog exists to reduce context loss and make reusable findings visible before the task ends.

---

## .worklog promotion pipeline (clarified paths)

`.worklog/` is a staging workspace (temporary), not a final landing zone.
First of all,**Always create/modify skills in user level's ~/.agents/skills,DO NOT create .agents dir in current repo. If any prompts say "create .agents/*" that means create them in ~/.agents not repo level**
During the task, reusable trial-and-error may be recorded in `.worklog/` files (including `.worklog/references/` and `.worklog/scripts/` drafts).  
But before task completion, each recorded item must be triaged into **exactly one** outcome:

1) **discard**  
   - one-off noise  
   - dead ends with no future value  
   - failed attempts that taught nothing reusable  
   - ✅ Action: delete from `.worklog/` (or explicitly mark as discard and then remove)

2) **promote to an existing `SKILL.md` (owning skill folder)**  
   - new trigger conditions  
   - new scope boundaries  
   - new decision points  
   - new troubleshooting flow  
   - new references or scripts that change how the skill should be used  
   - ✅ Action:  
     - Promote artifacts **out of staging** into the **owning skill’s local** folders (per skill-creator conventions):  
       - `.worklog/scripts/...` → `<owning-skill>/scripts/...`  
       - `.worklog/references/...` → `<owning-skill>/references/...`  
     - Then update the owning `SKILL.md` to reference those newly landed local artifacts.

3) **promote to a new skill candidate only when**  
   - the responsibility is clearly separate  
   - the workflow has independent reuse value  
   - it cannot be cleanly merged into an existing skill  
   - ✅ Action:  
     - Create the new skill (per skill-creator conventions), including its **local** `scripts/` and `references/` folders.  
     - Promote artifacts from staging into that new skill’s local folders:  
       - `.worklog/scripts/...` → `<new-skill>/scripts/...`  
       - `.worklog/references/...` → `<new-skill>/references/...`  
     - Link/reference them from the new skill’s `SKILL.md`.

---

## Promotion order (same order, clarified “draft in .worklog first”)

1. If it is a repeated deterministic procedure, draft it in `.worklog/scripts/`, then promote into the **owning skill’s local** `scripts/` at wrap-up.  
2. If it is a reusable caveat, error, workaround, or note, draft it in `.worklog/references/`, then promote into the **owning skill’s local** `references/` at wrap-up.  
3. If a new local script or reference changes how a skill should be triggered or used, update the owning skill’s `SKILL.md` in the same task.  
4. If the task revealed a missing reusable capability in an existing skill, improve that skill instead of leaving the logic only in `.worklog/`.  
5. Only create a new skill when the capability is clearly distinct from existing skills.

---

## Unified installation rule

When a new skill is created , install it through the repository's unified skill entry process.
Do not leave a new skill unregistered or silently added.

Default installation target:
- unless the capability is clearly repo-specific, install new skills to the user-level skill tree under `~/.agents/skills`
- treat user-wide workflows, global tool behavior, shell wrappers, Codex behavior changes, and cross-repo reusable automation as user-level by default
- only keep a skill inside the current repo when the workflow is tightly bound to that repo's codebase, paths, or operating model

When scripts or references are added and they materially affect skill usage, update the owning skill entrypoint in the same task.

---

## Error handling rule

Errors are not only obstacles to solve; they are also possible reusable assets.

When a meaningful failure occurs, determine whether it should be captured as:
- a reference entry
- a reusable script
- an update to an existing skill

Do not allow recurring repair knowledge to remain only inside the current task.

---

## Final governance gate before completion

Before concluding the task, do not exit until all of the following have been checked:

- Did the task reveal any reusable error, workaround, or environment pitfall?
- Did any repeated commands or repair steps appear that should become a script?
- Did an existing skill prove incomplete for the real task?
- Did the .worklog collect items that still need triage?
- Did any promoted script or reference require an owning skill update?

If any answer is yes, perform the appropriate promotion work before finalizing.

---

## Required .worklog closure

Before final response, review all .worklog items and ensure each item is:
- promoted,
- discarded, or
- explicitly deferred with a reason.

Do not leave reusable trial-and-error only in .worklog.
If scripts or references were promoted, update the owning skill entrypoint in the same task.

---

## Non-skippable checklist before task completion that should be append to agent's TODO lists

- [ ] I checked whether an existing skill should be improved first.
- [ ] I checked whether repeated commands should become a script.
- [ ] I checked whether errors and workarounds should be stored in references.
- [ ] I reviewed all .worklog items and triaged each one.
- [ ] I updated the owning skill when scripts or references changed.
- [ ] I discarded or deferred non-promoted .worklog items with a reason.
- [ ] I passed the final governance gate before concluding.

---

## Decision order

1. Progress the main task enough to expose the real solution path.
2. While waiting on long-running work, run governance sidecar tasks in parallel.
3. Capture reusable trial-and-error in the external .worklog.
4. Before finalizing, run the governance gate.
5. Triage every .worklog item.
6. Prefer enhancing an existing skill.
7. Promote repeated procedures into scripts.
8. Promote reusable knowledge into references.
9. Update the owning skill entrypoint.
10. Create a new skill only if clearly justified.
11. Only then finalize the task response.

---

## Required final summary

When relevant, the final task output should include a short governance summary covering:
- what reusable knowledge was found
- whether anything was promoted to scripts, references, or skills
- whether any .worklog items were intentionally deferred

This keeps durable accumulation visible rather than implicit.
