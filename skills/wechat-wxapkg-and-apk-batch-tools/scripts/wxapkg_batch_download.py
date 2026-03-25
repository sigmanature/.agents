#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch download WeChat mini-program packages (wxapkg) from URL list.

Inputs:
- urls file: one URL per line; allow blank lines and comments starting with '#'
- optional headers.json: a JSON object (dict) applied to all requests

Outputs (under output_dir):
- files/: downloaded binaries
- manifest.jsonl: per-URL structured result
- failed_urls.txt: URLs that failed after retries

This tool is intentionally standalone and does not depend on the UI automation code.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def load_urls(urls_path: Path) -> List[str]:
    if not urls_path.exists():
        raise FileNotFoundError(f"urls file not found: {urls_path}")

    urls: List[str] = []
    with urls_path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            urls.append(s)

    # de-dup but keep order
    seen = set()
    dedup: List[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        dedup.append(u)
    return dedup


def load_headers(headers_json_path: Optional[Path]) -> Dict[str, str]:
    if headers_json_path is None:
        return {}

    if not headers_json_path.exists():
        raise FileNotFoundError(f"headers.json not found: {headers_json_path}")

    with headers_json_path.open("r", encoding="utf-8") as f:
        obj = json.load(f)

    if not isinstance(obj, dict):
        raise ValueError("headers.json must be a JSON object (dict)")

    headers: Dict[str, str] = {}
    for k, v in obj.items():
        if v is None:
            continue
        headers[str(k)] = str(v)
    return headers


def sanitize_filename(name: str) -> str:
    # Remove query strings if accidentally included
    name = name.split("?", 1)[0].split("#", 1)[0]
    name = name.strip()

    # Avoid empty or dangerous names
    if not name or name in {".", ".."}:
        return "downloaded.bin"

    # Replace path separators and other problematic chars
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)

    # Limit length (common FS limits)
    if len(name) > 180:
        base, dot, ext = name.rpartition(".")
        if dot:
            base = base[:160]
            ext = ext[:16]
            name = f"{base}.{ext}"
        else:
            name = name[:180]

    return name


def filename_from_url(url: str) -> str:
    # Prefer last path segment
    # Example: https://host/path/a.wxapkg?token=... -> a.wxapkg
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+/(.*)$", url)
    if not m:
        return "downloaded.bin"
    path = m.group(1)
    seg = path.rsplit("/", 1)[-1]
    seg = seg.split("?", 1)[0].split("#", 1)[0]
    seg = sanitize_filename(seg)
    return seg or "downloaded.bin"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DownloadResult:
    url: str
    filename: str
    path: str
    ok: bool
    http_status: Optional[int]
    bytes: int
    sha256: Optional[str]
    error: Optional[str]
    attempt: int
    elapsed_ms: int


def _download_one(
    *,
    url: str,
    out_files_dir: Path,
    headers: Dict[str, str],
    timeout_s: int,
    max_retries: int,
    backoff_s: float,
    user_agent: Optional[str],
    skip_existing: bool,
) -> DownloadResult:
    t0 = time.time()

    filename = filename_from_url(url)
    target_path = out_files_dir / filename

    if skip_existing and target_path.exists() and target_path.is_file():
        size = target_path.stat().st_size
        digest = sha256_file(target_path)
        return DownloadResult(
            url=url,
            filename=filename,
            path=str(target_path),
            ok=True,
            http_status=None,
            bytes=size,
            sha256=digest,
            error=None,
            attempt=0,
            elapsed_ms=int((time.time() - t0) * 1000),
        )

    sess = requests.Session()
    req_headers = dict(headers)
    if user_agent:
        req_headers.setdefault("User-Agent", user_agent)

    last_err: Optional[str] = None
    last_status: Optional[int] = None

    tmp_path = target_path.with_suffix(target_path.suffix + ".part")

    for attempt in range(1, max_retries + 2):
        # attempt count includes first try; total tries = max_retries+1
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

        try:
            resp = sess.get(url, headers=req_headers, stream=True, timeout=timeout_s)
            last_status = resp.status_code

            # Treat 2xx as success
            if 200 <= resp.status_code < 300:
                _mkdir(out_files_dir)
                written = 0
                with tmp_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)

                # Atomic-ish replace
                tmp_path.replace(target_path)
                digest = sha256_file(target_path)
                return DownloadResult(
                    url=url,
                    filename=filename,
                    path=str(target_path),
                    ok=True,
                    http_status=resp.status_code,
                    bytes=written,
                    sha256=digest,
                    error=None,
                    attempt=attempt,
                    elapsed_ms=int((time.time() - t0) * 1000),
                )

            # Retry on common transient statuses
            if resp.status_code in {408, 429, 500, 502, 503, 504}:
                last_err = f"HTTP {resp.status_code}"
            else:
                # non-retryable (likely auth 401/403, not found 404, etc.)
                last_err = f"HTTP {resp.status_code}"
                break

        except requests.RequestException as e:
            last_err = f"request error: {type(e).__name__}: {e}"

        if attempt <= max_retries:
            time.sleep(backoff_s * attempt)

    # failed
    elapsed_ms = int((time.time() - t0) * 1000)
    return DownloadResult(
        url=url,
        filename=filename,
        path=str(target_path),
        ok=False,
        http_status=last_status,
        bytes=0,
        sha256=None,
        error=last_err or "unknown error",
        attempt=max_retries + 1,
        elapsed_ms=elapsed_ms,
    )


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Batch download wxapkg files from URL list")
    p.add_argument("--urls", required=True, help="Path to urls.txt (one URL per line)")
    p.add_argument(
        "--headers-json",
        default=None,
        help="Optional headers.json path (JSON object) applied to all requests",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Default: output/wxapkg_download_<timestamp>/",
    )
    p.add_argument("--workers", type=int, default=6, help="Concurrent download workers")
    p.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")
    p.add_argument("--retries", type=int, default=2, help="Retry count for transient failures")
    p.add_argument(
        "--backoff",
        type=float,
        default=1.0,
        help="Backoff seconds multiplier (sleep = backoff * attempt)",
    )
    p.add_argument(
        "--user-agent",
        default="Mozilla/5.0 (wxapkg-batch-downloader)",
        help="User-Agent header value",
    )
    p.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Do not skip existing files; always re-download",
    )

    args = p.parse_args(argv)

    urls_path = Path(args.urls)
    headers_path = Path(args.headers_json) if args.headers_json else None

    output_dir = Path(args.output_dir) if args.output_dir else Path("output") / f"wxapkg_download_{_now_ts()}"
    out_files_dir = output_dir / "files"
    _mkdir(out_files_dir)

    headers = load_headers(headers_path)
    urls = load_urls(urls_path)

    manifest_path = output_dir / "manifest.jsonl"
    failed_urls_path = output_dir / "failed_urls.txt"

    if not urls:
        print(f"No URLs found in {urls_path}", file=sys.stderr)
        write_jsonl(manifest_path, [])
        failed_urls_path.write_text("", encoding="utf-8")
        return 2

    results: List[DownloadResult] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [
            ex.submit(
                _download_one,
                url=u,
                out_files_dir=out_files_dir,
                headers=headers,
                timeout_s=args.timeout,
                max_retries=args.retries,
                backoff_s=args.backoff,
                user_agent=args.user_agent,
                skip_existing=(not args.no_skip_existing),
            )
            for u in urls
        ]

        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            results.append(r)

    # Keep deterministic order in manifest: same as input urls
    res_by_url = {r.url: r for r in results}
    ordered = [res_by_url[u] for u in urls if u in res_by_url]

    write_jsonl(manifest_path, [asdict(r) for r in ordered])

    failed = [r.url for r in ordered if not r.ok]
    failed_urls_path.write_text("\n".join(failed) + ("\n" if failed else ""), encoding="utf-8")

    ok_n = sum(1 for r in ordered if r.ok)
    fail_n = len(ordered) - ok_n

    print(f"Output dir: {output_dir}")
    print(f"Total: {len(ordered)} | ok: {ok_n} | failed: {fail_n}")
    print(f"Manifest: {manifest_path}")
    print(f"Failed URLs: {failed_urls_path}")

    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
