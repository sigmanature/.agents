#!/usr/bin/env bash
set -u -o pipefail

usage() {
  cat <<'EOF'
Usage: reverse_sync.sh [options]

Reverse sync: copy code changes from the shadow repo (common) back to the
full kernel tree (pixel/common).  Default mode reads the tracking file
to sync only new commits since the last run, then updates the pointer.

Options:
  -s, --source DIR       Shadow repo (code-only). Default: ~/learn_os/common
  -d, --dest DIR         Full kernel tree.        Default: ~/learn_os/pixel/common
  -b, --branch BRANCH    Branch to compare.       Default: main
      --status           Show what would be synced (no copy)
      --dry-run          Same as --status
      --reset            Forget the last-synced commit; next run syncs from
                         the first commit (will NOT overwrite)
      --commit           Also commit the changes in the full kernel tree
  -h, --help             Show this help

Examples:
  # See what changed since last sync
  bash reverse_sync.sh --status

  # Sync new changes from shadow -> full tree
  bash reverse_sync.sh

  # Sync + also commit in the full tree
  bash reverse_sync.sh --commit
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
shadow_dir="${HOME}/learn_os/common"
full_dir="${HOME}/learn_os/pixel/common"
branch="main"
do_status=0
do_reset=0
do_commit=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--source) [ "$#" -ge 2 ] || die "$1 requires a value"; shadow_dir=$2; shift 2 ;;
    -d|--dest)   [ "$#" -ge 2 ] || die "$1 requires a value"; full_dir=$2; shift 2 ;;
    -b|--branch) [ "$#" -ge 2 ] || die "$1 requires a value"; branch=$2; shift 2 ;;
    --status)    do_status=1; shift ;;
    --dry-run)   do_status=1; shift ;;
    --reset)     do_reset=1; shift ;;
    --commit)    do_commit=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -d "$shadow_dir/.git" ] || die "shadow repo not a git repo: $shadow_dir"
[ -d "$full_dir/.git"  ] || die "full tree not a git repo: $full_dir"

# Sentinel file lives inside the skill directory
sentinel="${HOME}/.agents/skills/kernel-code-sync/.last_synced_commit"

if [ "$do_reset" -eq 1 ]; then
  rm -f "$sentinel"
  echo "Reset: tracking pointer removed.  Next run will start fresh."
  exit 0
fi

# Determine the range of commits to compare
head_sha=$(git -C "$shadow_dir" rev-parse --quiet --verify HEAD) || die "cannot read HEAD"

if [ -f "$sentinel" ]; then
  last_synced=$(cat "$sentinel")
  # Verify the last-synced commit still exists in the repo
  if git -C "$shadow_dir" cat-file -e "$last_synced" 2>/dev/null; then
    range="$last_synced..$head_sha"
    range_label="$last_synced → HEAD"
  else
    echo "Previous tracking commit $last_synced no longer exists; syncing from root." >&2
    range="$head_sha"
    range_label="all files (fresh)"
  fi
else
  # No sentinel → first run.  List all tracked files so the user can decide.
  range="$head_sha"
  range_label="all files (first run)"
fi

# Get list of changed code files in the shadow repo
tmp_list=$(mktemp "${TMPDIR:-/tmp}/reverse_sync.XXXXXX") || die "failed to create temp file"
trap 'rm -f "$tmp_list"' EXIT INT TERM HUP

if [ "$range" = "$head_sha" ]; then
  # First run or forced full sync: list all code files
  git -C "$shadow_dir" ls-tree -r --name-only HEAD > "$tmp_list"
else
  git -C "$shadow_dir" diff --name-only "$range" > "$tmp_list"
fi

# Filter: keep only code files (same rules as sync.sh)
awk '
{
  n = split($0, parts, "/")
  name = parts[n]
  keep = 0
  if (name == "Makefile" || name == "Kbuild" || name == "BUILD" || name == "BUILD.bazel" ||
      name == "WORKSPACE" || name == "MODULE.bazel" || name == "Android.bp" || name == "Android.mk")
    keep = 1
  else if (name ~ /^Kconfig/) keep = 1
  else if (name ~ /\.(c|cc|cpp|cxx|h|hpp|hh|hxx|S|s|rs|py|sh|bash|pl|pm|awk|y|l|dts|dtsi|dtso|asn1|ld|lds|bzl|mk|bp|inc|tbl|uc|go)$/) keep = 1
  else if (name ~ /\.rs\.in$/) keep = 1
  if (!keep) next
  if (name ~ /^\.#/ || name ~ /~$/ || name ~ /\.tmp$/ || name ~ /\.sw[opx]$/ ||
      name ~ /\.cmd$/ || name ~ /\.o$/ || name ~ /\.ko$/ || name ~ /\.mod$/ ||
      name ~ /\.mod\.c$/ || name ~ /\.order$/ || name ~ /\.symvers$/) next
  print $0
}
' "$tmp_list" > "${tmp_list}.filtered"

file_count=$(wc -l < "${tmp_list}.filtered" | tr -d ' ')

if [ "$file_count" -eq 0 ]; then
  echo "No new code changes to reverse-sync."
  # Still advance the pointer so we don't re-check the same range
  echo "$head_sha" > "$sentinel"
  exit 0
fi

if [ "$do_status" -eq 1 ]; then
  echo "Range: $range_label"
  echo "Files to sync ($file_count):"
  cat "${tmp_list}.filtered"
  exit 0
fi

echo "Reverse-syncing $file_count files (range: $range_label) …"

while IFS= read -r rel_path; do
  src="$shadow_dir/$rel_path"
  dst="$full_dir/$rel_path"
  [ -f "$src" ] || continue
  dst_parent=$(dirname "$dst")
  [ -d "$dst_parent" ] || mkdir -p "$dst_parent" || die "mkdir failed: $dst_parent"
  cp "$src" "$dst" || die "cp failed: $src -> $dst"
done < "${tmp_list}.filtered"

# Advance tracking pointer
echo "$head_sha" > "$sentinel"
echo "Done.  Last synced commit: $(git -C "$shadow_dir" log -1 --format='%h %s' HEAD)"

if [ "$do_commit" -eq 1 ]; then
  git -C "$full_dir" add -A
  if git -C "$full_dir" diff --cached --quiet; then
    echo "Full tree: no changes to commit."
  else
    git -C "$full_dir" commit -m "reverse-sync: $file_count files from shadow repo ($(git -C "$shadow_dir" log -1 --format='%h' HEAD))"
    echo "Full tree commit created."
  fi
fi