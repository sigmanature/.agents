# Resource Map

This file explains how the old thesis repo resources map into the portable skill bundle.

## Executable Surface

- `scripts/word_toc_workflow.py`

This is the real executable for:

- `audit`
- `prepare`
- `restyle`
- `doctor`

This script is the only required executable for the TOC workflow.

## Migration Validation

- `scripts/selfcheck.py`

Use this on a new device before trusting the workflow. It can do:

- quick environment and font inspection
- live smoke-check against a user-provided current docx and template docx

## Reference Carry-Over

- `references/workflow.md`
  - portable workflow description
- `references/visual-checklist.md`
  - final Word-side acceptance criteria
- `references/legacy/thesis_wordtoc_wrapper.py`
  - historical Word-heading and TOC-field wrapper logic
- `references/legacy/thesis.py`
  - large formatting/content-construction reference library from the older repo-driven thesis generation workflow

## How To Think About Legacy Files

`thesis.py` is not part of the required TOC execution path anymore.

Keep it as reference when:

- you want to recover body formatting logic
- you want to inspect image/table/code formatting conventions
- you want a model to read the older thesis-generation abstractions for context

`thesis_wordtoc_wrapper.py` is also not the primary executable now.

Keep it as reference when:

- you want to understand the earlier Word-heading and TOC insertion approach
- you need historical context for the current `prepare` logic
