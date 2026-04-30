#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import time


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
ENTRY = ROOT_DIR / "scripts" / "opencode_secure_mcp_entry.sh"
CODEX_WRAPPER = pathlib.Path("/home/nzzhao/.agents/skills/codex-auth-secure-launch/scripts/codex_secure_launch.sh")


class McpClient:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self._next_id = 1

    def _read_message(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout unexpectedly")
        return json.loads(line.decode("utf-8"))

    def request(self, method: str, params: dict | None = None) -> dict:
        msg_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.proc.stdin.write(body + b"\n")
        self.proc.stdin.flush()
        response = self._read_message()
        if response.get("id") != msg_id:
            raise AssertionError(f"unexpected response id: {response}")
        return response

    def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.proc.stdin.write(body + b"\n")
        self.proc.stdin.flush()


def start_server_client(env: dict[str, str]) -> tuple[subprocess.Popen[bytes], McpClient]:
    proc = subprocess.Popen(
        ["bash", str(ENTRY)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=False,
    )
    client = McpClient(proc)
    init = client.request(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1"},
        },
    )
    assert init["result"]["serverInfo"]["name"] == "opencode-secure-mcp"
    client.notify("notifications/initialized", {})
    return proc, client


def main() -> int:
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="opencode-secure-mcp-"))
    try:
        auth_json = tmpdir / "auth.json"
        pass_file = tmpdir / "pass.txt"
        encrypted_file = tmpdir / "auth.key.enc"
        bin_dir = tmpdir / "bin"
        state_dir = tmpdir / "state"
        auth_json.write_text('{"OPENAI_API_KEY":"dummy-test-key"}\n', encoding="utf-8")
        pass_file.write_text("test-passphrase\n", encoding="utf-8")

        env = os.environ.copy()
        env["CODEX_AUTH_PASSPHRASE"] = "test-passphrase"
        subprocess.run(
            [
                "bash",
                str(CODEX_WRAPPER),
                "init",
                "--source",
                str(auth_json),
                "--output",
                str(encrypted_file),
            ],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        env.pop("CODEX_AUTH_PASSPHRASE", None)

        bin_dir.mkdir()
        fake_opencode = bin_dir / "opencode"
        fake_opencode.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                [[ "${1:-}" == "run" ]] || { echo "expected subcommand run" >&2; exit 10; }
                shift
                model=""
                message=""
                print_logs="false"
                log_level=""
                format_mode="default"
                pure="false"
                variant=""
                while [[ $# -gt 0 ]]; do
                  case "$1" in
                    -m|--model)
                      model="${2:-}"
                      shift 2
                      ;;
                    --print-logs)
                      print_logs="true"
                      shift
                      ;;
                    --log-level)
                      log_level="${2:-}"
                      shift 2
                      ;;
                    --format)
                      format_mode="${2:-}"
                      shift 2
                      ;;
                    --pure)
                      pure="true"
                      shift
                      ;;
                    --variant)
                      variant="${2:-}"
                      shift 2
                      ;;
                    --)
                      shift
                      break
                      ;;
                    *)
                      break
                      ;;
                  esac
                done
                if [[ $# -gt 0 ]]; then
                  message="$1"
                fi
                [[ -n "${OPENAI_API_KEY:-}" ]] || { echo "missing OPENAI_API_KEY" >&2; exit 11; }
                [[ "${OPENAI_API_KEY}" == "dummy-test-key" ]] || { echo "bad key" >&2; exit 12; }
                if [[ "${print_logs}" == "true" ]]; then
                  printf 'trace flags print_logs=%s log_level=%s format=%s pure=%s variant=%s\\n' \
                    "${print_logs}" "${log_level}" "${format_mode}" "${pure}" "${variant}" >&2
                fi
                if [[ "${message}" == "sleepy" ]]; then
                  printf 'sleepy-stdout-before-timeout\\n'
                  printf 'sleepy-stderr-before-timeout\\n' >&2
                  sleep 30
                fi
                if [[ "${message}" == "late-ok" ]]; then
                  printf 'provider-banner\\n' >&2
                  sleep 2
                fi
                if [[ "${message}" == "restart-case" ]]; then
                  printf 'restart-begin\\n'
                  sleep 2
                  printf 'restart-end\\n'
                fi
                if [[ "${message}" == "silent-hang" ]]; then
                  sleep 30
                fi
                if [[ "${message}" == "requires-eof" ]]; then
                  cat >/dev/null
                fi
                printf 'ok model=%s message=%s\\n' "${model}" "${message}"
                """
            ),
            encoding="utf-8",
        )
        fake_opencode.chmod(0o700)

        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["OPENCODE_SECURE_MCP_STATE_DIR"] = str(state_dir)

        proc, client = start_server_client(env)

        try:
            tools = client.request("tools/list")
            tool_names = {tool["name"] for tool in tools["result"]["tools"]}
            expected = {
                "opencode_run_task",
                "opencode_submit_task",
                "opencode_get_task",
                "opencode_cancel_task",
                "opencode_collect_artifacts",
            }
            assert expected.issubset(tool_names), tool_names

            run_resp = client.request(
                "tools/call",
                {
                    "name": "opencode_run_task",
                    "arguments": {
                        "instruction": "hello from mcp",
                        "model": "Mify-Kimi/Pro/moonshotai/Kimi-K2.5",
                        "encrypted_file": str(encrypted_file),
                        "pass_file": str(pass_file),
                    },
                },
            )
            structured = run_resp["result"]["structuredContent"]
            assert structured["ok"] is True
            assert "hello from mcp" in structured["stdout"]

            trace_resp = client.request(
                "tools/call",
                {
                    "name": "opencode_run_task",
                    "arguments": {
                        "instruction": "trace me",
                        "model": "Mify-Aili/tongyi/qwen3.6-plus-2026-04-02",
                        "encrypted_file": str(encrypted_file),
                        "pass_file": str(pass_file),
                        "diagnostics": {
                            "mode": "trace",
                            "opencode": {
                                "print_logs": True,
                                "log_level": "DEBUG",
                                "format": "json",
                                "pure": True,
                                "variant": "fast",
                            },
                        },
                    },
                },
            )
            trace_structured = trace_resp["result"]["structuredContent"]
            assert trace_structured["ok"] is True
            assert "trace flags print_logs=true" in trace_structured["stderr"]
            assert "log_level=DEBUG" in trace_structured["stderr"]
            assert "format=json" in trace_structured["stderr"]
            assert "pure=true" in trace_structured["stderr"]
            assert "variant=fast" in trace_structured["stderr"]

            eof_resp = client.request(
                "tools/call",
                {
                    "name": "opencode_run_task",
                    "arguments": {
                        "instruction": "requires-eof",
                        "model": "Mify-Aili/tongyi/qwen3.6-plus-2026-04-02",
                        "encrypted_file": str(encrypted_file),
                        "pass_file": str(pass_file),
                        "timeout_sec": 3,
                    },
                },
            )
            eof_structured = eof_resp["result"]["structuredContent"]
            assert eof_structured["ok"] is True
            assert "requires-eof" in eof_structured["stdout"]

            timeout_resp = client.request(
                "tools/call",
                {
                    "name": "opencode_run_task",
                    "arguments": {
                        "instruction": "sleepy",
                        "model": "Mify-Aili/tongyi/qwen3.6-plus-2026-04-02",
                        "encrypted_file": str(encrypted_file),
                        "pass_file": str(pass_file),
                        "timeout_sec": 1,
                        "diagnostics": {
                            "mode": "on_error",
                            "capture_stdout_tail_bytes": 1024,
                            "capture_stderr_tail_bytes": 1024,
                            "persist_artifacts": True,
                        },
                    },
                },
            )
            timeout_structured = timeout_resp["result"]["structuredContent"]
            assert timeout_structured["ok"] is False
            assert timeout_structured["error"]["code"] == "timeout"
            diag = timeout_structured["diagnostics"]
            assert diag["mode"] == "on_error"
            assert "sleepy-stdout-before-timeout" in diag["stdout_tail"]
            assert "sleepy-stderr-before-timeout" in diag["stderr_tail"]
            assert pathlib.Path(diag["artifact_paths"]["stdout"]).exists()
            assert pathlib.Path(diag["artifact_paths"]["stderr"]).exists()

            handoff_resp = client.request(
                "tools/call",
                {
                    "name": "opencode_run_task",
                    "arguments": {
                        "instruction": "late-ok",
                        "model": "Mify-Aili/tongyi/qwen3.6-plus-2026-04-02",
                        "encrypted_file": str(encrypted_file),
                        "pass_file": str(pass_file),
                        "timeout_sec": 1,
                        "diagnostics": {
                            "mode": "on_error",
                            "persist_artifacts": True,
                        },
                    },
                },
            )
            handoff_structured = handoff_resp["result"]["structuredContent"]
            assert handoff_structured["ok"] is False
            assert handoff_structured["error"]["code"] == "timeout"
            assert handoff_structured["job_id"]
            assert handoff_structured["status"] == "running"
            assert handoff_structured["timeout_context"]["likely_cause"] == "upstream_or_model_latency"
            assert handoff_structured["timeout_context"]["saw_output"] is True
            handoff_job_id = handoff_structured["job_id"]
            time.sleep(2.5)
            handoff_job = client.request(
                "tools/call",
                {"name": "opencode_get_task", "arguments": {"job_id": handoff_job_id}},
            )
            assert handoff_job["result"]["structuredContent"]["job"]["status"] == "succeeded"
            handoff_collect = client.request(
                "tools/call",
                {"name": "opencode_collect_artifacts", "arguments": {"job_id": handoff_job_id, "tail_bytes": 2048}},
            )
            assert "ok model=" in handoff_collect["result"]["structuredContent"]["stdout_tail"]
            assert "message=late-ok" in handoff_collect["result"]["structuredContent"]["stdout_tail"]

            silent_resp = client.request(
                "tools/call",
                {
                    "name": "opencode_run_task",
                    "arguments": {
                        "instruction": "silent-hang",
                        "model": "Mify-Aili/tongyi/qwen3.6-plus-2026-04-02",
                        "encrypted_file": str(encrypted_file),
                        "pass_file": str(pass_file),
                        "timeout_sec": 1,
                    },
                },
            )
            silent_structured = silent_resp["result"]["structuredContent"]
            assert silent_structured["ok"] is False
            assert silent_structured["error"]["code"] == "timeout"
            assert silent_structured["job_id"]
            assert silent_structured["status"] == "running"
            assert silent_structured["timeout_context"]["likely_cause"] == "local_or_wrapper_startup"
            assert silent_structured["timeout_context"]["saw_output"] is False
            silent_cancel = client.request(
                "tools/call",
                {"name": "opencode_cancel_task", "arguments": {"job_id": silent_structured["job_id"]}},
            )
            assert silent_cancel["result"]["structuredContent"]["job"]["status"] == "cancelled"

            submit_resp = client.request(
                "tools/call",
                {
                    "name": "opencode_submit_task",
                    "arguments": {
                        "instruction": "sleepy",
                        "model": "Mify-Kimi/Pro/moonshotai/Kimi-K2.5",
                        "encrypted_file": str(encrypted_file),
                        "pass_file": str(pass_file),
                        "timeout_sec": 60,
                    },
                },
            )
            job_id = submit_resp["result"]["structuredContent"]["job_id"]

            cancel_resp = client.request(
                "tools/call",
                {"name": "opencode_cancel_task", "arguments": {"job_id": job_id}},
            )
            assert cancel_resp["result"]["structuredContent"]["job"]["status"] == "cancelled"

            collect_resp = client.request(
                "tools/call",
                {"name": "opencode_collect_artifacts", "arguments": {"job_id": job_id, "tail_bytes": 2048}},
            )
            assert collect_resp["result"]["structuredContent"]["job_id"] == job_id

            restart_resp = client.request(
                "tools/call",
                {
                    "name": "opencode_submit_task",
                    "arguments": {
                        "instruction": "restart-case",
                        "model": "Mify-Kimi/Pro/moonshotai/Kimi-K2.5",
                        "encrypted_file": str(encrypted_file),
                        "pass_file": str(pass_file),
                        "timeout_sec": 60,
                    },
                },
            )
            restart_job_id = restart_resp["result"]["structuredContent"]["job_id"]
            time.sleep(0.5)
            proc.kill()
            proc.wait(timeout=10)

            proc, client = start_server_client(env)
            time.sleep(2.5)
            restart_get = client.request(
                "tools/call",
                {"name": "opencode_get_task", "arguments": {"job_id": restart_job_id}},
            )
            restart_structured = restart_get["result"]["structuredContent"]
            assert restart_structured["job"]["status"] == "succeeded"
            assert restart_structured["job"]["exit_code"] == 0
            assert "restart-end" in restart_structured["stdout_tail"]
        finally:
            assert proc.stdin is not None
            proc.stdin.close()
            proc.wait(timeout=10)
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
            if stderr.strip():
                print(stderr)
        print("ok")
        return 0
    finally:
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    raise SystemExit(main())
