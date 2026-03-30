#!/usr/bin/env python3
"""Build a non-interactive gdb-multiarch command for offline kernel crash lookup."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_BASE = "$HOME/learn_os"
DEFAULT_KERNEL_SRC = "$HOME/learn_os/f2fs"
DEFAULT_VMLINUX = "$HOME/learn_os/f2fs_upstream/vmlinux"
DEFAULT_GDBINIT = "$HOME/learn_os/.gdbinit"
DEFAULT_GDB = "/usr/bin/gdb-multiarch"
DEFAULT_VARS = "$HOME/learn_os/.vars.sh"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="location like func+0x12 or absolute address")
    parser.add_argument("--panic-file", help="path to panic/oops log")
    parser.add_argument("--panic-text", help="panic/oops text")
    parser.add_argument("--kernel-src", default=DEFAULT_KERNEL_SRC)
    parser.add_argument("--vmlinux", default=DEFAULT_VMLINUX)
    parser.add_argument("--gdbinit", default=DEFAULT_GDBINIT)
    parser.add_argument("--gdb", default=DEFAULT_GDB)
    parser.add_argument("--vars-file", default=DEFAULT_VARS)
    parser.add_argument("--lines", type=int, default=20)
    parser.add_argument("--include-disasm", action="store_true")
    parser.add_argument("--use-vars-sh", action="store_true", help="prefix the command with source vars.sh &&")
    parser.add_argument("--format", choices=["shell", "json", "gdb"], default="shell")
    return parser.parse_args()


def detect_symbol(args: argparse.Namespace) -> tuple[str | None, str]:
    if args.symbol:
        return args.symbol, "provided explicitly"

    parse_script = Path(__file__).with_name("parse_oops_target.py")
    cmd = [sys.executable, str(parse_script)]
    if args.panic_file:
        cmd += ["--panic-file", args.panic_file]
    elif args.panic_text:
        cmd += ["--text", args.panic_text]
    else:
        raw = sys.stdin.read()
        cmd += ["--text", raw]

    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise SystemExit(proc.stderr.strip() or "failed to parse panic text")

    data = json.loads(proc.stdout)
    return data.get("picked"), data.get("reason", "parsed from panic text")


def build_gdb_commands(symbol: str, lines: int, include_disasm: bool) -> list[str]:
    cmds = [
        "set pagination off",
        "set confirm off",
        f"set listsize {lines}",
        f"info line *{symbol}",
        f"list *{symbol}",
    ]
    if include_disasm:
        cmds.append(f"disassemble /s *{symbol}")
    return cmds


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def maybe_shell_path(value: str) -> str:
    if value.startswith('$HOME/') or value.startswith('$BASE/'):
        return value
    return shell_quote(value)


def build_shell_command(args: argparse.Namespace, symbol: str) -> str:
    ex_parts = [
        f"-ex {shell_quote('set pagination off')}",
        f"-ex {shell_quote('set confirm off')}",
        f"-ex {shell_quote(f'set listsize {args.lines}')}",
        f"-ex {shell_quote(f'directory {args.kernel_src}')}",
        f"-ex {shell_quote(f'source {args.gdbinit}')}",
        f"-ex {shell_quote(f'info line *{symbol}')}",
        f"-ex {shell_quote(f'list *{symbol}')}",
    ]
    if args.include_disasm:
        ex_parts.append(f"-ex {shell_quote(f'disassemble /s *{symbol}')}")

    gdb_cmd = (
        f"{maybe_shell_path(args.gdb)} -q -batch {maybe_shell_path(args.vmlinux)} "
        + " ".join(ex_parts)
    )
    if args.use_vars_sh:
        return f"source {maybe_shell_path(args.vars_file)} && {gdb_cmd}"
    return gdb_cmd


def main() -> int:
    args = parse_args()
    symbol, reason = detect_symbol(args)
    if not symbol:
        print("could not determine crash location", file=sys.stderr)
        return 1

    cmds = build_gdb_commands(symbol, args.lines, args.include_disasm)

    if args.format == "gdb":
        for line in [f"directory {args.kernel_src}", f"source {args.gdbinit}", *cmds]:
            print(line)
        return 0

    if args.format == "json":
        print(json.dumps({
            "picked": symbol,
            "reason": reason,
            "command": build_shell_command(args, symbol),
            "gdb_commands": [f"directory {args.kernel_src}", f"source {args.gdbinit}", *cmds],
        }, ensure_ascii=False, indent=2))
        return 0

    print(build_shell_command(args, symbol))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
