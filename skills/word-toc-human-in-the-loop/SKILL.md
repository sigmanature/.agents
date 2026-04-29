---
name: word-toc-human-in-the-loop
description: Use when updating a thesis or dissertation Word TOC after body edits, especially when the document has a hand-tuned TOC template, page numbers must be recalculated by Windows Word, or the user mentions dirty TOC, Word TOC, prepared/restyled docx, thesis目录, 目录页码不对, or wants the agent to handle everything except the manual Word refresh step.
---

# Word TOC Human-In-The-Loop

## Overview

Use this workflow when the thesis source of truth is a `.docx` file and the TOC must stay synchronized with body headings and page numbers.

The agent owns structure checks, `prepare`, `restyle`, and status reporting. The user owns the one step Linux tooling should not fake: opening the doc in Windows Word, updating the entire TOC, saving, and doing final visual judgment.

## Default Paths

Prefer these defaults unless the user gives a different current document:

- workflow CLI: `/home/nzzhao/graduate_paper/graduate_paper/word_toc_workflow.py`
- style template truth: `/home/nzzhao/下载/Final_Thesis_WordTOC_Spec_FINAL_toc_matched_v6.docx`
- deeper repo workflow note: `/home/nzzhao/graduate_paper/graduate_paper/WORD_TOC_WORKFLOW.md`

Important distinction:

- `matched_v6.docx` is the style template truth.
- The user's latest edited or Word-updated `.docx` is the content truth.
- Do not assume `matched_v6.docx` is still the current working thesis after the user says they edited a newer file.

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
python3 /home/nzzhao/graduate_paper/graduate_paper/word_toc_workflow.py audit CURRENT.docx
```

Interpretation:

- `is_dirty: True` means body headings and current TOC no longer match
- `page_numbering_ok: False` means body page restart is not yet encoded
- `摘要` / `ABSTRACT` are special TOC rows and should not be treated as dirty mismatches by themselves

### 3. Prepare when the doc still uses a manual TOC

If the current doc is still a manual-TOC baseline, run:

```bash
python3 /home/nzzhao/graduate_paper/graduate_paper/word_toc_workflow.py prepare CURRENT.docx --output CURRENT.prepared.docx
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
python3 /home/nzzhao/graduate_paper/graduate_paper/word_toc_workflow.py restyle WORD_UPDATED.docx --template /home/nzzhao/下载/Final_Thesis_WordTOC_Spec_FINAL_toc_matched_v6.docx --output WORD_UPDATED.restyled.docx
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

## Roman Numeral Boundary

Front-matter Roman numeral normalization is a separate concern from TOC synchronization.

Current workflow guarantees:

- body page restart can be encoded automatically
- TOC rows can be refreshed and restyled automatically

Current workflow does not by itself guarantee:

- front-matter page format changes such as `I`, `II`, `III`

Treat Roman numeral formatting as a follow-up step only when the user explicitly asks for it.

## Expected Reply Shape

When using this skill, structure the reply around ownership:

- current document chosen as source of truth
- what the agent already did
- the exact next manual Word step if handoff is needed
- what the agent will do after the user returns
- final visual checklist after restyle
