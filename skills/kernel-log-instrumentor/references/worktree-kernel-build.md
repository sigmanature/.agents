# Worktree-first kernel log flow

## When to use
Use this flow only when a temporary worktree is actually needed. If the repo already has a long-lived debug lane, use that first and reserve this document for isolation, risky rebases, or parallel conflicting experiments.

## Create the temp branch in its own worktree
Preferred helper:

```bash
/home/nzzhao/.agents/skills/kernel-log-instrumentor/scripts/git_temp_log_worktree.sh \
  <topic> <base_branch> <worktree_path>
```

Equivalent manual form:

```bash
git worktree add -b tmp/<topic>-<timestamp> <worktree_path> <base_branch>
```

Suggested naming:
- worktree: `/tmp/klog-<topic>-<timestamp>`
- output dir: `/tmp/klog-<topic>-out`

## Build with a separate `O=` directory
Give each temporary worktree its own build output directory. In `learn_os`, use the shared proven config from `/home/nzzhao/learn_os/f2fs_upstream/.config` as the default seed so scratch builds inherit the known-good instrumentation options.

Recommended command:

```bash
/home/nzzhao/learn_os/myscripts/make_upstream.sh \
  --src <worktree_path> \
  --out <out_dir> \
  --config-seed /home/nzzhao/learn_os/f2fs_upstream/.config \
  Image
```

Notes:
- Use `--refresh-config` when the output tree already exists but should be reseeded from the shared config.
- Do not reuse another temporary worktree's `O=` directory unless the user explicitly asks for that coupling.
- The explicit `--config-seed` avoids config drift such as losing `CONFIG_DYNAMIC_DEBUG` or other instrumentation-related options in a fresh scratch tree.

## Cleanup
If the temporary log branch is not needed anymore:

```bash
git worktree remove <worktree_path>
git branch -D tmp/<topic>-<timestamp>
```

If the commit escaped into shared history, keep the worktree long enough to identify the commit SHA and revert that commit instead of deleting history.
