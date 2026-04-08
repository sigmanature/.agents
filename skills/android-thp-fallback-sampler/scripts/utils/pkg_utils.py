from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence


def read_package_file(path_str: Optional[str]) -> List[str]:
    if not path_str:
        return []
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"package file not found: {path}")
    pkgs: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        x = line.strip()
        if not x or x.startswith("#"):
            continue
        pkgs.append(x)
    return pkgs


def unique_preserve_order(items: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(items))

