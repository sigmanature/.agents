# Restore Instructions

## Expected Base

The archived patches were captured from:

```text
tree: /home/nzzhao/learn_os/pixel/common
base before full mTHP experiment stack: dd369db1d7f5
full committed experiment stack: dd369db1d7f5..700e6147efec16084c99d190eebcd2bb04ebaf51
last experiment commit: 700e6147efec16084c99d190eebcd2bb04ebaf51
```

## Clean Restore On Original Base

```bash
cd /home/nzzhao/learn_os/pixel/common
git switch --detach dd369db1d7f5
git am /home/nzzhao/.agents/skills/archive/android-mthp-experiment-20260629/patches/0000-full-committed-mthp-experiment-stack.patch
git apply /home/nzzhao/.agents/skills/archive/android-mthp-experiment-20260629/patches/0002-dirty-mthp-experiment-worktree.patch
```

The second patch intentionally restores dirty worktree changes rather than creating a commit. Commit it yourself if you want a new single experiment commit.

## Restore From Stash

The dirty layer was also stashed in the original repository with a message like:

```text
mthp experiment archive 20260629 dirty trace counters data structures
```

To inspect:

```bash
cd /home/nzzhao/learn_os/pixel/common
git stash list --date=local
git stash show --stat stash^{/mthp experiment archive 20260629}
```

To restore:

```bash
git stash apply --index stash^{/mthp experiment archive 20260629}
```

Prefer the patch files when moving between machines, because the stash is local to one repository.

## If The Tree Has Moved Forward

Use three-way apply:

```bash
git am -3 patches/0000-full-committed-mthp-experiment-stack.patch
git apply -3 patches/0002-dirty-mthp-experiment-worktree.patch
```

Then check:

```bash
git diff --check
git status --short
```

## Build Reminder

After restoring experiment code:

```bash
cd /home/nzzhao/learn_os/pixel
bash build_slider.sh
```

Reboot the device before any performance comparison. Do not compare runs across restored/non-restored kernels without rebooting.
