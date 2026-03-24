#!/usr/bin/env python3
"""Best-effort Mermaid flowchart normalizer.

Goal:
- Convert common unquoted node labels like:  A[Some (text)]  ->  A["Some (text)"]
- Convert common unquoted decision labels like: D{Yes/No}   ->  D{"Yes/No"}
- Convert common unquoted round labels like:   A(Some)      ->  A("Some")
- Replace raw double-quotes inside labels with Mermaid entity #quot;.

Notes:
- This is intentionally conservative. It avoids rewriting complex/nested shapes.
- It only targets labels that do NOT already start with a quote.

Usage:
  python scripts/normalize_flowchart.py --in input.mmd --out output.mmd
  cat input.mmd | python scripts/normalize_flowchart.py > output.mmd
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Callable


def _escape_inner_quotes(label: str) -> str:
    # Mermaid supports entity codes like #quot; in text.
    # Keep this minimal and deterministic.
    return label.replace('"', '#quot;')


def _quote(label: str) -> str:
    label = label.strip()
    label = _escape_inner_quotes(label)
    return f'"{label}"'


def _rewrite_simple_shape(
    text: str,
    open_delim: str,
    close_delim: str,
) -> str:
    """Rewrite id<open>label<close> where label is unquoted and has no newlines.

    Example:  A[foo] -> A["foo"]

    Constraints:
    - id is \b[A-Za-z_][A-Za-z0-9_]*
    - label contains no newline and does not start with a quote
    - label does not contain the closing delimiter (otherwise ambiguous)
    """

    # Don't match markdown links or other bracketed constructs by keeping it strict.
    id_pat = r"(?P<id>\b[A-Za-z_][A-Za-z0-9_]*)"
    # Disallow quotes at the start; disallow newlines; disallow the closing delimiter.
    label_pat = rf"(?P<label>(?!\")[^\n{re.escape(close_delim)}]+?)"

    pat = re.compile(rf"{id_pat}{re.escape(open_delim)}{label_pat}{re.escape(close_delim)}")

    def repl(m: re.Match[str]) -> str:
        _id = m.group("id")
        label = m.group("label")
        return f"{_id}{open_delim}{_quote(label)}{close_delim}"

    return pat.sub(repl, text)


def normalize_flowchart(text: str) -> str:
    # Only operate on flowchart-ish Mermaid.
    # If it's not a flowchart, we still do a light rewrite (safe and optional).

    out = text

    # Normalize common shapes. Order matters slightly.
    out = _rewrite_simple_shape(out, "[", "]")  # rectangle
    out = _rewrite_simple_shape(out, "{", "}")  # decision
    out = _rewrite_simple_shape(out, "(", ")")  # rounded

    # Handle double-circle-ish nodes like: A((foo)) -> A(("foo"))
    # Conservative: only when it looks exactly like ((label)) with no internal parens.
    pat_dc = re.compile(r"(?P<id>\b[A-Za-z_][A-Za-z0-9_]*)\(\((?P<label>(?!\")[^\n\)]+?)\)\)")

    def repl_dc(m: re.Match[str]) -> str:
        _id = m.group("id")
        label = m.group("label")
        return f"{_id}(({_quote(label)}))"

    out = pat_dc.sub(repl_dc, out)

    # Subgraph titles: if 'subgraph <title>' and title isn't quoted, quote it.
    # Keep it conservative: only when title has spaces or punctuation.
    def maybe_quote_subgraph(line: str) -> str:
        m = re.match(r"^(\s*subgraph)\s+(?P<title>.+?)\s*$", line)
        if not m:
            return line
        head = m.group(1)
        title = m.group("title")
        if title.startswith('"') and title.endswith('"'):
            return line
        if re.fullmatch(r"[A-Za-z0-9_]+", title):
            return line
        title = _escape_inner_quotes(title)
        return f"{head} \"{title}\""

    out_lines = [maybe_quote_subgraph(ln) for ln in out.splitlines()]
    out = "\n".join(out_lines)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Normalize Mermaid flowchart labels to reduce syntax errors")
    ap.add_argument("--in", dest="infile", help="Input file (optional). If omitted, read stdin.")
    ap.add_argument("--out", dest="outfile", help="Output file (optional). If omitted, write stdout.")
    args = ap.parse_args()

    if args.infile:
        data = open(args.infile, "r", encoding="utf-8").read()
    else:
        data = sys.stdin.read()

    fixed = normalize_flowchart(data)

    if args.outfile:
        with open(args.outfile, "w", encoding="utf-8") as f:
            f.write(fixed)
    else:
        sys.stdout.write(fixed)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
