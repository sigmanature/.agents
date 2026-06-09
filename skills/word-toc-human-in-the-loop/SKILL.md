---
name: word-toc-human-in-the-loop
description: Use when updating a thesis or dissertation Word TOC after body edits, especially when the document has a hand-tuned TOC template, page numbers must be recalculated by Windows Word, the workflow must survive cross-device migration, or the user mentions dirty TOC, Word TOC, prepared/restyled docx, thesis目录, 目录页码不对, or wants the agent to handle everything except the manual Word refresh step.
---

# Word TOC Human-In-The-Loop

## Overview

Use this workflow when the thesis source of truth is a `.docx` file and the TOC must stay synchronized with body headings and page numbers.

The agent owns structure checks, `prepare`, `restyle`, and status reporting. The user owns the one step Linux tooling should not fake: opening the doc in Windows Word, updating the entire TOC, saving, and doing final visual judgment.

Runtime note for this workspace: the workflow commands need a Python interpreter that already has `python-docx` installed. On this machine, `/home/nzzhao/miniconda3/bin/python` works, while the system `python3` may not.

## Bundled Assets

This skill is now self-contained for migration:

- workflow CLI: `scripts/word_toc_workflow.py`
- portability self-check: `scripts/selfcheck.py`
- workflow reference: `references/workflow.md`
- migration notes: `references/migration.md`
- visual acceptance: `references/visual-checklist.md`
- legacy resource map: `references/resource-map.md`
- legacy snapshots:
  - `references/legacy/thesis.py`
  - `references/legacy/thesis_wordtoc_wrapper.py`

Read `references/migration.md` when the user is moving this workflow to a new machine.

If `python3` fails with `ModuleNotFoundError: No module named 'docx'`, rerun the workflow with the Miniconda base interpreter at `/home/nzzhao/miniconda3/bin/python` or another conda environment that provides `python-docx`.

## Current-Doc vs Template-Doc

Do not hardcode local machine paths.

Always separate:

- current working thesis docx: the user's latest edited or Word-updated document
- template docx: the style truth used for `restyle`

On a new device, these two files are user assets and must be supplied or migrated separately. The skill bundle does not assume they live under any fixed directory.

Example naming convention only:

- `thesis.current.docx`
- `thesis.template.docx`

## When To Stop And Hand Off

Stop and hand off to the user exactly when the current document has been converted into a Word-updatable TOC state and Windows Word must recalculate final pagination.

That handoff is mandatory because:

- final TOC page numbers must come from Windows Word's pagination engine
- the agent should not pretend ONLYOFFICE, python-docx, or PDF conversion can replace Word for final page numbering

## Agent-Owned Steps

### 1. Identify the current working document

Determine which file is the real current state:

- user-edited thesis docx
- pre-Word `*.prepared.docx`
- Word-updated `*.prepared.docx`
- already-restyled final docx

If multiple thesis-like files exist, prefer the newest file the user explicitly points to. If the user corrected the agent previously, trust the corrected file, not the old template.

### 2. Audit first

Run:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/word_toc_workflow.py audit CURRENT.docx
```

Interpretation:

- `is_dirty: True` means body headings and current TOC no longer match
- `page_numbering_ok: False` means body page restart is not yet encoded
- `摘要` / `ABSTRACT` are special TOC rows and should not be treated as dirty mismatches by themselves

If `audit` raises `no TOC entries found`, treat the file as a manual-TOC or otherwise non-Word-TOC baseline and move to `prepare` on the correct source document instead of assuming the audit data is valid.

### 3. Prepare when the doc still uses a manual TOC

If the current doc is still a manual-TOC baseline, run:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/word_toc_workflow.py prepare CURRENT.docx --output CURRENT.prepared.docx
```

This step is agent-owned. It should:

- preserve the TOC title
- replace manual TOC rows with a real Word TOC field
- mark `摘要` / `Abstract` as TOC-visible level-1 items
- write body page restart from `1`

### 4. Hand off for Windows Word refresh

After `prepare`, stop and tell the user exactly this kind of instruction:

1. open `CURRENT.prepared.docx` in Windows Word
2. click inside the TOC
3. choose `更新整个目录`
4. save the file
5. send back the saved docx path

Do not continue to `restyle` until the user confirms the Word-updated file path.

### 5. Restyle after Word update

Once the user returns a Word-updated docx, run:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/word_toc_workflow.py restyle WORD_UPDATED.docx --template TEMPLATE.docx --output WORD_UPDATED.restyled.docx
```

Then run `audit` again on the output and report whether:

- `is_dirty: False`
- `page_numbering_ok: True`

### 6. Report what still requires human eyes

Even when the workflow succeeds, explicitly say the remaining manual checks are:

- final left-side same-level alignment
- right-aligned page-number column
- long-line visual balance
- presence and level of `摘要`, `ABSTRACT`, `参考文献`, `致谢`
- whether chapter 1 starts at page `1`

Read `/home/nzzhao/.agents/skills/word-toc-human-in-the-loop/references/visual-checklist.md` when you need the exact checklist.
Read `references/visual-checklist.md` when you need the exact checklist.

## User-Owned Steps

The user must do these manually:

- edit thesis body content in Word
- keep real headings as Word heading levels instead of plain body paragraphs
- open the prepared doc in Windows Word and update the entire TOC
- make the final visual judgment
- optionally make last-mile micro-adjustments for a few extreme long lines near submission time

## What The Agent Must Not Do

- do not hand-edit TOC text rows as the normal workflow
- do not claim the final page numbers are correct before a Windows Word refresh happened
- do not confuse the template truth with the current working thesis
- do not promise pixel-identical reproduction of a hand-tuned TOC for every long line
- do not assume the old thesis repo exists on the target machine just because this skill was migrated

## Body Replacement Formatting Gate

Use this gate when the agent replaces thesis body content, such as regenerating a chapter from Markdown into an existing `.docx`.

## Workflow Contract

### Main Workflow
1. Start from the current/template thesis `.docx`; do not continue from a visually broken generated copy.
2. Replace only the intended body range, bounded by real Word heading styles such as `Heading 1`, not by TOC text rows.
3. Insert new content with the legacy body-format references:
   - headings: `references/legacy/thesis_wordtoc_wrapper.py` and `references/legacy/thesis.py`
   - body paragraphs: `references/legacy/thesis.py`
   - figures, captions, tables, and code blocks: `references/legacy/thesis.py`
4. Validate body formatting before any TOC handoff.
5. Report / hand off for Windows Word `更新整个目录` only after the body-format checks pass.

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Preflight | Replacement source is Markdown or another non-Word body source | Load legacy formatting references before writing the `.docx` | Confirm heading/body/figure/code rules are known | Stop and inspect `references/legacy/thesis.py` plus `references/legacy/thesis_wordtoc_wrapper.py` | block |
| Replacement | Target range could match TOC rows and body headings | Match body chapter boundaries by real Word heading styles, normally `Heading 1` | Print the start/end paragraph text, index, and style | Do not write output; refine the boundary predicate | block |
| Formatting | Generated body looks structurally valid but visually wrong | Discard the malformed generated copy and regenerate from the current/template `.docx` using legacy rules | Check heading styles, body paragraph 12 pt / 1.5 line spacing / 24 pt first-line indent, figure captions 10.5 pt centered, and code block dark style | Treat the generated copy as invalid; do not ask the user to refresh TOC yet | replace |
| Formatting | Prose humanization or AIGC cleanup inserts many new Word paragraphs / visible extra returns | Regenerate from the last stable `.docx` with paragraph-preserving replacements; do not split one source paragraph into multiple Word paragraphs unless explicitly requested | Compare source and output paragraph counts plus body-range counts; inspect abstract and chapter-start samples | Discard the split-heavy generated copy and keep it out of the handoff path | replace |
| Validation | Body was replaced successfully | Run `.docx` integrity check, `audit`, heading/style sampling, and risk-term scan for the edited chapter | Keep command output in the handoff notes | Fix the script or regenerate a fresh copy from the source `.docx` | continue |

### Output Contract
- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## Roman Numeral Boundary

Front-matter Roman numeral normalization is a separate concern from TOC synchronization.

Current workflow guarantees:

- body page restart can be encoded automatically
- TOC rows can be refreshed and restyled automatically

Current workflow does not by itself guarantee:

- front-matter page format changes such as `I`, `II`, `III`

Treat Roman numeral formatting as a follow-up step only when the user explicitly asks for it.

## Migration Notes

For cross-device migration, tell the user three separate things may need to move:

- this skill directory
- the current thesis docx
- the template docx used for `restyle`

Then tell them to run:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/selfcheck.py
```

If fonts are missing or the machine is different, ask them to read `references/migration.md` and, if needed, set:

- `WORD_TOC_SONG_FONTS`
- `WORD_TOC_HEI_FONTS`

## Expected Reply Shape

When using this skill, structure the reply around ownership:

- current document chosen as source of truth
- what the agent already did
- the exact next manual Word step if handoff is needed
- what the agent will do after the user returns
- final visual checklist after restyle
