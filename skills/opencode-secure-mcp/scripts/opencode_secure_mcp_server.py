#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import secrets
import signal
import subprocess
import sys
import time
from typing import Any


SERVER_NAME = "opencode-secure-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-03-26"
SKILL_ROOT = pathlib.Path(__file__).resolve().parents[1]
WRAPPER_PATH = SKILL_ROOT / "scripts" / "opencode_secure_run.sh"
STATE_DIR = pathlib.Path(
    os.environ.get("OPENCODE_SECURE_MCP_STATE_DIR", "~/.local/state/opencode-secure-mcp")
).expanduser()
JOBS_DIR = STATE_DIR / "jobs"
ARTIFACTS_DIR = STATE_DIR / "artifacts"
JOB_REGISTRY_PATH = STATE_DIR / "jobs.json"

PROCESS_TABLE: dict[str, subprocess.Popen[Any]] = {}
DEBUG_LOG_PATH = pathlib.Path(
    os.environ.get("OPENCODE_SECURE_MCP_DEBUG_LOG", "~/.local/state/opencode-secure-mcp/server.log")
).expanduser()


def ensure_dirs() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def debug_log(message: str) -> None:
    ensure_dirs()
    with DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{utc_now()} {message}\n")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_registry() -> dict[str, Any]:
    ensure_dirs()
    if not JOB_REGISTRY_PATH.exists():
        return {"jobs": {}}
    try:
        return json.loads(JOB_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"jobs": {}}


def save_registry(registry: dict[str, Any]) -> None:
    ensure_dirs()
    JOB_REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def gen_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d%H%M%S', time.gmtime())}_{secrets.token_hex(4)}"


def error_result(message: str, code: str = "bad_request") -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"ERROR: {message}"}],
        "structuredContent": {"ok": False, "error": {"code": code, "message": message}},
        "isError": True,
    }


def success_result(summary: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": summary}],
        "structuredContent": {"ok": True, **payload},
        "isError": False,
    }


def read_message() -> dict[str, Any] | None:
    first_line = sys.stdin.buffer.readline()
    if not first_line:
        debug_log("stdin eof")
        return None

    if first_line.lower().startswith(b"content-length:"):
        headers: dict[str, str] = {}
        line = first_line
        while True:
            if line in (b"\r\n", b"\n"):
                break
            name, _, value = line.decode("utf-8").partition(":")
            headers[name.strip().lower()] = value.strip()
            line = sys.stdin.buffer.readline()
            if not line:
                debug_log("stdin eof during content-length headers")
                return None
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            debug_log("invalid content-length header")
            return None
        body = sys.stdin.buffer.read(length)
        if not body:
            debug_log("stdin body eof")
            return None
        message = json.loads(body.decode("utf-8"))
        debug_log(f"recv legacy-framed method={message.get('method')} id={message.get('id')}")
        return message

    payload = first_line.strip()
    if not payload:
        debug_log("skip empty ndjson line")
        return read_message()
    message = json.loads(payload.decode("utf-8"))
    debug_log(f"recv ndjson method={message.get('method')} id={message.get('id')}")
    return message


def write_message(message: dict[str, Any]) -> None:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(body + b"\n")
    sys.stdout.buffer.flush()


def send_response(msg_id: Any, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result if result is not None else {}
    debug_log(f"send_response id={msg_id} has_error={error is not None}")
    write_message(payload)


def send_error(msg_id: Any, code: int, message: str) -> None:
    debug_log(f"send_error id={msg_id} code={code} message={message}")
    send_response(msg_id, error={"code": code, "message": message})


def tool_definitions() -> list[dict[str, Any]]:
    secure_props = {
        "encrypted_file": {"type": "string", "description": "Encrypted API-key file path."},
        "pass_file": {"type": "string", "description": "Passphrase file path. The server passes the path to the wrapper; it does not read the file."},
        "pass_env": {"type": "string", "description": "Passphrase environment variable name."},
        "env_keys": {"type": "array", "items": {"type": "string"}, "description": "Additional environment-variable names that should receive the decrypted key."},
    }
    run_like = {
        "type": "object",
        "required": ["instruction"],
        "properties": {
            "instruction": {"type": "string", "description": "Natural-language task for opencode run."},
            "task_type": {"type": "string", "description": "Optional routing label for the caller."},
            "cwd": {"type": "string", "description": "Working directory for opencode."},
            "model": {"type": "string", "description": "provider/model string passed to opencode."},
            "timeout_sec": {"type": "integer", "minimum": 1, "description": "Maximum runtime in seconds."},
            "request_id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            **secure_props,
        },
        "additionalProperties": False,
    }
    get_like = {
        "type": "object",
        "required": ["job_id"],
        "properties": {"job_id": {"type": "string"}},
        "additionalProperties": False,
    }
    return [
        {
            "name": "opencode_run_task",
            "description": "Run a secure opencode task synchronously through the encrypted-key wrapper.",
            "inputSchema": run_like,
        },
        {
            "name": "opencode_submit_task",
            "description": "Submit a secure opencode task for background execution.",
            "inputSchema": run_like,
        },
        {
            "name": "opencode_get_task",
            "description": "Fetch background job status and metadata.",
            "inputSchema": get_like,
        },
        {
            "name": "opencode_cancel_task",
            "description": "Best-effort cancel a running background opencode job.",
            "inputSchema": get_like,
        },
        {
            "name": "opencode_collect_artifacts",
            "description": "Collect stdout log tail and artifact metadata for a background job.",
            "inputSchema": {
                "type": "object",
                "required": ["job_id"],
                "properties": {
                    "job_id": {"type": "string"},
                    "tail_bytes": {"type": "integer", "minimum": 1, "description": "Maximum stdout bytes to return."},
                },
                "additionalProperties": False,
            },
        },
    ]


def build_wrapper_command(arguments: dict[str, Any]) -> tuple[list[str], str | None, int]:
    instruction = arguments.get("instruction", "")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("instruction must be a non-empty string")
    cwd = arguments.get("cwd")
    if cwd is not None:
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError("cwd must be a non-empty string when provided")
        if not pathlib.Path(cwd).exists():
            raise ValueError(f"cwd does not exist: {cwd}")

    timeout_sec = int(arguments.get("timeout_sec", 900))
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")

    cmd = ["bash", str(WRAPPER_PATH)]
    for opt_name in ("encrypted_file", "pass_file", "pass_env", "model"):
        opt_value = arguments.get(opt_name)
        if opt_value is not None:
            if not isinstance(opt_value, str) or not opt_value.strip():
                raise ValueError(f"{opt_name} must be a non-empty string when provided")
            cmd.extend([f"--{opt_name.replace('_', '-')}", opt_value])

    env_keys = arguments.get("env_keys") or []
    if not isinstance(env_keys, list) or any(not isinstance(v, str) or not v.strip() for v in env_keys):
        raise ValueError("env_keys must be a list of non-empty strings")
    for name in env_keys:
        cmd.extend(["--env-key", name])

    cmd.extend(["--", instruction])
    return cmd, cwd, timeout_sec


def read_log_tail(log_path: pathlib.Path, tail_bytes: int) -> str:
    if not log_path.exists():
        return ""
    data = log_path.read_bytes()
    return data[-tail_bytes:].decode("utf-8", errors="replace")


def persist_job(registry: dict[str, Any], job: dict[str, Any]) -> None:
    registry.setdefault("jobs", {})[job["job_id"]] = job
    save_registry(registry)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def refresh_jobs(registry: dict[str, Any]) -> None:
    now = time.time()
    changed = False
    for job in registry.get("jobs", {}).values():
        if job.get("status") != "running":
            continue
        job_id = job["job_id"]
        proc = PROCESS_TABLE.get(job_id)
        timeout_sec = int(job.get("timeout_sec", 0) or 0)
        started_ts = float(job.get("started_ts", 0.0) or 0.0)
        if timeout_sec > 0 and started_ts > 0 and now - started_ts > timeout_sec:
            pid = int(job.get("pid", 0))
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                pass
            job["status"] = "timed_out"
            job["finished_at"] = utc_now()
            job["error"] = {"code": "timeout", "message": f"job exceeded {timeout_sec}s timeout"}
            changed = True
            continue
        if proc is not None:
            rc = proc.poll()
            if rc is None:
                continue
            job["exit_code"] = rc
            job["finished_at"] = utc_now()
            job["status"] = "succeeded" if rc == 0 else "failed"
            if rc != 0:
                job["error"] = {"code": "nonzero_exit", "message": f"process exited with code {rc}"}
            PROCESS_TABLE.pop(job_id, None)
            changed = True
            continue
        pid = int(job.get("pid", 0))
        if pid and not pid_alive(pid):
            job["status"] = "finished_unknown"
            job["finished_at"] = utc_now()
            job["error"] = {"code": "process_lost", "message": "job process is no longer alive and no return code was captured"}
            changed = True
    if changed:
        save_registry(registry)


def handle_run_task(arguments: dict[str, Any]) -> dict[str, Any]:
    debug_log("handle_run_task")
    try:
        cmd, cwd, timeout_sec = build_wrapper_command(arguments)
    except ValueError as exc:
        return error_result(str(exc))

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return error_result(f"synchronous task timed out after {timeout_sec}s", code="timeout")

    payload = {
        "mode": "sync",
        "status": "succeeded" if proc.returncode == 0 else "failed",
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "cwd": cwd,
        "command": cmd,
        "finished_at": utc_now(),
    }
    if proc.returncode != 0:
        payload["error"] = {"code": "nonzero_exit", "message": f"process exited with code {proc.returncode}"}
        return {
            "content": [{"type": "text", "text": f"opencode_run_task failed with exit code {proc.returncode}"}],
            "structuredContent": {"ok": False, **payload},
            "isError": True,
        }
    return success_result("opencode_run_task completed successfully", payload)


def handle_submit_task(arguments: dict[str, Any]) -> dict[str, Any]:
    debug_log("handle_submit_task")
    registry = load_registry()
    refresh_jobs(registry)
    try:
        cmd, cwd, timeout_sec = build_wrapper_command(arguments)
    except ValueError as exc:
        return error_result(str(exc))

    job_id = gen_id("job")
    job_dir = ARTIFACTS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    stderr_path.touch()

    stdout_handle = stdout_path.open("wb")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=stdout_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    stdout_handle.close()
    PROCESS_TABLE[job_id] = proc

    job = {
        "job_id": job_id,
        "status": "running",
        "summary": "background opencode task started",
        "task_type": arguments.get("task_type"),
        "request_id": arguments.get("request_id"),
        "tags": arguments.get("tags", []),
        "command": cmd,
        "cwd": cwd,
        "pid": proc.pid,
        "timeout_sec": timeout_sec,
        "submitted_at": utc_now(),
        "started_at": utc_now(),
        "started_ts": time.time(),
        "finished_at": None,
        "exit_code": None,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "error": None,
    }
    persist_job(registry, job)
    return success_result(
        f"submitted background job {job_id}",
        {
            "job_id": job_id,
            "status": job["status"],
            "stdout_path": job["stdout_path"],
            "submitted_at": job["submitted_at"],
        },
    )


def fetch_job(job_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    registry = load_registry()
    refresh_jobs(registry)
    return registry.get("jobs", {}).get(job_id), registry


def handle_get_task(arguments: dict[str, Any]) -> dict[str, Any]:
    debug_log("handle_get_task")
    job_id = arguments.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return error_result("job_id must be a non-empty string")
    job, _ = fetch_job(job_id)
    if job is None:
        return error_result(f"unknown job_id: {job_id}", code="not_found")
    return success_result(
        f"job {job_id} is {job['status']}",
        {
            "job": job,
            "stdout_tail": read_log_tail(pathlib.Path(job["stdout_path"]), 4096),
        },
    )


def handle_cancel_task(arguments: dict[str, Any]) -> dict[str, Any]:
    debug_log("handle_cancel_task")
    job_id = arguments.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return error_result("job_id must be a non-empty string")
    registry = load_registry()
    refresh_jobs(registry)
    job = registry.get("jobs", {}).get(job_id)
    if job is None:
        return error_result(f"unknown job_id: {job_id}", code="not_found")
    if job.get("status") != "running":
        return success_result(f"job {job_id} is already {job['status']}", {"job": job})
    pid = int(job.get("pid", 0))
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception as exc:
        return error_result(f"failed to cancel job {job_id}: {exc}", code="cancel_failed")
    proc = PROCESS_TABLE.get(job_id)
    if proc is not None:
        try:
            proc.wait(timeout=5)
            job["exit_code"] = proc.returncode
        except Exception:
            pass
        PROCESS_TABLE.pop(job_id, None)
    job["status"] = "cancelled"
    job["finished_at"] = utc_now()
    job["error"] = {"code": "cancelled", "message": "job was cancelled by request"}
    save_registry(registry)
    return success_result(f"cancelled job {job_id}", {"job": job})


def handle_collect_artifacts(arguments: dict[str, Any]) -> dict[str, Any]:
    debug_log("handle_collect_artifacts")
    job_id = arguments.get("job_id")
    tail_bytes = int(arguments.get("tail_bytes", 8192))
    if not isinstance(job_id, str) or not job_id:
        return error_result("job_id must be a non-empty string")
    if tail_bytes <= 0:
        return error_result("tail_bytes must be positive")
    job, _ = fetch_job(job_id)
    if job is None:
        return error_result(f"unknown job_id: {job_id}", code="not_found")
    stdout_path = pathlib.Path(job["stdout_path"])
    payload = {
        "job_id": job_id,
        "status": job["status"],
        "artifacts": [
            {"kind": "stdout", "path": str(stdout_path), "exists": stdout_path.exists()},
        ],
        "stdout_tail": read_log_tail(stdout_path, tail_bytes),
    }
    return success_result(f"collected artifacts for {job_id}", payload)


TOOL_HANDLERS = {
    "opencode_run_task": handle_run_task,
    "opencode_submit_task": handle_submit_task,
    "opencode_get_task": handle_get_task,
    "opencode_cancel_task": handle_cancel_task,
    "opencode_collect_artifacts": handle_collect_artifacts,
}


def main() -> int:
    ensure_dirs()
    debug_log("server start")
    while True:
        try:
            message = read_message()
            if message is None:
                debug_log("server exit clean")
                return 0
            method = message.get("method")
            msg_id = message.get("id")
            if method == "initialize":
                send_response(
                    msg_id,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    },
                )
                continue
            if method == "notifications/initialized":
                debug_log("initialized notification")
                continue
            if method == "ping":
                send_response(msg_id, {})
                continue
            if method == "tools/list":
                send_response(msg_id, {"tools": tool_definitions()})
                continue
            if method == "tools/call":
                params = message.get("params", {})
                name = params.get("name")
                arguments = params.get("arguments", {})
                handler = TOOL_HANDLERS.get(name)
                if handler is None:
                    send_error(msg_id, -32602, f"unknown tool: {name}")
                    continue
                send_response(msg_id, handler(arguments))
                continue
            if msg_id is None:
                debug_log(f"ignored notification method={method}")
                continue
            send_error(msg_id, -32601, f"method not found: {method}")
        except Exception as exc:
            debug_log(f"fatal_exception type={type(exc).__name__} message={exc}")
            raise


if __name__ == "__main__":
    raise SystemExit(main())
