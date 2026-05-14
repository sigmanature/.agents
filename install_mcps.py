#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Install MCP server manifests from ~/.agents/mcps into supported agent vendors.

Examples:
  python3 install_mcps.py --scope user --vendor codex --all
  python3 install_mcps.py opencode_secure --scope user --vendor codex --dry-run
  python3 install_mcps.py opencode_secure --scope user --vendor opencode
  python3 install_mcps.py opencode_secure --scope project --vendor roo --workspace /path/to/repo
  python3 install_mcps.py opencode_secure --scope user --vendor codex --uninstall
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VENDORS = ("codex", "claude", "roo", "opencode")
PRUNE_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".DS_Store",
}
OPENCODE_CONFIG_FILENAMES = ("opencode.jsonc", "opencode.json")


@dataclass(frozen=True)
class McpManifest:
    name: str
    transport: str
    command: str | None
    args: list[str]
    url: str | None
    env: dict[str, str]
    cwd: str | None
    vendors: list[str]
    roo: dict[str, Any]
    source: Path


def expand_pathish(value: str, home: Path) -> str:
    if value == "~":
        return str(home)
    if value.startswith("~/"):
        return str(home / value[2:])
    return os.path.expandvars(value)


def parse_manifest(raw: dict[str, Any], source: Path, home: Path | None = None) -> McpManifest:
    home = home or Path.home()
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{source}: manifest requires non-empty string field 'name'")

    transport = raw.get("transport", "stdio")
    if transport not in {"stdio", "http", "sse"}:
        raise ValueError(f"{source}: transport must be one of stdio/http/sse")

    command = raw.get("command")
    if command is not None and not isinstance(command, str):
        raise ValueError(f"{source}: command must be a string when provided")
    if command is not None:
        command = expand_pathish(command, home)

    args_raw = raw.get("args", [])
    if not isinstance(args_raw, list) or not all(isinstance(v, str) for v in args_raw):
        raise ValueError(f"{source}: args must be a list of strings")
    args = [expand_pathish(v, home) for v in args_raw]

    url = raw.get("url")
    if url is not None and not isinstance(url, str):
        raise ValueError(f"{source}: url must be a string when provided")

    if transport == "stdio" and not command:
        raise ValueError(f"{source}: stdio manifest requires 'command'")
    if transport in {"http", "sse"} and not url:
        raise ValueError(f"{source}: {transport} manifest requires 'url'")

    env_raw = raw.get("env", {})
    if not isinstance(env_raw, dict) or not all(isinstance(k, str) for k in env_raw):
        raise ValueError(f"{source}: env must be an object with string keys")
    env = {k: expand_pathish(str(v), home) for k, v in env_raw.items()}

    cwd = raw.get("cwd")
    if cwd is not None:
        if not isinstance(cwd, str):
            raise ValueError(f"{source}: cwd must be a string when provided")
        cwd = expand_pathish(cwd, home)

    vendors_raw = raw.get("vendors", list(VENDORS))
    if not isinstance(vendors_raw, list) or not all(isinstance(v, str) for v in vendors_raw):
        raise ValueError(f"{source}: vendors must be a list of strings")
    unknown = sorted(set(vendors_raw) - set(VENDORS))
    if unknown:
        raise ValueError(f"{source}: unsupported vendors: {', '.join(unknown)}")

    roo_raw = raw.get("roo", {})
    if not isinstance(roo_raw, dict):
        raise ValueError(f"{source}: roo must be an object when provided")

    return McpManifest(
        name=name.strip(),
        transport=transport,
        command=command,
        args=args,
        url=url,
        env=env,
        cwd=cwd,
        vendors=list(dict.fromkeys(vendors_raw)),
        roo=roo_raw,
        source=source,
    )


def load_manifest(path: Path, home: Path | None = None) -> McpManifest:
    raw = read_json_object(path)
    return parse_manifest(raw, source=path, home=home)


def strip_json_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, len(text))
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def strip_trailing_commas(text: str) -> str:
    out: list[str] = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]

        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def parse_json_text(text: str, *, source: Path) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = json.loads(strip_trailing_commas(strip_json_comments(text)))
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: JSON root must be an object")
    return raw


def default_manifest_root(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".agents" / "mcps"


def resolve_manifest_sources(
    names_or_paths: list[str],
    *,
    manifest_root: Path,
    all_manifests: bool,
) -> list[Path]:
    if all_manifests or not names_or_paths:
        sources = sorted(manifest_root.glob("*.json"))
        if not sources:
            raise FileNotFoundError(f"no MCP manifests found under {manifest_root}")
        return sources

    sources: list[Path] = []
    for raw in names_or_paths:
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            sources.append(candidate.resolve())
            continue

        named = manifest_root / f"{raw}.json"
        if named.is_file():
            sources.append(named.resolve())
            continue

        raise FileNotFoundError(f"manifest not found: {raw} (also tried {named})")

    return sources


def env_flags(flag: str, env: dict[str, str]) -> list[str]:
    out: list[str] = []
    for key in sorted(env):
        out.extend([flag, f"{key}={env[key]}"])
    return out


def build_codex_commands(mcp: McpManifest) -> tuple[list[str], list[str]]:
    remove = ["codex", "mcp", "remove", mcp.name]
    add = ["codex", "mcp", "add"]

    if mcp.transport == "stdio":
        add.extend(env_flags("--env", mcp.env))
        add.extend([mcp.name, "--", mcp.command or ""])
        add.extend(mcp.args)
    else:
        add.extend([mcp.name, "--url", mcp.url or ""])

    return remove, add


def build_claude_commands(mcp: McpManifest, scope: str) -> tuple[list[str], list[str]]:
    remove = ["claude", "mcp", "remove", "--scope", scope, mcp.name]
    add = ["claude", "mcp", "add", "--scope", scope, "--transport", mcp.transport]

    if mcp.transport == "stdio":
        add.extend(env_flags("--env", mcp.env))
        add.extend([mcp.name, "--", mcp.command or ""])
        add.extend(mcp.args)
    else:
        add.extend([mcp.name, mcp.url or ""])

    return remove, add


def run_command(cmd: list[str], *, dry_run: bool, ignore_failure: bool = False) -> int:
    printable = " ".join(json.dumps(part) if " " in part else part for part in cmd)
    prefix = "[DRY-RUN]" if dry_run else "[RUN]"
    print(f"{prefix} {printable}")
    if dry_run:
        return 0

    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0 and not ignore_failure:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return proc.returncode


def install_cli_vendor(vendor: str, mcp: McpManifest, *, scope: str, dry_run: bool) -> None:
    if vendor == "codex":
        if scope != "user":
            raise ValueError("Codex MCP registration is global in this environment; use --scope user")
        remove, add = build_codex_commands(mcp)
    elif vendor == "claude":
        remove, add = build_claude_commands(mcp, scope=scope)
    else:
        raise ValueError(f"unsupported CLI vendor: {vendor}")

    run_command(remove, dry_run=dry_run, ignore_failure=True)
    run_command(add, dry_run=dry_run)


def uninstall_cli_vendor(vendor: str, name: str, *, scope: str, dry_run: bool) -> None:
    if vendor == "codex":
        if scope != "user":
            raise ValueError("Codex MCP registration is global in this environment; use --scope user")
        cmd = ["codex", "mcp", "remove", name]
    elif vendor == "claude":
        cmd = ["claude", "mcp", "remove", "--scope", scope, name]
    else:
        raise ValueError(f"unsupported CLI vendor: {vendor}")
    run_command(cmd, dry_run=dry_run, ignore_failure=True)


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return parse_json_text(path.read_text(encoding="utf-8"), source=path)


def write_json_object(path: Path, data: dict[str, Any], *, dry_run: bool) -> None:
    print(f"{'[DRY-RUN]' if dry_run else '[WRITE]'} {path}")
    if dry_run:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = next_backup_path(path)
        shutil.copy2(path, backup)
        print(f"[BACKUP] {backup}")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def next_backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = path.with_name(f"{path.name}.bak.{stamp}")
    if not candidate.exists():
        return candidate
    for i in range(1, 1000):
        candidate = path.with_name(f"{path.name}.bak.{stamp}.{i}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not allocate backup path for {path}")


def roo_server_config(mcp: McpManifest) -> dict[str, Any]:
    if mcp.transport == "stdio":
        server: dict[str, Any] = {
            "command": mcp.command,
            "args": mcp.args,
        }
    else:
        server = {
            "type": mcp.transport,
            "url": mcp.url,
        }

    if mcp.env:
        server["env"] = mcp.env
    if mcp.cwd:
        server["cwd"] = mcp.cwd

    server["disabled"] = bool(mcp.roo.get("disabled", False))
    if "alwaysAllow" in mcp.roo:
        always_allow = mcp.roo["alwaysAllow"]
        if not isinstance(always_allow, list) or not all(isinstance(v, str) for v in always_allow):
            raise ValueError(f"{mcp.source}: roo.alwaysAllow must be a list of strings")
        server["alwaysAllow"] = always_allow

    return server


def opencode_server_config(mcp: McpManifest) -> dict[str, Any]:
    if mcp.transport == "stdio":
        server: dict[str, Any] = {
            "type": "local",
            "command": [mcp.command or "", *mcp.args],
            "enabled": True,
        }
        if mcp.env:
            server["environment"] = mcp.env
        if mcp.cwd:
            print(f"[WARN] opencode config does not support cwd directly; ignoring cwd for {mcp.name}")
        return server

    if mcp.cwd:
        print(f"[WARN] opencode config does not support cwd directly; ignoring cwd for {mcp.name}")
    return {
        "type": "remote",
        "url": mcp.url,
        "enabled": True,
    }


def install_roo_config(config_path: Path, mcp: McpManifest, *, dry_run: bool) -> None:
    data = read_json_object(config_path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{config_path}: mcpServers must be an object")
    servers[mcp.name] = roo_server_config(mcp)
    write_json_object(config_path, data, dry_run=dry_run)


def uninstall_roo_config(config_path: Path, name: str, *, dry_run: bool) -> None:
    data = read_json_object(config_path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        print(f"[SKIP] {config_path}: {name} not configured")
        return
    del servers[name]
    write_json_object(config_path, data, dry_run=dry_run)


def install_opencode_config(config_path: Path, mcp: McpManifest, *, dry_run: bool) -> None:
    data = read_json_object(config_path)
    servers = data.setdefault("mcp", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{config_path}: mcp must be an object")
    servers[mcp.name] = opencode_server_config(mcp)
    write_json_object(config_path, data, dry_run=dry_run)


def uninstall_opencode_config(config_path: Path, name: str, *, dry_run: bool) -> None:
    data = read_json_object(config_path)
    servers = data.get("mcp")
    if not isinstance(servers, dict) or name not in servers:
        print(f"[SKIP] {config_path}: {name} not configured")
        return
    del servers[name]
    write_json_object(config_path, data, dry_run=dry_run)


def find_project_roo_configs(root: Path, max_depth: int) -> list[Path]:
    root = root.resolve()
    found: set[Path] = set()
    for dirpath, dirnames, _ in os.walk(root):
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(root).parts)
        except ValueError:
            depth = 0
        if depth > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        if ".roo" in dirnames:
            found.add((current / ".roo" / "mcp.json").resolve())
    return sorted(found)


def roo_config_paths(scope: str, *, home: Path, workspaces: list[str], max_depth: int) -> list[Path]:
    if scope == "user":
        return [home / ".roo" / "mcp.json"]

    if workspaces:
        return [(Path(ws).expanduser().resolve() / ".roo" / "mcp.json") for ws in workspaces]

    return find_project_roo_configs(Path.cwd(), max_depth=max_depth)


def opencode_config_path_for_workspace(workspace: Path) -> Path:
    workspace = workspace.expanduser().resolve()
    for filename in OPENCODE_CONFIG_FILENAMES:
        candidate = workspace / filename
        if candidate.exists():
            return candidate
    return workspace / "opencode.json"


def find_project_opencode_configs(root: Path, max_depth: int) -> list[Path]:
    root = root.resolve()
    found: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(root).parts)
        except ValueError:
            depth = 0
        if depth > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        if "opencode.jsonc" in filenames:
            found.add((current / "opencode.jsonc").resolve())
            continue
        if "opencode.json" in filenames:
            found.add((current / "opencode.json").resolve())
            continue
        if ".opencode" in dirnames:
            found.add((current / "opencode.json").resolve())
    return sorted(found)


def opencode_config_paths(scope: str, *, home: Path, workspaces: list[str], max_depth: int) -> list[Path]:
    if scope == "user":
        return [home / ".config" / "opencode" / "opencode.json"]

    if workspaces:
        return [opencode_config_path_for_workspace(Path(ws)) for ws in workspaces]

    return find_project_opencode_configs(Path.cwd(), max_depth=max_depth)


def selected_vendors(mcp: McpManifest, explicit: list[str] | None) -> list[str]:
    if explicit:
        return list(dict.fromkeys(explicit))
    return mcp.vendors


def install_manifest(
    mcp: McpManifest,
    *,
    vendors: list[str],
    scope: str,
    home: Path,
    workspaces: list[str],
    max_depth: int,
    dry_run: bool,
) -> None:
    print(f"\n[MCP] install {mcp.name} from {mcp.source}")
    for vendor in vendors:
        if vendor == "roo":
            paths = roo_config_paths(scope, home=home, workspaces=workspaces, max_depth=max_depth)
            if not paths:
                print("[WARN] no Roo .roo directories found for project scope")
            for path in paths:
                install_roo_config(path, mcp, dry_run=dry_run)
        elif vendor == "opencode":
            paths = opencode_config_paths(scope, home=home, workspaces=workspaces, max_depth=max_depth)
            if scope == "project" and not paths:
                print("[WARN] no OpenCode workspaces found for project scope")
            for path in paths:
                install_opencode_config(path, mcp, dry_run=dry_run)
        else:
            install_cli_vendor(vendor, mcp, scope=scope, dry_run=dry_run)


def uninstall_manifest(
    mcp: McpManifest,
    *,
    vendors: list[str],
    scope: str,
    home: Path,
    workspaces: list[str],
    max_depth: int,
    dry_run: bool,
) -> None:
    print(f"\n[MCP] uninstall {mcp.name} from {mcp.source}")
    for vendor in vendors:
        if vendor == "roo":
            for path in roo_config_paths(scope, home=home, workspaces=workspaces, max_depth=max_depth):
                uninstall_roo_config(path, mcp.name, dry_run=dry_run)
        elif vendor == "opencode":
            for path in opencode_config_paths(scope, home=home, workspaces=workspaces, max_depth=max_depth):
                uninstall_opencode_config(path, mcp.name, dry_run=dry_run)
        else:
            uninstall_cli_vendor(vendor, mcp.name, scope=scope, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests", nargs="*", help="MCP manifest paths or names under ~/.agents/mcps")
    parser.add_argument("--all", action="store_true", help="Install every *.json manifest under the manifest root")
    parser.add_argument("--manifest-root", default=str(default_manifest_root()), help="Manifest directory")
    parser.add_argument("--scope", choices=["user", "project"], default="user", help="Installation scope")
    parser.add_argument("--vendor", action="append", choices=VENDORS, help="Target vendor; repeatable")
    parser.add_argument("--workspace", action="append", default=[], help="Project workspace root for Roo project scope")
    parser.add_argument("--max-depth", type=int, default=6, help="Project scan depth for Roo .roo directories")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without modifying anything")
    parser.add_argument("--uninstall", action="store_true", help="Remove configured MCPs instead of installing")
    args = parser.parse_args(argv)

    home = Path.home()
    manifest_root = Path(args.manifest_root).expanduser()

    try:
        sources = resolve_manifest_sources(
            args.manifests,
            manifest_root=manifest_root,
            all_manifests=args.all,
        )
        manifests = [load_manifest(path, home=home) for path in sources]
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    failed = 0
    for mcp in manifests:
        vendors = selected_vendors(mcp, args.vendor)
        try:
            if args.uninstall:
                uninstall_manifest(
                    mcp,
                    vendors=vendors,
                    scope=args.scope,
                    home=home,
                    workspaces=args.workspace,
                    max_depth=args.max_depth,
                    dry_run=args.dry_run,
                )
            else:
                install_manifest(
                    mcp,
                    vendors=vendors,
                    scope=args.scope,
                    home=home,
                    workspaces=args.workspace,
                    max_depth=args.max_depth,
                    dry_run=args.dry_run,
                )
        except Exception as e:
            failed += 1
            print(f"[ERROR] {mcp.name}: {e}", file=sys.stderr)

    print(f"\n[SUMMARY] ok={len(manifests) - failed}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
