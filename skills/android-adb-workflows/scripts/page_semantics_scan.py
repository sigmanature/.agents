#!/usr/bin/env python3
"""Page-level section mapping and byte-pattern triage for ART artifacts."""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


READELF_LINE_RE = re.compile(
    r"^\s*\[\s*\d+\]\s+"
    r"(?P<name>\S*)\s+"
    r"(?P<type>\S+)\s+"
    r"(?P<addr>[0-9A-Fa-f]+)\s+"
    r"(?P<off>[0-9A-Fa-f]+)\s+"
    r"(?P<size>[0-9A-Fa-f]+)\s+"
    r"(?P<es>[0-9A-Fa-f]+)\s+"
    r"(?P<flags>\S*)\s+"
    r"(?P<link>\d+)\s+"
    r"(?P<info>\d+)\s+"
    r"(?P<align>\d+)"
)
OBJDUMP_LINE_RE = re.compile(
    r"^\s*[0-9a-f]+:\s+"
    r"(?P<bytes>(?:[0-9a-f]{8}|(?:[0-9a-f]{2}\s+){4,}))\s+"
    r"(?P<asm>.+?)\s*$"
)


@dataclasses.dataclass(frozen=True)
class Section:
    name: str
    type_name: str
    addr: int
    offset: int
    size: int
    flags: str

    @property
    def end_offset(self) -> int:
        return self.offset + self.size

    @property
    def is_executable(self) -> bool:
        return "X" in self.flags


@dataclasses.dataclass(frozen=True)
class SectionMatch:
    name: str
    flags: str
    is_executable: bool
    section_offset: int
    file_offset: int


@dataclasses.dataclass(frozen=True)
class PageCluster:
    start_offset: int
    end_offset: int
    page_count: int
    max_gap_pages: int


def parse_readelf_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    for line in text.splitlines():
        m = READELF_LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name") or "<anon>"
        sections.append(
            Section(
                name=name,
                type_name=m.group("type"),
                addr=int(m.group("addr"), 16),
                offset=int(m.group("off"), 16),
                size=int(m.group("size"), 16),
                flags=m.group("flags"),
            )
        )
    sections.sort(key=lambda s: (s.offset, s.size, s.name))
    return [s for s in sections if s.size > 0 and s.type_name != "NOBITS"]


def map_offset_to_section(offset: int, sections: Iterable[Section]) -> SectionMatch | None:
    for section in sections:
        if section.offset <= offset < section.end_offset:
            return SectionMatch(
                name=section.name,
                flags=section.flags,
                is_executable=section.is_executable,
                section_offset=offset - section.offset,
                file_offset=offset,
            )
    return None


def cluster_page_offsets(
    offsets: Iterable[int],
    *,
    page_size: int = 4096,
    max_gap_pages: int = 0,
) -> list[PageCluster]:
    sorted_offsets = sorted(set(offsets))
    if not sorted_offsets:
        return []
    clusters: list[list[int]] = [[sorted_offsets[0]]]
    max_distance = page_size * (max_gap_pages + 1)
    for off in sorted_offsets[1:]:
        if off - clusters[-1][-1] <= max_distance:
            clusters[-1].append(off)
        else:
            clusters.append([off])
    return [
        PageCluster(
            start_offset=cluster[0],
            end_offset=cluster[-1],
            page_count=len(cluster),
            max_gap_pages=max_gap_pages,
        )
        for cluster in clusters
    ]


def run_checked(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, check=True)
    return proc.stdout


def detect_file_kind(path: Path) -> str:
    proc = subprocess.run(["file", str(path)], text=True, capture_output=True, check=True)
    line = proc.stdout
    if "ELF 64-bit" in line and "ARM aarch64" in line:
        return "elf-aarch64"
    if "Dalvik dex file" in line:
        return "dex"
    return "other"


def shannon_entropy(blob: bytes) -> float:
    if not blob:
        return 0.0
    counts: dict[int, int] = {}
    for b in blob:
        counts[b] = counts.get(b, 0) + 1
    total = len(blob)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return entropy


def longest_zero_run(blob: bytes) -> int:
    best = cur = 0
    for b in blob:
        if b == 0:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


def page_a64_stats(blob: bytes) -> dict[str, int | float | str | None]:
    objdump = shutil.which("aarch64-linux-gnu-objdump") or shutil.which("llvm-objdump") or shutil.which("objdump")
    if objdump is None:
        return {"objdump": None, "returncode": None}
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(blob)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [objdump, "-D", "-b", "binary", "-m", "aarch64", str(tmp_path)],
            text=True,
            capture_output=True,
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    decoded = 0
    inst_directives = 0
    undefined_like = 0
    common = 0
    for line in proc.stdout.splitlines():
        m = OBJDUMP_LINE_RE.match(line)
        if not m:
            continue
        decoded += 1
        asm = m.group("asm")
        if asm.startswith(".inst"):
            inst_directives += 1
        if any(tok in asm for tok in ("undefined", "unallocated", "udf")):
            undefined_like += 1
        head = asm.split()[0]
        if head in {
            "adrp",
            "add",
            "sub",
            "mov",
            "ldr",
            "ldp",
            "str",
            "stp",
            "cmp",
            "b",
            "bl",
            "ret",
            "cbz",
            "cbnz",
            "tbz",
            "tbnz",
            "nop",
        }:
            common += 1
    return {
        "objdump": Path(objdump).name,
        "returncode": proc.returncode,
        "decoded_lines": decoded,
        "inst_directive_lines": inst_directives,
        "undefined_like_lines": undefined_like,
        "common_mnemonic_lines": common,
    }


def diff_pages(left: bytes, right: bytes, page_size: int) -> list[dict[str, int]]:
    limit = min(len(left), len(right))
    rows: list[dict[str, int]] = []
    for off in range(0, limit, page_size):
        a = left[off : off + page_size]
        b = right[off : off + page_size]
        diff = sum(1 for x, y in zip(a, b) if x != y)
        if diff:
            rows.append({"offset": off, "diff_bytes": diff})
    if len(left) != len(right):
        big = max(len(left), len(right))
        start = limit - (limit % page_size)
        for off in range(start, big, page_size):
            if all(row["offset"] != off for row in rows):
                chunk_diff = abs(len(left[off : off + page_size]) - len(right[off : off + page_size]))
                if chunk_diff:
                    rows.append({"offset": off, "diff_bytes": chunk_diff})
    return rows


def build_page_row(
    *,
    file_bytes: bytes,
    offset: int,
    page_size: int,
    sections: list[Section],
    diff_bytes: int | None = None,
    include_disasm: bool = False,
) -> dict[str, object]:
    blob = file_bytes[offset : offset + page_size]
    section = map_offset_to_section(offset, sections)
    row: dict[str, object] = {
        "offset": offset,
        "page_index": offset // page_size,
        "folio16k_subpage": (offset // page_size) % 4,
        "size": len(blob),
        "entropy": round(shannon_entropy(blob), 4),
        "zero_bytes": blob.count(0),
        "zero_run_max": longest_zero_run(blob),
        "section": section.name if section else None,
        "section_flags": section.flags if section else None,
        "section_offset": section.section_offset if section else None,
        "is_executable_section": section.is_executable if section else False,
    }
    if diff_bytes is not None:
        row["diff_bytes"] = diff_bytes
    if include_disasm:
        row.update(page_a64_stats(blob))
    return row


def choose_focus_offsets(
    *,
    diff_rows: list[dict[str, int]],
    focus_offsets: list[int],
) -> list[int]:
    if diff_rows:
        return [row["offset"] for row in diff_rows]
    return sorted(set(focus_offsets))


def render_summary(payload: dict[str, object]) -> str:
    lines = [
        f"file={payload['file']}",
        f"kind={payload['file_kind']}",
    ]
    peer = payload.get("peer")
    if peer:
        lines.append(f"peer={peer}")
        lines.append(
            f"diff_pages={payload['diff_pages']} total_diff_bytes={payload['total_diff_bytes']} same_size={payload['same_size']}"
        )
    lines.append(f"focus_pages={len(payload['page_rows'])}")
    for cluster in payload.get("clusters", []):
        lines.append(
            "cluster="
            f"0x{cluster['start_offset']:x}-0x{cluster['end_offset']:x} "
            f"pages={cluster['page_count']} gap={cluster['max_gap_pages']}"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--peer", type=Path)
    ap.add_argument("--page-size", type=int, default=4096)
    ap.add_argument("--cluster-gap-pages", type=int, default=1)
    ap.add_argument("--focus-offset", action="append", default=[])
    ap.add_argument("--out-json", type=Path)
    ap.add_argument("--out-tsv", type=Path)
    ap.add_argument("--max-disasm-pages", type=int, default=64)
    ap.add_argument("--disasm-all-focus-pages", action="store_true")
    args = ap.parse_args()

    file_kind = detect_file_kind(args.file)
    readelf_sections: list[Section] = []
    if file_kind.startswith("elf"):
        readelf_sections = parse_readelf_sections(run_checked(["readelf", "-W", "-S", str(args.file)]))

    file_bytes = args.file.read_bytes()
    peer_bytes = args.peer.read_bytes() if args.peer else None
    diff_rows = diff_pages(file_bytes, peer_bytes, args.page_size) if peer_bytes is not None else []
    focus_offsets = [int(v, 0) for v in args.focus_offset]
    selected_offsets = choose_focus_offsets(diff_rows=diff_rows, focus_offsets=focus_offsets)
    diff_map = {row["offset"]: row["diff_bytes"] for row in diff_rows}

    page_rows: list[dict[str, object]] = []
    for index, offset in enumerate(selected_offsets):
        section = map_offset_to_section(offset, readelf_sections)
        include_disasm = bool(
            index < args.max_disasm_pages
            and (
                args.disasm_all_focus_pages
                or (section is not None and section.is_executable)
            )
        )
        page_rows.append(
            build_page_row(
                file_bytes=file_bytes,
                offset=offset,
                page_size=args.page_size,
                sections=readelf_sections,
                diff_bytes=diff_map.get(offset),
                include_disasm=include_disasm,
            )
        )

    clusters = [
        dataclasses.asdict(cluster)
        for cluster in cluster_page_offsets(
            [row["offset"] for row in diff_rows],
            page_size=args.page_size,
            max_gap_pages=args.cluster_gap_pages,
        )
    ]
    payload: dict[str, object] = {
        "file": str(args.file),
        "peer": str(args.peer) if args.peer else None,
        "file_kind": file_kind,
        "size": len(file_bytes),
        "peer_size": len(peer_bytes) if peer_bytes is not None else None,
        "same_size": (len(file_bytes) == len(peer_bytes)) if peer_bytes is not None else None,
        "diff_pages": len(diff_rows),
        "total_diff_bytes": sum(row["diff_bytes"] for row in diff_rows),
        "sections": [dataclasses.asdict(section) for section in readelf_sections],
        "clusters": clusters,
        "page_rows": page_rows,
    }

    if args.out_json:
        args.out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.out_tsv:
        header = [
            "offset",
            "page_index",
            "folio16k_subpage",
            "section",
            "section_flags",
            "section_offset",
            "is_executable_section",
            "diff_bytes",
            "entropy",
            "zero_bytes",
            "zero_run_max",
            "decoded_lines",
            "inst_directive_lines",
            "undefined_like_lines",
            "common_mnemonic_lines",
        ]
        lines = ["\t".join(header)]
        for row in page_rows:
            lines.append("\t".join(str(row.get(col, "")) for col in header))
        args.out_tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(render_summary(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
