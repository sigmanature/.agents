# Log format (printk / pr_emerg)

## Goals
- Be **grep-friendly** and **machine-parsable**.
- Every line is self-contained: you can diagnose without scrolling up.
- Every line can be paired (enter/exit, lock+/lock-, wait+/wait-).

## Mandatory fields
Put these in **every** log line:

1. **Stable prefix/tag**: pick one short tag and keep it consistent.
   - Example: `KLOG` or `KLOG:<topic>`
2. **Function name**: always print `__func__`.
3. **Key=value fields**: prefer `k=v` pairs over prose.

Recommended minimum set:
- `cpu=%d` (use `raw_smp_processor_id()`)
- `pid=%d` (use `task_pid_nr(current)`)
- `comm=%s` (use `current->comm`)
- when debug fs code,always print `inode->i_ino`
## Canonical line template

Prefer this general shape:

`KLOG <subtag> <phase> fn=<__func__> cpu=.. pid=.. comm=.. k1=v1 k2=v2 ...`

Where `<phase>` is one of:
- `ENTER`, `EXIT`
- `LOCK+`, `LOCK-`
- `WAIT+`, `WAIT-`
- `STATE` (only when a tracked variable changes)
- `ERR` (error path)

## printk level rule
Use **KERN_EMERG** for all printk-based logs.

### Safe wrapper macros
When you need copy/paste-ready macros, generate one of these:

```c
#include <linux/printk.h>
#include <linux/sched.h>

#define KLOG_TAG "KLOG"

/* Default: use pr_emerg */
#define KLOGE(subtag, fmt, ...) \
	pr_emerg(KLOG_TAG " " subtag " %s " fmt "\n", __func__, ##__VA_ARGS__)

/* If you must avoid immediate console flushing in atomic/locked context */
#define KLOGE_DEFERRED(subtag, fmt, ...) \
	printk_deferred(KERN_EMERG KLOG_TAG " " subtag " %s " fmt "\n", __func__, ##__VA_ARGS__)
```

Then structure arguments as key=value pairs:

```c
KLOGE("STATE", "cpu=%d pid=%d comm=%s state=%d->%d", raw_smp_processor_id(),
      task_pid_nr(current), current->comm, old_state, new_state);
```

## Pointers and ids
- Prefer stable ids over raw pointers (e.g., `inode->i_ino`, `skb->hash`).
- If a pointer is necessary, prefer `%p` variants that respect kernel restrictions (e.g., `%pK`).

## Noise control (optional but recommended)
If logs are still too noisy even on a temp branch:
- Log **state transitions only** (`old->new`).
- Add a **rate limit** (`pr_emerg_ratelimited` / `printk_ratelimited`).
- Switch hot paths to **tracepoints** (see `tracepoint-upgrade.md`).
