#!/usr/bin/env bash
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

usage() {
  cat <<'EOF'
Usage: tools/sync_kernel_code.sh [options]

Detect modified code files in the source kernel tree, copy them to the
shadow repository, and optionally commit+push.

Options:
  -s, --source DIR       Source tree (full kernel). Default: common_my_dec
  -d, --dest DIR         Shadow repo (code-only). Default: common_kernel_code
  -r, --remote URL       Git remote URL (if dest has no remote set)
  -b, --branch BRANCH    Branch to push/pull. Default: main
  -f, --from REF         Sync files changed since git REF (e.g. HEAD~1, main)
                         Uses 'git diff REF..HEAD' instead of 'git status'.
                         Default: unset (use working-tree changes only)
       --push             Commit and push changes to remote (implies --pull first)
       --pull             Pull latest from remote before syncing (automatic with --push)
      --status           Show what would be synced (no changes)
      --dry-run          Show what would be synced without copying
  -h, --help             Show this help

Examples:
  # Sync uncommitted working-tree changes (default)
  bash tools/sync_kernel_code.sh --status

  # Sync files changed since last commit (post-commit CI)
  bash tools/sync_kernel_code.sh --from HEAD~1 --push

  # Sync files changed in a feature branch
  bash tools/sync_kernel_code.sh --from main --push

  # Pull remote changes first, then sync
  bash tools/sync_kernel_code.sh --pull --push
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

source_dir="common_my_dec"
dest_dir="common_kernel_code"
remote_url=""
branch="main"
from_ref=""
do_push=0
do_pull=0
do_status=0
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--source) [ "$#" -ge 2 ] || die "$1 requires a value"; source_dir=$2; shift 2 ;;
    -d|--dest)   [ "$#" -ge 2 ] || die "$1 requires a value"; dest_dir=$2; shift 2 ;;
    -r|--remote) [ "$#" -ge 2 ] || die "$1 requires a value"; remote_url=$2; shift 2 ;;
    -b|--branch) [ "$#" -ge 2 ] || die "$1 requires a value"; branch=$2; shift 2 ;;
    -f|--from)   [ "$#" -ge 2 ] || die "$1 requires a value"; from_ref=$2; shift 2 ;;
    --push)      do_push=1; shift ;;
    --pull)      do_pull=1; shift ;;
    --status)    do_status=1; shift ;;
    --dry-run)   dry_run=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

if [ "$do_push" -eq 1 ]; then
  do_pull=1
fi

[ -d "$source_dir" ] || die "source directory not found: $source_dir"
[ -d "$dest_dir/.git" ] || die "destination not a git repo: $dest_dir"

git -C "$source_dir" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: $source_dir"

if [ -n "$remote_url" ]; then
  if ! git -C "$dest_dir" remote get-url origin >/dev/null 2>&1; then
    git -C "$dest_dir" remote add origin "$remote_url"
  fi
fi

if [ "$do_pull" -eq 1 ]; then
  echo "Pulling from remote..."
  git -C "$dest_dir" pull origin "$branch" || die "git pull failed"
fi

tmp_list=""
cleanup() { [ -n "$tmp_list" ] && [ -f "$tmp_list" ] && rm -f "$tmp_list"; }
trap cleanup EXIT INT TERM HUP

# Shared awk filter: keep only code files, drop build artifacts
filter_code_files() {
  awk '
  {
    path = $0
    n = split(path, parts, "/")
    name = parts[n]

    keep = 0
    if (name == "Makefile" || name == "Kbuild" || name == "BUILD" || name == "BUILD.bazel" || name == "WORKSPACE" || name == "MODULE.bazel" || name == "Android.bp" || name == "Android.mk") keep = 1
    else if (name ~ /^Kconfig/) keep = 1
    else if (name ~ /\.(c|cc|cpp|cxx|h|hpp|hh|hxx|S|s|rs|py|sh|bash|pl|pm|awk|y|l|dts|dtsi|dtso|asn1|ld|lds|bzl|mk|bp|inc|tbl|uc|go)$/) keep = 1
    else if (name ~ /\.rs\.in$/) keep = 1

    if (!keep) next
    if (name ~ /^\.#/ || name ~ /~$/ || name ~ /\.tmp$/ || name ~ /\.sw[opx]$/ || name ~ /\.cmd$/ || name ~ /\.o$/ || name ~ /\.ko$/ || name ~ /\.mod$/ || name ~ /\.mod\.c$/ || name ~ /\.order$/ || name ~ /\.symvers$/) next

    print path
  }
  '
}

tmp_list=$(mktemp "${TMPDIR:-/tmp}/sync_kernel_code.XXXXXX") || die "failed to create temp file"

if [ -n "$from_ref" ]; then
  echo "Collecting files changed since '$from_ref'..."
  git -C "$source_dir" diff --name-only "$from_ref" HEAD | awk -v prefix="$source_dir/" '{ print prefix $0 }' | filter_code_files > "$tmp_list"
else
  # Default: sync uncommitted working-tree changes
  git -C "$source_dir" status --porcelain --no-renames | awk -v prefix="$source_dir/" '
  {
    fname = substr($0, 4)
    print prefix fname
  }
  ' | filter_code_files > "$tmp_list"
fi

file_count=$(wc -l < "$tmp_list" | tr -d ' ')

if [ "$file_count" -eq 0 ]; then
  echo "No modified code files to sync."
  exit 0
fi

if [ "$do_status" -eq 1 ] || [ "$dry_run" -eq 1 ]; then
  cat "$tmp_list"
  printf '\n%d code files to sync\n' "$file_count"
  exit 0
fi

echo "Files to sync ($file_count):"
cat "$tmp_list" | while IFS= read -r f; do echo "  ${f#$source_dir/}"; done

i=0
while IFS= read -r src_path; do
  rel_path="${src_path#$source_dir/}"
  dest_path="$dest_dir/$rel_path"
  dest_parent=$(dirname "$dest_path")
  [ -d "$dest_parent" ] || mkdir -p "$dest_parent" || die "mkdir failed: $dest_parent"
  cp "$src_path" "$dest_path" || die "cp failed: $src_path -> $dest_path"
  i=$((i + 1))
  printf '\r  [%d/%d] %s\033[K' "$i" "$file_count" "$rel_path"
done < "$tmp_list"
printf '\r\033[K'
echo "Copied $file_count files to $dest_dir/"

# Commit in destination
git -C "$dest_dir" add -A
if git -C "$dest_dir" diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

commit_msg="sync: $(date '+%Y-%m-%d %H:%M:%S') - $file_count files from $(hostname)"
git -C "$dest_dir" commit -m "$commit_msg" || die "git commit failed"
commit_hash=$(git -C "$dest_dir" rev-parse --short HEAD)
echo "Committed: $commit_hash — $commit_msg"

if [ "$do_push" -eq 1 ]; then
  if git -C "$dest_dir" push origin "$branch" 2>"${TMPDIR:-/tmp}/sync_push_stderr.$$"; then
    echo "Pushed $file_count files to remote."
  else
    local_hash=$(git -C "$dest_dir" rev-parse --short HEAD 2>/dev/null || echo "?")
    remote_hash=$(git -C "$dest_dir" rev-parse --short "origin/$branch" 2>/dev/null || echo "?")
    printf '\n%s\n' '============================================'
    printf '%s\n' '  PUSH REJECTED — manual intervention required'
    printf '%s\n' '============================================'
    printf '  local  HEAD  : %s\n' "$local_hash"
    printf '  remote %-5s: %s\n' "$branch" "$remote_hash"
    if [ -s "${TMPDIR:-/tmp}/sync_push_stderr.$$" ]; then
      printf '  stderr:\n'
      sed 's/^/    /' "${TMPDIR:-/tmp}/sync_push_stderr.$$"
    fi
    printf '\n%s\n' '  To resolve:'
    printf '    cd %s\n' "$dest_dir"
    printf '    git pull --rebase origin %s   # integrate remote changes\n' "$branch"
    printf '    git push origin %s             # retry after rebase\n' "$branch"
    rm -f "${TMPDIR:-/tmp}/sync_push_stderr.$$"
    exit 1
  fi
else
  echo "Committed $file_count files locally. Use --push to push."
fi
