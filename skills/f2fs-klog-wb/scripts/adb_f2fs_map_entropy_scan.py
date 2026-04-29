#!/usr/bin/env python3
"""
Scan a preserved Android /data artifact for F2FS logical->physical block
mapping deltas and per-page entropy.

The script is intentionally conservative for crash forensics:
- use the preserved hardlink inode on device for klog reads;
- parse effective physical block as m_pblk + (index - m_lblk);
- keep raw per-chunk dmesg files so missing rows can be audited;
- classify high entropy pages by ELF section when the host blob is ELF.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Iterable


MAP_RESULT_RE = re.compile(
    r"stage=map_result .*?index=(\d+).*?"
    r"m_pblk=(\d+) m_lblk=(\d+) m_len=(\d+).*?"
    r"map_state=([^\s]+)"
)


def run(cmd: list[str], *, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def adb_su(serial: str, script: str) -> subprocess.CompletedProcess:
    return run(["adb", "-s", serial, "shell", "su", "-c", f"sh -c {shlex.quote(script)}"])


def page_entropy(data: bytes) -> tuple[float, collections.Counter[int]]:
    if len(data) < 4096:
        data = data.ljust(4096, b"\0")
    counts: collections.Counter[int] = collections.Counter(data)
    total = len(data)
    ent = -sum((n / total) * math.log2(n / total) for n in counts.values())
    return ent, counts


def readelf_sections(path: Path) -> list[dict[str, object]]:
    cp = run(["readelf", "-SW", str(path)])
    if cp.returncode != 0:
        return []
    sections: list[dict[str, object]] = []
    # Example:
    # [ 6] .text PROGBITS 000... 02ea8000 0a550098 00 AX 0 0 16384
    rx = re.compile(
        r"^\s*\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+"
        r"([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"
        r"[0-9a-fA-F]+\s+(\S*)\s+"
    )
    for line in cp.stdout.splitlines():
        m = rx.match(line)
        if not m:
            continue
        nr, name, typ, addr, off, size, flags = m.groups()
        off_i = int(off, 16)
        size_i = int(size, 16)
        sections.append(
            {
                "nr": int(nr),
                "name": name,
                "type": typ,
                "addr": int(addr, 16),
                "offset": off_i,
                "size": size_i,
                "end": off_i + size_i,
                "flags": flags,
            }
        )
    return sections


def sections_for_page(sections: list[dict[str, object]], page_idx: int) -> str:
    start = page_idx * 4096
    end = start + 4096
    names: list[str] = []
    for sec in sections:
        if sec["type"] == "NOBITS" or int(sec["size"]) <= 0:
            continue
        if start < int(sec["end"]) and end > int(sec["offset"]):
            names.append(str(sec["name"]))
    return ",".join(names) if names else "."


def entropy_rows(blob: Path, sections: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    size = blob.stat().st_size
    pages = (size + 4095) // 4096
    with blob.open("rb") as f:
        for idx in range(pages):
            data = f.read(4096)
            if len(data) < 4096:
                data = data.ljust(4096, b"\0")
            ent, counts = page_entropy(data)
            rows.append(
                {
                    "idx": idx,
                    "entropy": f"{ent:.6f}",
                    "unique": len(counts),
                    "max_byte_pct": f"{max(counts.values()) / 4096 * 100:.4f}",
                    "zero_pct": f"{counts.get(0, 0) / 4096 * 100:.4f}",
                    "ff_pct": f"{counts.get(255, 0) / 4096 * 100:.4f}",
                    "sha256_12": hashlib.sha256(data).hexdigest()[:12],
                    "first16": data[:16].hex(),
                    "sections": sections_for_page(sections, idx),
                }
            )
    return rows


def write_tsv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, delimiter="\t", fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def collect_mapping(args: argparse.Namespace, pages: int, out: Path) -> list[dict[str, object]]:
    chunk_dir = out / "dmesg_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    end_page = args.end_page if args.end_page is not None else pages - 1
    start_page = args.start_page
    rows: list[dict[str, object]] = []
    prev_eff: int | None = None
    for lo in range(start_page, end_page + 1, args.chunk_pages):
        hi = min(end_page, lo + args.chunk_pages - 1)
        count = hi - lo + 1
        device_script = f"""
set -e
SYS=/sys/fs/f2fs/{args.f2fs_dev}
echo 0 > $SYS/klog_wb_enable
echo {args.inode} > $SYS/klog_wb_ino
echo "" > $SYS/klog_wb_suffix
echo {lo} > $SYS/klog_wb_idx_lo
echo {hi} > $SYS/klog_wb_idx_hi
echo 1 > $SYS/klog_wb_sample
echo 2 > $SYS/klog_wb_detail
dmesg -C || true
echo 1 > $SYS/klog_wb_enable
echo 3 > /proc/sys/vm/drop_caches
dd if={shlex.quote(args.device_path)} of=/dev/null bs=4096 skip={lo} count={count} 2>/dev/null
sleep {args.settle_sec}
dmesg
echo 0 > $SYS/klog_wb_enable
echo 0 > $SYS/klog_wb_ino
echo 0 > $SYS/klog_wb_idx_lo
echo 0 > $SYS/klog_wb_idx_hi
echo 0 > $SYS/klog_wb_sample
echo 0 > $SYS/klog_wb_detail
"""
        cp = adb_su(args.serial, device_script)
        chunk_path = chunk_dir / f"dmesg_{lo:06d}_{hi:06d}.txt"
        chunk_path.write_text(cp.stdout)
        if cp.stderr:
            (chunk_dir / f"dmesg_{lo:06d}_{hi:06d}.stderr.txt").write_text(cp.stderr)

        recs: dict[int, tuple[int, int, int, int, str]] = {}
        for line in cp.stdout.splitlines():
            m = MAP_RESULT_RE.search(line)
            if not m:
                continue
            idx, m_pblk, m_lblk, m_len, state = (
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
                m.group(5),
            )
            if lo <= idx <= hi and idx not in recs:
                recs[idx] = (m_pblk + (idx - m_lblk), m_pblk, m_lblk, m_len, state)

        for idx in range(lo, hi + 1):
            if idx not in recs:
                rows.append(
                    {
                        "idx": idx,
                        "effective_pblk": "",
                        "delta": "",
                        "state": "MISSING",
                        "m_pblk": "",
                        "m_lblk": "",
                        "m_len": "",
                    }
                )
                continue
            eff, m_pblk, m_lblk, m_len, state = recs[idx]
            delta = "" if prev_eff is None else eff - prev_eff
            rows.append(
                {
                    "idx": idx,
                    "effective_pblk": eff,
                    "delta": delta,
                    "state": state,
                    "m_pblk": m_pblk,
                    "m_lblk": m_lblk,
                    "m_len": m_len,
                }
            )
            prev_eff = eff
        print(
            f"chunk {lo}-{hi}: parsed={len(recs)}/{count} "
            f"missing={count - len(recs)}",
            flush=True,
        )
    return rows


def summarize(out: Path, entropy: list[dict[str, object]], mapping: list[dict[str, object]]) -> None:
    by_idx = {int(r["idx"]): r for r in entropy}
    plus2 = [r for r in mapping if r["delta"] == 2]
    non1 = [
        r
        for r in mapping
        if r["state"] != "MISSING" and r["delta"] not in ("", 1)
    ]
    missing = [r for r in mapping if r["state"] == "MISSING"]
    high = sorted(entropy, key=lambda r: float(r["entropy"]), reverse=True)
    suspicious_high = [
        r
        for r in high
        if float(r["entropy"]) >= 7.5
        and (
            ".text" in str(r["sections"]).split(",")
            or ".rodata" in str(r["sections"]).split(",")
        )
        and ".gnu_debugdata" not in str(r["sections"]).split(",")
    ]
    with (out / "summary.txt").open("w") as f:
        f.write(f"plus2_count={len(plus2)}\n")
        f.write("plus2_pages=" + ",".join(str(r["idx"]) for r in plus2[:200]) + "\n")
        f.write(f"non_plus1_count={len(non1)}\n")
        f.write(
            "non_plus1_first="
            + ",".join(f"{r['idx']}:{r['delta']}:{r['state']}" for r in non1[:80])
            + "\n"
        )
        f.write(f"missing_count={len(missing)}\n")
        f.write("missing_first=" + ",".join(str(r["idx"]) for r in missing[:80]) + "\n")
        f.write(f"high_entropy_ge_7_5_count={sum(float(r['entropy']) >= 7.5 for r in entropy)}\n")
        f.write(f"suspicious_text_rodata_high_entropy_count={len(suspicious_high)}\n")
        f.write("\nTop entropy pages:\n")
        for r in high[:80]:
            m = next((x for x in mapping if x["idx"] == r["idx"]), None)
            delta = "." if not m else m["delta"]
            eff = "." if not m else m["effective_pblk"]
            f.write(
                f"{r['idx']}\tent={r['entropy']}\tsections={r['sections']}\t"
                f"eff={eff}\tdelta={delta}\tsha={r['sha256_12']}\tfirst16={r['first16']}\n"
            )
        f.write("\nSuspicious .text/.rodata high entropy pages:\n")
        for r in suspicious_high[:120]:
            m = next((x for x in mapping if x["idx"] == r["idx"]), None)
            delta = "." if not m else m["delta"]
            eff = "." if not m else m["effective_pblk"]
            f.write(
                f"{r['idx']}\tent={r['entropy']}\tsections={r['sections']}\t"
                f"eff={eff}\tdelta={delta}\tsha={r['sha256_12']}\tfirst16={r['first16']}\n"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--f2fs-dev", required=True)
    ap.add_argument("--inode", type=int, required=True)
    ap.add_argument("--device-path", required=True)
    ap.add_argument("--host-blob", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start-page", type=int, default=0)
    ap.add_argument("--end-page", type=int)
    ap.add_argument("--chunk-pages", type=int, default=128)
    ap.add_argument("--settle-sec", type=float, default=0.15)
    ap.add_argument("--entropy-only", action="store_true")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    size = args.host_blob.stat().st_size
    pages = (size + 4095) // 4096
    sections = readelf_sections(args.host_blob)
    if sections:
        with (args.out / "elf_sections.tsv").open("w") as f:
            f.write("nr\tname\ttype\toffset\tend\tsize\tflags\n")
            for s in sections:
                f.write(
                    f"{s['nr']}\t{s['name']}\t{s['type']}\t{s['offset']}\t"
                    f"{s['end']}\t{s['size']}\t{s['flags']}\n"
                )

    erows = entropy_rows(args.host_blob, sections)
    write_tsv(
        args.out / "entropy_pages.tsv",
        erows,
        [
            "idx",
            "entropy",
            "unique",
            "max_byte_pct",
            "zero_pct",
            "ff_pct",
            "sha256_12",
            "first16",
            "sections",
        ],
    )

    if args.entropy_only:
        mapping: list[dict[str, object]] = []
    else:
        mapping = collect_mapping(args, pages, args.out)
        write_tsv(
            args.out / "map_effective.tsv",
            mapping,
            ["idx", "effective_pblk", "delta", "state", "m_pblk", "m_lblk", "m_len"],
        )
    summarize(args.out, erows, mapping)
    print(args.out / "summary.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
