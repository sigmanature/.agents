#!/usr/bin/env python3
"""
Trace pipe capture with intelligent chunking.

Guarantees line integrity, supports size/line dual limits,
optional gzip compression, and graceful shutdown.

Usage:
    python3 capture_trace_pipe.py --outdir /tmp/mmap_lock_trace --compress
"""
import argparse
import gzip
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class TracePipeCapture:
    def __init__(self, outdir, chunk_lines=500000, chunk_size_mb=500,
                 compress=False, serial=None, adb_timeout_sec=10):
        self.outdir = Path(outdir)
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.chunk_lines = chunk_lines
        self.chunk_size = chunk_size_mb * 1024 * 1024
        self.compress = compress
        self.serial = serial
        self.adb_timeout_sec = adb_timeout_sec

        self.chunk_idx = 0
        self.line_count = 0
        self.byte_count = 0
        self.current_file = None
        self.current_path = None
        self.proc = None
        self._shutdown = False

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        print(f"\nReceived signal {signum}, shutting down gracefully...", file=sys.stderr)
        self._shutdown = True
        if self.proc:
            self.proc.terminate()

    def _open_new_chunk(self):
        if self.current_file:
            self.current_file.close()
            print(f"Closed: {self.current_path} ({self.line_count} lines, {self.byte_count} bytes)",
                  file=sys.stderr)

        ts = time.strftime("%Y%m%d_%H%M%S")
        suffix = ".txt.gz" if self.compress else ".txt"
        self.current_path = self.outdir / f"trace_stream_{ts}_{self.chunk_idx:04d}{suffix}"

        if self.compress:
            self.current_file = gzip.open(self.current_path, 'wt', compresslevel=1)
        else:
            self.current_file = open(self.current_path, 'w')

        self.line_count = 0
        self.byte_count = 0
        self.chunk_idx += 1
        print(f"Opened: {self.current_path}", file=sys.stderr)

    def _build_adb_cmd(self):
        cmd = ["adb"]
        if self.serial:
            cmd.extend(["-s", self.serial])
        cmd.extend(["exec-out", "su -c 'cat /sys/kernel/debug/tracing/trace_pipe'"])
        return cmd

    def run(self):
        self._open_new_chunk()

        cmd = self._build_adb_cmd()
        print(f"Starting: {' '.join(cmd)}", file=sys.stderr)

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1
        )

        try:
            for line in self.proc.stdout:
                if self._shutdown:
                    break

                line_bytes = len(line.encode('utf-8', errors='replace'))

                if self.line_count >= self.chunk_lines or \
                   (self.byte_count + line_bytes) > self.chunk_size:
                    self._open_new_chunk()

                self.current_file.write(line)
                self.line_count += 1
                self.byte_count += line_bytes

                if self.line_count % 1000 == 0:
                    self.current_file.flush()

        except BrokenPipeError:
            print("ADB pipe broken. Exiting.", file=sys.stderr)
        finally:
            self._cleanup()

    def _cleanup(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

        if self.current_file:
            self.current_file.close()
            print(f"Final chunk: {self.current_path} ({self.line_count} lines, {self.byte_count} bytes)",
                  file=sys.stderr)

        print(f"Capture complete. Output directory: {self.outdir}", file=sys.stderr)
        print(f"Total chunks: {self.chunk_idx}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description='Capture trace_pipe with intelligent chunking'
    )
    parser.add_argument('--outdir', required=True,
                        help='Output directory for trace chunks')
    parser.add_argument('--chunk-lines', type=int, default=500000,
                        help='Maximum lines per chunk (default: 500000)')
    parser.add_argument('--chunk-size-mb', type=int, default=500,
                        help='Maximum size per chunk in MB (default: 500)')
    parser.add_argument('--compress', action='store_true',
                        help='Enable gzip compression (level=1, fast)')
    parser.add_argument('--serial',
                        help='ADB device serial number')
    args = parser.parse_args()

    capture = TracePipeCapture(
        outdir=args.outdir,
        chunk_lines=args.chunk_lines,
        chunk_size_mb=args.chunk_size_mb,
        compress=args.compress,
        serial=args.serial
    )
    capture.run()


if __name__ == '__main__':
    main()
