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
JOB_RUNNER_PATH = SKILL_ROOT / "scripts" / "opencode_secure_job_runner.py"
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
DIAGNOSTIC_MODES = {"off", "on_error", "trace"}
LOG_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR"}
FORMAT_MODES = {"default", "json"}
SYNC_POLL_INTERVAL_SEC = 0.2
SYNC_HANDOFF_MIN_TIMEOUT_SEC = 300
DEFAULT_OPENCODE_MODEL_STATE_PATH = "~/.local/state/opencode/model.json"
AUTO_MODEL_TOKENS = {"", "auto", "default", "stable", "recent"}
BUILTIN_MODEL_ALIASES = {
    "kimi": ("kimi",),
    "deepseek": ("deepseek",),
    "qwen": ("qwen",),
    "glm": ("glm",),
    "gpt": ("gpt",),
    "claude": ("claude",),
    "minimax": ("minimax",),
}


class ModelResolutionError(ValueError):
    def __init__(self, message: str, *, requested_model: str | None, candidates: list[str]) -> None:
        super().__init__(message)
        self.requested_model = requested_model
        self.candidates = candidates


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


def ensure_text_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def tail_text(text: str, tail_bytes: int) -> str:
    if tail_bytes <= 0:
        return ""
    data = text.encode("utf-8", errors="replace")
    return data[-tail_bytes:].decode("utf-8", errors="replace")


def write_artifact_text(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_log_text(log_path: pathlib.Path) -> str:
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8", errors="replace")


def read_json_file(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def opencode_model_state_path() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("OPENCODE_MODEL_STATE_PATH", DEFAULT_OPENCODE_MODEL_STATE_PATH)
    ).expanduser()


def normalize_model_token(value: str) -> str:
    return value.strip().casefold()


def build_model_candidate(full_id: str, *, source: str) -> dict[str, str]:
    provider_id, sep, model_path = full_id.partition("/")
    if not sep or not provider_id.strip() or not model_path.strip():
        raise ValueError(f"invalid full model id: {full_id}")
    model_path = model_path.strip()
    return {
        "full_id": f"{provider_id.strip()}/{model_path}",
        "provider_id": provider_id.strip(),
        "model_path": model_path,
        "model_leaf": model_path.rsplit("/", 1)[-1],
        "source": source,
    }


def validated_model_candidates() -> list[dict[str, str]]:
    state = read_json_file(opencode_model_state_path()) or {}
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_candidate(full_id: str, *, source: str) -> None:
        try:
            candidate = build_model_candidate(full_id, source=source)
        except ValueError:
            return
        normalized = normalize_model_token(candidate["full_id"])
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append(candidate)

    recent = state.get("recent")
    if isinstance(recent, list):
        for item in recent:
            if not isinstance(item, dict):
                continue
            provider_id = item.get("providerID")
            model_id = item.get("modelID")
            if isinstance(provider_id, str) and provider_id.strip() and isinstance(model_id, str) and model_id.strip():
                add_candidate(f"{provider_id.strip()}/{model_id.strip()}", source="recent")

    variant = state.get("variant")
    if isinstance(variant, dict):
        for full_id in variant:
            if isinstance(full_id, str) and full_id.strip():
                add_candidate(full_id.strip(), source="variant")

    return candidates


def candidate_suggestions(candidates: list[dict[str, str]], limit: int = 5) -> list[str]:
    return [candidate["full_id"] for candidate in candidates[:limit]]


def match_validated_candidate(model: str, candidates: list[dict[str, str]]) -> dict[str, str] | None:
    normalized = normalize_model_token(model)
    for candidate in candidates:
        if normalized == normalize_model_token(candidate["full_id"]):
            return candidate
    for candidate in candidates:
        if normalized == normalize_model_token(candidate["model_path"]):
            return candidate
    for candidate in candidates:
        if normalized == normalize_model_token(candidate["model_leaf"]):
            return candidate
    suffix = f"/{normalized}"
    for candidate in candidates:
        candidate_full = normalize_model_token(candidate["full_id"])
        candidate_path = normalize_model_token(candidate["model_path"])
        if candidate_full.endswith(suffix) or candidate_path.endswith(suffix):
            return candidate
    return None


def match_builtin_alias(alias: str, candidates: list[dict[str, str]]) -> dict[str, str] | None:
    keywords = BUILTIN_MODEL_ALIASES.get(alias)
    if keywords is None:
        return None
    normalized_keywords = [normalize_model_token(keyword) for keyword in keywords]
    for candidate in candidates:
        haystacks = (
            normalize_model_token(candidate["full_id"]),
            normalize_model_token(candidate["model_path"]),
            normalize_model_token(candidate["model_leaf"]),
        )
        if any(keyword in haystack for keyword in normalized_keywords for haystack in haystacks):
            return candidate
    return None


def resolve_model_argument(raw_model: Any) -> dict[str, str | None]:
    if raw_model is None:
        requested_model = ""
    elif isinstance(raw_model, str):
        requested_model = raw_model.strip()
    else:
        raise ValueError("model must be a non-empty string when provided")

    normalized = normalize_model_token(requested_model)
    candidates = validated_model_candidates()

    if normalized in AUTO_MODEL_TOKENS:
        if not candidates:
            raise ModelResolutionError(
                "model was omitted or set to auto/default, but no validated local opencode models were found",
                requested_model=requested_model or None,
                candidates=[],
            )
        return {
            "requested_model": requested_model or None,
            "resolved_model": candidates[0]["full_id"],
            "resolution_source": "recent_default",
        }

    builtin_alias_match = match_builtin_alias(normalized, candidates)
    if builtin_alias_match is not None:
        return {
            "requested_model": requested_model,
            "resolved_model": builtin_alias_match["full_id"],
            "resolution_source": "alias_builtin",
        }

    validated_match = match_validated_candidate(requested_model, candidates)
    if validated_match is not None:
        resolution_source = "explicit" if normalized == normalize_model_token(validated_match["full_id"]) else "recent_match"
        return {
            "requested_model": requested_model,
            "resolved_model": validated_match["full_id"],
            "resolution_source": resolution_source,
        }

    if "/" in requested_model:
        return {
            "requested_model": requested_model,
            "resolved_model": requested_model,
            "resolution_source": "explicit",
        }

    raise ModelResolutionError(
        f"could not resolve model '{requested_model}' from local validated opencode models",
        requested_model=requested_model,
        candidates=candidate_suggestions(candidates),
    )


def model_resolution_error_result(exc: ModelResolutionError) -> dict[str, Any]:
    candidate_text = ", ".join(exc.candidates) if exc.candidates else "(none)"
    requested = exc.requested_model if exc.requested_model else "<auto>"
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"ERROR: {exc}. requested={requested}. "
                    f"recent candidates: {candidate_text}"
                ),
            }
        ],
        "structuredContent": {
            "ok": False,
            "error": {"code": "model_resolution_failed", "message": str(exc)},
            "requested_model": exc.requested_model,
            "candidate_models": exc.candidates,
            "builtin_aliases": sorted(BUILTIN_MODEL_ALIASES),
            "model_state_path": str(opencode_model_state_path()),
        },
        "isError": True,
    }


def diagnostics_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": sorted(DIAGNOSTIC_MODES),
                "description": "Diagnostic verbosity. `on_error` keeps the success path quiet but enriches failures.",
            },
            "capture_stdout_tail_bytes": {
                "type": "integer",
                "minimum": 1,
                "description": "Maximum stdout bytes to keep in failure diagnostics.",
            },
            "capture_stderr_tail_bytes": {
                "type": "integer",
                "minimum": 1,
                "description": "Maximum stderr bytes to keep in failure diagnostics.",
            },
            "persist_artifacts": {
                "type": "boolean",
                "description": "Persist stdout/stderr artifacts for later collection.",
            },
            "redact_sensitive": {
                "type": "boolean",
                "description": "Reserved for future redaction controls. Defaults to true.",
            },
            "opencode": {
                "type": "object",
                "properties": {
                    "print_logs": {"type": "boolean"},
                    "log_level": {"type": "string", "enum": sorted(LOG_LEVELS)},
                    "format": {"type": "string", "enum": sorted(FORMAT_MODES)},
                    "pure": {"type": "boolean"},
                    "variant": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def normalize_diagnostics(arguments: dict[str, Any]) -> dict[str, Any]:
    raw = arguments.get("diagnostics") or {}
    if not isinstance(raw, dict):
        raise ValueError("diagnostics must be an object when provided")

    mode = raw.get("mode", "on_error")
    if mode not in DIAGNOSTIC_MODES:
        raise ValueError(f"diagnostics.mode must be one of {sorted(DIAGNOSTIC_MODES)}")

    stdout_tail_bytes = int(raw.get("capture_stdout_tail_bytes", 8192))
    stderr_tail_bytes = int(raw.get("capture_stderr_tail_bytes", 8192))
    if stdout_tail_bytes <= 0 or stderr_tail_bytes <= 0:
        raise ValueError("diagnostic tail byte limits must be positive")

    persist_artifacts = raw.get("persist_artifacts", True)
    if not isinstance(persist_artifacts, bool):
        raise ValueError("diagnostics.persist_artifacts must be a boolean")

    redact_sensitive = raw.get("redact_sensitive", True)
    if not isinstance(redact_sensitive, bool):
        raise ValueError("diagnostics.redact_sensitive must be a boolean")

    opencode_raw = raw.get("opencode") or {}
    if not isinstance(opencode_raw, dict):
        raise ValueError("diagnostics.opencode must be an object when provided")

    print_logs = opencode_raw.get("print_logs")
    if print_logs is None:
        print_logs = mode == "trace"
    elif not isinstance(print_logs, bool):
        raise ValueError("diagnostics.opencode.print_logs must be a boolean")

    log_level = opencode_raw.get("log_level")
    if log_level is None and mode == "trace":
        log_level = "DEBUG"
    if log_level is not None:
        if not isinstance(log_level, str) or log_level not in LOG_LEVELS:
            raise ValueError(f"diagnostics.opencode.log_level must be one of {sorted(LOG_LEVELS)}")

    format_mode = opencode_raw.get("format")
    if format_mode is None and mode == "trace":
        format_mode = "json"
    if format_mode is not None:
        if not isinstance(format_mode, str) or format_mode not in FORMAT_MODES:
            raise ValueError(f"diagnostics.opencode.format must be one of {sorted(FORMAT_MODES)}")

    pure = opencode_raw.get("pure")
    if pure is not None and not isinstance(pure, bool):
        raise ValueError("diagnostics.opencode.pure must be a boolean when provided")

    variant = opencode_raw.get("variant")
    if variant is not None and (not isinstance(variant, str) or not variant.strip()):
        raise ValueError("diagnostics.opencode.variant must be a non-empty string when provided")

    return {
        "mode": mode,
        "capture_stdout_tail_bytes": stdout_tail_bytes,
        "capture_stderr_tail_bytes": stderr_tail_bytes,
        "persist_artifacts": persist_artifacts,
        "redact_sensitive": redact_sensitive,
        "opencode": {
            "print_logs": print_logs,
            "log_level": log_level,
            "format": format_mode,
            "pure": pure,
            "variant": variant,
        },
    }


def build_failure_diagnostics(
    diagnostics: dict[str, Any],
    cmd: list[str],
    cwd: str | None,
    timeout_sec: int,
    stdout_text: str,
    stderr_text: str,
    artifact_paths: dict[str, str] | None,
) -> dict[str, Any]:
    return {
        "mode": diagnostics["mode"],
        "timeout_sec": timeout_sec,
        "cwd": cwd,
        "command": cmd,
        "stdout_tail": tail_text(stdout_text, diagnostics["capture_stdout_tail_bytes"]),
        "stderr_tail": tail_text(stderr_text, diagnostics["capture_stderr_tail_bytes"]),
        "artifact_paths": artifact_paths or {},
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
            "model": {
                "type": "string",
                "description": "Optional model selector. Supports full provider/model ids, validated short aliases such as `kimi`, provider-less validated suffixes such as `moonshot/kimi-k2.6`, or empty/`auto`/`default`/`stable` to use the most recent validated local model.",
            },
            "timeout_sec": {"type": "integer", "minimum": 1, "description": "Maximum runtime in seconds."},
            "request_id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "diagnostics": diagnostics_schema(),
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


def build_wrapper_command(arguments: dict[str, Any]) -> tuple[list[str], str | None, int, dict[str, Any], dict[str, str | None]]:
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
    diagnostics = normalize_diagnostics(arguments)
    model_resolution = resolve_model_argument(arguments.get("model"))
    debug_log(
        "model_resolved "
        f"requested={model_resolution['requested_model']!r} "
        f"resolved={model_resolution['resolved_model']!r} "
        f"source={model_resolution['resolution_source']}"
    )

    cmd = ["bash", str(WRAPPER_PATH)]
    for opt_name in ("encrypted_file", "pass_file", "pass_env"):
        opt_value = arguments.get(opt_name)
        if opt_value is not None:
            if not isinstance(opt_value, str) or not opt_value.strip():
                raise ValueError(f"{opt_name} must be a non-empty string when provided")
            cmd.extend([f"--{opt_name.replace('_', '-')}", opt_value])
    cmd.extend(["--model", str(model_resolution["resolved_model"])])

    env_keys = arguments.get("env_keys") or []
    if not isinstance(env_keys, list) or any(not isinstance(v, str) or not v.strip() for v in env_keys):
        raise ValueError("env_keys must be a list of non-empty strings")
    for name in env_keys:
        cmd.extend(["--env-key", name])

    cmd.append("--")
    opencode = diagnostics["opencode"]
    if opencode["print_logs"]:
        cmd.append("--print-logs")
    if opencode["log_level"] is not None:
        cmd.extend(["--log-level", opencode["log_level"]])
    if opencode["format"] is not None:
        cmd.extend(["--format", opencode["format"]])
    if opencode["pure"] is True:
        cmd.append("--pure")
    if opencode["variant"] is not None:
        cmd.extend(["--variant", opencode["variant"]])
    cmd.append(instruction)
    return cmd, cwd, timeout_sec, diagnostics, model_resolution


def read_log_tail(log_path: pathlib.Path, tail_bytes: int) -> str:
    if not log_path.exists():
        return ""
    data = log_path.read_bytes()
    return data[-tail_bytes:].decode("utf-8", errors="replace")


def sync_handoff_timeout_sec(requested_timeout_sec: int) -> int:
    return max(requested_timeout_sec, SYNC_HANDOFF_MIN_TIMEOUT_SEC)


def build_timeout_context(stdout_text: str, stderr_text: str) -> dict[str, Any]:
    saw_stdout = bool(stdout_text)
    saw_stderr = bool(stderr_text)
    saw_output = saw_stdout or saw_stderr
    return {
        "saw_output": saw_output,
        "saw_stdout": saw_stdout,
        "saw_stderr": saw_stderr,
        "likely_cause": "upstream_or_model_latency" if saw_output else "local_or_wrapper_startup",
    }


def start_job(
    registry: dict[str, Any],
    *,
    cmd: list[str],
    cwd: str | None,
    timeout_sec: int,
    diagnostics: dict[str, Any],
    task_type: str | None,
    request_id: str | None,
    tags: list[str],
    summary: str,
    run_mode: str,
    model_resolution: dict[str, str | None],
    requested_timeout_sec: int | None = None,
) -> dict[str, Any]:
    job_id = gen_id("job")
    job_dir = ARTIFACTS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    completion_path = job_dir / "completion.json"
    launch_cmd = [
        "python3",
        str(JOB_RUNNER_PATH),
        "--completion-path",
        str(completion_path),
        "--stdout-path",
        str(stdout_path),
        "--stderr-path",
        str(stderr_path),
    ]
    if cwd is not None:
        launch_cmd.extend(["--cwd", cwd])
    launch_cmd.extend(["--", *cmd])
    proc = subprocess.Popen(
        launch_cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    PROCESS_TABLE[job_id] = proc

    job = {
        "job_id": job_id,
        "status": "running",
        "summary": summary,
        "task_type": task_type,
        "request_id": request_id,
        "tags": tags,
        "command": cmd,
        "cwd": cwd,
        "pid": proc.pid,
        "timeout_sec": timeout_sec,
        "requested_timeout_sec": requested_timeout_sec if requested_timeout_sec is not None else timeout_sec,
        "diagnostics": diagnostics,
        "requested_model": model_resolution["requested_model"],
        "resolved_model": model_resolution["resolved_model"],
        "resolution_source": model_resolution["resolution_source"],
        "submitted_at": utc_now(),
        "started_at": utc_now(),
        "started_ts": time.time(),
        "finished_at": None,
        "exit_code": None,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "completion_path": str(completion_path),
        "error": None,
        "run_mode": run_mode,
    }
    persist_job(registry, job)
    return job


def persist_job(registry: dict[str, Any], job: dict[str, Any]) -> None:
    registry.setdefault("jobs", {})[job["job_id"]] = job
    save_registry(registry)


def pid_alive(pid: int) -> bool:
    proc_stat = pathlib.Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            stat_text = proc_stat.read_text(encoding="utf-8", errors="replace")
            rparen = stat_text.rfind(")")
            if rparen != -1 and len(stat_text) > rparen + 2:
                return stat_text[rparen + 2] != "Z"
        except Exception:
            pass
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
        completion_path = pathlib.Path(job.get("completion_path", ""))
        completion = read_json_file(completion_path) if job.get("completion_path") else None
        if completion is not None:
            job["exit_code"] = completion.get("exit_code")
            job["finished_at"] = completion.get("finished_at") or utc_now()
            job["status"] = completion.get("status") or ("succeeded" if job["exit_code"] == 0 else "failed")
            job["error"] = completion.get("error")
            PROCESS_TABLE.pop(job_id, None)
            changed = True
            continue
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
        cmd, cwd, timeout_sec, diagnostics, model_resolution = build_wrapper_command(arguments)
    except ModelResolutionError as exc:
        return model_resolution_error_result(exc)
    except ValueError as exc:
        return error_result(str(exc))

    registry = load_registry()
    refresh_jobs(registry)
    job = start_job(
        registry,
        cmd=cmd,
        cwd=cwd,
        timeout_sec=sync_handoff_timeout_sec(timeout_sec),
        diagnostics=diagnostics,
        task_type=arguments.get("task_type"),
        request_id=arguments.get("request_id"),
        tags=arguments.get("tags", []),
        summary="synchronous opencode task started",
        run_mode="sync",
        model_resolution=model_resolution,
        requested_timeout_sec=timeout_sec,
    )
    deadline = time.time() + timeout_sec

    while True:
        refresh_jobs(registry)
        job = registry.get("jobs", {}).get(job["job_id"], job)
        if job.get("status") != "running":
            break
        now = time.time()
        if now >= deadline:
            stdout_path = pathlib.Path(job["stdout_path"])
            stderr_path = pathlib.Path(job["stderr_path"])
            stdout_text = read_log_text(stdout_path)
            stderr_text = read_log_text(stderr_path)
            artifact_paths = {"stdout": str(stdout_path), "stderr": str(stderr_path)}
            return {
                "content": [{"type": "text", "text": f"ERROR: synchronous task timed out after {timeout_sec}s; background job {job['job_id']} is still running"}],
                "structuredContent": {
                    "ok": False,
                    "job_id": job["job_id"],
                    "status": "running",
                    "requested_model": job["requested_model"],
                    "resolved_model": job["resolved_model"],
                    "resolution_source": job["resolution_source"],
                    "error": {"code": "timeout", "message": f"synchronous task timed out after {timeout_sec}s"},
                    "timeout_context": build_timeout_context(stdout_text, stderr_text),
                    "diagnostics": build_failure_diagnostics(
                        diagnostics,
                        cmd,
                        cwd,
                        timeout_sec,
                        stdout_text,
                        stderr_text,
                        artifact_paths if diagnostics["persist_artifacts"] else None,
                    ),
                },
                "isError": True,
            }
        time.sleep(min(SYNC_POLL_INTERVAL_SEC, deadline - now))

    stdout_path = pathlib.Path(job["stdout_path"])
    stderr_path = pathlib.Path(job["stderr_path"])
    stdout_text = read_log_text(stdout_path)
    stderr_text = read_log_text(stderr_path)
    artifact_paths = {"stdout": str(stdout_path), "stderr": str(stderr_path)} if diagnostics["persist_artifacts"] else {}

    payload = {
        "mode": "sync",
        "job_id": job["job_id"],
        "status": job["status"],
        "exit_code": job["exit_code"],
        "requested_model": job["requested_model"],
        "resolved_model": job["resolved_model"],
        "resolution_source": job["resolution_source"],
        "stdout": stdout_text,
        "stderr": stderr_text,
        "cwd": cwd,
        "command": cmd,
        "submitted_at": job["submitted_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }
    if artifact_paths:
        payload["artifact_paths"] = artifact_paths
    if job["status"] != "succeeded":
        error_code = "nonzero_exit"
        error_message = f"process exited with code {job['exit_code']}"
        if job.get("status") == "timed_out":
            error_code = "timeout"
            error_message = job.get("error", {}).get("message", "job exceeded timeout")
        elif job.get("status") == "cancelled":
            error_code = "cancelled"
            error_message = "job was cancelled"
        elif job.get("status") == "finished_unknown":
            error_code = "process_lost"
            error_message = job.get("error", {}).get("message", "job process is no longer alive")
        payload["error"] = {"code": error_code, "message": error_message}
        if diagnostics["mode"] != "off":
            payload["diagnostics"] = build_failure_diagnostics(
                diagnostics,
                cmd,
                cwd,
                timeout_sec,
                stdout_text,
                stderr_text,
                artifact_paths if artifact_paths else None,
            )
        return {
            "content": [{"type": "text", "text": f"opencode_run_task failed with status {job['status']}"}],
            "structuredContent": {"ok": False, **payload},
            "isError": True,
        }
    return success_result("opencode_run_task completed successfully", payload)


def handle_submit_task(arguments: dict[str, Any]) -> dict[str, Any]:
    debug_log("handle_submit_task")
    registry = load_registry()
    refresh_jobs(registry)
    try:
        cmd, cwd, timeout_sec, diagnostics, model_resolution = build_wrapper_command(arguments)
    except ModelResolutionError as exc:
        return model_resolution_error_result(exc)
    except ValueError as exc:
        return error_result(str(exc))
    job = start_job(
        registry,
        cmd=cmd,
        cwd=cwd,
        timeout_sec=timeout_sec,
        diagnostics=diagnostics,
        task_type=arguments.get("task_type"),
        request_id=arguments.get("request_id"),
        tags=arguments.get("tags", []),
        summary="background opencode task started",
        run_mode="background",
        model_resolution=model_resolution,
    )
    return success_result(
        f"submitted background job {job['job_id']}",
        {
            "job_id": job["job_id"],
            "status": job["status"],
            "requested_model": job["requested_model"],
            "resolved_model": job["resolved_model"],
            "resolution_source": job["resolution_source"],
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
            "stderr_tail": read_log_tail(pathlib.Path(job["stderr_path"]), 4096),
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
    stderr_path = pathlib.Path(job["stderr_path"])
    payload = {
        "job_id": job_id,
        "status": job["status"],
        "artifacts": [
            {"kind": "stdout", "path": str(stdout_path), "exists": stdout_path.exists()},
            {"kind": "stderr", "path": str(stderr_path), "exists": stderr_path.exists()},
        ],
        "stdout_tail": read_log_tail(stdout_path, tail_bytes),
        "stderr_tail": read_log_tail(stderr_path, tail_bytes),
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
