---
name: bug-possibility-matrix
description: Use when debugging a complex bug and you need a maintained hypothesis matrix whose statuses are updated only with executable code paths and exact file:line evidence, especially for kernel, filesystem, writeback, mmap, race, or corruption investigations where vague speculation is harmful.
---

# Bug Possibility Matrix

Use this skill when free-form debugging discussion starts to drift and every hypothesis must be tracked as a first-class item with a strict status.

## Core Rule

Every hypothesis row must be one of:

- `开放`
- `排除`
- `基本确凿`

No row may change status without:

1. an executable code path,
2. exact file and line references,
3. concrete runtime evidence when runtime evidence exists,
4. a written reason that connects step 1 and step 3 without gaps.

## Forbidden Language

Do not write:

- “可能别的线程造成这个问题”
- “可能某个代码路径造成这个问题”
- “大概是”
- “猜测”
- “看起来像”

Do not use any wording that leaves the causal bridge unstated.

If the bridge is incomplete, keep the row at `开放` and write the exact missing edge.

## Workflow Contract

### Main Workflow
1. Name the bug and pin the concrete failing sample, inode, address, folio, page index, or artifact label.
2. Record a compact path card or equivalent round label so resume/compression can reload the current discriminator quickly.
3. Initialize the hypothesis matrix with only the hypotheses that already have a real trigger.
4. For each hypothesis, write the shortest executable call chain from current evidence to the relevant code.
5. Assign a status using the decision table below.
6. On every new log or code read, update only the affected rows and record why the status changed.
7. Keep a short “next candidate slots” section for not-yet-initialized hypotheses.

### Decision Table
| Status | Required condition | Required evidence | Forbidden shortcut |
|---|---|---|---|
| `开放` | The code path exists, but one or more causal edges are still unproven | Exact code lines plus the specific missing proof edge | Saying “maybe X or Y” without naming the missing edge |
| `排除` | The hypothesis contradicts either the code path or the observed runtime evidence | Exact code lines and exact counter-evidence lines | Declaring exclusion from intuition or from absence of thought |
| `基本确凿` | The code path and the runtime evidence line up end-to-end for the current sample | Exact call chain, exact runtime lines, and exact bridge from one to the other | Using “likely” instead of writing the bridge |

### Output Contract
- bug:
- sample:
- path card:
- rows changed:
- new status:
- reason with file:line chain:
- next missing edge:

## Reopen Rule

Do not reopen a row already marked `排除` unless the new round contains explicit new evidence that invalidates the old exclusion.

Required format when reopening:

1. cite the old exclusion reason,
2. cite the new contradictory evidence,
3. name the exact missing bridge that is open again.

Do not reopen a row only because the current session forgot the earlier analysis.

## Matrix Format

Always maintain both:

1. a compact matrix table
2. a detailed reasoning ledger

The compact matrix table must have these columns:

- `ID`
- `Hypothesis`
- `Status`
- `Reason`

The detailed reasoning ledger must repeat each row ID and expand the reason into numbered steps.

## Reason Format

Each reason must be a numbered chain.

Good:

1. function A calls function B at `foo.c:10-18`
2. function B returns `-ENOSPC` through `bar.c:90-103`
3. `vmf_fs_error(-ENOSPC)` converts that to `VM_FAULT_SIGBUS` at `mm.h:4002-4011`
4. runtime log line `guest_console.log:402707` shows the same `err=-28 ret=0x2`

Bad:

- kernel probably returned an allocation error somewhere
- maybe writeback later skipped this folio

## Layer-Splitting Rule

When the current sample proves an outer return code but does not yet prove the inner subcause, split them into separate rows.

Required pattern:

1. one row for the proved outer layer
2. one or more rows for the still-open inner causes

Example:

- `外层已证实`: `page_mkwrite` returned `-ENOSPC` and `vmf_fs_error()` turned it into `SIGBUS`
- `内层开放`: `-ENOSPC` came from checkpoint-not-ready
- `内层开放`: `-ENOSPC` came from valid-block-count admission
- `内层开放`: `-ENOSPC` came from segment/block allocation

Do not collapse these into a single sentence such as “disk is full”.

## Initialization Rule

Do not inflate the matrix on day one.

Only initialize:

- hypotheses already suggested by observed evidence,
- hypotheses the user explicitly wants tracked,
- hypotheses with an immediately readable code path.

Everything else belongs in `Next Candidate Slots`, not in the live matrix.

## Runtime Evidence Rule

When logs exist, cite exact log line numbers.

When a log search returns no hits, do not write “no hits” as the only proof.
Instead:

1. prove the code would have emitted a log on that path,
2. show the same probe emitted on other comparable paths,
3. show the target sample's own relevant lines.

When a probe logs local variables, first verify where each variable is initialized and on which exits it is updated.

Do not infer a branch solely from a logged field if an earlier return can leave that field at its default initializer.

## Static-Code Rule

When the goal is static analysis, prefer:

- direct caller-to-callee chains,
- exact return-value handling,
- state transitions on the same variable,
- invariant checks and warnings,
- file-format or sample-layout boundaries that line up with page or folio boundaries.

Do not substitute broad architecture summaries for the exact chain.

## Skill Output Style

When answering the user:

- lead with rows whose statuses changed,
- state the new status in the first sentence,
- cite the exact file:line chain in the same paragraph,
- keep the matrix current in `.worklog`,
- carry forward only unresolved rows.

## Minimal Template

```md
| ID | Hypothesis | Status | Reason |
|---|---|---|---|
| H001 | ... | 开放 | 1. ... `a.c:10-20` 2. ... `b.c:90-100` 3. missing edge: ... |

### H001
1. ...
2. ...
3. ...
```
