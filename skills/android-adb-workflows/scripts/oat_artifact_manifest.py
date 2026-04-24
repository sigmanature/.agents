#!/usr/bin/env python3

import argparse
import json
import shlex
import sys
from pathlib import Path

from pm_art_dump_summary import parse_pm_art_dump_text


IGNORED_FIELDS = [
    "raw_sha256",
    "tmp_file_names",
    "layout_offsets",
    "padding_bytes",
    "whole_file_hash",
]


def parse_cmdline_flags(cmdline: str) -> dict[str, str]:
    flags: dict[str, str] = {}
    if not cmdline:
        return flags
    for token in shlex.split(cmdline):
        if token.startswith("--") and "=" in token:
            key, value = token[2:].split("=", 1)
            flags[key] = value
    return flags


def parse_header_file(path: Path) -> dict:
    key_values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if " = " not in raw_line:
            continue
        key, value = raw_line.split(" = ", 1)
        key_values[key.strip()] = value.strip()

    cmdline = key_values.get("dex2oat-cmdline", "")
    cmd_flags = parse_cmdline_flags(cmdline)
    stable_anchors = {
        "compiler_filter": key_values.get("compiler-filter"),
        "bootclasspath_checksums": key_values.get("bootclasspath-checksums"),
        "bootclasspath": key_values.get("bootclasspath"),
        "class_loader_context": cmd_flags.get("class-loader-context"),
        "compilation_reason": cmd_flags.get("compilation-reason"),
        "instruction_set": cmd_flags.get("instruction-set"),
        "instruction_set_features": cmd_flags.get("instruction-set-features"),
        "instruction_set_variant": cmd_flags.get("instruction-set-variant"),
        "cmdline_compiler_filter": cmd_flags.get("compiler-filter"),
        "profile_guided": "profile-file-fd" in cmd_flags,
    }

    issues: list[str] = []
    if not stable_anchors["compiler_filter"]:
        issues.append("missing compiler-filter header field")
    if not cmdline:
        issues.append("missing dex2oat-cmdline header field")
    if stable_anchors["compiler_filter"] and stable_anchors["cmdline_compiler_filter"]:
        if stable_anchors["compiler_filter"] != stable_anchors["cmdline_compiler_filter"]:
            issues.append("compiler-filter header mismatch with dex2oat-cmdline")

    return {
        "type": "oat_header",
        "path": str(path),
        "stable_anchors": stable_anchors,
        "issues": issues,
        "structurally_valid": not issues,
    }


def parse_vdex_summary(path: Path) -> dict:
    info = json.loads(path.read_text(encoding="utf-8"))
    issues = list(info.get("issues") or [])
    stable_anchors = {
        "magic": info.get("magic"),
        "version": info.get("version"),
        "number_of_sections": info.get("number_of_sections"),
        "checksum_entry_count": ((info.get("checksum_section") or {}).get("entry_count")),
        "embedded_dex_count": len(((info.get("dex_section") or {}).get("embedded_dexes") or [])),
        "verifier_deps_size": ((info.get("verifier_deps_section") or {}).get("size")),
        "type_lookup_table_size": ((info.get("type_lookup_table_section") or {}).get("size")),
    }
    structurally_valid = bool(info.get("structurally_valid", not issues))
    if stable_anchors["magic"] != "vdex":
        structurally_valid = False
        issues.append("unexpected vdex magic")
    return {
        "type": "vdex",
        "path": str(path),
        "stable_anchors": stable_anchors,
        "issues": issues,
        "structurally_valid": structurally_valid,
    }


def artifact_key_for(path: Path) -> str:
    if path.name.endswith(".header.txt"):
        return path.name[: -len(".header.txt")]
    if path.name.endswith(".header.rc"):
        return path.name[: -len(".header.rc")]
    if path.name.endswith(".rc"):
        return path.name[: -len(".rc")]
    if path.name.endswith(".json"):
        return path.name[: -len(".json")]
    return path.stem


def read_rc_file(path: Path) -> int | None:
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return None
    return int(raw, 10)


def apply_validation_rc(artifact: dict, rc: int | None, label: str) -> dict:
    if rc is None:
        artifact.setdefault("validation", {})[label] = None
        return artifact
    artifact.setdefault("validation", {})[label] = rc
    if rc != 0:
        artifact["structurally_valid"] = False
        artifact.setdefault("issues", []).append(f"{label} exited with rc={rc}")
    return artifact


def build_manifest(
    snapshot_dir: Path,
    requested_filter: str | None,
    requested_reason: str | None,
) -> dict:
    pm_art_dump_path = snapshot_dir / "pm_art_dump.txt"
    if not pm_art_dump_path.exists():
        raise FileNotFoundError(f"missing snapshot file: {pm_art_dump_path}")
    effective = parse_pm_art_dump_text(pm_art_dump_path.read_text(encoding="utf-8", errors="replace"))

    artifacts: dict[str, dict] = {}
    for header_path in sorted(snapshot_dir.glob("*.header.txt")):
        artifacts[artifact_key_for(header_path)] = parse_header_file(header_path)
    for vdex_path in sorted(snapshot_dir.glob("*.vdex.json")):
        artifacts[artifact_key_for(vdex_path)] = parse_vdex_summary(vdex_path)
    for rc_path in sorted(snapshot_dir.glob("*.header.rc")):
        key = artifact_key_for(rc_path)
        artifact = artifacts.setdefault(
            key,
            {"type": "oat_header", "path": str(rc_path), "stable_anchors": {}, "issues": [], "structurally_valid": False},
        )
        artifacts[key] = apply_validation_rc(artifact, read_rc_file(rc_path), "oatdump_rc")
    for rc_path in sorted(snapshot_dir.glob("*.rc")):
        if rc_path.name.endswith(".header.rc"):
            continue
        key = artifact_key_for(rc_path)
        artifact = artifacts.setdefault(
            key,
            {"type": "vdex", "path": str(rc_path), "stable_anchors": {}, "issues": [], "structurally_valid": False},
        )
        artifacts[key] = apply_validation_rc(artifact, read_rc_file(rc_path), "vdexdump_rc")

    overall_valid = all(item["structurally_valid"] for item in artifacts.values()) if artifacts else False
    primary_effective = next((item for item in effective.get("entries", []) if item.get("abi") == "arm64"), None)
    if primary_effective is None and effective.get("entries"):
        primary_effective = effective["entries"][0]
    return {
        "snapshot_dir": str(snapshot_dir.resolve()),
        "requested_filter": requested_filter,
        "requested_reason": requested_reason,
        "effective": effective,
        "effective_primary": primary_effective,
        "effective_filter_matches_request": (
            None
            if requested_filter is None or primary_effective is None
            else primary_effective.get("status") == requested_filter
        ),
        "effective_reason_matches_request": (
            None
            if requested_reason is None or primary_effective is None
            else primary_effective.get("reason") == requested_reason
        ),
        "ignored_fields": IGNORED_FIELDS,
        "artifacts": artifacts,
        "overall_structurally_valid": overall_valid,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build invariant manifest v1 from one artifact snapshot directory."
    )
    parser.add_argument("--snapshot-dir", required=True, type=Path, help="Snapshot directory containing pm_art_dump.txt and artifact outputs")
    parser.add_argument("--requested-filter", default=None, help="Requested compiler filter for this snapshot")
    parser.add_argument("--requested-reason", default=None, help="Requested compilation reason for this snapshot")
    parser.add_argument("--out", type=Path, default=None, help="Write manifest JSON to this file instead of stdout")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        manifest = build_manifest(args.snapshot_dir, args.requested_filter, args.requested_reason)
    except (OSError, json.JSONDecodeError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    rendered = json.dumps(manifest, indent=2, sort_keys=False)
    if args.out is not None:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
