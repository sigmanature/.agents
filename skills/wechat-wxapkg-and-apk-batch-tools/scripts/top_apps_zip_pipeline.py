#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone Top apps ZIP pipeline.

This script intentionally does NOT import launcher_ui/widget_automation_test.py.
It carries over the essential logic:
- top-app to ZIP URL mapping
- arbitrary range ZIP selection by two-level index (chunk + offset)
- download ZIP(s)
- unzip selected APK subset locally
- install extracted APKs to one or two devices (parallel by device)

Outputs under --output-dir:
- downloads/                (zip cache + extracted dirs)
- install_log.jsonl         (per-device per-apk install records)
- all_packages.txt          (all apk-derived package names from selected rank range)
- installed_packages.txt    (package names installed successfully on all devices)
- failed_apks.txt           (apk filenames that failed on any device)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse
from urllib.request import urlopen


ZIP_MAPPING: Dict[int, str] = {
    50: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top50.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=hzNix%2FeXH%2BoXOZR62fuuV3vsG58%3D",
    100: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top51_100.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=fwQPriy6bPSrliuDnnWWlP%2FhndE%3D",
    200: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top101_200.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=5hTgyJghMx%2F0NH0RFUPJk8Yidn4%3D",
    300: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top201_300.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=6MtnIbl%2FwM37MFoMKyO3JM7zhQo%3D",
    400: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top301_400.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=t4dfgvHJ%2B8cQh%2B6qJ2IIt959oPs%3D",
    500: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top401_500.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=04znxw3O1Ey0XY0sVFjIxSlI0bU%3D",
    700: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top501_700.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=u6gqAIKdbAvR7pmBH94hS4T8AvY%3D",
    900: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top701_900.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=qnB%2FZKkJ4ajtygi1cNKwoILAawg%3D",
    1000: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top901_1000.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=VU%2BHcbqlLi3gTsyPyeG1x3cUAfM%3D",
    1200: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top1001_1200.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=2Yf%2BtGiGWXnIhkZmgCoSZ5JbIO8%3D",
    1500: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top1201_1500.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=g0tt7Xe8UNS2S6UAS8YuXou4HQw%3D",
    2000: "https://cnbj1-fds.api.xiaomi.net/hyper-os-rust/top1501_2000.zip?GalaxyAccessKeyId=5151729087601&Expires=9223372036854775807&Signature=kCB1KKT5P67XHEQBkI4Noq9XnZg%3D",
}

RANGE_TO_KEY = {
    (1, 50): 50,
    (51, 100): 100,
    (101, 200): 200,
    (201, 300): 300,
    (301, 400): 400,
    (401, 500): 500,
    (501, 700): 700,
    (701, 900): 900,
    (901, 1000): 1000,
    (1001, 1200): 1200,
    (1201, 1500): 1500,
    (1501, 2000): 2000,
}


@dataclass(frozen=True)
class ZipChunk:
    start: int
    end: int
    key: int
    url: str


@dataclass
class InstallRecord:
    serial: str
    apk: str
    package_name: str
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: int


def build_chunks() -> List[ZipChunk]:
    chunks: List[ZipChunk] = []
    for (start, end), key in sorted(RANGE_TO_KEY.items(), key=lambda x: x[0][0]):
        url = ZIP_MAPPING.get(key)
        if not url:
            raise ValueError(f"missing zip url for key={key}")
        chunks.append(ZipChunk(start=start, end=end, key=key, url=url))
    return chunks


ALL_CHUNKS = build_chunks()
MAX_RANK = max(c.end for c in ALL_CHUNKS)


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def default_output_dir() -> Path:
    return Path("output") / f"top_apps_pipeline_{now_ts()}"


def parse_top_app_spec(top_app: str) -> Tuple[int, int]:
    top_app = str(top_app).strip()
    if "-" in top_app:
        try:
            start, end = map(int, top_app.split("-", 1))
        except ValueError as exc:
            raise ValueError(f"区间格式错误: {top_app}，正确格式如 20-70") from exc
    else:
        try:
            end = int(top_app)
        except ValueError as exc:
            raise ValueError(f"top-app 参数错误: {top_app}，应为整数或区间") from exc
        start = 1

    if start < 1 or end < 1 or start > end:
        raise ValueError(f"无效区间: {start}-{end}")
    if end > MAX_RANK:
        raise ValueError(f"最大支持到 top{MAX_RANK}，收到: {start}-{end}")
    return start, end


def resolve_chunks_for_range(start: int, end: int) -> List[ZipChunk]:
    chunks: List[ZipChunk] = []
    for c in ALL_CHUNKS:
        if end < c.start or start > c.end:
            continue
        chunks.append(c)
    if not chunks:
        raise ValueError(f"无法为区间 {start}-{end} 找到 ZIP chunk")
    return chunks


def adb_devices() -> List[str]:
    cp = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "adb devices failed")
    out: List[str] = []
    for line in cp.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def resolve_serials(device1: Optional[str], device2: Optional[str]) -> List[str]:
    serials: List[str] = []
    if device1:
        serials.append(device1)
    if device2 and device2 not in serials:
        serials.append(device2)
    if serials:
        return serials

    found = adb_devices()
    if not found:
        raise RuntimeError("No device in adb 'device' state")
    return found


def zip_name_from_url(url: str) -> str:
    return unquote(os.path.basename(urlparse(url).path))


def download_file(url: str, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    with urlopen(url, timeout=60) as resp, tmp.open("wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(path)


def download_zips(chunks: List[ZipChunk], download_dir: Path) -> List[Tuple[ZipChunk, Path]]:
    download_dir.mkdir(parents=True, exist_ok=True)
    out: List[Tuple[ZipChunk, Path]] = []
    for c in chunks:
        name = zip_name_from_url(c.url)
        p = download_dir / name
        if p.exists() and p.stat().st_size > 0:
            size_mb = p.stat().st_size / (1024 * 1024)
            print(f"[cache-hit][zip] {name} -> {p} ({size_mb:.2f} MiB)")
        else:
            print(f"[download] {name}")
            download_file(c.url, p)
            size_mb = p.stat().st_size / (1024 * 1024)
            print(f"[download-done][zip] {name} ({size_mb:.2f} MiB)")
        out.append((c, p))
    return out


def infer_package_name(apk_path: Path) -> str:
    n = apk_path.name
    return n[:-4] if n.lower().endswith(".apk") else apk_path.stem


def install_one(serial: str, apk_path: Path, timeout_s: int) -> InstallRecord:
    t0 = time.time()
    pkg = infer_package_name(apk_path)
    try:
        cp = subprocess.run(
            ["adb", "-s", serial, "install", "-r", str(apk_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        txt = (cp.stdout or "") + "\n" + (cp.stderr or "")
        ok = cp.returncode == 0 and "Success" in txt
        return InstallRecord(
            serial=serial,
            apk=apk_path.name,
            package_name=pkg,
            ok=ok,
            returncode=cp.returncode,
            stdout=(cp.stdout or "").strip(),
            stderr=(cp.stderr or "").strip(),
            elapsed_ms=int((time.time() - t0) * 1000),
        )
    except subprocess.TimeoutExpired as e:
        return InstallRecord(
            serial=serial,
            apk=apk_path.name,
            package_name=pkg,
            ok=False,
            returncode=124,
            stdout=((e.stdout.decode("utf-8", "ignore") if isinstance(e.stdout, bytes) else (e.stdout or "")).strip()),
            stderr=((e.stderr.decode("utf-8", "ignore") if isinstance(e.stderr, bytes) else (e.stderr or "")).strip()) or f"timeout after {timeout_s}s",
            elapsed_ms=int((time.time() - t0) * 1000),
        )


def install_on_device(serial: str, apks: List[Path], timeout_s: int) -> List[InstallRecord]:
    recs: List[InstallRecord] = []
    print(f"[{serial}] install {len(apks)} apks")
    for i, apk in enumerate(apks, start=1):
        r = install_one(serial, apk, timeout_s=timeout_s)
        recs.append(r)
        status = "OK" if r.ok else "FAIL"
        msg = r.stderr or r.stdout
        print(f"[{serial}] [{i}/{len(apks)}] {status} {apk.name} {msg}")
    return recs


def select_and_extract_apks(
    chunk_zips: List[Tuple[ZipChunk, Path]],
    download_dir: Path,
    range_start: int,
    range_end: int,
    clean: str,
) -> Tuple[List[Path], List[Path]]:
    ranked_apks: List[Tuple[int, Path]] = []
    extracted_dirs: List[Path] = []

    for chunk, zpath in chunk_zips:
        base = zpath.name[:-4] if zpath.name.lower().endswith(".zip") else zpath.stem
        extract_root = download_dir / base
        extract_root.mkdir(parents=True, exist_ok=True)
        extracted_dirs.append(extract_root)

        extracted_count = 0
        cache_hit_count = 0

        with zipfile.ZipFile(zpath, "r") as zf:
            members = [m for m in zf.namelist() if m.lower().endswith(".apk") and not m.endswith("/")]
            if not members:
                print(f"[warn] no apk in {zpath.name}")
                continue

            local_start = max(range_start, chunk.start) - chunk.start
            local_end = min(range_end, chunk.end) - chunk.start
            if local_start >= len(members):
                print(f"[warn] {zpath.name} size={len(members)} but need offset {local_start}")
                continue
            local_end = min(local_end, len(members) - 1)

            print(
                f"[select] {zpath.name} chunk={chunk.start}-{chunk.end} "
                f"offset={local_start}-{local_end}"
            )

            for i in range(local_start, local_end + 1):
                member = members[i]
                rank = chunk.start + i
                out_path = extract_root / member
                if out_path.exists():
                    cache_hit_count += 1
                    print(f"[cache-hit][apk] rank={rank} {out_path}")
                else:
                    zf.extract(member, path=extract_root)
                    extracted_count += 1
                ranked_apks.append((rank, out_path))

        print(
            f"[chunk-summary] {zpath.name} selected={local_end - local_start + 1} "
            f"cache-hit={cache_hit_count} extracted={extracted_count}"
        )

        if clean == "all":
            try:
                zpath.unlink()
            except FileNotFoundError:
                pass

    ranked_apks.sort(key=lambda x: x[0])
    return [p for _, p in ranked_apks], extracted_dirs


def write_lines(path: Path, lines: List[str]) -> None:
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Top apps pipeline: download -> unzip -> adb install")
    p.add_argument("--top-app", type=str, default="100", help="100 or range like 20-70")
    p.add_argument("--device1", type=str, default=None, help="Device serial 1")
    p.add_argument("--device2", type=str, default=None, help="Device serial 2")
    p.add_argument("--output-dir", type=str, default=None, help="Output directory")
    p.add_argument("--clean", choices=["all", "folder", "none"], default="folder")
    p.add_argument("--timeout", type=int, default=180, help="adb install timeout seconds")

    args = p.parse_args(argv)

    try:
        range_start, range_end = parse_top_app_spec(args.top_app)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    serials = resolve_serials(args.device1, args.device2)
    chunks = resolve_chunks_for_range(range_start, range_end)

    out_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    download_dir = out_dir / "downloads"

    print(f"Output dir: {out_dir}")
    print(f"Devices: {', '.join(serials)}")
    print(f"Top range: {range_start}-{range_end} ({range_end - range_start + 1} apps)")
    print("ZIP chunks: " + ", ".join(f"{c.start}-{c.end}" for c in chunks))

    chunk_zips = download_zips(chunks, download_dir)
    apks, extracted_dirs = select_and_extract_apks(
        chunk_zips=chunk_zips,
        download_dir=download_dir,
        range_start=range_start,
        range_end=range_end,
        clean=args.clean,
    )

    if not apks:
        print("No APKs selected after unzip", file=sys.stderr)
        return 3

    all_pkgs = [infer_package_name(apk) for apk in apks]
    write_lines(out_dir / "all_packages.txt", all_pkgs)

    all_records: List[InstallRecord] = []
    with ThreadPoolExecutor(max_workers=max(1, len(serials))) as ex:
        futs = [ex.submit(install_on_device, s, apks, args.timeout) for s in serials]
        for fut in futs:
            all_records.extend(fut.result())

    by_apk: Dict[str, Dict[str, bool]] = {apk.name: {s: False for s in serials} for apk in apks}
    for r in all_records:
        by_apk.setdefault(r.apk, {})[r.serial] = r.ok

    installed_all: List[str] = []
    failed_apks: List[str] = []
    for apk in apks:
        pkg = infer_package_name(apk)
        ok_all = all(by_apk.get(apk.name, {}).get(s, False) for s in serials)
        if ok_all:
            installed_all.append(pkg)
        else:
            failed_apks.append(apk.name)

    (out_dir / "install_log.jsonl").write_text(
        "".join(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in all_records),
        encoding="utf-8",
    )
    write_lines(out_dir / "installed_packages.txt", installed_all)
    write_lines(out_dir / "failed_apks.txt", failed_apks)

    if args.clean in ("all", "folder"):
        for d in extracted_dirs:
            shutil.rmtree(d, ignore_errors=True)

    print(f"APKs total: {len(apks)}")
    print(f"Installed on all devices: {len(installed_all)}")
    print(f"Failed on any device: {len(failed_apks)}")
    print(f"Package list: {out_dir / 'installed_packages.txt'}")

    return 0 if not failed_apks else 1


if __name__ == "__main__":
    raise SystemExit(main())
