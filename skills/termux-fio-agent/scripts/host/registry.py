#!/usr/bin/env python3
"""Maintain a Termux-over-SSH device registry for host agents."""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

FIELDS = [
    "alias",
    "serial",
    "local_port",
    "remote_port",
    "termux_user",
    "identity_file",
    "host_key_alias",
    "status",
    "notes",
]

DEFAULT_STATE_SUBDIR = ".termux-fio-agent"
DEFAULT_IDENTITY_FILE = "~/.ssh/termux_fio"
DEFAULT_REMOTE_PORT = "8022"
DEFAULT_BASE_PORT = 8022
DEFAULT_ALIAS_PREFIX = "termux-fio"


def skill_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def seed_path() -> Path:
    return skill_dir() / "state" / "devices.tsv"


def state_dir(args: argparse.Namespace) -> Path:
    value = getattr(args, "state_dir", None) or os.environ.get("TERMUX_FIO_STATE_DIR")
    if value:
        return Path(value).expanduser().resolve()
    return (Path.cwd() / DEFAULT_STATE_SUBDIR).resolve()


def registry_path(args: argparse.Namespace) -> Path:
    return state_dir(args) / "devices.tsv"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def init_registry(args: argparse.Namespace) -> int:
    dst = registry_path(args)
    ensure_parent(dst)
    if dst.exists() and not args.force:
        print(str(dst))
        print("registry already exists; use --force to overwrite", file=sys.stderr)
        return 0
    src = seed_path()
    if not src.exists():
        raise SystemExit(f"seed registry not found: {src}")
    shutil.copyfile(src, dst)
    print(str(dst))
    return 0


def read_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows: List[Dict[str, str]] = []
        for row in reader:
            clean = {field: (row.get(field) or "") for field in FIELDS}
            if clean["alias"]:
                rows.append(clean)
        return rows


def write_rows(path: Path, rows: List[Dict[str, str]]) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def load_or_init(args: argparse.Namespace) -> Path:
    path = registry_path(args)
    if not path.exists():
        tmp = argparse.Namespace(**vars(args))
        tmp.force = False
        init_registry(tmp)
    return path


def find_row(rows: List[Dict[str, str]], alias: str) -> Dict[str, str] | None:
    for row in rows:
        if row.get("alias") == alias:
            return row
    return None


def used_ports(rows: List[Dict[str, str]]) -> set[int]:
    ports: set[int] = set()
    for row in rows:
        value = row.get("local_port", "")
        try:
            ports.add(int(value))
        except ValueError:
            pass
    return ports


def next_port_value(rows: List[Dict[str, str]]) -> int:
    ports = used_ports(rows)
    if not ports:
        return DEFAULT_BASE_PORT
    port = max(max(ports) + 1, DEFAULT_BASE_PORT)
    while port in ports:
        port += 1
    return port


def next_alias_value(rows: List[Dict[str, str]]) -> str:
    used = {row.get("alias", "") for row in rows}
    n = 1
    while f"{DEFAULT_ALIAS_PREFIX}-{n}" in used:
        n += 1
    return f"{DEFAULT_ALIAS_PREFIX}-{n}"


def cmd_list(args: argparse.Namespace) -> int:
    path = load_or_init(args)
    rows = read_rows(path)
    if args.format == "tsv":
        with path.open("r", encoding="utf-8") as f:
            print(f.read(), end="")
        return 0
    if not rows:
        print("no devices")
        return 0
    widths = {field: len(field) for field in FIELDS}
    for row in rows:
        for field in FIELDS:
            widths[field] = max(widths[field], len(row.get(field, "")))
    print("  ".join(field.ljust(widths[field]) for field in FIELDS))
    print("  ".join("-" * widths[field] for field in FIELDS))
    for row in rows:
        print("  ".join(row.get(field, "").ljust(widths[field]) for field in FIELDS))
    return 0


def cmd_next_port(args: argparse.Namespace) -> int:
    path = load_or_init(args)
    print(next_port_value(read_rows(path)))
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    path = load_or_init(args)
    rows = read_rows(path)
    print(f"alias={next_alias_value(rows)}")
    print(f"local_port={next_port_value(rows)}")
    print(f"remote_port={DEFAULT_REMOTE_PORT}")
    return 0


def normalize_row(args: argparse.Namespace, existing: Dict[str, str] | None = None) -> Dict[str, str]:
    row = {field: "" for field in FIELDS}
    if existing:
        row.update(existing)
    mapping = {
        "alias": args.alias,
        "serial": getattr(args, "serial", None),
        "local_port": getattr(args, "local_port", None),
        "remote_port": getattr(args, "remote_port", None),
        "termux_user": getattr(args, "termux_user", None),
        "identity_file": getattr(args, "identity_file", None),
        "host_key_alias": getattr(args, "host_key_alias", None),
        "status": getattr(args, "status", None),
        "notes": getattr(args, "notes", None),
    }
    for field, value in mapping.items():
        if value is not None:
            row[field] = str(value)
    if not row["remote_port"]:
        row["remote_port"] = DEFAULT_REMOTE_PORT
    if not row["identity_file"]:
        row["identity_file"] = DEFAULT_IDENTITY_FILE
    if not row["host_key_alias"]:
        row["host_key_alias"] = row["alias"]
    if not row["status"]:
        row["status"] = "pending"
    return row


def cmd_add(args: argparse.Namespace) -> int:
    path = load_or_init(args)
    rows = read_rows(path)
    if not args.alias:
        args.alias = next_alias_value(rows)
    if not args.local_port:
        args.local_port = str(next_port_value(rows))
    existing = find_row(rows, args.alias)
    if existing and not args.replace:
        raise SystemExit(f"alias already exists: {args.alias}; use --replace")
    row = normalize_row(args)
    if existing:
        rows = [row if r.get("alias") == args.alias else r for r in rows]
    else:
        rows.append(row)
    write_rows(path, rows)
    print(row["alias"])
    print(f"local_port={row['local_port']}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    path = load_or_init(args)
    rows = read_rows(path)
    row = find_row(rows, args.alias)
    if not row:
        raise SystemExit(f"alias not found: {args.alias}")
    updated = normalize_row(args, row)
    rows = [updated if r.get("alias") == args.alias else r for r in rows]
    write_rows(path, rows)
    print(args.alias)
    return 0


def ssh_config_block(row: Dict[str, str]) -> str:
    alias = row["alias"]
    user = row.get("termux_user") or "UNKNOWN"
    port = row.get("local_port") or "8022"
    identity = row.get("identity_file") or DEFAULT_IDENTITY_FILE
    host_key_alias = row.get("host_key_alias") or alias
    return "\n".join(
        [
            f"Host {alias}",
            "  HostName 127.0.0.1",
            f"  Port {port}",
            f"  User {user}",
            f"  IdentityFile {identity}",
            "  IdentitiesOnly yes",
            f"  HostKeyAlias {host_key_alias}",
            "  StrictHostKeyChecking accept-new",
            "  ServerAliveInterval 30",
        ]
    )


def get_required_row(args: argparse.Namespace) -> Dict[str, str]:
    path = load_or_init(args)
    rows = read_rows(path)
    row = find_row(rows, args.alias)
    if not row:
        raise SystemExit(f"alias not found: {args.alias}")
    return row


def cmd_ssh_config(args: argparse.Namespace) -> int:
    print(ssh_config_block(get_required_row(args)))
    return 0


def cmd_write_ssh_config(args: argparse.Namespace) -> int:
    row = get_required_row(args)
    alias = row["alias"]
    ssh_path = Path(args.ssh_config).expanduser()
    ssh_path.parent.mkdir(parents=True, exist_ok=True)
    begin = f"# BEGIN termux-fio-agent {alias}"
    end = f"# END termux-fio-agent {alias}"
    block = f"{begin}\n{ssh_config_block(row)}\n{end}\n"
    old = ssh_path.read_text(encoding="utf-8") if ssh_path.exists() else ""
    lines = old.splitlines()
    new_lines: List[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        if lines[i].strip() == begin:
            replaced = True
            while i < len(lines) and lines[i].strip() != end:
                i += 1
            if i < len(lines):
                i += 1
            if new_lines and new_lines[-1] != "":
                new_lines.append("")
            new_lines.extend(block.rstrip("\n").splitlines())
        else:
            new_lines.append(lines[i])
            i += 1
    if not replaced:
        if new_lines and new_lines[-1] != "":
            new_lines.append("")
        new_lines.extend(block.rstrip("\n").splitlines())
    ssh_path.write_text("\n".join(new_lines).rstrip("\n") + "\n", encoding="utf-8")
    try:
        ssh_path.chmod(0o600)
    except OSError:
        pass
    print(str(ssh_path))
    return 0


def cmd_adb_forward(args: argparse.Namespace) -> int:
    row = get_required_row(args)
    serial = row.get("serial", "")
    if not serial or serial == "UNKNOWN":
        raise SystemExit(f"serial unknown for {row['alias']}; ask human for adb serial first")
    local = row.get("local_port") or "8022"
    remote = row.get("remote_port") or DEFAULT_REMOTE_PORT
    cmd = ["adb", "-s", serial, "forward", f"tcp:{local}", f"tcp:{remote}"]
    if args.dry_run:
        print(" ".join(cmd))
        return 0
    subprocess.check_call(cmd)
    print(f"{row['alias']}: 127.0.0.1:{local} -> {serial}:{remote}")
    return 0


def cmd_bootstrap_prompt(args: argparse.Namespace) -> int:
    serial = args.serial or "DEVICE_SERIAL"
    print("[HUMAN ACTION REQUIRED]")
    print("On the Android device, open Termux and run the bootstrap commands or script.")
    print("Then send back the printed USER, HOME, SSHD, and AUTHORIZED_KEYS lines.")
    print("")
    print("Host-side public key push command:")
    print(f"adb -s {serial} push ~/.ssh/termux_fio.pub /sdcard/Download/termux_fio.pub")
    print("")
    print("Termux-side command if the script was copied to Download:")
    print("bash ~/storage/downloads/termux_bootstrap.sh")
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", default=None, help="writable state directory")


def add_row_args(parser: argparse.ArgumentParser, require_alias: bool = True) -> None:
    parser.add_argument("--alias", required=require_alias)
    parser.add_argument("--serial")
    parser.add_argument("--local-port")
    parser.add_argument("--remote-port")
    parser.add_argument("--termux-user")
    parser.add_argument("--identity-file")
    parser.add_argument("--host-key-alias")
    parser.add_argument("--status")
    parser.add_argument("--notes")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="maintain Termux fio device registry")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    add_common(p)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=init_registry)

    p = sub.add_parser("list")
    add_common(p)
    p.add_argument("--format", choices=["table", "tsv"], default="table")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("next-port")
    add_common(p)
    p.set_defaults(func=cmd_next_port)

    p = sub.add_parser("suggest")
    add_common(p)
    p.set_defaults(func=cmd_suggest)

    p = sub.add_parser("add")
    add_common(p)
    add_row_args(p, require_alias=False)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("update")
    add_common(p)
    add_row_args(p, require_alias=True)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("ssh-config")
    add_common(p)
    p.add_argument("--alias", required=True)
    p.set_defaults(func=cmd_ssh_config)

    p = sub.add_parser("write-ssh-config")
    add_common(p)
    p.add_argument("--alias", required=True)
    p.add_argument("--ssh-config", default="~/.ssh/config")
    p.set_defaults(func=cmd_write_ssh_config)

    p = sub.add_parser("adb-forward")
    add_common(p)
    p.add_argument("--alias", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_adb_forward)

    p = sub.add_parser("bootstrap-prompt")
    p.add_argument("--serial")
    p.set_defaults(func=cmd_bootstrap_prompt)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
