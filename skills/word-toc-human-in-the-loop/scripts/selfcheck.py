#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

from lxml import etree


SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW = SCRIPT_DIR / "word_toc_workflow.py"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def quick_check() -> int:
    result = _run([sys.executable, str(WORKFLOW), "doctor"])
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def _verify_prepared_docx(prepared: Path) -> int:
    with ZipFile(prepared) as package:
        root = etree.fromstring(package.read("word/document.xml"))
    fld_simple = int(root.xpath("count(//w:fldSimple)", namespaces=NS))
    starts = root.xpath("//w:sectPr/w:pgNumType/@w:start", namespaces=NS)
    print(f"prepared_fldSimple: {fld_simple}")
    print(f"prepared_pgNumType_starts: {starts}")
    if fld_simple < 1:
        print("selfcheck_error: prepared doc is missing Word TOC field", file=sys.stderr)
        return 1
    if "1" not in starts:
        print("selfcheck_error: prepared doc is missing body page-number restart", file=sys.stderr)
        return 1
    return 0


def live_check_manual(docx: Path) -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        prepared = tmpdir_path / "prepared.docx"

        for cmd in (
            [sys.executable, str(WORKFLOW), "audit", str(docx)],
            [sys.executable, str(WORKFLOW), "prepare", str(docx), "--output", str(prepared)],
        ):
            result = _run(cmd)
            print(f"$ {' '.join(cmd)}")
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            if result.returncode != 0:
                return result.returncode

        verify = _verify_prepared_docx(prepared)
        if verify != 0:
            return verify

        print(f"selfcheck_output: {prepared}")
    return 0


def live_check_word_updated(docx: Path, template: Path) -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        restyled = tmpdir_path / "restyled.docx"

        for cmd in (
            [sys.executable, str(WORKFLOW), "audit", str(docx)],
            [sys.executable, str(WORKFLOW), "restyle", str(docx), "--template", str(template), "--output", str(restyled)],
            [sys.executable, str(WORKFLOW), "audit", str(restyled)],
        ):
            result = _run(cmd)
            print(f"$ {' '.join(cmd)}")
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            if result.returncode != 0:
                return result.returncode

        print(f"selfcheck_output: {restyled}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual-docx", type=Path)
    parser.add_argument("--word-updated-docx", type=Path)
    parser.add_argument("--template", type=Path)
    args = parser.parse_args(argv)

    if args.manual_docx is None and args.word_updated_docx is None and args.template is None:
        return quick_check()

    if args.manual_docx is not None and args.word_updated_docx is not None:
        parser.error("--manual-docx and --word-updated-docx are mutually exclusive")

    if args.manual_docx is not None:
        return live_check_manual(args.manual_docx)

    if args.word_updated_docx is not None:
        if args.template is None:
            parser.error("--template is required with --word-updated-docx")
        return live_check_word_updated(args.word_updated_docx, args.template)

    parser.error("pass either --manual-docx or --word-updated-docx, or no args for quick check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
