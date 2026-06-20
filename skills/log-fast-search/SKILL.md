# log-fast-search: Large Trace/Log Fast Search Skill

## Purpose

Fast, memory-safe search and analysis of large (100MB+) trace/log files.
Uses `rg` (ripgrep) pre-filtering + Python streaming + `bisect` correlation.
Avoids OOM from loading full files into memory.

## When to Use

- Trace file > 100MB, too large for naive `read()` or `grep`
- Need to correlate events across time windows (e.g., "madvise followed by split within 10ms")
- Need per-process/per-category statistics from millions of events
- Python script keeps timing out or OOM-killed on trace files
- Need to extract specific event types from ftrace/perfetto output

## Core Pattern: rg → Python → bisect

```
[282MB trace] → rg filter → [40MB events] → Python streaming parse → bisect correlate → JSON
                 <1s               ~4s                   ~1s
```

### Step 1: rg pre-filtering

```python
import subprocess

def rg_filter(paths, patterns):
    """Extract lines matching any pattern from large files."""
    cmd = ['rg', '--no-heading', '-N', '-j', '0'] + patterns + paths
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          env={**os.environ, 'LC_ALL': 'C'}).stdout
```

Key `rg` flags:
| Flag | Meaning |
|---|---|
| `--no-heading` | No filename prefix per line, clean output |
| `-N` | No line numbers (saves bytes, faster) |
| `-j 0` | Use all CPU cores |
| `-n` | Include line numbers for trace ordering |

### Step 2: Streaming parse (never load full file)

```python
import re

EVENT_RE = re.compile(r'...(regex for your trace format)...')

def parse_events(text: str):
    """Streaming: splitlines, match, never .read() the whole file."""
    events = []
    for line in text.splitlines():
        m = EVENT_RE.match(line)
        if m:
            events.append(build_event(m))
    return events
```

### Step 3: bisect correlation (not O(n²) nested loops)

```python
from bisect import bisect_left

def has_event_in_window(sorted_ts_list, target_ts, window_us=10000):
    """Check if any event in sorted_ts_list falls in [target, target+window]."""
    lo = bisect_left(sorted_ts_list, target_ts)
    return lo < len(sorted_ts_list) and \
           (sorted_ts_list[lo] - target_ts) * 1e6 <= window_us
```

Complexity: O(n × log m) instead of O(n × m).

## Ready-to-Use Script

```bash
cp scripts/rg_parse.py your_experiment/
```

Edit the event regexes for your trace format, then:

```bash
python3 rg_parse.py <trace_dir> <output.json>
```

## Performance Reference

| Trace Size | Event Lines | rg filter | parse | correlate | Total |
|---|---|---|---|---|---|
| 300MB | 1.5M | 1.1s | 4.2s | 1.4s | 6.7s |
| 1GB | ~5M | 3s | ~14s | ~5s | ~22s |

Memory: < 500MB peak (streaming), vs 4GB+ for naive read-all approach.

## Common Trace Formats

### ftrace trace_pipe format

```
<comm>-<pid>  [<cpu>] <flags> <ts>: <event>: <payload>
           => <stacktrace lines>
```

Regex: `r'^\s*(?P<comm>[^\s-]+)-(?P<pid>\d+)\s+\[\d+\]\s+\S+\s+(?P<ts>\d+\.\d+):\s+(?P<evt>\w+):\s+(?P<payload>.*)'`

### logcat threadtime format

```
MM-DD HH:MM:SS.mmm  <pid>  <tid> <level> <tag>: <message>
```

## Customization Checklist

When adapting `rg_parse.py` to a new trace format:

1. Change `EVENT_RE` regex to match your trace line format
2. Change `rg_filter` pattern list to your event names
3. Change `parse_*` functions to extract your fields
4. Change `classify()` logic to your categorization rules
5. Change `COLLECT_WINDOW_US` if you need different correlation window
6. Change output JSON schema in `main()`

## Anti-Patterns (Never Do)

- `with open('300MB.trace') as f: text = f.read()` → OOM
- `for e1 in events: for e2 in events:` → O(n²), timeout
- `re.findall(large_pattern, huge_text)` → catastrophic backtracking
- `grep` on 300MB files → 10x slower than rg
- `sort | uniq -c | sort -rn` pipeline → slow for millions of lines

## References

- `references/patterns.md` — common time-window correlation recipes
- `scripts/rg_parse.py` — reusable template script