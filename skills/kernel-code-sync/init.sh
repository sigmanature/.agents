#!/usr/bin/env bash
set -u -o pipefail

usage() {
  cat <<'EOF'
Usage: tools/init_kernel_code_repo.sh [options]

Initialize the kernel code shadow repository (common_kernel_code)
by batch-copying code files from common_my_dec and pushing incrementally.

Options:
  -s, --source DIR       Source tree. Default: common_my_dec
  -d, --dest DIR         Shadow repo. Default: common_kernel_code
  -r, --remote URL       Git remote URL to add and push to
  -b, --branch BRANCH    Git branch name. Default: main
      --batch-size MB    Max MB per batch. Default: 80
      --dry-run          Show batches without copying or pushing
      --skip-push        Commit locally but do not push
  -h, --help             Show this help

Examples:
  # Dry-run to see batch plan
  bash tools/init_kernel_code_repo.sh --dry-run

  # Initialize with remote
  bash tools/init_kernel_code_repo.sh --remote git@github.com:your/repo.git

  # Local-only init (no remote)
  bash tools/init_kernel_code_repo.sh --skip-push
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

source_dir="common_my_dec"
dest_dir="common_kernel_code"
remote_url=""
branch="main"
batch_size_mb=50
skip_push=0
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--source) [ "$#" -ge 2 ] || die "$1 requires a value"; source_dir=$2; shift 2 ;;
    -d|--dest)   [ "$#" -ge 2 ] || die "$1 requires a value"; dest_dir=$2; shift 2 ;;
    -r|--remote) [ "$#" -ge 2 ] || die "$1 requires a value"; remote_url=$2; shift 2 ;;
    -b|--branch) [ "$#" -ge 2 ] || die "$1 requires a value"; branch=$2; shift 2 ;;
    --batch-size) [ "$#" -ge 2 ] || die "$1 requires a value"; batch_size_mb=$2; shift 2 ;;
    --skip-push) skip_push=1; shift ;;
    --dry-run)   dry_run=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -d "$source_dir" ] || die "source directory not found: $source_dir"

git -C "$source_dir" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: $source_dir"

batch_size_bytes=$(( batch_size_mb * 1048576 ))

tmp_all=$(mktemp "${TMPDIR:-/tmp}/init_kernel_code.XXXXXX") || die "failed to create temp file"

find "$source_dir" -type f -not -path "$source_dir/arch/*" -not -path "$source_dir/drivers/*" -printf '%s %p\n' | awk '
{
  size = $1
  path = $2
  n = split(path, parts, "/")
  name = parts[n]

  keep = 0
  if (name == "Makefile" || name == "Kbuild" || name == "BUILD" || name == "BUILD.bazel" || name == "WORKSPACE" || name == "MODULE.bazel" || name == "Android.bp" || name == "Android.mk") keep = 1
  else if (name ~ /^Kconfig/) keep = 1
  else if (name ~ /\.(c|cc|cpp|cxx|h|hpp|hh|hxx|S|s|rs|py|sh|bash|pl|pm|awk|y|l|dts|dtsi|dtso|asn1|ld|lds|bzl|mk|bp|inc|tbl|uc|go)$/) keep = 1
  else if (name ~ /\.rs\.in$/) keep = 1

  if (!keep) next

  if (name ~ /^\.#/ || name ~ /~$/ || name ~ /\.tmp$/ || name ~ /\.sw[opx]$/ || name ~ /\.cmd$/ || name ~ /\.o$/ || name ~ /\.ko$/ || name ~ /\.mod$/ || name ~ /\.mod\.c$/ || name ~ /\.order$/ || name ~ /\.symvers$/) next

  print size, path
}
' | LC_ALL=C sort -k2 > "$tmp_all"

total_files=$(wc -l < "$tmp_all" | tr -d ' ')
total_bytes=$(awk '{s+=$1} END {print s}' "$tmp_all")
[ "$total_files" -gt 0 ] || die "no code files found in $source_dir"

echo "Total code files: $total_files"
echo "Total size: $(awk "BEGIN {printf \"%.2f MB\", $total_bytes/1048576}")"
echo "Batch size limit: ${batch_size_mb} MB"
echo ""

awk -v src="$source_dir" -v batch_size="$batch_size_bytes" '
{
  size = $1
  path = $2
  n = split(path, parts, "/")
  top = (n >= 2) ? parts[2] : "root"
  print top, size, path
}
' "$tmp_all" | awk -v batch_size="$batch_size_bytes" '
{
  top = $1
  size = $2
  path = $3
  for (i = 4; i <= NF; i++) path = path " " $i

  if (top != prev_top) {
    if (current_size > 0) {
      flush_batch()
    }
    prev_top = top
  }

  if (current_size + size > batch_size && current_size > 0) {
    flush_batch()
  }

  current_files[++current_count] = path
  current_size += size
}

END {
  if (current_size > 0) flush_batch()
}

function flush_batch() {
  batch_num++
  printf "BATCH %d: %s (%d files, %.2f MB)\n", batch_num, prev_top, current_count, current_size / 1048576
  for (i = 1; i <= current_count; i++) {
    printf "  %s\n", current_files[i]
  }
  current_count = 0
  current_size = 0
}
' > "${tmp_all}.batches"

batch_count=$(grep -c '^BATCH ' "${tmp_all}.batches" || echo 0)
echo "Planned batches: $batch_count"

if [ "$dry_run" -eq 1 ]; then
  cat "${tmp_all}.batches"
  exit 0
fi

if [ -d "$dest_dir/.git" ]; then
  echo "Destination repo exists: $dest_dir"
  if [ -n "$(git -C "$dest_dir" status --porcelain)" ] || [ -n "$(git -C "$dest_dir" log --oneline -n 1 2>/dev/null)" ]; then
    echo "WARNING: $dest_dir is not empty. Remove it first if you want a clean init."
    echo "Run: rm -rf $dest_dir"
    exit 1
  fi
else
  echo "Initializing git repo: $dest_dir"
  mkdir -p "$dest_dir"
  git -C "$dest_dir" init -b "$branch" || die "git init failed"
   cp "$source_dir/.gitignore" "$dest_dir/.gitignore"
   git -C "$dest_dir" add .gitignore
   git -C "$dest_dir" commit -m "init: add .gitignore from $source_dir"
fi

if [ -n "$remote_url" ]; then
  if ! git -C "$dest_dir" remote get-url origin >/dev/null 2>&1; then
    git -C "$dest_dir" remote add origin "$remote_url"
    echo "Added remote: $remote_url"
  fi
fi

batch_num=0
while IFS= read -r line; do
  case "$line" in
    BATCH*)
      batch_num=$((batch_num + 1))
      echo ""
      echo "=========================================="
      echo "Processing $line"
      echo "=========================================="

      if [ "$batch_num" -gt 1 ]; then
        git -C "$dest_dir" add -A
        git -C "$dest_dir" commit -m "batch $((batch_num - 1)): import code files" || die "git commit failed for batch $((batch_num - 1))"
        if [ "$skip_push" -eq 0 ] && [ -n "$remote_url" ]; then
          git -C "$dest_dir" push -u origin "$branch" || git -C "$dest_dir" push -u origin "$branch" --force || die "git push failed for batch $((batch_num - 1))"
        fi
      fi
      ;;
    \ \ *)
      src_path=$(printf '%s' "$line" | sed 's/^  //')
      rel_path="${src_path#$source_dir/}"
      dest_path="$dest_dir/$rel_path"
      dest_parent=$(dirname "$dest_path")
      [ -d "$dest_parent" ] || mkdir -p "$dest_parent" || die "mkdir failed: $dest_parent"
      cp "$src_path" "$dest_path" || die "cp failed: $src_path -> $dest_path"
      ;;
  esac
done < "${tmp_all}.batches"

if [ "$batch_num" -gt 0 ]; then
  echo ""
  echo "=========================================="
  echo "Finalizing batch $batch_num"
  echo "=========================================="
  git -C "$dest_dir" add -A
  git -C "$dest_dir" commit -m "batch $batch_num: import code files" || die "git commit failed for batch $batch_num"
  if [ "$skip_push" -eq 0 ] && [ -n "$remote_url" ]; then
    git -C "$dest_dir" push -u origin "$branch" || die "git push failed for batch $batch_num"
  fi
fi

echo ""
echo "Done. Initialized $dest_dir with $total_files code files in $batch_num batches."
if [ "$skip_push" -eq 0 ] && [ -n "$remote_url" ]; then
  echo "Pushed to: $remote_url"
else
  echo "Commits are local. Push manually when ready."
fi
