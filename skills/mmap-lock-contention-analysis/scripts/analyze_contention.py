#!/usr/bin/env python3
"""
Complete contention chain analysis for mmap_lock / per-VMA lock.

Finds Thread 1 (fault), Thread 2 (VMA writer), and Thread 3 (blocked on mmap_lock).
Outputs structured contention chains with delay attribution.
"""
import argparse
import json
import re
from collections import defaultdict, Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class ContentionChain:
    fault_pid: int = 0
    fault_comm: str = ""
    fault_ts: float = 0.0
    fault_addr: int = 0
    fault_ino: int = 0
    write_pid: int = 0
    write_comm: str = ""
    write_ts: float = 0.0
    write_caller: str = ""
    wait_ns: float = 0.0
    blocked_pids: list = field(default_factory=list)
    blocked_comms: list = field(default_factory=list)
    blocked_syscalls: list = field(default_factory=list)
    vma_start: int = 0
    vma_end: int = 0


def parse_trace(trace_file):
    events = []
    with open(trace_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if 'filemap_fault_begin' in line:
                m = re.search(
                    r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+filemap_fault_begin:'
                    r'.*address=([0-9a-f]+).*ino=([0-9a-f]+)',
                    line
                )
                if m:
                    comm, pid, ts, addr, ino = m.groups()
                    events.append(('fault', int(pid), comm, float(ts),
                                   int(addr, 16), int(ino, 16)))
                else:
                    m2 = re.search(
                        r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+filemap_fault_begin:'
                        r'.*address=([0-9a-f]+)',
                        line
                    )
                    if m2:
                        comm, pid, ts, addr = m2.groups()
                        events.append(('fault', int(pid), comm, float(ts),
                                       int(addr, 16), 0))

            elif 'vma_start_write_begin' in line:
                m = re.search(
                    r'pid=(\d+).*comm=(\S+).*vm_start=([0-9a-f]+)'
                    r'.*vm_end=([0-9a-f]+).*caller=(\S+).*ino=([0-9a-f]+)',
                    line
                )
                if m:
                    pid, comm, vm_start, vm_end, caller, ino = m.groups()
                    events.append(('write', int(pid), comm, 0.0,
                                   int(vm_start, 16), int(vm_end, 16),
                                   caller, int(ino, 16)))
                else:
                    m2 = re.search(
                        r'pid=(\d+).*comm=(\S+).*vm_start=([0-9a-f]+)'
                        r'.*vm_end=([0-9a-f]+).*caller=(\S+)',
                        line
                    )
                    if m2:
                        pid, comm, vm_start, vm_end, caller = m2.groups()
                        events.append(('write', int(pid), comm, 0.0,
                                       int(vm_start, 16), int(vm_end, 16),
                                       caller, 0))

            elif 'mmap_lock_write_blocked' in line:
                m = re.search(
                    r'holder_pid=(\d+).*waiter_pid=(\d+).*wait_ns=(\d+)',
                    line
                )
                if m:
                    holder_pid, waiter_pid, wait_ns = m.groups()
                    events.append(('blocked', int(holder_pid), '', 0.0,
                                   int(waiter_pid), int(wait_ns)))

            elif 'sys_enter' in line:
                m = re.search(
                    r'(\S+)-(\d+)\s+.*sys_enter_(\w+)',
                    line
                )
                if m:
                    comm, pid, syscall = m.groups()
                    events.append(('syscall', int(pid), comm, 0.0, syscall))

    return events


def find_contention_chains(events, time_window_ms=1.0):
    write_events = [e for e in events if e[0] == 'write' and e[-1] > 0]
    fault_events = [e for e in events if e[0] == 'fault']
    blocked_events = [e for e in events if e[0] == 'blocked']
    syscall_events = [e for e in events if e[0] == 'syscall']

    chains = []

    for w in write_events:
        _, wpid, wcomm, _, wstart, wend, wcaller, wino = w

        for f in fault_events:
            _, fpid, fcomm, fts, faddr, fino = f
            if fpid == wpid and wstart <= faddr < wend:
                blocked = []
                for b in blocked_events:
                    _, holder_pid, _, _, waiter_pid, wait_ns = b
                    if holder_pid == wpid:
                        blocked.append((waiter_pid, wait_ns))

                syscalls = []
                for s in syscall_events:
                    _, spid, scomm, _, syscall = s
                    if spid == wpid:
                        syscalls.append(syscall)

                chains.append(ContentionChain(
                    fault_pid=fpid,
                    fault_comm=fcomm,
                    fault_ts=fts,
                    fault_addr=faddr,
                    fault_ino=fino,
                    write_pid=wpid,
                    write_comm=wcomm,
                    write_ts=0.0,
                    write_caller=wcaller,
                    wait_ns=0.0,
                    blocked_pids=[b[0] for b in blocked],
                    blocked_comms=[],
                    blocked_syscalls=syscalls,
                    vma_start=wstart,
                    vma_end=wend,
                ))
                break

    return chains


def analyze_wait_times(trace_file):
    pid_wait_times = defaultdict(list)
    current_wait = {}

    with open(trace_file) as f:
        for line in f:
            if 'vma_start_write_wait_start' in line or \
               'vma_start_write_wait_end' in line:
                parts = line.strip().split()
                ts = None
                for p in parts:
                    try:
                        ts = float(p.rstrip(':'))
                        if ts > 1000:
                            break
                    except ValueError:
                        continue
                if ts is None:
                    continue

                pid = parts[0].split('-')[-1] if '-' in parts[0] else "0"

                if 'wait_start' in line:
                    current_wait[pid] = ts
                elif 'wait_end' in line and pid in current_wait:
                    wait_ms = (ts - current_wait[pid]) * 1000
                    pid_wait_times[pid].append(wait_ms)
                    del current_wait[pid]

    return pid_wait_times


def summarize(trace_file):
    events = parse_trace(trace_file)
    chains = find_contention_chains(events)
    pid_wait_times = analyze_wait_times(trace_file)

    print(f"=== Contention Summary ===")
    print(f"Total events parsed: {len(events)}")
    print(f"Contention chains found: {len(chains)}")

    by_process = defaultdict(list)
    for c in chains:
        by_process[c.fault_comm].append(c)

    print(f"\n=== By Process ===")
    for proc, proc_chains in sorted(by_process.items(), key=lambda x: -len(x[1]))[:10]:
        print(f"  {proc}: {len(proc_chains)} chains")
        c = proc_chains[0]
        print(f"    Thread 1 (fault): PID={c.fault_pid}")
        print(f"    Thread 2 (write): PID={c.write_pid} caller={c.write_caller}")
        if c.blocked_pids:
            print(f"    Thread 3 (blocked): PIDs={c.blocked_pids}")
        if c.blocked_syscalls:
            syscalls = Counter(c.blocked_syscalls)
            print(f"    Syscalls: {syscalls.most_common(5)}")
        print(f"    VMA range: [{hex(c.vma_start)}, {hex(c.vma_end)}]")
        print(f"    File: ino={c.fault_ino}")

    all_waits = []
    for pid, waits in sorted(pid_wait_times.items(), key=lambda x: -len(x[1]))[:15]:
        avg = sum(waits) / len(waits)
        max_wait = max(waits)
        all_waits.extend(waits)
        print(f"\n  PID {pid}: {len(waits)} waits, avg={avg:.3f}ms, max={max_wait:.3f}ms")

    if all_waits:
        print(f"\n=== Overall Wait Stats ===")
        print(f"Total waits: {len(all_waits)}")
        print(f"Avg: {sum(all_waits)/len(all_waits):.3f}ms")
        print(f"Max: {max(all_waits):.3f}ms")
        print(f">1ms: {sum(1 for w in all_waits if w > 1)}")
        print(f">10ms: {sum(1 for w in all_waits if w > 10)}")

    json_path = Path(trace_file).with_suffix('.summary.json')
    summary = {
        "trace_file": trace_file,
        "total_events": len(events),
        "contention_chains": len(chains),
        "by_process": {
            k: len(v) for k, v in by_process.items()
        },
        "wait_stats": {
            "total_waits": len(all_waits),
            "avg_ms": round(sum(all_waits)/len(all_waits), 3) if all_waits else 0,
            "max_ms": round(max(all_waits), 3) if all_waits else 0,
            "over_1ms": sum(1 for w in all_waits if w > 1),
            "over_10ms": sum(1 for w in all_waits if w > 10),
        },
        "chains": [asdict(c) for c in chains[:100]],
    }
    with open(json_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary written to: {json_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze mmap_lock contention from trace data'
    )
    parser.add_argument('trace_file', help='Path to trace_stream.txt')
    args = parser.parse_args()

    summarize(args.trace_file)


if __name__ == '__main__':
    main()
