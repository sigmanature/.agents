#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
功能：
  1) 统一以 ~/.agents/AGENTS.md 作为“单一来源”，然后安装/适配到：
     - Roo（project）：<workspace>/AGENTS.md
     - Codex（project）：<workspace>/AGENTS.md
     - Codex（user）：~/.codex/AGENTS.md
     - Claude（project）：<workspace>/CLAUDE.md  (内容为 @AGENTS.md + 可追加说明)
     - Claude（user）：~/.claude/CLAUDE.md      (内容为 @~/.agents/AGENTS.md)
     - Roo（user）：~/.roo/rules/00-AGENTS.md   (作为全局 rules 文件注入)

  2) project 范围：递归扫描当前目录树，找到 .roo/.claude/.codex 这些“厂商目录”，
     以它们的父目录作为 workspace root，在该 root 写入/链接 AGENTS.md 与 CLAUDE.md。
  3) user 范围：对 ~/.roo ~/.claude ~/.codex 进行安装（如目录存在则安装）。
  4) 支持卸载：仅删除“由本脚本管理”的文件/软链（尽量避免误删你手写的文件）。

用法：
  # project（非交互）
  python3 install_agents.py -p
  python3 install_agents.py -p --force
  python3 install_agents.py -p --uninstall

  # user（非交互）
  python3 install_agents.py -u
  python3 install_agents.py -u --force
  python3 install_agents.py -u --uninstall

  # 指定一个来源文件（会复制到 ~/.agents/AGENTS.md，再统一安装）
  python3 install_agents.py /path/to/AGENTS.md -p --force
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

# 你要“先适配”的三个厂商目录（project 扫描时用）
PROJECT_VENDOR_DIRS = [
    ".roo",     # Roo Code（project-local）
    ".claude",  # Claude Code（project scope）
    ".codex",   # Codex CLI（project override）
]

# user scope 下会用到的厂商根目录（存在才安装）
USER_VENDOR_DIRS = [
    ".roo",
    ".claude",
    ".codex",
]

PRUNE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".idea", ".vscode", ".DS_Store",
}

MANAGED_MARKER = "Managed by install_agents.py"


def safe_unlink(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except FileNotFoundError:
        return


def ensure_symlink(link_path: Path, target: Path, force: bool) -> str:
    """
    创建 link_path -> target 的软链接（优先相对链接）。
    """
    if link_path.exists() or link_path.is_symlink():
        if link_path.is_symlink():
            try:
                if link_path.resolve() == target.resolve():
                    return "skip (already linked)"
            except Exception:
                pass

        if not force:
            return "skip (exists; use --force to replace)"

        # force 替换
        if link_path.is_dir() and (not link_path.is_symlink()):
            shutil.rmtree(link_path)
        else:
            safe_unlink(link_path)

    link_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        rel = os.path.relpath(target, start=link_path.parent)
        os.symlink(rel, link_path)
    except Exception:
        os.symlink(str(target), link_path)

    return "linked"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_managed_text_file(path: Path, content: str, force: bool) -> str:
    """
    创建/覆盖一个“脚本管理”的文本文件。
    - 若已存在且不 force：
        - 如果包含 MANAGED_MARKER：对比内容相同则 skip，否则 skip（提示用 --force）
        - 如果不包含 MANAGED_MARKER：skip（避免误覆盖用户手写）
    """
    if path.exists():
        try:
            existing = read_text(path)
        except Exception:
            existing = ""

        if MANAGED_MARKER in existing:
            if existing == content:
                return "skip (already up-to-date)"
            if not force:
                return "skip (managed but different; use --force to replace)"
        else:
            if not force:
                return "skip (user file exists; use --force to replace)"

    write_text(path, content)
    return "written"


def validate_agents_md(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"AGENTS.md 不存在：{path}")
    if not path.is_file():
        raise ValueError(f"不是文件：{path}")


def canonicalize_source(src: Path, canonical: Path, force: bool) -> Path:
    """
    统一来源到 ~/.agents/AGENTS.md（默认复制，不删除原文件）。
    """
    src = src.expanduser()
    canonical = canonical.expanduser()

    # 已经是 canonical
    try:
        if src.resolve() == canonical.resolve():
            validate_agents_md(canonical)
            return canonical
    except Exception:
        pass

    validate_agents_md(src)

    canonical.parent.mkdir(parents=True, exist_ok=True)

    if canonical.exists() and not force:
        # 不强制就不覆盖
        return canonical

    shutil.copy2(str(src), str(canonical))
    return canonical


def find_project_workspaces(root: Path, vendor_names: set[str], max_depth: int) -> dict[Path, set[str]]:
    """
    扫描当前目录树，发现 .roo/.claude/.codex 目录后，
    以其父目录作为 workspace root，聚合这个 workspace 命中的 vendor。
    """
    root = root.resolve()
    workspaces: dict[Path, set[str]] = {}

    for dirpath, dirnames, _ in os.walk(root):
        p = Path(dirpath)

        # 深度限制
        try:
            depth = len(p.relative_to(root).parts)
        except Exception:
            depth = 0
        if depth > max_depth:
            dirnames[:] = []
            continue

        # 剪枝
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]

        for d in list(dirnames):
            if d in vendor_names:
                vd = (p / d).resolve()
                ws = vd.parent.resolve()
                workspaces.setdefault(ws, set()).add(d)

    return dict(sorted(workspaces.items(), key=lambda kv: str(kv[0])))


def is_symlink_to(path: Path, target: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        return path.resolve() == target.resolve()
    except Exception:
        return False


def uninstall_project(ws: Path, canonical_agents: Path) -> list[str]:
    logs: list[str] = []

    # 1) AGENTS.md（只删指向 canonical 的软链）
    agents_link = ws / "AGENTS.md"
    if is_symlink_to(agents_link, canonical_agents):
        safe_unlink(agents_link)
        logs.append(f"- {ws}: removed AGENTS.md symlink")
    else:
        logs.append(f"- {ws}: keep AGENTS.md (not our symlink)")

    # 2) CLAUDE.md（只删包含 marker 的“托管文件”）
    claude_md = ws / "CLAUDE.md"
    if claude_md.exists() and claude_md.is_file():
        try:
            txt = read_text(claude_md)
        except Exception:
            txt = ""
        if MANAGED_MARKER in txt:
            safe_unlink(claude_md)
            logs.append(f"- {ws}: removed managed CLAUDE.md")
        else:
            logs.append(f"- {ws}: keep CLAUDE.md (user file)")

    # 3) .claude/CLAUDE.md（有些人放在这里；同样只删 marker）
    claude_md_alt = ws / ".claude" / "CLAUDE.md"
    if claude_md_alt.exists() and claude_md_alt.is_file():
        try:
            txt = read_text(claude_md_alt)
        except Exception:
            txt = ""
        if MANAGED_MARKER in txt:
            safe_unlink(claude_md_alt)
            logs.append(f"- {ws}: removed managed .claude/CLAUDE.md")
        else:
            logs.append(f"- {ws}: keep .claude/CLAUDE.md (user file)")

    return logs


def uninstall_user(canonical_agents: Path) -> list[str]:
    logs: list[str] = []

    # Codex: ~/.codex/AGENTS.md
    codex_agents = Path.home() / ".codex" / "AGENTS.md"
    if is_symlink_to(codex_agents, canonical_agents):
        safe_unlink(codex_agents)
        logs.append("- removed ~/.codex/AGENTS.md symlink")
    else:
        logs.append("- keep ~/.codex/AGENTS.md (not our symlink / missing)")

    # Roo: ~/.roo/rules/00-AGENTS.md
    roo_agents = Path.home() / ".roo" / "rules" / "00-AGENTS.md"
    if is_symlink_to(roo_agents, canonical_agents):
        safe_unlink(roo_agents)
        logs.append("- removed ~/.roo/rules/00-AGENTS.md symlink")
    else:
        logs.append("- keep ~/.roo/rules/00-AGENTS.md (not our symlink / missing)")

    # Claude: ~/.claude/CLAUDE.md（只删 marker）
    claude_global = Path.home() / ".claude" / "CLAUDE.md"
    if claude_global.exists() and claude_global.is_file():
        try:
            txt = read_text(claude_global)
        except Exception:
            txt = ""
        if MANAGED_MARKER in txt:
            safe_unlink(claude_global)
            logs.append("- removed managed ~/.claude/CLAUDE.md")
        else:
            logs.append("- keep ~/.claude/CLAUDE.md (user file)")
    else:
        logs.append("- ~/.claude/CLAUDE.md not found")

    return logs


def install_project(ws: Path, canonical_agents: Path, force: bool) -> list[str]:
    logs: list[str] = []

    # 1) 统一：<workspace>/AGENTS.md 软链到 canonical
    status_agents = ensure_symlink(ws / "AGENTS.md", canonical_agents, force=force)
    logs.append(f"- {ws}/AGENTS.md: {status_agents}")

    # 2) Claude：生成一个 CLAUDE.md wrapper：@AGENTS.md
    #    官方建议：Claude 读 CLAUDE.md，不读 AGENTS.md，因此用 import 复用同一份规则。
    claude_wrapper = (
        f"<!-- {MANAGED_MARKER}; edit {canonical_agents} -->\n"
        f"@AGENTS.md\n\n"
        f"## Claude Code\n"
        f"- 如果需要 Claude 专属规则，可以追加写在本段下面（此文件会随会话加载）。\n"
    )

    # Claude 支持两种 project 位置：./CLAUDE.md 或 ./.claude/CLAUDE.md
    # 这里默认用 ./CLAUDE.md（更通用）；如果你更喜欢放 .claude/ 里，改下面 target 即可。
    target_claude = ws / "CLAUDE.md"
    status_claude = ensure_managed_text_file(target_claude, claude_wrapper, force=force)
    logs.append(f"- {target_claude}: {status_claude}")

    return logs


def install_user(canonical_agents: Path, force: bool) -> list[str]:
    logs: list[str] = []

    # Codex: ~/.codex/AGENTS.md
    codex_home = Path.home() / ".codex"
    if codex_home.exists() or force:
        codex_home.mkdir(parents=True, exist_ok=True)
        status = ensure_symlink(codex_home / "AGENTS.md", canonical_agents, force=force)
        logs.append(f"- ~/.codex/AGENTS.md: {status}")
    else:
        logs.append("- ~/.codex not found, skip")

    # Roo: ~/.roo/rules/00-AGENTS.md（全局 rules 注入）
    roo_home = Path.home() / ".roo"
    if roo_home.exists() or force:
        rules_dir = roo_home / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        status = ensure_symlink(rules_dir / "00-AGENTS.md", canonical_agents, force=force)
        logs.append(f"- ~/.roo/rules/00-AGENTS.md: {status}")
    else:
        logs.append("- ~/.roo not found, skip")

    # Claude: ~/.claude/CLAUDE.md（import home 的 AGENTS.md）
    claude_home = Path.home() / ".claude"
    if claude_home.exists() or force:
        claude_home.mkdir(parents=True, exist_ok=True)
        wrapper = (
            f"<!-- {MANAGED_MARKER}; edit {canonical_agents} -->\n"
            f"@{canonical_agents}\n\n"
            f"## Claude Code\n"
            f"- 如果需要 Claude 全局偏好（格式/工具/工作流），可追加写在本段下面。\n"
        )
        status = ensure_managed_text_file(claude_home / "CLAUDE.md", wrapper, force=force)
        logs.append(f"- ~/.claude/CLAUDE.md: {status}")
    else:
        logs.append("- ~/.claude not found, skip")

    return logs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="可选：外部 AGENTS.md 路径（会复制到 ~/.agents/AGENTS.md）")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("-p", "--project", action="store_true", help="project 范围（扫描当前目录树）")
    g.add_argument("-u", "--user", action="store_true", help="user 范围（安装到家目录厂商配置）")
    parser.add_argument("--scope", choices=["project", "user"], help="兼容参数：project 或 user（可不用）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在目标/链接")
    parser.add_argument("--max-depth", type=int, default=6, help="project 级递归搜索最大深度(默认 6)")
    parser.add_argument("--uninstall", action="store_true", help="卸载（尽量只删除脚本托管的文件/软链）")
    args = parser.parse_args()

    # scope：非交互；默认 project
    if args.project:
        scope = "project"
    elif args.user:
        scope = "user"
    elif args.scope:
        scope = args.scope
    else:
        scope = "project"

    canonical_agents = Path.home() / ".agents" / "AGENTS.md"

    # 确保 canonical 来源存在/更新
    if args.path:
        try:
            canonical_agents = canonicalize_source(Path(args.path), canonical_agents, force=args.force)
        except Exception as e:
            print(f"[ERROR] 处理来源 AGENTS.md 失败：{e}", file=sys.stderr)
            return 1
    else:
        # 用户没传 path，就要求 canonical 本身存在
        try:
            validate_agents_md(canonical_agents)
        except Exception as e:
            print(f"[ERROR] 未找到默认来源 {canonical_agents}：{e}", file=sys.stderr)
            print("        你可以：python3 install_agents.py /path/to/AGENTS.md -p", file=sys.stderr)
            return 1

    if args.uninstall:
        if scope == "user":
            print("[UNINSTALL] user scope")
            for line in uninstall_user(canonical_agents):
                print(line)
            return 0

        print("[UNINSTALL] project scope")
        workspaces = find_project_workspaces(Path.cwd(), set(PROJECT_VENDOR_DIRS), max_depth=args.max_depth)
        if not workspaces:
            print("[WARN] 未在当前目录树中找到任何 .roo/.claude/.codex，无法定位 workspace")
            return 0

        for ws in workspaces.keys():
            for line in uninstall_project(ws, canonical_agents):
                print(line)
        return 0

    # 安装
    if scope == "user":
        print("[INSTALL] user scope")
        for line in install_user(canonical_agents, force=args.force):
            print(line)
        return 0

    print("[INSTALL] project scope")
    workspaces = find_project_workspaces(Path.cwd(), set(PROJECT_VENDOR_DIRS), max_depth=args.max_depth)
    if not workspaces:
        print("[WARN] 未在当前目录树中找到任何 .roo/.claude/.codex，无法定位 workspace")
        print("       你可以先在项目根目录创建对应目录（例如 .claude/ 或 .roo/ 或 .codex/）再运行。")
        return 1

    for ws, vendors in workspaces.items():
        print(f"\n[WS] {ws}  vendors={sorted(vendors)}")
        for line in install_project(ws, canonical_agents, force=args.force):
            print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
