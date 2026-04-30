# Portable Workflow

This reference describes the portable, cross-device workflow for keeping a thesis Word TOC synchronized without pretending the whole process can run headlessly.

## Roles

- agent:
  - `audit`
  - `prepare`
  - `restyle`
  - environment self-check
  - status reporting
- user:
  - edit the thesis body in Word
  - open the prepared doc in Windows Word
  - choose `更新整个目录`
  - save
  - make final visual judgment

## Inputs You Need On Any Device

- current thesis `.docx`
- template `.docx` used as style truth for `restyle`
- this skill directory under `~/.agents/skills/word-toc-human-in-the-loop`

The template doc and the current doc are different concepts.

- current doc: content truth
- template doc: style truth

## Agent Step 1: Audit

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/word_toc_workflow.py audit CURRENT.docx
```

Interpretation:

- `is_dirty: True`: headings and TOC disagree
- `page_numbering_ok: False`: body page restart is not yet encoded

## Agent Step 2: Prepare

Use this when the current document still contains a manual TOC and has not yet been converted into a Word-updatable TOC state.

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/word_toc_workflow.py prepare CURRENT.docx --output CURRENT.prepared.docx
```

What this writes:

- a real Word TOC field
- body page restart from `1`
- outline level for `摘要` / `Abstract`

## User Step 3: Word Refresh

Open `CURRENT.prepared.docx` in Windows Word, then:

1. click inside the TOC
2. choose `更新整个目录`
3. save

This is the only trusted way to get final TOC page numbers.

## Agent Step 4: Restyle

After the user returns the Word-updated docx:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/word_toc_workflow.py restyle WORD_UPDATED.docx --template TEMPLATE.docx --output WORD_UPDATED.restyled.docx
```

Then verify:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/word_toc_workflow.py audit WORD_UPDATED.restyled.docx
```

Expected success state:

- `is_dirty: False`
- `page_numbering_ok: True`

## What The Workflow Does Not Promise

- pixel-identical reproduction of a manually micro-tuned TOC on every long line
- headless replacement for Windows Word pagination
- automatic Roman numeral formatting for front matter unless explicitly handled as a separate follow-up task

## Portable Self-Check

Quick environment check:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/selfcheck.py
```

Manual-baseline smoke check:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/selfcheck.py --manual-docx CURRENT.docx
```

Word-updated smoke check:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/selfcheck.py --word-updated-docx WORD_UPDATED.docx --template TEMPLATE.docx
```
