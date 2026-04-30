#!/usr/bin/env python3
import argparse
import base64
import json
import os
import socket
import sys


DEFAULT_SOCK = "/tmp/qga.sock"


def qga_call(obj, sock_path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    try:
        sock.sendall((json.dumps(obj) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    return json.loads(data.decode().strip())


def qga_return(obj):
    if "error" in obj:
        err = obj["error"]
        desc = err.get("desc") or err.get("class") or err
        raise RuntimeError(f"QGA error: {desc}")
    return obj["return"]


def pull_file(sock_path, guest_path, host_path, chunk_size, progress):
    os.makedirs(os.path.dirname(os.path.abspath(host_path)) or ".", exist_ok=True)
    handle = qga_return(
        qga_call(
            {
                "execute": "guest-file-open",
                "arguments": {"path": guest_path, "mode": "rb"},
            },
            sock_path,
        )
    )
    total = 0
    try:
        with open(host_path, "wb") as out:
            while True:
                ret = qga_return(
                    qga_call(
                        {
                            "execute": "guest-file-read",
                            "arguments": {"handle": handle, "count": chunk_size},
                        },
                        sock_path,
                    )
                )
                data = base64.b64decode(ret.get("buf-b64", ""))
                if data:
                    out.write(data)
                    total += len(data)
                    if progress and total // progress != (total - len(data)) // progress:
                        print(f"pulled {total} bytes", file=sys.stderr, flush=True)
                if ret.get("eof"):
                    break
        return total
    finally:
        qga_call({"execute": "guest-file-close", "arguments": {"handle": handle}}, sock_path)


def main(argv):
    parser = argparse.ArgumentParser(description="Pull a guest file through QEMU Guest Agent")
    parser.add_argument("--sock", default=os.environ.get("QGA_SOCK", DEFAULT_SOCK))
    parser.add_argument("--chunk-size", type=int, default=1024 * 1024)
    parser.add_argument("--progress", type=int, default=64 * 1024 * 1024)
    parser.add_argument("guest_path")
    parser.add_argument("host_path")
    ns = parser.parse_args(argv)
    total = pull_file(ns.sock, ns.guest_path, ns.host_path, ns.chunk_size, ns.progress)
    print(f"pulled {total} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
