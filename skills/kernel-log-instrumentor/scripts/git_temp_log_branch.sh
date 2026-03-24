#!/usr/bin/env bash
set -euo pipefail

# Create a temporary debug branch for kernel logging changes.
# Usage:
#   ./git_temp_log_branch.sh <topic> [base_branch]
# Example:
#   ./git_temp_log_branch.sh net-stall main

topic=${1:-klog}
base=${2:-"$(git rev-parse --abbrev-ref HEAD)"}
ts=$(date +%Y%m%d-%H%M%S)
branch="tmp/${topic}-${ts}"

# Ensure we're in a git repo.
git rev-parse --is-inside-work-tree >/dev/null

# Switch to base and create the temp branch.
git switch "$base"
git switch -c "$branch"

echo "[OK] created temp log branch: $branch (from $base)"
echo
cat <<EOF
Next steps:
  1) Make your logging edits
  2) git add -A
  3) git commit -m "debug(klog): ${topic}"

Rollback options:
  - If NOT merged:
      git switch "$base" && git branch -D "$branch"
  - If merged (shared history):
      git revert <the_log_commit_sha>
EOF
