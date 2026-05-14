#!/usr/bin/env python3
"""
Analyze process lifecycle from mmap_lock trace data.
Shows per-process timeline of VMA operations, filemap faults, and refault detection.

Usage:
    python3 analyze_lifecycle.py <trace_file_or_dir>

Output:
    - Per-process timeline (mmap/munmap/mprotect/fork/exec/exit/fault)
    - Filemap fault windows per process
    - Refault candidates (same address faulted multiple times)
    - Syscall breakdown with stack traces
"""

import re
import sys
from collections import defaultdict, Counter
from pathlib import Path


def parse_trace_file(filepath):
    """Parse a single trace file, return list of events."""
    events = []
    with open(filepath) as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'\s*(\S+)-(\d+)\s+\[\d+\].*?(\d+\.\d+):\s+(\S+):', line)
        if not m:
            i += 1
            continue
        
        comm, pid, ts, event = m.groups()
        ts = float(ts)
        pid = int(pid)
        
        entry = {
            'comm': comm,
            'pid': pid,
            'ts': ts,
            'event': event,
            'raw': line.strip()
        }
        
        # Parse event-specific fields
        if event == 'vma_start_write_begin':
            m2 = re.search(r'tgid=(\d+).*mm=([0-9a-fA-F]+).*vm_start=([0-9a-fA-F]+).*vm_end=([0-9a-fA-F]+).*caller=(\S+).*ino=(\d+)', line)
            if m2:
                entry['tgid'] = int(m2.group(1))
                entry['mm'] = m2.group(2)
                entry['vm_start'] = int(m2.group(3), 16)
                entry['vm_end'] = int(m2.group(4), 16)
                entry['caller'] = m2.group(5)
                entry['ino'] = int(m2.group(6))
        
        elif event == 'filemap_fault_begin':
            m2 = re.search(r'dev=\d+:\d+\s+ino=([0-9a-fA-F]+).*pgoff=([0-9a-fA-F]+).*address=([0-9a-fA-F]+).*mm=([0-9a-fA-F]+).*tgid=(\d+)', line)
            if m2:
                entry['ino'] = int(m2.group(1), 16)
                entry['pgoff'] = int(m2.group(2), 16)
                entry['addr'] = int(m2.group(3), 16)
                entry['mm'] = m2.group(4)
                entry['tgid'] = int(m2.group(5))
        
        elif event in ('mmap_lock_wait_start', 'mmap_lock_wait_end', 
                        'mmap_lock_hold_start', 'mmap_lock_hold_end'):
            m2 = re.search(r'mm=([0-9a-fA-F]+).*write=(\w+)', line)
            if m2:
                entry['mm'] = m2.group(1)
                entry['write'] = m2.group(2) == 'true'
        
        events.append(entry)
        
        # Collect stack trace (lines after with =>)
        j = i + 1
        stack = []
        while j < len(lines):
            nxt = lines[j]
            if '=>' in nxt:
                frame = nxt.strip().split('=>')[-1].strip()
                stack.append(frame)
                j += 1
            elif re.match(r'^\s+<\.\.\.>', nxt):
                # ellipsis line indicating truncated stack
                j += 1
            else:
                break
        
        if stack:
            entry['stack'] = stack
        
        i = j if j > i + 1 else i + 1
    
    return events


def caller_to_syscall(caller):
    """Map kernel caller to user-facing syscall."""
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


def analyze_lifecycle(events):
    """Analyze process lifecycle from events."""
    # Group by TGID
    by_tgid = defaultdict(list)
    for e in events:
        tgid = e.get('tgid', e['pid'])
        by_tgid[tgid].append(e)
    
    # Sort each group's events by timestamp
    for tgid in by_tgid:
        by_tgid[tgid].sort(key=lambda x: x['ts'])
    
    return by_tgid


def find_refaults(events):
    """Find addresses that fault multiple times (refault candidates)."""
    # Group by (mm, addr)
    fault_map = defaultdict(list)
    for e in events:
        if e['event'] == 'filemap_fault_begin' and 'mm' in e and 'addr' in e:
            key = (e['mm'], e['addr'])
            fault_map[key].append(e['ts'])
    
    refaults = []
    for (mm, addr), timestamps in fault_map.items():
        if len(timestamps) >= 2:
            refaults.append({
                'mm': mm,
                'addr': hex(addr),
                'count': len(timestamps),
                'first_ts': min(timestamps),
                'last_ts': max(timestamps),
                'span_s': max(timestamps) - min(timestamps)
            })
    
    # Sort by span (longer span = more interesting)
    refaults.sort(key=lambda x: -x['span_s'])
    return refaults


def print_process_timeline(tgid, proc_events, max_events=30):
    """Print timeline for a single process."""
    comm = proc_events[0]['comm'] if proc_events else 'unknown'
    print(f"\n{'='*100}")
    print(f"Process: {comm} (TGID={tgid})")
    print(f"Events: {len(proc_events)}")
    print(f"{'='*100}")
    
    # Event type summary
    event_counts = Counter(e['event'] for e in proc_events)
    print("Event summary:")
    for evt, count in event_counts.most_common():
        print(f"  {evt:40s} {count:5d}")
    
    # Timeline (first N events)
    print(f"\nTimeline (first {max_events} events):")
    print(f"{'Timestamp':>12s}  {'Delta(ms)':>10s}  {'Event':35s}  {'Details'}")
    print("-" * 100)
    
    prev_ts = proc_events[0]['ts'] if proc_events else 0
    for e in proc_events[:max_events]:
        delta = (e['ts'] - prev_ts) * 1000
        prev_ts = e['ts']
        
        if e['event'] == 'vma_start_write_begin':
            syscall = caller_to_syscall(e.get('caller', ''))
            detail = f"{syscall:15s} vma=[{hex(e.get('vm_start', 0))}, {hex(e.get('vm_end', 0))}] ino={e.get('ino', 0)}"
        elif e['event'] == 'filemap_fault_begin':
            detail = f"addr={hex(e.get('addr', 0))} pgoff={hex(e.get('pgoff', 0))} mm={e.get('mm', 'N/A')[:8]}"
        elif e['event'] in ('mmap_lock_wait_start', 'mmap_lock_wait_end'):
            detail = f"write={e.get('write', False)} mm={e.get('mm', 'N/A')[:8]}"
        else:
            detail = ""
        
        print(f"{e['ts']:>12.6f}  {delta:>10.3f}  {e['event']:35s}  {detail}")
    
    # Stack trace samples for vma_start_write
    print("\nSample stack traces (vma_start_write):")
    write_events = [e for e in proc_events if e['event'] == 'vma_start_write_begin' and 'stack' in e]
    for e in write_events[:3]:
        print(f"  [{e['ts']:.6f}] {caller_to_syscall(e.get('caller', ''))}")
        for frame in e.get('stack', [])[:6]:
            print(f"    => {frame}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_lifecycle.py <trace_file_or_dir>")
        sys.exit(1)
    
    path = Path(sys.argv[1])
    
    if path.is_dir():
        # Analyze all trace files in directory
        files = sorted(path.glob("trace_stream_*.txt"))
        print(f"Found {len(files)} trace files")
    else:
        files = [path]
    
    all_events = []
    for tf in files[:5]:  # Limit to first 5 files for speed
        print(f"Parsing {tf.name}...")
        events = parse_trace_file(tf)
        all_events.extend(events)
    
    print(f"\nTotal events parsed: {len(all_events)}")
    
    # Lifecycle analysis
    by_tgid = analyze_lifecycle(all_events)
    
    print(f"\n{'#'*100}")
    print("PROCESS LIFECYCLE ANALYSIS")
    print(f"{'#'*100}")
    
    # Pick representative processes
    # Sort by event count, pick top processes with diverse patterns
    sorted_procs = sorted(by_tgid.items(), key=lambda x: -len(x[1]))
    
    interesting_tgids = []
    for tgid, events in sorted_procs:
        comm = events[0]['comm']
        # Pick diverse process types
        if len(interesting_tgids) < 10:
            interesting_tgids.append(tgid)
    
    for tgid in interesting_tgids[:6]:
        print_process_timeline(tgid, by_tgid[tgid], max_events=20)
    
    # Refault analysis
    print(f"\n{'#'*100}")
    print("REFAULT ANALYSIS")
    print(f"{'#'*100}")
    
    refaults = find_refaults(all_events)
    print(f"\nTotal refault candidates: {len(refaults)}")
    
    if refaults:
        print("\nTop 10 refaults (by time span):")
        for r in refaults[:10]:
            print(f"  mm={r['mm'][:8]} addr={r['addr']} count={r['count']} span={r['span_s']:.3f}s")
    
    # Overall statistics
    print(f"\n{'#'*100}")
    print("OVERALL STATISTICS")
    print(f"{'#'*100}")
    
    # Syscall breakdown
    syscalls = Counter()
    for e in all_events:
        if e['event'] == 'vma_start_write_begin' and 'caller' in e:
            syscalls[caller_to_syscall(e['caller'])] += 1
    
    print("\nVMA write operations by syscall:")
    for syscall, count in syscalls.most_common():
        print(f"  {syscall:20s} {count:6d}")
    
    # File vs anon breakdown
    file_vmas = sum(1 for e in all_events 
                   if e['event'] == 'vma_start_write_begin' and e.get('ino', 0) != 0)
    anon_vmas = sum(1 for e in all_events 
                   if e['event'] == 'vma_start_write_begin' and e.get('ino', 0) == 0)
    
    print(f"\nVMA type breakdown (vma_start_write):")
    print(f"  File-backed (ino!=0): {file_vmas}")
    print(f"  Anonymous (ino=0):    {anon_vmas}")
    
    # Fault statistics
    fault_events = [e for e in all_events if e['event'] == 'filemap_fault_begin']
    print(f"\nFilemap faults: {len(fault_events)}")
    
    if fault_events:
        # Unique processes with faults
        fault_tgids = set(e.get('tgid', e['pid']) for e in fault_events)
        print(f"  Unique processes: {len(fault_tgids)}")
        
        # Fault wait events
        fault_waits = len([e for e in all_events if e['event'] == 'filemap_fault_wait_start'])
        print(f"  Fault waits: {fault_waits}")


if __name__ == '__main__':
    main()
