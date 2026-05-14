#!/usr/bin/env python3
"""
Complete contention chain analysis for mmap_lock / per-VMA lock v2.

Uses mm matching instead of PID matching to correctly associate
filemap_fault (binder threads) with vma_start_write (worker threads).
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
    fault_tgid: int = 0
    fault_ts: float = 0.0
    fault_addr: int = 0
    fault_ino: int = 0
    fault_pgoff: int = 0
    fault_major: int = 0
    fault_minor: int = 0
    write_pid: int = 0
    write_comm: str = ""
    write_tgid: int = 0
    write_ts: float = 0.0
    write_caller: str = ""
    vma_wait_ms: float = 0.0
    mmap_lock_wait_ms: float = 0.0
    blocked_pids: list = field(default_factory=list)
    blocked_comms: list = field(default_factory=list)
    blocked_syscalls: list = field(default_factory=list)
    vma_start: int = 0
    vma_end: int = 0
    mm: str = ""
    contention_type: str = ""
    # optional stacks captured from trace_pipe for writer and fault contexts
    writer_stack: list = field(default_factory=list)
    fault_stack: list = field(default_factory=list)


def parse_trace_v2(trace_file):
    """Parse trace with mm field support."""
    events = []
    # Read all lines so we can attach contiguous stack lines following an event
    with open(trace_file) as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip('\n')
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        # Helper to attach subsequent contiguous stack lines to payload
        def attach_stack(payload, start_index):
            j = start_index
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    break
                # typical ftrace kernel stack lines start with whitespace and '=>'
                if nxt.lstrip().startswith('=>') or re.match(r'^\s+=>', nxt):
                    frame = nxt.strip()
                    stack = payload.setdefault('stack', [])
                    # cap stored frames in-memory to avoid runaway memory use
                    if len(stack) < 128:
                        stack.append(frame)
                    j += 1
                    continue
                # Some setups include lines like '    [<f>] func+0x123/0x456' - be conservative
                if re.match(r'^\s+\[<.*>\]\s+\S+', nxt):
                    frame = nxt.strip()
                    stack = payload.setdefault('stack', [])
                    if len(stack) < 128:
                        stack.append(frame)
                    j += 1
                    continue
                break
            return j

        # filemap_fault_begin
        if 'filemap_fault_begin' in line:
            m = re.search(
                r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+filemap_fault_begin:'
                r'.*?dev=(\d+):(\d+).*?ino=([0-9a-fx]+).*?pgoff=([0-9a-fx]+)'
                r'.*?address=([0-9a-fx]+).*?mm=([0-9a-fx]+).*?tgid=(\d+).*?comm=(\S+)',
                line
            )
            if m:
                comm, pid, ts, major, minor, ino, pgoff, addr, mm, tgid, comm2 = m.groups()
                payload = {
                    'pid': int(pid), 'comm': comm, 'ts': float(ts),
                    'addr': int(addr, 16), 'address': int(addr, 16),
                    'pgoff': int(pgoff, 16), 'mm': mm, 'tgid': int(tgid),
                    'ino': int(ino, 16), 'major': int(major), 'minor': int(minor),
                    'stack': []
                }
                events.append(('fault_begin', payload))
                i = attach_stack(payload, i + 1)
                continue
            else:
                m2 = re.search(
                    r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+filemap_fault_begin:.*address=([0-9a-fx]+)',
                    line
                )
                if m2:
                    comm, pid, ts, addr = m2.groups()
                    payload = {
                        'pid': int(pid), 'comm': comm, 'ts': float(ts),
                        'addr': int(addr, 16), 'address': int(addr, 16),
                        'pgoff': 0, 'mm': '', 'tgid': 0, 'ino': 0, 'major': 0, 'minor': 0,
                        'stack': []
                    }
                    events.append(('fault_begin', payload))
                    i = attach_stack(payload, i + 1)
                    continue

        elif 'vma_start_write_begin' in line:
                m = re.search(
                    r'pid=(\d+).*comm=(\S+).*mm=([0-9a-f]+)'
                    r'.*vm_start=([0-9a-f]+).*vm_end=([0-9a-f]+)'
                    r'.*caller=(\S+)',
                    line
                )
                if m:
                    pid, comm, mm, vm_start, vm_end, caller = m.groups()
                    payload = {
                        'pid': int(pid), 'comm': comm, 'mm': mm,
                        'vm_start': int(vm_start, 16), 'vm_end': int(vm_end, 16), 'caller': caller,
                        'ts': None, 'stack': []
                    }
                    events.append(('write_begin', payload))
                    i = attach_stack(payload, i + 1)
                    continue

        elif 'vma_start_write_wait_start' in line:
                # capture pid/tgid/comm/ino/dev if present (kernel may log them)
                m = re.search(
                    r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+vma_start_write_wait_start:.*?vm_start=([0-9a-f]+).*?vm_end=([0-9a-f]+).*?mm=([0-9a-f]+).*?caller=(\S+).*',
                    line
                )
                if m:
                    comm, pid, ts, vm_start, vm_end, mm, caller = m.groups()
                    # try to find ino/dev if present later in printk; fallback to 0
                    ino = 0
                    major = 0
                    minor = 0
                    m2 = re.search(r'ino=(\d+)|ino=(0x[0-9a-fA-F]+)', line)
                    if m2:
                        try:
                            ino = int(m2.group(1) or m2.group(2), 0)
                        except Exception:
                            ino = 0
                    m3 = re.search(r'dev=(\d+):(\d+)', line)
                    if m3:
                        major = int(m3.group(1)); minor = int(m3.group(2))
                    payload = {
                        'pid': int(pid), 'comm': comm, 'ts': float(ts), 'mm': mm,
                        'vm_start': int(vm_start, 16), 'vm_end': int(vm_end, 16), 'caller': caller,
                        'ino': ino, 'major': major, 'minor': minor, 'stack': []
                    }
                    events.append(('write_wait_start', payload))
                    i = attach_stack(payload, i + 1)
                    continue
                else:
                    # fallback: timestamp + mm
                    m4 = re.search(r'(\d+\.\d+):\s+vma_start_write_wait_start:.*mm=([0-9a-f]+)', line)
                    if m4:
                        ts, mm = m4.groups()
                        payload = {'ts': float(ts), 'mm': mm, 'stack': []}
                        events.append(('write_wait_start', payload))
                        i = attach_stack(payload, i + 1)
                        continue

        elif 'vma_start_write_wait_end' in line:
                m = re.search(r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+vma_start_write_wait_end:.*?vm_start=([0-9a-f]+).*?vm_end=([0-9a-f]+).*?mm=([0-9a-f]+).*?caller=(\S+)', line)
                if m:
                    comm, pid, ts, vm_start, vm_end, mm, caller = m.groups()
                    ino = 0; major = 0; minor = 0
                    m2 = re.search(r'ino=(\d+)|ino=(0x[0-9a-fA-F]+)', line)
                    if m2:
                        try:
                            ino = int(m2.group(1) or m2.group(2), 0)
                        except Exception:
                            ino = 0
                    m3 = re.search(r'dev=(\d+):(\d+)', line)
                    if m3:
                        major = int(m3.group(1)); minor = int(m3.group(2))
                    payload = {
                        'pid': int(pid), 'comm': comm, 'ts': float(ts), 'mm': mm,
                        'vm_start': int(vm_start, 16), 'vm_end': int(vm_end, 16), 'caller': caller,
                        'ino': ino, 'major': major, 'minor': minor, 'stack': []
                    }
                    events.append(('write_wait_end', payload))
                    i = attach_stack(payload, i + 1)
                    continue
                else:
                    m4 = re.search(r'(\d+\.\d+):\s+vma_start_write_wait_end:.*mm=([0-9a-f]+)', line)
                    if m4:
                        ts, mm = m4.groups()
                        payload = {'ts': float(ts), 'mm': mm, 'stack': []}
                        events.append(('write_wait_end', payload))
                        i = attach_stack(payload, i + 1)
                        continue

        elif 'mmap_lock_wait_start' in line:
                m = re.search(
                    r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+mmap_lock_wait_start:'
                    r'.*mm=([0-9a-f]+).*write=(\w+)',
                    line
                )
                if m:
                    comm, pid, ts, mm, write = m.groups()
                    payload = {'pid': int(pid), 'comm': comm, 'ts': float(ts), 'mm': mm, 'write': write == 'true', 'stack': []}
                    events.append(('ml_wait_start', payload))
                    i = attach_stack(payload, i + 1)
                    continue

        elif 'mmap_lock_wait_end' in line:
                m = re.search(
                    r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+mmap_lock_wait_end:'
                    r'.*mm=([0-9a-f]+)',
                    line
                )
                if m:
                    comm, pid, ts, mm = m.groups()
                    payload = {'pid': int(pid), 'ts': float(ts), 'mm': mm, 'stack': []}
                    events.append(('ml_wait_end', payload))
                    i = attach_stack(payload, i + 1)
                    continue

            # filemap fault wait/end/retry/end events
        elif 'filemap_fault_wait_start' in line:
                m = re.search(r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+filemap_fault_wait_start:.*dev=(\d+):(\d+).*ino=([0-9a-fx]+).*pgoff=([0-9a-fx]+).*address=([0-9a-fx]+).*mm=([0-9a-fx]+).*tgid=(\d+)', line)
                if m:
                    comm, pid, ts, major, minor, ino, pgoff, address, mm, tgid = m.groups()
                    payload = {
                        'pid': int(pid), 'comm': comm, 'ts': float(ts),
                        'pgoff': int(pgoff, 16), 'address': int(address, 16), 'mm': mm, 'tgid': int(tgid),
                        'ino': int(ino, 16), 'major': int(major), 'minor': int(minor), 'stack': []
                    }
                    events.append(('fault_wait_start', payload))
                    i = attach_stack(payload, i + 1)
                    continue
                else:
                    m2 = re.search(r'(\d+\.\d+):\s+filemap_fault_wait_start:.*pgoff=([0-9a-fx]+)', line)
                    if m2:
                        ts, pgoff = m2.groups()
                        payload = {'ts': float(ts), 'pgoff': int(pgoff, 16), 'stack': []}
                        events.append(('fault_wait_start', payload))
                        i = attach_stack(payload, i + 1)
                        continue

        elif 'filemap_fault_wait_end' in line:
                m = re.search(r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+filemap_fault_wait_end:.*dev=(\d+):(\d+).*ino=([0-9a-fx]+).*pgoff=([0-9a-fx]+).*address=([0-9a-fx]+).*mm=([0-9a-fx]+).*tgid=(\d+)', line)
                if m:
                    comm, pid, ts, major, minor, ino, pgoff, address, mm, tgid = m.groups()
                    payload = {
                        'pid': int(pid), 'comm': comm, 'ts': float(ts),
                        'pgoff': int(pgoff, 16), 'address': int(address, 16), 'mm': mm, 'tgid': int(tgid),
                        'ino': int(ino, 16), 'major': int(major), 'minor': int(minor), 'stack': []
                    }
                    events.append(('fault_wait_end', payload))
                    i = attach_stack(payload, i + 1)
                    continue

        elif 'filemap_fault_end' in line:
                m = re.search(r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+filemap_fault_end:.*dev=(\d+):(\d+).*ino=([0-9a-fx]+).*pgoff=([0-9a-fx]+).*address=([0-9a-fx]+).*mm=([0-9a-fx]+).*tgid=(\d+)', line)
                if m:
                    comm, pid, ts, major, minor, ino, pgoff, address, mm, tgid = m.groups()
                    payload = {
                        'pid': int(pid), 'comm': comm, 'ts': float(ts),
                        'pgoff': int(pgoff, 16), 'address': int(address, 16), 'mm': mm, 'tgid': int(tgid),
                        'ino': int(ino, 16), 'major': int(major), 'minor': int(minor), 'stack': []
                    }
                    events.append(('fault_end', payload))
                    i = attach_stack(payload, i + 1)
                    continue

        elif 'filemap_fault_retry' in line:
                m = re.search(r'(\S+)-(\d+)\s+.*?(\d+\.\d+):\s+filemap_fault_retry:.*dev=(\d+):(\d+).*ino=([0-9a-fx]+).*pgoff=([0-9a-fx]+).*address=([0-9a-fx]+).*mm=([0-9a-fx]+).*tgid=(\d+)', line)
                if m:
                    comm, pid, ts, major, minor, ino, pgoff, address, mm, tgid = m.groups()
                    payload = {
                        'pid': int(pid), 'comm': comm, 'ts': float(ts),
                        'pgoff': int(pgoff, 16), 'address': int(address, 16), 'mm': mm, 'tgid': int(tgid),
                        'ino': int(ino, 16), 'major': int(major), 'minor': int(minor), 'stack': []
                    }
                    events.append(('fault_retry', payload))
                    i = attach_stack(payload, i + 1)
                    continue

        # If we reached here, no header matched; advance by one
        i += 1
    return events


def build_event_index(events):
    """Index events by mm for fast lookup."""
    writes_by_mm = defaultdict(list)
    faults_by_mm = defaultdict(list)
    wait_starts = {}
    wait_ends = {}
    ml_wait_starts = {}
    ml_wait_ends = {}

    # Events are recorded as (type, payload) where payload is a dict for richer fields
    for e in events:
        etype = e[0]
        payload = e[1] if len(e) > 1 else None

        if etype == 'write_begin':
            w = payload
            mm = w.get('mm', '')
            writes_by_mm[mm].append({
                'pid': w.get('pid', 0), 'comm': w.get('comm', ''), 'mm': mm,
                'vm_start': w.get('vm_start', 0), 'vm_end': w.get('vm_end', 0),
                'caller': w.get('caller', ''), 'wait_ms': 0.0,
                'wait_start_ts': None, 'wait_end_ts': None,
                'stack': list(w.get('stack', []))
            })

        elif etype == 'write_wait_start':
            w = payload
            mm = w.get('mm', '')
            # try to attach to an existing write with same vm range
            attached = False
            for wr in writes_by_mm.get(mm, []):
                if ('vm_start' in wr and 'vm_end' in wr and
                        wr.get('vm_start') == w.get('vm_start') and wr.get('vm_end') == w.get('vm_end')):
                    wr['wait_start_ts'] = w.get('ts')
                    if not wr.get('stack') and w.get('stack'):
                        wr['stack'] = list(w.get('stack', []))
                    attached = True
                    break
            if not attached:
                # create placeholder write entry
                writes_by_mm[mm].append({
                    'pid': w.get('pid', 0), 'comm': w.get('comm', ''), 'mm': mm,
                    'vm_start': w.get('vm_start', 0), 'vm_end': w.get('vm_end', 0),
                    'caller': w.get('caller', ''), 'wait_ms': 0.0,
                    'wait_start_ts': w.get('ts'), 'wait_end_ts': None,
                    'stack': list(w.get('stack', []))
                })

        elif etype == 'write_wait_end':
            w = payload
            mm = w.get('mm', '')
            for wr in writes_by_mm.get(mm, []):
                if wr.get('vm_start') == w.get('vm_start') and wr.get('vm_end') == w.get('vm_end'):
                    wr['wait_end_ts'] = w.get('ts')
                    # compute wait_ms if start present
                    if wr.get('wait_start_ts') is not None:
                        wr['wait_ms'] = max(0.0, (wr['wait_end_ts'] - wr['wait_start_ts']) * 1000)
                    break

        elif etype in ('fault_begin', 'fault_wait_start', 'fault_wait_end', 'fault_end', 'fault_retry'):
            f = payload
            mm = f.get('mm', '')
            # match existing fault by address+pgoff when possible
            addr = f.get('address') or f.get('addr')
            pgoff = f.get('pgoff')
            matched = False
            if mm and addr is not None:
                for existing in faults_by_mm.get(mm, []):
                    if existing.get('address') == addr and existing.get('pgoff') == pgoff:
                        # update fields
                        existing.update(f)
                        matched = True
                        break
            if not matched:
                # create a new fault instance
                fault_entry = {
                    'pid': f.get('pid', 0),
                    'comm': f.get('comm', ''),
                    'ts': f.get('ts', 0.0),
                    'address': addr or 0,
                    'pgoff': pgoff or 0,
                    'mm': mm,
                    'tgid': f.get('tgid', 0),
                    'ino': f.get('ino', 0),
                    'major': f.get('major', 0), 'minor': f.get('minor', 0),
                    'wait_start_ts': None, 'wait_end_ts': None, 'retry_ts': None, 'end_ts': None,
                    'stack': f.get('stack', [])
                }
                if etype == 'fault_wait_start':
                    fault_entry['wait_start_ts'] = f.get('ts')
                if etype == 'fault_wait_end':
                    fault_entry['wait_end_ts'] = f.get('ts')
                if etype == 'fault_end':
                    fault_entry['end_ts'] = f.get('ts')
                if etype == 'fault_retry':
                    fault_entry['retry_ts'] = f.get('ts')
                faults_by_mm[mm].append(fault_entry)

        elif etype == 'ml_wait_start':
            m = payload
            # store full payload so we keep stack info
            ml_wait_starts[(m.get('pid', 0), m.get('mm', ''))] = m

        elif etype == 'ml_wait_end':
            m = payload
            ml_wait_ends[(m.get('pid', 0), m.get('mm', ''))] = m.get('ts')

    # Calculate vma wait times
    for mm in wait_starts:
        if mm in wait_ends:
            wait_ms = (wait_ends[mm] - wait_starts[mm]) * 1000
            if mm in writes_by_mm and writes_by_mm[mm]:
                writes_by_mm[mm][-1]['wait_ms'] = wait_ms

    # Calculate mmap_lock wait times
    ml_waits = []
    for (pid, mm), m in ml_wait_starts.items():
        start_ts = m.get('ts')
        comm = m.get('comm')
        write = m.get('write', False)
        if (pid, mm) in ml_wait_ends:
            end_ts = ml_wait_ends[(pid, mm)]
            ml_waits.append({
                'pid': pid, 'mm': mm, 'comm': comm, 'write': write,
                'start': start_ts, 'end': end_ts,
                'wait_ms': (end_ts - start_ts) * 1000,
                'stack': m.get('stack', [])
            })

    return writes_by_mm, faults_by_mm, ml_waits


def caller_to_syscall(caller):
    if 'mmap_region' in caller:
        return 'mmap()'
    elif 'mprotect_fixup' in caller:
        return 'mprotect()'
    elif 'vms_gather_munmap_vmas' in caller or 'do_munmap' in caller:
        return 'munmap()/exit()'
    elif '__split_vma' in caller:
        return 'mremap()/split'
    elif 'vma_expand' in caller:
        return 'VMA expand'
    elif 'vma_modify' in caller:
        return 'VMA modify'
    elif 'do_brk_flags' in caller:
        return 'brk()/heap'
    elif 'free_pgtables' in caller:
        return 'munmap()/cleanup'
    elif 'dup_mmap' in caller:
        return 'fork()/clone()'
    elif 'madvise' in caller:
        return 'madvise()'
    else:
        return caller


def find_contention_chains_v2(writes_by_mm, faults_by_mm, ml_waits, time_window_ms=10.0):
    """Find Type-A contention: filemap_fault holds read lock while vma_start_write waits."""
    chains = []

    for mm, writes in writes_by_mm.items():
        faults = faults_by_mm.get(mm, [])
        if not faults:
            continue

        for w in writes:
            if w['wait_ms'] < 0.001:
                continue

            # Find competing faults: same mm, time overlap with wait window
            # We need to know when the write wait happened
            # Since we don't have exact ts in write event, use fault ts as proxy
            competing_faults = []
            for f in faults:
                if w['vm_start'] <= f['address'] < w['vm_end']:
                    competing_faults.append(f)

            if not competing_faults:
                continue

            # Find mmap_lock waits for this write
            mmap_lock_wait_ms = 0.0
            for mlw in ml_waits:
                if mlw['mm'] == mm and mlw['write']:
                    # Rough match: same mm and writer
                    mmap_lock_wait_ms = max(mmap_lock_wait_ms, mlw['wait_ms'])

            # Determine contention type
            if competing_faults:
                contention_type = "Type-A: filemap_fault blocks vma_start_write"
            else:
                contention_type = "Type-B: mmap_lock contention without fault"

            chains.append(ContentionChain(
                fault_pid=competing_faults[0]['pid'],
                fault_comm=competing_faults[0]['comm'],
                fault_tgid=competing_faults[0]['tgid'],
                fault_ts=competing_faults[0]['ts'],
                fault_addr=competing_faults[0]['address'],
                fault_ino=competing_faults[0]['ino'],
                fault_pgoff=competing_faults[0]['pgoff'],
                fault_major=competing_faults[0]['major'],
                fault_minor=competing_faults[0]['minor'],
                writer_stack=list(w.get('stack', [])),
                fault_stack=list(competing_faults[0].get('stack', [])),
                write_pid=w['pid'],
                write_comm=w['comm'],
                write_tgid=w.get('tgid', 0),
                write_ts=0.0,
                write_caller=w['caller'],
                vma_wait_ms=w['wait_ms'],
                mmap_lock_wait_ms=mmap_lock_wait_ms,
                vma_start=w['vm_start'],
                vma_end=w['vm_end'],
                mm=mm,
                contention_type=contention_type
            ))

    return chains


def print_chain_detail(chain, idx):
    syscall = caller_to_syscall(chain.write_caller)
    print(f"\n{'='*100}")
    print(f"=== 竞争链 #{idx} ===")
    print(f"{'='*100}")
    print(f"竞争类型: {chain.contention_type}")
    print(f"mm: {chain.mm}")
    print(f"")
    print(f"[Thread 1 - filemap_fault (reader)]")
    print(f"  应用:     {chain.fault_comm} (PID={chain.fault_pid}, TGID={chain.fault_tgid})")
    print(f"  地址:     {hex(chain.fault_addr)}")
    print(f"  文件页:   dev={chain.fault_major}:{chain.fault_minor} ino={chain.fault_ino} pgoff={hex(chain.fault_pgoff)}")
    print(f"  时间:     {chain.fault_ts:.6f}")
    print(f"")
    print(f"[Thread 2 - vma_start_write (writer)]")
    print(f"  应用:     {chain.write_comm} (PID={chain.write_pid})")
    print(f"  系统调用: {syscall}")
    print(f"  内核函数: {chain.write_caller}")
    print(f"  VMA:      [{hex(chain.vma_start)}, {hex(chain.vma_end)}]")
    print(f"  地址包含: {chain.vma_start <= chain.fault_addr < chain.vma_end}")
    print(f"")
    print(f"[等待时间]")
    print(f"  vma_start_write_wait: {chain.vma_wait_ms:.3f}ms")
    if chain.mmap_lock_wait_ms > 0:
        print(f"  mmap_lock_wait:       {chain.mmap_lock_wait_ms:.3f}ms")
    if chain.writer_stack:
        print(f"[Writer Stack Top]")
        for frame in chain.writer_stack[:8]:
            print(f"  {frame}")
    if chain.fault_stack:
        print(f"[Fault Stack Top]")
        for frame in chain.fault_stack[:8]:
            print(f"  {frame}")
    print(f"{'='*100}")


def summarize_v2(trace_file):
    events = parse_trace_v2(trace_file)
    writes_by_mm, faults_by_mm, ml_waits = build_event_index(events)
    chains = find_contention_chains_v2(writes_by_mm, faults_by_mm, ml_waits)

    print(f"=== Contention Summary v2 ===")
    print(f"Total events parsed: {len(events)}")
    print(f"Unique mm with writes: {len(writes_by_mm)}")
    print(f"Unique mm with faults: {len(faults_by_mm)}")
    print(f"mmap_lock waits: {len(ml_waits)}")
    print(f"Contention chains found: {len(chains)}")

    # Print top chains by wait time
    chains.sort(key=lambda c: -c.vma_wait_ms)

    print(f"\n=== TOP 10 Contention Chains (by vma_wait) ===")
    for i, c in enumerate(chains[:10]):
        print_chain_detail(c, i + 1)

    # Statistics by process
    by_process = defaultdict(list)
    for c in chains:
        by_process[c.write_comm].append(c)

    print(f"\n=== By Writer Process ===")
    for proc, proc_chains in sorted(by_process.items(), key=lambda x: -len(x[1]))[:10]:
        total_wait = sum(c.vma_wait_ms for c in proc_chains)
        max_wait = max(c.vma_wait_ms for c in proc_chains)
        print(f"  {proc}: {len(proc_chains)} chains, total_wait={total_wait:.2f}ms, max={max_wait:.2f}ms")

    # Statistics by syscall
    by_syscall = defaultdict(lambda: {'count': 0, 'total_wait': 0, 'max_wait': 0})
    for c in chains:
        syscall = caller_to_syscall(c.write_caller)
        by_syscall[syscall]['count'] += 1
        by_syscall[syscall]['total_wait'] += c.vma_wait_ms
        by_syscall[syscall]['max_wait'] = max(by_syscall[syscall]['max_wait'], c.vma_wait_ms)

    print(f"\n=== By Syscall ===")
    for syscall, stats in sorted(by_syscall.items(), key=lambda x: -x[1]['count']):
        print(f"  {syscall}: {stats['count']} times, avg={stats['total_wait']/stats['count']:.2f}ms, max={stats['max_wait']:.2f}ms")

    by_fault_file_page = defaultdict(lambda: {'count': 0, 'max_wait': 0.0})
    for c in chains:
        key = f"{c.fault_major}:{c.fault_minor}:{c.fault_ino}:{hex(c.fault_pgoff)}"
        by_fault_file_page[key]['count'] += 1
        by_fault_file_page[key]['max_wait'] = max(by_fault_file_page[key]['max_wait'], c.vma_wait_ms)

    if by_fault_file_page:
        print(f"\n=== By Fault File Page ===")
        for key, stats in sorted(by_fault_file_page.items(), key=lambda x: (-x[1]['count'], -x[1]['max_wait']))[:10]:
            print(f"  {key}: {stats['count']} times, max_wait={stats['max_wait']:.2f}ms")

    # Overall stats
    all_waits = [c.vma_wait_ms for c in chains]
    if all_waits:
        print(f"\n=== Overall Wait Stats ===")
        print(f"Total chains: {len(all_waits)}")
        print(f"Avg wait: {sum(all_waits)/len(all_waits):.3f}ms")
        print(f"Max wait: {max(all_waits):.3f}ms")
        print(f">1ms: {sum(1 for w in all_waits if w > 1)}")
        print(f">10ms: {sum(1 for w in all_waits if w > 10)}")

    # Write JSON
    json_path = Path(trace_file).with_suffix('.summary.json')
    summary = {
        "trace_file": trace_file,
        "total_events": len(events),
        "contention_chains": len(chains),
        "by_process": {k: len(v) for k, v in by_process.items()},
        "by_syscall": {k: v['count'] for k, v in by_syscall.items()},
        "by_fault_file_page": {k: v['count'] for k, v in by_fault_file_page.items()},
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
        description='Analyze mmap_lock contention from trace data (v2 with mm matching)'
    )
    parser.add_argument('trace_file', help='Path to trace_stream.txt')
    args = parser.parse_args()

    summarize_v2(args.trace_file)


if __name__ == '__main__':
    main()
