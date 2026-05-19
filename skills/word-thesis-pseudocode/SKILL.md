---
name: word-thesis-pseudocode
description: Use when a thesis or paper is authored in Word and the agent needs to insert editable, thesis-style pseudocode blocks into an existing .docx without converting them to images
---

# Word Thesis Pseudocode

## Overview

Insert thesis-style pseudocode into an existing `.docx` while keeping the result editable in Word.

The full workflow supports:

- multiple algorithm insertions in one run
- automatic chapter detection and per-chapter numbering
- algorithm references like `{{ALGREF:demo}}`
- an algorithm list placeholder `{{LOA}}`
- a small LaTeX-like DSL for common pseudocode commands
- a generated Word acceptance pack for visual review

## When To Use

Use this skill when:

- the source of truth is a Word `.docx`
- the user wants pseudocode that looks like thesis algorithms, not VS Code code blocks
- the pseudocode must remain editable and paginable in Word
- the user mentions placeholders such as `{{ALG:demo}}`

Do not use this skill when:

- the user wants LaTeX compilation or screenshot-based insertion
- the pseudocode should live as a normal code listing with syntax highlight

## Workflow

1. Put standalone algorithm placeholders in the document, for example `{{ALG:demo}}`.
2. Optionally put `{{LOA}}` where the algorithm list should appear.
3. Optionally put inline references such as `见{{ALGREF:demo}}。`.
4. Write one or more algorithm DSL files with headers starting with `@algorithm`.
5. Run:

```bash
python3 ~/.agents/skills/word-thesis-pseudocode/scripts/insert_pseudocode.py \
  --input thesis.docx \
  --alg demo.alg \
  --alg another.alg \
  --output thesis.with-alg.docx
```

6. Open the output in Word and do the visual acceptance check in `references/style-acceptance.md`.

## Demo Pack

Generate a ready-to-review acceptance directory with:

```bash
python3 ~/.agents/skills/word-thesis-pseudocode/scripts/generate_acceptance_demo.py
```

By default this writes:

- `~/.agents/skills/word-thesis-pseudocode/acceptance/word-visual-review/00-README.md`
- `~/.agents/skills/word-thesis-pseudocode/acceptance/word-visual-review/01-input.docx`
- `~/.agents/skills/word-thesis-pseudocode/acceptance/word-visual-review/02-output.docx`
- `~/.agents/skills/word-thesis-pseudocode/acceptance/word-visual-review/03-视觉验收清单.md`

## Bundled Files

- script: `scripts/insert_pseudocode.py`
- demo pack generator: `scripts/generate_acceptance_demo.py`
- DSL reference: `references/dsl-spec.md`
- visual acceptance: `references/style-acceptance.md`

## Contract

- Each `{{ALG:id}}` placeholder is replaced by one algorithm title and one algorithm table.
- `chapter=auto` uses the nearest previous chapter-looking paragraph such as `3 系统设计` or `第 3 章`.
- `index=auto` counts algorithms within the resolved chapter.
- `{{ALGREF:id}}` becomes a textual cross-reference like `算法 3-1`.
- `{{LOA}}` becomes an algorithm list block.
- The output stays as a `.docx`, not an image-based rendering.
