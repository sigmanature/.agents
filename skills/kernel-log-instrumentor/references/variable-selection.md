# Variable selection rules

## Objective
Given a user's natural-language debugging intent, pick the **smallest** set of variables that explain:
- **why control flow chose a path** (predicates)
- **how state evolves** (loop-carried/shared state)
- **why a failure happens** (return codes, error paths)

## Step 1: Extract "semantic variables" from the user request
From the user's text, identify:
- **Objects**: queue, buffer, request, socket, inode, folios, task, lock, work item
- **Properties**: length, count, refcount, state, flags, mode, owner, index, timeout
- **Events**: retry, drop, wakeup, timeout, block, stall, deadlock, leak

Map them to code-level candidates:
- `len/size` → `*len`, `size`, `count`, `nr_*`, `*_bytes`, helper (`skb_queue_len()`, `iov_iter_count()`)
- `state` → enum/state machine fields (`state`, `status`, `phase`)
- `refcount/leak` → `refcount_t`, `atomic_t`, `kref`, `users`, `refs`
- `timeout` → `jiffies`, `deadline`, `expires`, `timeout_ms`
- `owner` → `current`, `task_struct *`, `tgid/pid`, `uid`, `cpu`

## Step 2: Prioritize by explanatory power
Always include, in this order:

1. **Predicate variables**
   - Variables used in `if (...)`, `while (...)`, `for (...; cond; ...)`, and early returns.
   - If the predicate is compound, log each component.

2. **Return values from key calls**
   - Especially calls that can block, fail, or change state.
   - Log both `ret` and the relevant inputs.

3. **Loop-carried state (must for loops)**
   - Variables that change each iteration and influence the next iteration.
   - Examples: `i`, `idx`, `pos`, `remaining`, `budget`, `credits`, `state`.

4. **Shared/concurrency state**
   - Fields protected by locks, atomics, refcounts, wait-queue predicates.
   - Use `READ_ONCE()` when reading without holding the protecting lock.

5. **Identity fields** (to correlate lines)
   - request id, inode number, socket tuple, pointer-hash, etc.

## Step 3: Prefer transitions over raw values
When a variable evolves, log `old->new`.

Suggested patterns:
- `state=%d->%d`
- `flags=0x%lx->0x%lx`
- `len=%u->%u`
- `ref=%d->%d`

If you need to detect change in-place:
- Keep a local `prev_*` and only log when different.

## Step 4: When the user says "loop" or symptoms look like a loop
Instrument:
- loop entry (`iter=0`)
- **every state transition** (not necessarily every iteration)
- loop exit + reason (predicate false / error / break)

If iterations are huge:
- log on `iter == 0`, `iter == 1`, `iter % N == 0`, and on **state change**.
- consider switching to tracepoints.

## Step 5: Concurrency-specific variables
If concurrency is involved, always include:
- lock-protected predicate variables (the condition for progress)
- ownership markers (pid/cpu)
- wait/wakeup counters or sequence numbers (if present)

See `concurrency-logging.md` for predicate + lock boundary rules.
