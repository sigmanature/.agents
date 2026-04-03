#!/usr/bin/env python3
import argparse
import csv
import json
import re
import sys
from pathlib import Path


KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_.:-]*)=([^\s]+)')
TS_RE = re.compile(r'^\<[0-9]+\>\[\s*([0-9]+\.[0-9]+)\]')


def parse_args():
    p = argparse.ArgumentParser(
        description="Parse table-friendly kernel log lines with k=v fields and filter them like a table."
    )
    p.add_argument("logfile")
    p.add_argument("--tag", default="", help="Only keep lines containing this tag")
    p.add_argument("--eq", action="append", default=[], help="Exact-match field filter k=v")
    p.add_argument(
        "--contains-field",
        action="append",
        default=[],
        help="Substring field filter k=s",
    )
    p.add_argument(
        "--show",
        default="line,ts,tag,phase,fn,pid,comm,msg",
        help="Comma-separated output fields",
    )
    p.add_argument(
        "--format",
        choices=("tsv", "csv", "jsonl"),
        default="tsv",
        help="Output format",
    )
    return p.parse_args()


def split_kv(expr: str):
    if "=" not in expr:
        raise ValueError(f"invalid k=v filter: {expr}")
    return expr.split("=", 1)


def extract_tag_phase(raw: str):
    tokens = raw.split()
    tag = ""
    phase = ""
    for idx, tok in enumerate(tokens):
        if tok.startswith("KLOG"):
            tag = tok
            if idx + 2 < len(tokens):
                phase = tokens[idx + 2]
            break
    return tag, phase


def iter_rows(path: Path, tag_filter: str):
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.rstrip("\n")
            if tag_filter and tag_filter not in raw:
                continue
            row = {"line": str(lineno), "raw": raw}
            ts_m = TS_RE.match(raw)
            row["ts"] = ts_m.group(1) if ts_m else ""
            tag, phase = extract_tag_phase(raw)
            row["tag"] = tag
            row["phase"] = phase
            for key, value in KV_RE.findall(raw):
                row[key] = value
            row["fn"] = row.get("fn", "")
            row["msg"] = raw
            yield row


def keep_row(row, eq_filters, contains_filters):
    for key, expected in eq_filters:
        if row.get(key, "") != expected:
            return False
    for key, needle in contains_filters:
        if needle not in row.get(key, ""):
            return False
    return True


def emit(rows, fields, fmt):
    if fmt == "jsonl":
        for row in rows:
            print(json.dumps({field: row.get(field, "") for field in fields}, ensure_ascii=False))
        return

    if fmt == "csv":
        writer = csv.writer(sys.stdout)
    else:
        writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")

    writer.writerow(fields)
    for row in rows:
        writer.writerow([row.get(field, "") for field in fields])


def main():
    args = parse_args()
    path = Path(args.logfile)
    if not path.is_file():
        raise SystemExit(f"ERROR: log file not found: {path}")

    eq_filters = [split_kv(item) for item in args.eq]
    contains_filters = [split_kv(item) for item in args.contains_field]
    fields = [item.strip() for item in args.show.split(",") if item.strip()]

    rows = [
        row
        for row in iter_rows(path, args.tag)
        if keep_row(row, eq_filters, contains_filters)
    ]
    emit(rows, fields, args.format)


if __name__ == "__main__":
    main()
