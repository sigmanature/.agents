#!/usr/bin/env python3
# -*- coding: utf-8 -*-


"""
功能：
  1. 安装技能：将一个 skill 目录集中安装到 ~/.agents/skills，并在各厂商 agent 目录下创建 skills/<同名软链接>。
  2. 卸载技能：删除已安装的技能及其软链接。

用法：
  python3 install_skill.py /path/to/skill_dir
  python3 install_skill.py /path/to/skill_dir --scope project
  python3 install_skill.py /path/to/skill_dir --scope user
  python3 install_skill.py /path/to/skill_dir --force
  python3 install_skill.py /path/to/skill_dir --uninstall
  python3 install_skill.py "/path/to/skills/*"
"""

import argparse
import glob
import os
import shutil
import sys
from pathlib import Path

# 你可以按需扩展/删减
PROJECT_VENDOR_DIRS = [
    ".claude",      # Claude Code（project scope）
    ".continue",    # Continue（repo 内）
    ".cursor",      # Cursor（repo 内）
    ".windsurf",    # Windsurf（workspace/repo 内）
    ".codex",       # Codex CLI（project override）
    ".openhands",   # OpenHands（repo 内）
    ".roo",         # Roo Code（project-local）
]

USER_VENDOR_DIRS = [
    ".claude",      # Claude Code（user scope）
    ".codex",       # Codex CLI（user defaults）
    ".roo",         # Roo Code（global）
    ".cline",       # 可选：如果你把 Cline CLI 配置放这里
    ".tabby",       # 可选
    ".ollama",      # 可选
]

# project 递归扫描时跳过的重目录
PRUNE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".idea", ".vscode", ".DS_Store",
}


def ask_choice(prompt: str, choices: dict) -> str:
    keys = "/".join(choices.keys())
    while True:
        ans = input(f"{prompt} ({keys}): ").strip().lower()
        if ans in choices:
            return ans
        print("请输入有效选项：", ", ".join([f"{k}={v}" for k, v in choices.items()]))


def safe_rmtree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def has_glob_chars(s: str) -> bool:
    return any(ch in s for ch in ["*", "?", "["])


def expand_skill_sources(raw: str) -> list[Path]:
    """
    支持：
      - 普通路径：/a/b/skill
      - 通配符：/a/b/*（匹配多个目录）
    返回：已去重、排序后的目录 Path 列表
    """
    expanded = os.path.expanduser(raw.strip())
    if has_glob_chars(expanded):
        matches = [Path(p) for p in glob.glob(expanded)]
        matches = [p for p in matches if p.is_dir()]
        if not matches:
            raise FileNotFoundError(f"通配符未匹配到任何目录：{raw}")
        # resolve 去重
        uniq = {}
        for p in matches:
            try:
                uniq[str(p.resolve())] = p.resolve()
            except Exception:
                uniq[str(p)] = p
        return sorted(uniq.values(), key=lambda x: x.name.lower())
    else:
        return [Path(expanded).expanduser()]


def validate_skill_dir(skill_dir: Path) -> None:
    """
    校验 skill 目录顶层是否存在 SKILL.md（兼容 skill.md）。
    """
    if not skill_dir.exists():
        raise FileNotFoundError(f"源目录不存在：{skill_dir}")
    if not skill_dir.is_dir():
        raise ValueError(f"源路径不是目录：{skill_dir}")

    p_upper = skill_dir / "SKILL.md"
    p_lower = skill_dir / "skill.md"

    if p_upper.is_file():
        return

    if p_lower.is_file():
        # 兼容，但给个提示
        print(f"[WARN] {skill_dir} 顶层没有 SKILL.md，但存在 skill.md（已兼容通过）")
        return

    raise FileNotFoundError(f"校验失败：{skill_dir} 顶层未找到 SKILL.md（或 skill.md）")


def move_skill_dir(src: Path, dest_root: Path, force: bool) -> Path:
    if not src.exists():
        raise FileNotFoundError(f"源目录不存在：{src}")
    if not src.is_dir():
        raise ValueError(f"源路径不是目录：{src}")

    dest_root.mkdir(parents=True, exist_ok=True)
    dst = dest_root / src.name

    # 已经在目标位置就不移动
    try:
        if src.resolve() == dst.resolve():
            return dst
    except Exception:
        pass

    if dst.exists():
        if not force:
            raise FileExistsError(f"目标已存在：{dst}（可用 --force 覆盖）")
        safe_rmtree(dst)

    shutil.move(str(src), str(dst))
    return dst


def ensure_symlink(link_path: Path, target: Path, force: bool) -> str:
    # 已存在：如果是正确链接则跳过，否则根据 force 决定是否替换
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
        if link_path.exists() or link_path.is_symlink():
            if link_path.is_dir() and (not link_path.is_symlink()):
                shutil.rmtree(link_path)
            else:
                try:
                    link_path.unlink()
                except FileNotFoundError:
                    pass

    # 优先创建相对链接（更便携）
    try:
        rel = os.path.relpath(target, start=link_path.parent)
        os.symlink(rel, link_path)
    except Exception:
        os.symlink(str(target), link_path)

    return "linked"


def find_project_vendor_dirs(root: Path, names: set[str], max_depth: int) -> list[Path]:
    root = root.resolve()
    found = set()

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

        # 剪枝：跳过重目录
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]

        # 发现 vendor 目录
        for d in dirnames:
            if d in names:
                found.add((p / d).resolve())

    return sorted(found)


def uninstall_skill(skill_name: str, vendor_dirs: list[Path], agent_root: Path) -> None:
    # 删除技能文件和链接
    skill_dir = agent_root / skill_name
    if skill_dir.exists():
        print(f"删除技能目录：{skill_dir}")
        safe_rmtree(skill_dir)
    else:
        print(f"[WARN] 未找到技能目录：{skill_name}")

    # 删除厂商目录下的软链接
    for vd in vendor_dirs:
        skills_dir = vd / "skills"
        link_path = skills_dir / skill_name
        if link_path.is_symlink():
            print(f"删除软链接：{link_path}")
            link_path.unlink()
        else:
            print(f"[WARN] 未找到软链接：{link_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="要安装的 skill 目录路径（支持通配符）")
    parser.add_argument("--scope", choices=["project", "user"], help="安装范围：project 或 user")
    parser.add_argument("--force", action="store_true", help="覆盖已存在目标/链接")
    parser.add_argument("--max-depth", type=int, default=6, help="project 级递归搜索最大深度(默认 6)")
    parser.add_argument("--uninstall", action="store_true", help="卸载技能")
    args = parser.parse_args()

    raw = args.path if args.path else input("请输入 skill 目录路径(支持通配符): ").strip()
    try:
        sources = expand_skill_sources(raw)
    except Exception as e:
        print(f"[ERROR] 解析路径失败：{e}", file=sys.stderr)
        return 1

    scope = args.scope
    if scope is None:
        c = ask_choice("选择安装范围", {"p": "project(遍历当前路径树)", "u": "user(遍历~顶层目录)"})
        scope = "project" if c == "p" else "user"

    # vendor dirs 只扫一次
    if scope == "project":
        scan_root = Path.cwd()
        vendor_names = set(PROJECT_VENDOR_DIRS)
        vendor_dirs = find_project_vendor_dirs(scan_root, vendor_names, max_depth=args.max_depth)
    else:
        scan_root = Path.home()
        vendor_names = set(USER_VENDOR_DIRS)
        vendor_dirs = []
        for n in sorted(vendor_names):
            p = (scan_root / n)
            if p.is_dir():
                vendor_dirs.append(p.resolve())

    agent_root = Path.home() / ".agents" / "skills"
    agent_root.mkdir(parents=True, exist_ok=True)

    if args.uninstall:
        # 卸载技能
        for src in sources:
            skill_name = Path(src).name
            uninstall_skill(skill_name, vendor_dirs, agent_root)
        return 0

    ok_count = 0
    fail_count = 0

    for src in sources:
        src = Path(src).expanduser()

        # ✅ 校验：顶层 SKILL.md / skill.md
        try:
            validate_skill_dir(src)
        except Exception as e:
            fail_count += 1
            print(f"\n[ERROR] 跳过 {src}：{e}", file=sys.stderr)
            continue

        # ✅ 安装（移动到 ~/.agents/skills/<skill_name>）
        try:
            installed_skill = move_skill_dir(src, agent_root, force=args.force)
        except Exception as e:
            fail_count += 1
            print(f"\n[ERROR] 移动 skill 失败（{src}）：{e}", file=sys.stderr)
            continue

        skill_name = installed_skill.name
        target = installed_skill.resolve()
        print(f"\n[OK] Skill 已安装到: {target}")

        # ✅ 建立 vendor 软链
        if vendor_dirs:
            print(f"[INFO] 将在 {len(vendor_dirs)} 个厂商目录下创建 skills/ 并添加软链接：{skill_name}\n")
            linked = 0
            skipped = 0

            for vd in vendor_dirs:
                skills_dir = vd / "skills"
                try:
                    skills_dir.mkdir(parents=True, exist_ok=True)
                    link_path = skills_dir / skill_name
                    status = ensure_symlink(link_path, target, force=args.force)
                    if status == "linked":
                        linked += 1
                    else:
                        skipped += 1
                    print(f"- {vd} -> {link_path}: {status}")
                except PermissionError:
                    skipped += 1
                    print(f"- {vd}: skip (permission denied)")
                except OSError as e:
                    skipped += 1
                    print(f"- {vd}: skip ({e})")

            print(f"\n[DONE] {skill_name}: linked={linked}, skipped={skipped}")

        ok_count += 1

    print(f"\n[SUMMARY] ok={ok_count}, failed={fail_count}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
