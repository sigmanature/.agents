# Example Implementation: CVD Synthetic mTHP Workload Harness

This is an implementation example for the abstract harness contract. Do not hardcode it into unrelated domains.

## Intent

Exercise Android Cuttlefish A/B images under synthetic app pressure that hits:

- ART app startup and Java heap allocation;
- native anonymous VMA full-fault;
- Scudo/bionic allocation burst;
- `dlopen` and read-only file mappings;
- fork/COW write faults;
- kernel order-2 mTHP counters.

## Minimal Controls

Keep controls semantic:

- `process_count`
- `vma_count`
- `vma_size_kb`
- `anon_full_fault_pages`
- `java_live_mb`
- `scudo_live_mb`
- `dlopen_lib_count`
- `filemap_file_mb`
- `fork_children`
- `cow_pages_per_child`
- `child_hold_ms` only if resident/swap pressure is part of the experiment

Do not add these unless explicitly justified:

- `fork_interval_ms` when only first COW burst is needed;
- fixed sleep before first COW round;
- scudo `usleep(200ms)` loop;
- filemap `usleep(500ms)` loop;
- arbitrary touch stride when the requirement is full-fault;
- background churn intervals when the requirement is startup pressure.

## Correct Workload Shape

Startup must perform the first pressure burst immediately:

1. Start activity/service.
2. Log parsed profile.
3. Allocate anonymous VMAs and write-fault every page.
4. Load pad `.so` files and read-fault every page.
5. Allocate/touch Scudo live set to target.
6. If COW profile, fork immediately and write the requested pages.
7. Parent records actual pages written by children, not just the target.
8. Only after the first burst may optional steady-state behavior begin.

## Known Bad Signatures

Discard the cell if any are true:

- `ActivityTaskManager ... result code=-92` for synthetic app launches.
- Component name is truncated from `WorkloadRuntime$MainActivity` to `WorkloadRuntime`.
- No `ZZMthpSynthNative started` log lines.
- No `regions=... anon_pages_written=...` log lines.
- COW profiles launched but no `fork_round=1` log lines.
- `cow_pages_written` is absent or far below `fork_children * cow_pages_per_child` without OOM/crash evidence.
- `cycle_log.jsonl` has launches but logcat has zero workload markers.

## Example Diff-Knob Hook

A task-local `validate_diff_knobs.py` should inspect newly modified synthetic workload files and fail if new pacing knobs appear without an allowlist entry:

```python
FORBIDDEN_PATTERNS = [
    'sleep(', 'usleep(', 'nanosleep(', 'Thread.sleep', 'time.sleep',
    'interval_ms', 'delay_ms', 'throttle', 'touch_stride', 'stride_kb',
]
ALLOWLIST = {
    # Example: sampler cycle sleep is part of external launch schedule, not inner workload.
    'run_memstress_and_collect_logs.py:cycle_sleep_ms': 'external cycle schedule',
}
```

The hook should report file:line, matched token, and required action: remove, justify, or move after first burst.

## Example Launch Evidence Hook

A task-local `validate_launch_evidence.py` should parse logcat and cycle logs:

```text
required:
- no result code=-92 for target package launches
- native marker count > 0
- started marker count >= number of unique launched packages expected in the gate
- regions marker count > 0
- for COW packages: fork_round marker count > 0
```

Do not trust `cycle_log.jsonl` alone. It only proves the host issued a launch command.

## Example Early Gate

For a short CVD synthetic mTHP gate:

- run 5-10 cycles, not 120 cycles;
- require at least one heavy COW package to launch;
- require `ZZMthpSynthNative started`;
- require `fork_round=1` for COW profiles;
- require actual `cow_pages_written` roughly matches the profile target;
- require vmstat COW counters to move in the expected direction;
- if `allocstall` is expected for this phase, check early; if zero is surprising, stop and inspect pressure before full run.

## Example Output Contract

```text
phase reached: early gate / abort
minimal controls used: process_count, vma_count, cow_pages_per_child, ...
invented knobs removed: fork_interval_ms, scudo sleep, filemap sleep
launch evidence: no result=-92; N started markers; N regions markers
self-evidence: M fork_round markers; X actual COW pages
metric gate: cow_order2 delta=..., cow_fallback0 delta=..., allocstall=...
discarded artifacts: old cells with no native markers
next action: rebuild APKs / rerun gate / run full A/B cells
```
