#!/usr/bin/env bash
set -euo pipefail

# Create a temporary debug branch in a disposable worktree.
# Usage:
#   ./git_temp_log_worktree.sh <topic> [base_branch] [worktree_path]
# Example:
#   ./git_temp_log_worktree.sh gc-path main /tmp/klog-gc-path

topic=${1:-klog}
base=${2:-"$(git rev-parse --abbrev-ref HEAD)"}
ts=$(date +%Y%m%d-%H%M%S)
branch="tmp/${topic}-${ts}"
worktree=${3:-"/tmp/klog-${topic}-${ts}"}

git rev-parse --is-inside-work-tree >/dev/null

if [[ -e "$worktree" ]]; then
  echo "[FATAL] worktree path already exists: $worktree" >&2
  exit 2
fi

mkdir -p "$(dirname "$worktree")"
git worktree add -b "$branch" "$worktree" "$base"

echo "[OK] created temp log worktree: $worktree"
echo "[OK] branch: $branch (from $base)"
echo
cat <<EOF
Next steps:
  1) Edit and test inside: $worktree
  2) Commit the temporary log change as one commit:
       git -C "$worktree" add -A
       git -C "$worktree" commit -m "debug(klog): ${topic}"

Rollback options:
  - If NOT merged:
      git worktree remove "$worktree"
      git branch -D "$branch"
  - If merged (shared history):
      git -C "$worktree" rev-parse HEAD
      git revert <the_log_commit_sha>
EOF
