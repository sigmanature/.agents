# Concurrency logging rules (locks, waits, predicates)

## Core principle
When debugging concurrency, logs must make it possible to answer:
- **who** held a lock
- **for how long**
- **what predicate/state** was true at acquire and before release
- **whether progress happened** between lock+ and lock-

## Lock boundary placement (mandatory)
For each critical section:

1. **Immediately after acquiring the lock**
   - After `mutex_lock()` returns, after successful `spin_lock()` (or `spin_lock_irqsave()`), after `down()`.
   - Emit `LOCK+`.

2. **Immediately before releasing the lock**
   - Right before `mutex_unlock()` / `spin_unlock()` / `up()`.
   - Emit `LOCK-`.

3. **Include predicate variables**
   - Log the condition variables that gate progress (e.g., `state`, `ready`, `count`, `head!=tail`).
   - At `LOCK-`, log the **same** predicate vars again.

## Lock pairing fields
Every lock+ / lock- line should include the same correlation fields:
- `lock=<name or %p>`
- `cpu`, `pid`, `comm`
- `seq=<monotonic sequence>` (optional but very useful)
- `t0=<time>` and/or `dt=<hold time>` if practical

### Hold time measurement
If you need hold time:
- Use `ktime_get_ns()` at lock+ and compute `dt` at lock-.
- If the region is hot, prefer a tracepoint.

## Atomic/IRQ context safety
`printk()` can take locks internally (console/logbuf paths). In atomic context or inside spinlocks:
- Prefer **tracepoints**.
- If you must printk, prefer `printk_deferred(KERN_EMERG ...)` to reduce risk of stalls.

## Condition variables and waits
For wait-based concurrency (wait queues, completions, condvars emulation):

### WAIT+ (before sleeping)
Log:
- the predicate expression (as components)
- the values of predicate variables
- the planned timeout/deadline

### WAIT- (after waking)
Log:
- whether the predicate is now satisfied
- what woke you (signal/timeout/explicit wakeup) if knowable

### Wakeups
At the producer side (where `wake_up*()` / `complete()` / `signal` happens):
- log **before** the wakeup call with the same predicate variables
- if possible, log which waiter(s) you're targeting (id/queue)

## "Healthy" concurrency logs (what good looks like)
Treat these as positive signals:

1. **Perfect pairing**
   - Every `LOCK+` has **ONLY ONE** matching `LOCK-` from the same thread for the same lock.

2. **Bounded hold times**
   - `dt` is stable and within expected range; no outliers.

3. **Predicate monotonicity**
   - The predicate variables move in the expected direction (e.g., `count` decreases to 0, `state` advances forward).

4. **Wait/wake coherence**
   - A `WAIT+` is eventually followed by `WAIT-`, and after `WAIT-` the predicate is true or a clear timeout is reported.

5. **Consistent lock order**
   - Nested locks appear in a stable order across threads.

## "Suspicious" patterns (what to flag)
Use these as rules to call out likely bugs:

### A. Missing pairs or mismatched owners
- `LOCK+` without `LOCK-` (thread exits/returns/error path skipped unlock)
- `LOCK-` without a prior `LOCK+` (double-unlock)
- lock+ and lock- from different owners for the same lock id (data race / corrupted lock usage)

### B. Lock hold anomalies
- **Long hold time outliers** (often indicates blocking inside lock, console/printk stall, or stuck loop)
- lock held across `schedule()` / `msleep()` / blocking IO (unless intentionally designed)

### C. Predicate inconsistencies
- predicate variables change **outside** the protecting lock (race)
- predicate never changes despite repeated lock acquisition (livelock)
- state machine regresses (e.g., `RUNNING->INIT` unexpectedly)

### D. Wait/wake pathologies
- repeated `WAIT+` cycles where the predicate is still false after `WAIT-` (missed wakeup or wrong predicate)
- wakeups happen but producer-side predicate variables do not reflect the change (wake without state update)
- timeout dominates (many wake-ups are actually timeouts)

### E. Lock-order inversion clues
- same pair of locks acquired in opposite order by different threads (`A then B` vs `B then A`)

## Recommended lock log snippets
### Mutex example
```c
u64 t0 = ktime_get_ns();
mutex_lock(&ctx->lock);
KLOGE("LOCK+", "cpu=%d pid=%d comm=%s lock=ctx.lock t0=%llu state=%d count=%d",
      raw_smp_processor_id(), task_pid_nr(current), current->comm,
      t0, ctx->state, ctx->count);

/* critical section */

KLOGE("LOCK-", "cpu=%d pid=%d comm=%s lock=ctx.lock dt=%llu state=%d count=%d",
      raw_smp_processor_id(), task_pid_nr(current), current->comm,
      ktime_get_ns() - t0, ctx->state, ctx->count);
mutex_unlock(&ctx->lock);
```

### Spinlock (prefer tracepoint, else deferred)
```c
unsigned long flags;
u64 t0 = ktime_get_ns();
spin_lock_irqsave(&ctx->lock, flags);
KLOGE_DEFERRED("LOCK+", "cpu=%d pid=%d lock=ctx.lock t0=%llu ready=%d",
               raw_smp_processor_id(), task_pid_nr(current), t0, ctx->ready);

/* critical section */

KLOGE_DEFERRED("LOCK-", "cpu=%d pid=%d lock=ctx.lock dt=%llu ready=%d",
               raw_smp_processor_id(), task_pid_nr(current), ktime_get_ns()-t0, ctx->ready);
spin_unlock_irqrestore(&ctx->lock, flags);
```