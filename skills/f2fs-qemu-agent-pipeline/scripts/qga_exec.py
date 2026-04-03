#!/usr/bin/env python3
import argparse
import base64
import json
import socket
import sys
import time

SOCK = "/tmp/qga.sock"


def qga_call(obj):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall((json.dumps(obj) + "\n").encode())
    data = b""
    # Read one JSON line (QGA is typically request/response)
    while not data.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data.decode().strip())


def exec_in_vm(cmd, poll_interval=0.1, timeout=30, capture_output=True):
    r = qga_call(
        {
            "execute": "guest-exec",
            "arguments": {
                "path": "/bin/bash",
                "arg": ["-lc", cmd],
                "capture-output": bool(capture_output),
            },
        }
    )
    pid = r["return"]["pid"]

    deadline = time.time() + timeout
    while True:
        st = qga_call({"execute": "guest-exec-status", "arguments": {"pid": pid}})
        ret = st["return"]
        if ret.get("exited"):
            out_b64 = ret.get("out-data", "")
            err_b64 = ret.get("err-data", "")
            out = base64.b64decode(out_b64).decode(errors="replace") if out_b64 else ""
            err = base64.b64decode(err_b64).decode(errors="replace") if err_b64 else ""
            return ret.get("exitcode", -1), out, err
        if time.time() > deadline:
            raise TimeoutError(f"command timed out (pid={pid})")
        time.sleep(poll_interval)


def main(argv):
    p = argparse.ArgumentParser(description="Execute a command in the QEMU guest via QGA")
    p.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait for command completion")
    p.add_argument("--poll", type=float, default=0.1, help="Polling interval in seconds")
    p.add_argument(
        "--no-capture",
        action="store_true",
        help="Disable QGA stdout/stderr capture (use guest-side redirection instead)",
    )
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run (passed to /bin/bash -lc)")
    ns = p.parse_args(argv)

    cmd = " ".join(ns.cmd).strip() if ns.cmd else "id; uname -a"
    code, out, err = exec_in_vm(
        cmd,
        poll_interval=ns.poll,
        timeout=ns.timeout,
        capture_output=(not ns.no_capture),
    )
    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
