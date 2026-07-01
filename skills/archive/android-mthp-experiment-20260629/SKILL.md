---
name: android-mthp-experiment-archive-20260629
description: Archived Pixel common mTHP/order-0 experiment code from June 2026, including debugfs counters, tracepoints, swap birth records, VMA suitability attribution, UFFD/madvise/deferred-split instrumentation, and restore instructions. Use when asked to restore or inspect the archived high-overhead mTHP experiment patches.
---

# Android mTHP Experiment Archive 20260629

This archive preserves the high-overhead Pixel common mTHP experiment code that was removed from the active tree because it affects performance.

Kernel tree:
- `/home/nzzhao/learn_os/pixel/common`

Archived state:
- Base before the full mTHP experiment stack: `dd369db1d7f5`
- Full committed experiment stack: `dd369db1d7f5..700e6147efec16084c99d190eebcd2bb04ebaf51`
- Last experiment commit: `700e6147efec16084c99d190eebcd2bb04ebaf51`
- Dirty worktree patch: additional uncommitted counters, tracepoints, and `mthp_experiment` data structures.

Files:
- `patches/0000-full-committed-mthp-experiment-stack.patch`: full committed stack, including file-page/order-2, mmap alignment, zram large compression, direct reclaim counters, VMA/deferred-split/madvise trace, and the final committed trace enrichments.
- `patches/0001-head-mthp-experiment-commit.patch`: the committed experiment layer.
- `patches/0002-dirty-mthp-experiment-worktree.patch`: the uncommitted experiment layer.
- `snapshots/include-linux-mthp_experiment.h`: full header snapshot for the dirty layer.
- `snapshots/mm-mthp_experiment.c`: full implementation snapshot for the dirty layer.
- `references/restore.md`: exact restore commands and notes.
- `references/inventory.md`: what this experiment code contained.

Use policy:
- Do not apply this archive during normal kernel builds.
- Restore only for targeted trace/counter experiments.
- After applying, rebuild and reboot before trusting performance data.
- Expect measurable overhead from tracepoints, per-cpu counters, debugfs reads, and swap birth-record tracking.

Quick restore:

```bash
cd /home/nzzhao/learn_os/pixel/common
git am /home/nzzhao/.agents/skills/archive/android-mthp-experiment-20260629/patches/0000-full-committed-mthp-experiment-stack.patch
git apply /home/nzzhao/.agents/skills/archive/android-mthp-experiment-20260629/patches/0002-dirty-mthp-experiment-worktree.patch
```

Read `references/restore.md` before restoring into a tree that has moved forward.
