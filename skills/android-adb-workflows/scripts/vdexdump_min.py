#!/usr/bin/env python3

import argparse
import json
import os
import struct
import sys
from typing import Dict, List, Tuple


HEADER_STRUCT = struct.Struct("<4s4sI")
SECTION_STRUCT = struct.Struct("<III")
UINT32_STRUCT = struct.Struct("<I")

SECTION_NAMES = {
    0: "checksum",
    1: "dex_file",
    2: "verifier_deps",
    3: "type_lookup_table",
}

DEX_MAGIC_PREFIXES = (b"dex\n", b"cdex")
DEX_MAGIC_SIZE = 8
DEX_FILE_SIZE_OFFSET = 32


class ParseError(Exception):
    pass


def read_struct(blob: bytes, offset: int, fmt: struct.Struct) -> Tuple[int, ...]:
    end = offset + fmt.size
    if end > len(blob):
        raise ParseError(
            f"offset {offset} exceeds file size while reading {fmt.format} ({fmt.size} bytes)"
        )
    return fmt.unpack_from(blob, offset)


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def decode_ascii(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")


def section_name(kind: int) -> str:
    return SECTION_NAMES.get(kind, f"unknown_{kind}")


def parse_sections(blob: bytes, number_of_sections: int) -> List[Dict[str, int]]:
    sections: List[Dict[str, int]] = []
    offset = HEADER_STRUCT.size
    for index in range(number_of_sections):
        kind, section_offset, section_size = read_struct(blob, offset, SECTION_STRUCT)
        sections.append(
            {
                "index": index,
                "kind": kind,
                "name": section_name(kind),
                "offset": section_offset,
                "size": section_size,
            }
        )
        offset += SECTION_STRUCT.size
    return sections


def validate_section_ranges(blob: bytes, sections: List[Dict[str, int]]) -> List[str]:
    issues: List[str] = []
    file_size = len(blob)
    for section in sections:
        start = section["offset"]
        size = section["size"]
        end = start + size
        if start == 0 and size == 0:
            continue
        if start > file_size:
            issues.append(
                f"section {section['name']} offset {start} is beyond file size {file_size}"
            )
        elif end > file_size:
            issues.append(
                f"section {section['name']} end {end} is beyond file size {file_size}"
            )
    return issues


def find_section(sections: List[Dict[str, int]], kind: int) -> Dict[str, int]:
    for section in sections:
        if section["kind"] == kind:
            return section
    return {"index": -1, "kind": kind, "name": section_name(kind), "offset": 0, "size": 0}


def parse_checksums(blob: bytes, section: Dict[str, int]) -> Tuple[List[Dict[str, object]], List[str]]:
    issues: List[str] = []
    entries: List[Dict[str, object]] = []
    if section["size"] == 0:
        return entries, issues
    if section["size"] % UINT32_STRUCT.size != 0:
        issues.append(
            f"checksum section size {section['size']} is not a multiple of {UINT32_STRUCT.size}"
        )
    count = section["size"] // UINT32_STRUCT.size
    for index in range(count):
        value_offset = section["offset"] + index * UINT32_STRUCT.size
        (value,) = read_struct(blob, value_offset, UINT32_STRUCT)
        entries.append(
            {
                "index": index,
                "offset": value_offset,
                "value": value,
                "value_hex": f"0x{value:08x}",
            }
        )
    return entries, issues


def looks_like_dex(raw_magic: bytes) -> bool:
    return any(raw_magic.startswith(prefix) for prefix in DEX_MAGIC_PREFIXES)


def parse_embedded_dexes(
    blob: bytes, section: Dict[str, int], expected_count: int
) -> Tuple[List[Dict[str, object]], List[str]]:
    issues: List[str] = []
    entries: List[Dict[str, object]] = []
    if section["size"] == 0:
        return entries, issues

    start = section["offset"]
    end = start + section["size"]
    cursor = start
    max_entries = expected_count if expected_count > 0 else sys.maxsize

    for index in range(max_entries):
        if cursor >= end:
            break
        if cursor + DEX_MAGIC_SIZE > end:
            issues.append(
                f"dex candidate {index} at offset {cursor} does not have room for an 8-byte magic"
            )
            break

        magic = blob[cursor : cursor + DEX_MAGIC_SIZE]
        if not looks_like_dex(magic):
            issues.append(
                f"dex candidate {index} at offset {cursor} does not start with dex/cdex magic"
            )
            break

        file_size_offset = cursor + DEX_FILE_SIZE_OFFSET
        if file_size_offset + UINT32_STRUCT.size > end:
            issues.append(
                f"dex candidate {index} at offset {cursor} does not have room for file_size"
            )
            break

        (file_size,) = read_struct(blob, file_size_offset, UINT32_STRUCT)
        if file_size == 0:
            issues.append(f"dex candidate {index} at offset {cursor} has file_size 0")
            break

        next_cursor = cursor + file_size
        if next_cursor > end:
            issues.append(
                f"dex candidate {index} at offset {cursor} extends past dex section end {end}"
            )
            break

        entries.append(
            {
                "index": index,
                "offset": cursor,
                "magic": magic.hex(),
                "magic_ascii": magic.decode("ascii", errors="replace"),
                "file_size": file_size,
                "file_size_offset": file_size_offset,
            }
        )
        cursor = align_up(next_cursor, 4)

    return entries, issues


def parse_vdex(path: str) -> Dict[str, object]:
    with open(path, "rb") as handle:
        blob = handle.read()

    if len(blob) < HEADER_STRUCT.size:
        raise ParseError(
            f"file is too small for a VDEX header: {len(blob)} bytes < {HEADER_STRUCT.size}"
        )

    magic_raw, version_raw, number_of_sections = read_struct(blob, 0, HEADER_STRUCT)
    sections = parse_sections(blob, number_of_sections)

    issues = validate_section_ranges(blob, sections)
    checksum_section = find_section(sections, 0)
    dex_section = find_section(sections, 1)
    verifier_deps_section = find_section(sections, 2)
    type_lookup_section = find_section(sections, 3)

    checksum_entries, checksum_issues = parse_checksums(blob, checksum_section)
    issues.extend(checksum_issues)

    embedded_dexes, dex_issues = parse_embedded_dexes(blob, dex_section, len(checksum_entries))
    issues.extend(dex_issues)

    structurally_valid = (
        decode_ascii(magic_raw) == "vdex"
        and len(issues) == 0
        and dex_section["offset"] + dex_section["size"] <= len(blob)
    )

    return {
        "path": os.path.abspath(path),
        "file_size": len(blob),
        "magic": decode_ascii(magic_raw),
        "version": decode_ascii(version_raw),
        "number_of_sections": number_of_sections,
        "sections": sections,
        "checksum_section": {
            "offset": checksum_section["offset"],
            "size": checksum_section["size"],
            "entry_count": len(checksum_entries),
            "entries": checksum_entries,
        },
        "dex_section": {
            "exists": dex_section["size"] != 0,
            "offset": dex_section["offset"],
            "size": dex_section["size"],
            "embedded_dexes": embedded_dexes,
        },
        "verifier_deps_section": {
            "offset": verifier_deps_section["offset"],
            "size": verifier_deps_section["size"],
        },
        "type_lookup_table_section": {
            "offset": type_lookup_section["offset"],
            "size": type_lookup_section["size"],
        },
        "structurally_valid": structurally_valid,
        "issues": issues,
    }


def render_text(info: Dict[str, object]) -> str:
    lines = [
        f"path: {info['path']}",
        f"file_size: {info['file_size']}",
        f"magic: {info['magic']}",
        f"version: {info['version']}",
        f"number_of_sections: {info['number_of_sections']}",
        "sections:",
    ]

    for section in info["sections"]:
        lines.append(
            "  - "
            f"[{section['index']}] {section['name']} "
            f"(kind={section['kind']}, offset={section['offset']}, size={section['size']})"
        )

    checksum_section = info["checksum_section"]
    lines.append(f"checksum_entry_count: {checksum_section['entry_count']}")
    for entry in checksum_section["entries"]:
        lines.append(
            "  - "
            f"[{entry['index']}] {entry['value_hex']} "
            f"(decimal={entry['value']}, offset={entry['offset']})"
        )

    dex_section = info["dex_section"]
    lines.append(f"dex_section_exists: {'yes' if dex_section['exists'] else 'no'}")
    lines.append(f"verifier_deps_size: {info['verifier_deps_section']['size']}")
    lines.append(f"type_lookup_table_size: {info['type_lookup_table_section']['size']}")

    embedded_dexes = dex_section["embedded_dexes"]
    if embedded_dexes:
        lines.append("embedded_dexes:")
        for dex in embedded_dexes:
            lines.append(
                "  - "
                f"[{dex['index']}] offset={dex['offset']} magic={dex['magic_ascii']!r} "
                f"file_size={dex['file_size']} file_size_offset={dex['file_size_offset']}"
            )
    else:
        lines.append("embedded_dexes: none")

    if info["issues"]:
        lines.append("issues:")
        for issue in info["issues"]:
            lines.append(f"  - {issue}")

    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Minimal host-side VDEX parser for header, sections, checksums, and embedded dex info."
    )
    parser.add_argument("vdex_path", help="Path to the input .vdex file")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of readable text")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when the file parses but is structurally invalid.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        info = parse_vdex(args.vdex_path)
    except (OSError, ParseError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(info, indent=2, sort_keys=False))
    else:
        print(render_text(info))
    if args.strict and not info["structurally_valid"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
