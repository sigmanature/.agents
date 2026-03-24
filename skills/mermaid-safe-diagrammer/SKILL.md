---
name: mermaid-safe-diagrammer
description: "generate and repair mermaid diagrams (especially flowcharts) so they render without syntax errors. use when the user asks to turn natural language or markdown into mermaid, debug or fix mermaid code that fails to render, or avoid mermaid syntax pitfalls by enforcing quoted labels and escaping special characters."
---

# Mermaid Safe Diagrammer

Produce Mermaid that **renders on first try**.

This skill is opinionated: it trades a little verbosity for reliability by enforcing **quoted labels everywhere** and using Mermaid’s **entity codes** for problematic characters.

## Default rules (do not negotiate)

1. **Always quote any label that appears inside a node shape**, even if it’s plain ASCII.
   - ✅ `A["..."]`, `A("...")`, `A{"..."}`, `A(("..."))`
   - ❌ `A[ ... ]` with unquoted text
2. **If a label contains characters that can confuse the parser** (e.g., parentheses, quotes, brackets), prefer quoted labels; if still risky, use entity codes.
3. **Never use raw double quotes inside a quoted label.** Replace `"` with `#quot;`.
4. Prefer **safe IDs**: `n1`, `n2`, `svc_api`, `db_main` (letters/digits/underscore only). Never use spaces, punctuation, or non-ascii in IDs.
5. Output Mermaid in a **single fenced block**:

```mermaid
...code...
```

## Workflow decision tree

### If the user provides Mermaid code and it fails to render
1. Identify diagram type (`flowchart`/`sequenceDiagram`/etc.).
2. Apply the **lint checklist** (below).
3. For flowcharts: normalize node labels to quoted form.
   - Optional: run `scripts/normalize_flowchart.py` to do a best-effort auto-fix.
4. Return the corrected Mermaid only.

### If the user provides natural language / markdown and wants a diagram
1. Choose diagram type.
2. Build a short plan (nodes + edges).
3. Assign safe IDs.
4. Render Mermaid following the rules.
5. Self-check using the lint checklist.

## Diagram selection heuristic

- Process / decision / workflow → **flowchart** (default)
- Message passing / API calls / timeline of interactions → **sequenceDiagram**
- State machine / lifecycle → **stateDiagram-v2**
- Data model / tables & relations → **erDiagram**
- Schedule / milestones → **gantt**

If unsure: choose `flowchart`.

## Flowchart output spec

- Start with `flowchart TB` (top-to-bottom). Use `LR` only if user explicitly wants left-to-right.
- Node shapes:
  - Process/step: `n1["..."]`
  - Decision: `d1{"..."}`
  - Start/End: `s(("..."))`
- Edge labels (when needed): `n1 -- "..." --> n2`
- Subgraph titles: quote if not a single bare word: `subgraph "..."`

## Label escaping rules

Use Mermaid entity codes for characters that are hard to keep safe.

- `"` → `#quot;`
- `#` → `#35;`
- `;` (esp. in sequence messages) → `#59;`

If you need more, use decimal code points: `[` → `#91;`, `]` → `#93;`, `{` → `#123;`, `}` → `#125;`.

See `references/mermaid-safe-rules.md` for examples.

## Lint checklist (run mentally before final output)

- [ ] Diagram header exists (`flowchart TB`, `sequenceDiagram`, etc.)
- [ ] **No unquoted text inside node shape delimiters** (`[](){}(())`)
- [ ] Any `"` inside labels converted to `#quot;`
- [ ] IDs contain only `[A-Za-z0-9_]` and are unique
- [ ] No accidental reserved words as IDs (especially `end` in flowcharts)
- [ ] No unbalanced brackets/parentheses/braces

## Bundled resources

- `scripts/normalize_flowchart.py`: best-effort fixer for common flowchart label issues
- `references/mermaid-safe-rules.md`: quick rules + gotchas + examples
- `references/full-prompt-zh.md`: a copy/paste “system prompt” for generating safe Mermaid from natural language
