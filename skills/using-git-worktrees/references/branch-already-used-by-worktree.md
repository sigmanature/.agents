# Git worktree: branch already used by another worktree

## Symptom

在一个 worktree 里切换到某个 branch 时失败，例如：

```text
fatal: '<branch>' is already used by worktree at '<path>'
```

这是 git 的保护机制：**同一个 branch 不能同时被两个 worktree checkout**。

## Safe fixes (preferred order)

### Fix A: detach 到该 branch 的 commit（最安全，适合“只想验证/编译”）

```bash
git switch --detach <branch>
```

- 这样拿到 `<branch>` 的内容，但不占用 `<branch>`（因此不会和其他 worktree 冲突）。
- 验证结束后，用 `git switch <your-original-branch>` 切回即可。

### Fix B: 在现有 worktree 上换到别的分支，释放该 branch

在“占用 `<branch>`”的 worktree 上：

```bash
git switch <other-branch>
```

然后你就可以在另一个 worktree 里正常 `git switch <branch>` 了。

### Fix C: 移除占用该 branch 的 worktree（谨慎）

先确认哪个 worktree 占用了 branch：

```bash
git worktree list
```

然后移除：

```bash
git worktree remove <path>
```

注意：这会删除那个 worktree 的工作目录；如果其中有未提交改动，需要先处理。

