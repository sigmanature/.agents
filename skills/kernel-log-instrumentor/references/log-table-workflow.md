# Log table workflow

## Purpose
When detailed kernel logs already print function name plus stable `k=v` fields, treat the logs as a table instead of as prose.

This is especially effective when:
- many threads touch the same shared object
- you need to follow one `ino`, `folio`, `index`, `seq`, or request id
- the bug is concurrent and same code path is executed by different `pid` or `comm`

## What to print

### 1. Actor ids
- `pid`
- `comm`
- `cpu`

### 2. Shared-object ids
- filesystem: `ino`, `index`, `nid`
- memory or folio: `folio`, `index`
- request or work item: `req`, `bio`, `seq`, custom transaction id

### 3. Decision fields
- the exact predicate components that explain path selection
- counters or state fields that show progress or lack of progress
- transitions such as `state=1->2`, `count=4->3`

### 4. Function identity
- always print `fn=<__func__>`

## Method

1. Emit table-friendly lines.
   - every relevant line should be self-contained
   - prefer `k=v` over prose
2. Parse with `scripts/kernel_log_kv_query.py`.
3. Filter by the shared-object id first.
4. Then split by actor ids (`pid`, `comm`) to see interleaving.
5. Correlate suspicious gaps or repeated predicates with lock or wait lines.

## Example queries

### Track one inode across all threads
```bash
python3 /home/nzzhao/.agents/skills/kernel-log-instrumentor/scripts/kernel_log_kv_query.py \
  guest_console.log --tag KLOG --eq ino=10591 --show ts,fn,pid,comm,phase,state,msg
```

### Follow one sequence id
```bash
python3 /home/nzzhao/.agents/skills/kernel-log-instrumentor/scripts/kernel_log_kv_query.py \
  guest_console.log --tag KLOG --eq seq=20 --show ts,fn,pid,comm,phase,msg
```

### Filter one inode and one pid
```bash
python3 /home/nzzhao/.agents/skills/kernel-log-instrumentor/scripts/kernel_log_kv_query.py \
  guest_console.log --tag KLOG --eq ino=10591 --eq pid=1634 --show ts,fn,phase,state,msg
```

## Interpretation pattern

Good use of this method usually reveals one of these:
- the same object id is touched by several pids in an unexpected order
- one pid repeatedly sees the same predicate and never makes progress
- lock ownership and shared-object id line up with a stalled path
- a wait or wake line is missing for the object id you are tracking

## F2FS note
For existing `[WBDBG]` style logs, you can also use the skill-local `scripts/f2fs_log_field_query.sh` helper for a pre-parsed table focused on WBDBG and sysrq records.
