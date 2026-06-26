#!/usr/bin/env python3
"""Recompute cycle_timing stats excluding first N warmup cycles."""
import json, sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
warmup = int(sys.argv[2]) if len(sys.argv) > 2 else 10

t = json.loads((p / 'memstress' / 'cycle_timing.json').read_text())
deltas = t.get('deltas_s', [])
if len(deltas) <= warmup:
    print(f'only {len(deltas)} deltas, cannot exclude {warmup} warmup')
    sys.exit(1)

d = deltas[warmup:]  # exclude first N
d.sort()
n = len(d)
total = sum(d)
print(json.dumps({
    'total_cycles': n,
    'total_elapsed_s': round(total, 3),
    'mean_cycle_s': round(total/n, 3),
    'max_cycle_s': round(max(d), 3),
    'min_cycle_s': round(min(d), 3),
    'median_cycle_s': round(d[n//2], 3),
    'p90_cycle_s': round(d[int(n*0.9)], 3),
    'p95_cycle_s': round(d[int(n*0.95)], 3),
    'warmup_excluded': warmup,
    'unit': 'seconds',
}, indent=2))
