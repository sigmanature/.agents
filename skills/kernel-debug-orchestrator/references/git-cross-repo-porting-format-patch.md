# Git: cross-repo porting with `format-patch` / `am`

This is for the common case where you have **a commit in repo A** and you want the **same change** in **repo B**, but the repos do not share object IDs (different histories).

## Safe baseline

Before porting, always make a rollback pointer:

```bash
stamp=$(date +%Y%m%d_%H%M%S)
git branch backup/<branch>_$stamp HEAD
```

## Recommended flow (works across unrelated repos)

### 1) Export patch from source repo (repo A)

```bash
git format-patch -1 <commit> --stdout > /tmp/port.patch
```

### 2) Apply in target repo (repo B)

Prefer plain `git am` first:

```bash
git am /tmp/port.patch
```

If it fails due to context drift, you have two common options:

**Option A (still keeps author/message): use reject mode**

```bash
git am --abort
git am --reject /tmp/port.patch
```

Then manually resolve rejects (`*.rej`), `git add`, and continue:

```bash
git add <fixed files>
git am --continue
```

**Option B (manual commit): use `git apply` then `git commit`**

```bash
git am --abort
git apply --reject /tmp/port.patch
git add <fixed files>
git commit -m "<new message>"
```

## Important gotcha: avoid `git am -3` across unrelated repos

If you try:

```bash
git am -3 /tmp/port.patch
```

It may fail with:

```text
error: sha1 information is lacking or useless (<file>)
error: could not build fake ancestor
```

Reason: `-3` tries to synthesize a base using the blob IDs from the patch (`index <old>..<new>`), which won’t exist in a different repo.

Fix: use plain `git am` (context apply) or `git am --reject`, or fall back to `git apply` + manual commit.

