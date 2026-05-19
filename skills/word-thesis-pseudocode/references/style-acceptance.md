# Visual Style Acceptance

Use this checklist after opening the generated `.docx` in Word.

## Expected Look

- If `{{LOA}}` was present, there is a centered `算法目录` block before the algorithms.
- There is a title line like `算法 3-2 Large Folio Dispatch`.
- The title sits where the placeholder paragraph used to be.
- A restrained two-column black-and-white algorithm frame appears below the title.
- The left column contains line numbers starting from `1`.
- The right column contains pseudocode text.
- Indented lines visibly shift right relative to their parent line.
- The algorithm uses white background, not dark code-block styling.
- The algorithm directory looks like a clean list, not a boxed data table.
- The body algorithm block no longer looks like a full spreadsheet grid.
- The algorithm block remains selectable and editable as normal Word text.
- If `{{ALGREF:id}}` was present, the body shows a readable label such as `算法 3-1`.

## Border Expectation

- The body algorithm block has a visible top border.
- The body algorithm block has a visible bottom border.
- The left line-number column is separated from the code column by one thin vertical rule.
- The body algorithm block does not show full row-by-row spreadsheet borders.
- The algorithm directory does not show an outer box.

## Typography Expectation

- Title looks like a thesis caption, not a chapter heading.
- Code rows look monospaced or near-monospaced.
- The body is readable in black on white.
- Line spacing is compact and consistent across rows.

## Rejection Cases

Reject the result if any of these happen:

- the placeholder text is still visible
- the algorithm becomes an image or screenshot
- the algorithm list placeholder is still visible
- the algorithm reference placeholder is still visible
- indentation collapses into a flat left edge
- line numbers are missing
- the algorithm block still looks like a heavy boxed spreadsheet
- the table runs with dark background or colored syntax highlight
- Word cannot edit individual lines as text
