# Detail mode (hierarchical logging)

## When to use
Enable detail mode when:
- the user explicitly asks for "详细模式 / deep";
- normal mode cannot disambiguate where state diverges;
- the target function is a coordinator that calls many helpers (state changes happen in callees).

## Detail mode objective
Create a **call-depth trace** that shows:
- which child/grandchild calls were executed
- which of them changed tracked variables
- which returned errors or blocked

Avoid logging every trivial helper especially when they are irrelevant to variables user's are interested to track. Pick the calls most likely to explain the symptom.

## How to choose which callees get logs
### Always instrument
Instrument a callee (and sometimes its child) if it does any of:

1. **Locking / concurrency**
   - acquires/releases locks
   - waits/wakes/sleeps/schedules
   - submits work (workqueue/timer/tasklet)
   - uses RCU primitives

2. **State mutation**
   - writes to any tracked state variable (the variables selected from `variable-selection.md`)
   - modifies shared structs (list/queue manipulation, refcount changes)

3. **Blocking or latency risk**
   - memory alloc/free with GFP flags, page allocation
   - IO submit/flush, device interactions
   - copy_to/from_user

4. **Error boundary**
   - returns negative errno or has multiple failure exits

### Usually skip
Skip (unless evidence suggests otherwise):
- pure getters, cheap wrappers, small inline conversions
- logging wrappers
- constant-time helpers that don't touch shared state

## Depth encoding
In printk logs, include a numeric depth and a short call edge label.

Recommended fields:
- `d=<0|1|2>`
- `edge=<caller->callee>`

Example:
```c
KLOGE("ENTER", "d=0 cpu=%d pid=%d id=%llu", cpu, pid, id);
KLOGE("CALL",  "d=1 edge=foo->bar x=%d", x);
KLOGE("RET",   "d=1 edge=foo->bar ret=%d", ret);
```

## Minimal detail-mode insertion pattern
For a target function `foo()` calling `bar()` and `baz()`:

1. `foo()`:
   - `ENTER` at start
   - `EXIT` at each return (include `ret`)
   - `STATE` when tracked vars change

2. For each selected callee (`bar`, `baz`):
   - log **before call** with input args (only those tied to tracked vars)
   - log **after call** with `ret` and key output state

3. For each selected grandchild:
   - repeat step 2, but only for calls likely to be causal (locks, waits, state mutation, errors)

## Choosing grandchild targets (the "careful inference")
When you see a callee you selected (say `bar()`), inspect its body and select grandchildren that:
- change the same tracked vars the user cares about
- are the first point of failure (`ret < 0` origin)
- interact with concurrency primitives
- cross subsystem boundaries (e.g., from core logic into driver, net stack, fs)

If the user cannot share full bodies, infer based on function names and common kernel patterns, and state assumptions explicitly.
