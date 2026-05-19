# DSL Spec

## Goal

This DSL is intentionally smaller than LaTeX. It is designed for stable Word insertion, not full `algorithm2e` compatibility.

## Header

The first non-empty line must be:

```text
@algorithm id=demo chapter=3 index=2 title=Large Folio Dispatch
```

Required keys:

- `id`
- `title`

Optional keys:

- `chapter`
- `index`

If `chapter` or `index` are omitted, the script treats them as `auto`.

## Body

Each non-empty line becomes one algorithm row.

Examples:

```text
Input: folio, state
Output: dispatch target
for each candidate in dispatch_table:
    if match(candidate, folio):
        return candidate.handler
return default_handler
```

Rules:

- four leading spaces = one indent level
- blank lines are ignored in the minimal version
- body lines are emitted as plain text rows

## LaTeX-Like Aliases

The script accepts a compact set of aliases:

```text
\Input inode, folio
\Output dispatch result
\For each candidate in dispatch_table
    \If match(candidate, folio)
        \Return candidate.handler
    \Else
        scan next candidate
    \EndIf
\EndFor
\Return default_handler
```

Supported aliases:

- `\Input`, `\KwIn`
- `\Output`, `\KwOut`
- `\For`
- `\While`
- `\If`
- `\Else`
- `\Return`
- `\State`
- `\Comment`
- `\EndIf`, `\EndFor`, `\EndWhile` as structural terminators

These aliases are converted into plain thesis-style pseudocode rows before insertion.

## Document Placeholders

- `{{ALG:id}}`: insert one algorithm block
- `{{ALGREF:id}}`: insert one textual reference such as `算法 4-2`
- `{{LOA}}`: insert the algorithm list

## Current Limits

- one algorithm per `.alg` file
- `{{ALG:id}}` should occupy its own paragraph
- chapter inference depends on nearby chapter-like text
- references are rendered as text labels, not live Word REF fields
- no full LaTeX parsing
