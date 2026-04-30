#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_completion(path: pathlib.Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an opencode wrapper command and persist completion metadata.")
    parser.add_argument("--completion-path", required=True)
    parser.add_argument("--stdout-path", required=True)
    parser.add_argument("--stderr-path", required=True)
    parser.add_argument("--cwd")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.command[0] != "--" or len(args.command) == 1:
        parser.error("expected command after --")
    args.command = args.command[1:]
    return args


def main() -> int:
    args = parse_args()
    completion_path = pathlib.Path(args.completion_path)
    stdout_path = pathlib.Path(args.stdout_path)
    stderr_path = pathlib.Path(args.stderr_path)
    cwd = args.cwd

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    exit_code: int | None = None
    status = "failed"
    error: dict | None = None
    try:
        with stdout_path.open("ab") as stdout_handle, stderr_path.open("ab") as stderr_handle:
            proc = subprocess.Popen(
                args.command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
            )
            exit_code = proc.wait()
    except Exception as exc:
        error = {"code": "runner_exception", "message": f"{type(exc).__name__}: {exc}"}
    else:
        status = "succeeded" if exit_code == 0 else "failed"
        if exit_code != 0:
            error = {"code": "nonzero_exit", "message": f"process exited with code {exit_code}"}
    finally:
        write_completion(
            completion_path,
            {
                "status": status,
                "exit_code": exit_code,
                "finished_at": utc_now(),
                "error": error,
            },
        )

    return exit_code if exit_code is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
