#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
install_hooks.py —— 安装 hook/plugin 目录到 ~/.agents/hooks/，并在各厂商目录创建 symlink。

对标 install_skills.py / install_mcps.py 的统一管理策略：
  本体统一存放在 ~/.agents/hooks/<hook_name>/
  通过 symlink 分发到各厂商的 plugins/ 目录下

用法：
  python3 install_hooks.py /path/to/hook_dir                          # 安装单个 hook
  python3 install_hooks.py /path/to/hook_dir --scope user --all-vendors
  python3 install_hooks.py /path/to/hook_dir --vendor opencode --vendor claude
  python3 install_hooks.py /path/to/hook_dir --scope project
  python3 install_hooks.py /path/to/hook_dir --uninstall
  python3 install_hooks.py --relink --all-vendors                     # 重建所有已有 hook 的 symlink
  python3 install_hooks.py --relink --vendor opencode                 # 只重建 opencode 的
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
from pathlib import Path


# ── 厂商 hooks/plugins 目录映射 ────────────────────────────────────────────
#
# 用户级：各厂商的 plugins 目录（hook 以 symlink 方式挂载在此）
# opencode 的路径特殊：~/.config/opencode/plugins/，不是 ~/.opencode/plugins/

def user_plugin_dir(vendor: str) -> Path:
    """返回某厂商的用户级 plugins 目录。"""
    home = Path.home()
    if vendor == "opencode":
        return home / ".config" / "opencode" / "plugins"
    return home / f".{vendor}" / "plugins"


# 需要对路径做特殊处理的厂商（key=vendor, value=相对于 .<vendor> 的子路径）
VENDOR_PLUGIN_SUBDIR: dict[str, str] = {
    "opencode": ".config/opencode/plugins",
}

ALL_VENDORS = ("opencode", "claude", "codex", "roo")

# project 递归扫描时跳过的目录
PRUNE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".idea", ".vscode", ".DS_Store",
}

# project scope 下需要扫描的 vendor 目录名
PROJECT_VENDOR_DIR_NAMES: dict[str, str] = {
    "opencode": ".opencode",
    "claude": ".claude",
    "codex": ".codex",
    "roo": ".roo",
}


# ── 工具函数 ───────────────────────────────────────────────────────────────

def ask_choice(prompt: str, choices: dict[str, str]) -> str:
    keys = "/".join(choices)
    while True:
        ans = input(f"{prompt} ({keys}): ").strip().lower()
        if ans in choices:
            return ans
        valid = ", ".join(f"{k}={v}" for k, v in choices.items())
        print(f"请输入有效选项：{valid}")


def safe_rmtree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def has_hook_marker(hook_dir: Path) -> bool:
    """校验 hook 目录：必须有 index.ts。"""
    return (hook_dir / "index.ts").is_file()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# ── 安装逻辑 ───────────────────────────────────────────────────────────────

def install_one(
    hook_dir: Path,
    *,
    vendors: list[str],
    scope: str,
    force: bool,
    dry_run: bool,
    max_depth: int,
) -> tuple[int, int]:
    """
    安装单个 hook：
      1. 校验
      2. 移动到 ~/.agents/hooks/<name>（如果不在那里）
      3. 在指定厂商的 plugins/ 目录下创建 symlink
    返回 (linked, skipped)
    """
    hook_dir = hook_dir.expanduser().resolve()

    if not hook_dir.exists():
        raise FileNotFoundError(f"源目录不存在：{hook_dir}")
    if not hook_dir.is_dir():
        raise ValueError(f"源路径不是目录：{hook_dir}")
    if not has_hook_marker(hook_dir):
        raise FileNotFoundError(f"校验失败：{hook_dir} 顶层未找到 index.ts")

    agent_root = Path.home() / ".agents" / "hooks"
    agent_root.mkdir(parents=True, exist_ok=True)

    name = hook_dir.name
    target = agent_root / name

    # 如果 hook 已经在 ~/.agents/hooks/ 下，直接使用
    try:
        if hook_dir.resolve() == target.resolve():
            pass  # 已经在目标位置
        else:
            if target.exists():
                if not force:
                    raise FileExistsError(f"目标已存在：{target}（可用 --force 覆盖）")
                safe_rmtree(target)
            if not dry_run:
                shutil.move(str(hook_dir), str(target))
            print(f"[MOVE] {hook_dir} → {target}")
    except Exception as e:
        raise RuntimeError(f"移动 hook 失败：{e}") from e

    target = target.resolve()
    print(f"[OK] Hook 本体：{target}")

    linked, skipped = 0, 0

    for vendor in vendors:
        if scope == "user":
            plugin_dirs = [user_plugin_dir(vendor)]
        else:
            plugin_dirs = find_project_vendor_plugin_dirs(Path.cwd(), vendor, max_depth)

        if not plugin_dirs:
            print(f"  [{vendor}] {scope} scope 未找到 plugins 目录，跳过")
            skipped += 1
            continue

        for plugin_dir in plugin_dirs:
            ensure_dir(plugin_dir)
            link_path = plugin_dir / name

            if dry_run:
                print(f"  [DRY-RUN] ln -s {target} {link_path}")
                linked += 1
                continue

            status = ensure_symlink(link_path, target, force=force)
            print(f"  [{vendor}] {link_path}: {status}")
            if status == "linked":
                linked += 1
            else:
                skipped += 1

    return linked, skipped


def ensure_symlink(link_path: Path, target: Path, force: bool) -> str:
    """创建 symlink，返回状态字符串。"""
    # 已存在且指向正确目标
    if link_path.is_symlink():
        try:
            if link_path.resolve() == target.resolve():
                return "skip (already linked)"
        except Exception:
            pass

    # 存在但不是正确链接
    if link_path.exists() or link_path.is_symlink():
        if not force:
            return "skip (exists; use --force to replace)"
        safe_rmtree(link_path)

    try:
        rel = os.path.relpath(target, start=link_path.parent)
        os.symlink(rel, link_path)
    except Exception:
        os.symlink(str(target), link_path)

    return "linked"


# ── project scope 扫描 ─────────────────────────────────────────────────────

def find_project_vendor_plugin_dirs(
    root: Path,
    vendor: str,
    max_depth: int,
) -> list[Path]:
    """扫描项目树，找到某厂商的 plugins 目录。"""
    root = root.resolve()
    dir_name = PROJECT_VENDOR_DIR_NAMES.get(vendor, f".{vendor}")
    found: set[Path] = set()

    for dirpath, dirnames, _files in os.walk(root):
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(root).parts)
        except ValueError:
            depth = 0
        if depth > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        if dir_name in dirnames:
            vendor_dir = (current / dir_name).resolve()
            plugin_dir = vendor_dir / "plugins"
            found.add(plugin_dir)

    return sorted(found)


# ── 卸载逻辑 ───────────────────────────────────────────────────────────────

def uninstall_one(
    name: str,
    *,
    vendors: list[str],
    scope: str,
    dry_run: bool,
    max_depth: int,
) -> tuple[int, int]:
    """卸载：删除 ~/.agents/hooks/<name> 及所有 symlink。"""
    agent_root = Path.home() / ".agents" / "hooks"
    hook_dir = agent_root / name

    removed, skipped = 0, 0

    if hook_dir.exists():
        if dry_run:
            print(f"[DRY-RUN] rm -rf {hook_dir}")
        else:
            safe_rmtree(hook_dir)
            print(f"[RM] {hook_dir}")
        removed += 1
    else:
        print(f"[WARN] 本体不存在：{hook_dir}")
        skipped += 1

    for vendor in vendors:
        if scope == "user":
            plugin_dirs = [user_plugin_dir(vendor)]
        else:
            plugin_dirs = find_project_vendor_plugin_dirs(Path.cwd(), vendor, max_depth)

        if not plugin_dirs:
            continue

        for plugin_dir in plugin_dirs:
            link_path = plugin_dir / name
            if link_path.is_symlink():
                if dry_run:
                    print(f"[DRY-RUN] unlink {link_path}")
                else:
                    link_path.unlink()
                    print(f"[UNLINK] {link_path}")
                removed += 1
            elif link_path.exists():
                print(f"[WARN] {link_path} 存在但不是 symlink，跳过")
                skipped += 1

    return removed, skipped


# ── relink：重建已有 hook 的 symlink ────────────────────────────────────────

def relink_all(
    *,
    vendors: list[str],
    scope: str,
    force: bool,
    dry_run: bool,
    max_depth: int,
) -> tuple[int, int]:
    """扫描 ~/.agents/hooks/ 下所有已有 hook，重建各厂商 symlink。"""
    agent_root = Path.home() / ".agents" / "hooks"
    if not agent_root.is_dir():
        print("[WARN] ~/.agents/hooks/ 目录不存在")
        return 0, 0

    hook_dirs = sorted(
        [d for d in agent_root.iterdir() if d.is_dir() and has_hook_marker(d)],
        key=lambda d: d.name,
    )

    if not hook_dirs:
        print("[INFO] 没有找到已安装的 hook")
        return 0, 0

    total_linked, total_skipped = 0, 0
    for d in hook_dirs:
        name = d.name
        print(f"\n[{name}]")
        for vendor in vendors:
            if scope == "user":
                plugin_dirs = [user_plugin_dir(vendor)]
            else:
                plugin_dirs = find_project_vendor_plugin_dirs(Path.cwd(), vendor, max_depth)

            if not plugin_dirs:
                print(f"  [{vendor}] 未找到 plugins 目录")
                total_skipped += 1
                continue

            for plugin_dir in plugin_dirs:
                ensure_dir(plugin_dir)
                link_path = plugin_dir / name
                if dry_run:
                    print(f"  [DRY-RUN] ln -sf {d.resolve()} {link_path}")
                    total_linked += 1
                else:
                    status = ensure_symlink(link_path, d.resolve(), force=True)
                    print(f"  [{vendor}] {link_path}: {status}")
                    if status == "linked":
                        total_linked += 1
                    else:
                        total_skipped += 1

    return total_linked, total_skipped


# ── 入口 ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="安装 hook/plugin 到 ~/.agents/hooks/，并 symlink 到各厂商 plugins/"
    )
    parser.add_argument(
        "path", nargs="?",
        help="Hook 目录路径（必须有 index.ts）。与 --relink / --uninstall 互斥。"
    )
    parser.add_argument(
        "--scope", choices=["user", "project"], default=None,
        help="安装范围：user（~/下各厂商目录）或 project（扫描当前项目树）"
    )
    parser.add_argument(
        "--vendor", action="append", choices=ALL_VENDORS, default=[],
        help="目标厂商，可重复指定。默认 opencode"
    )
    parser.add_argument(
        "--all-vendors", action="store_true",
        help="安装到所有支持的厂商"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="覆盖已存在的目标/链接"
    )
    parser.add_argument(
        "--max-depth", type=int, default=6,
        help="project 级递归扫描最大深度（默认 6）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅打印操作，不实际执行"
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="卸载 hook"
    )
    parser.add_argument(
        "--relink", action="store_true",
        help="重建 ~/.agents/hooks/ 下所有已有 hook 的厂商 symlink（无需指定 path）"
    )

    args = parser.parse_args(argv)

    # 确定厂商列表
    vendors = list(args.vendor) if args.vendor else []
    if args.all_vendors:
        vendors = list(ALL_VENDORS)
    if not vendors:
        vendors = ["opencode"]

    # 确定 scope
    scope = args.scope
    if scope is None:
        ans = ask_choice(
            "选择安装范围",
            {"p": "project（扫描当前项目树）", "u": "user（~/下各厂商目录）"}
        )
        scope = "project" if ans == "p" else "user"

    # ── relink 模式 ──
    if args.relink:
        linked, skipped = relink_all(
            vendors=vendors, scope=scope, force=args.force,
            dry_run=args.dry_run, max_depth=args.max_depth,
        )
        print(f"\n[SUMMARY] linked={linked}, skipped={skipped}")
        return 0

    # ── uninstall 模式 ──
    if args.uninstall:
        if not args.path:
            parser.error("--uninstall 需要指定 hook 目录路径或名称")
        name = Path(args.path).expanduser().name
        removed, skipped = uninstall_one(
            name, vendors=vendors, scope=scope,
            dry_run=args.dry_run, max_depth=args.max_depth,
        )
        print(f"\n[SUMMARY] removed={removed}, skipped={skipped}")
        return 0 if removed > 0 else 1

    # ── install 模式 ──
    if not args.path:
        parser.error("需要指定 hook 目录路径")

    raw = os.path.expanduser(args.path.strip())
    if any(ch in raw for ch in ["*", "?", "["]):
        sources = [Path(p) for p in glob.glob(raw) if Path(p).is_dir()]
    else:
        sources = [Path(raw)]

    if not sources:
        print(f"[ERROR] 未找到匹配的目录：{args.path}", file=sys.stderr)
        return 1

    total_linked, total_skipped, failed = 0, 0, 0
    for src in sources:
        try:
            print(f"\n{'=' * 60}")
            linked, skipped = install_one(
                src, vendors=vendors, scope=scope,
                force=args.force, dry_run=args.dry_run,
                max_depth=args.max_depth,
            )
            total_linked += linked
            total_skipped += skipped
        except Exception as e:
            failed += 1
            print(f"[ERROR] {src}: {e}", file=sys.stderr)

    print(f"\n{'=' * 60}")
    print(f"[SUMMARY] linked={total_linked}, skipped={total_skipped}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
