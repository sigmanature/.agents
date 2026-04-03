#!/usr/bin/env python3
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def wait_for_port(port: int, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        finally:
            sock.close()
        time.sleep(0.1)
    return False


def http_post(url: str, body: dict, headers: dict | None = None) -> tuple[int, dict, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            **(headers or {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8", errors="replace")


def parse_event_stream_body(raw: str):
    data_lines = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            data_lines.append(line[len("data: "):])
    if not data_lines:
        raise ValueError("no data: lines found in event-stream body")
    return json.loads("\n".join(data_lines))


def read_listener_snapshot(port: int) -> str:
    try:
        result = subprocess.run(
            ["ss", "-ltnp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return f"ss failed: {exc}"

    wanted = f":{port}"
    lines = [ln for ln in result.stdout.splitlines() if wanted in ln]
    return "\n".join(lines) if lines else "no ss listener line found"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=os.path.expanduser("~/.local/bin/open-websearch"))
    ap.add_argument("--port", type=int, default=39030)
    ap.add_argument("--query", default="openai")
    ap.add_argument("--engine", default="bing")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--startup-timeout", type=float, default=12.0)
    args = ap.parse_args()

    env = os.environ.copy()
    env["MODE"] = "http"
    env["PORT"] = str(args.port)
    env["ENABLE_CORS"] = "false"

    proc = subprocess.Popen(
        [args.exe],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    lines: list[str] = []
    startup_ok = False
    startup_error = None
    start_deadline = time.time() + args.startup_timeout

    try:
        while time.time() < start_deadline:
            line = proc.stdout.readline()
            if line:
                lines.append(line.rstrip("\n"))
                if f"HTTP server running on port {args.port}" in line:
                    startup_ok = True
                    break
            elif proc.poll() is not None:
                startup_error = f"process exited early with code {proc.returncode}"
                break
            else:
                time.sleep(0.05)

        if not startup_ok and startup_error is None:
            startup_ok = wait_for_port(args.port, 2.0)
            if not startup_ok:
                startup_error = "timeout waiting for HTTP listener"

        result: dict[str, object] = {
            "exe": args.exe,
            "port": args.port,
            "startup_ok": startup_ok,
            "startup_error": startup_error,
            "startup_log_tail": lines[-20:],
            "listener_snapshot": read_listener_snapshot(args.port),
        }

        if startup_ok:
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "probe-open-websearch", "version": "0.1"},
                },
            }
            try:
                status, headers, body = http_post(f"http://127.0.0.1:{args.port}/mcp", init_req)
                init_result = {
                    "status": status,
                    "headers": headers,
                    "raw_body": body,
                }
                try:
                    if headers.get("content-type", "").startswith("text/event-stream"):
                        init_result["body"] = parse_event_stream_body(body)
                    else:
                        init_result["body"] = json.loads(body)
                except Exception as exc:
                    init_result["body_parse_error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                result["initialize"] = init_result
                session_id = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")

                if session_id:
                    notify_req = {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                    try:
                        http_post(
                            f"http://127.0.0.1:{args.port}/mcp",
                            notify_req,
                            headers={"mcp-session-id": session_id},
                        )
                    except Exception as exc:
                        result["initialized_notification_error"] = str(exc)

                    tool_req = {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "search",
                            "arguments": {
                                "query": args.query,
                                "limit": args.limit,
                                "engines": [args.engine],
                            },
                        },
                    }
                    try:
                        status, headers, body = http_post(
                            f"http://127.0.0.1:{args.port}/mcp",
                            tool_req,
                            headers={"mcp-session-id": session_id},
                        )
                        if headers.get("content-type", "").startswith("text/event-stream"):
                            parsed_body = parse_event_stream_body(body)
                        else:
                            parsed_body = json.loads(body)
                        result["search_call"] = {
                            "status": status,
                            "headers": headers,
                            "body": parsed_body,
                        }
                    except urllib.error.HTTPError as exc:
                        result["search_call_error"] = {
                            "type": "HTTPError",
                            "status": exc.code,
                            "body": exc.read().decode("utf-8", errors="replace"),
                        }
                    except Exception as exc:
                        result["search_call_error"] = {"type": type(exc).__name__, "message": str(exc)}
                else:
                    result["initialize_missing_session_id"] = True
            except urllib.error.HTTPError as exc:
                result["initialize_error"] = {
                    "type": "HTTPError",
                    "status": exc.code,
                    "body": exc.read().decode("utf-8", errors="replace"),
                }
            except Exception as exc:
                result["initialize_error"] = {"type": type(exc).__name__, "message": str(exc)}

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


if __name__ == "__main__":
    sys.exit(main())
