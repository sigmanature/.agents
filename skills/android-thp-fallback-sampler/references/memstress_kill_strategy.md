# Memstress start/kill strategy (parameter-only)

`scripts/run_memstress_and_collect_logs.py` uses a single generalized strategy that can express different behaviors via parameters (no separate “policy” enum).

## Core invariant

- Every successful `am start ...` (optionally `-W`) puts the package into an `alive` deque (most-recent at tail).
- After each successful launch:
  - sleep `--hold-ms` (dwell time)
  - optionally perform a post-launch action (default: press HOME) to exit foreground without killing
  - optionally enforce `--max-alive` by killing the oldest packages first (`am force-stop <pkg>`) until `len(alive) <= max_alive` (only when `--force-stop-evict`)

When `--no-force-stop-evict` (default), the script will not `force-stop` at all; processes are left cached and eviction is left to LMKD / natural pressure.

## Useful parameter sets

### 1) Fast launch, exit to HOME (no kill, high churn + background retention)

```bash
--burst-size 1 \
--hold-ms 200 \
--launch-gap-ms 0 \
--cycle-sleep-ms 0 \
--no-am-start-wait \
--post-launch-action home \
--no-force-stop-evict
```

This produces the behavior you described: **don’t fully wait Activity startup**, then after ~200ms **exit to HOME** but do **not** kill the process.

### 2) Start then force-stop immediately (extreme churn, old behavior)

```bash
--burst-size 1 \
--max-alive 0 \
--hold-ms 200 \
--launch-gap-ms 0 \
--cycle-sleep-ms 0 \
--am-start-wait \
--post-launch-action none \
--force-stop-evict
```

### 3) Keep only the most recent app alive (1-back, force-stop eviction)

```bash
--max-alive 1 --hold-ms 0 --force-stop-evict
```

This makes “launch next => kill previous” (LRU eviction when exceeding 1).

### 4) Keep N alive, evict oldest (LRU-ish, steady pressure, force-stop eviction)

```bash
--max-alive 8 --hold-ms 0 --force-stop-evict
```

As new apps are launched, the oldest ones are force-stopped to maintain the bound.

## Verifying behavior

- Per-cycle decisions are logged in `<out_dir>/<serial>/memstress/cycle_log.jsonl`:
  - `launched`: packages successfully started
  - `killed`: packages force-stopped due to `max-alive` enforcement
