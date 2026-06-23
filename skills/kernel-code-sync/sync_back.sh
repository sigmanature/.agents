#!/usr/bin/env bash
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

usage() {
  cat <<'EOF'
Usage: sync_back.sh [options]

Sync changed code files from the shadow repository back into the main kernel
worktree using a three-way check:
  base   = shadow repo tree at last successful reverse-sync commit
  source = current shadow repo HEAD tree
  dest   = current main worktree file content

Only real conflicts are blocked: a path is rejected only when both source and
dest diverged from the same base and do not converge to identical content.

Options:
  -s, --source DIR        Shadow repo source. Default: common_kernel_code
  -d, --dest DIR          Main kernel worktree. Default: common_my_dec
  -f, --from REF          Compare source changes from REF..HEAD instead of the
                          saved reverse-sync base commit
      --state-file FILE   Override persistent state file path
      --init-state        Initialize state to current source HEAD if missing
      --reset-state       Force-reset state to current source HEAD
      --replay            Ignore saved state and re-run the current source HEAD
                          against its first parent as the base
      --status            Show planned actions / conflicts only
      --dry-run           Same as --status
  -h, --help              Show this help
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

source_dir="common_kernel_code"
dest_dir="common_my_dec"
from_ref=""
state_file=""
do_init_state=0
do_reset_state=0
do_replay=0
do_status=0
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--source) [ "$#" -ge 2 ] || die "$1 requires a value"; source_dir=$2; shift 2 ;;
    -d|--dest)   [ "$#" -ge 2 ] || die "$1 requires a value"; dest_dir=$2; shift 2 ;;
    -f|--from)   [ "$#" -ge 2 ] || die "$1 requires a value"; from_ref=$2; shift 2 ;;
    --state-file) [ "$#" -ge 2 ] || die "$1 requires a value"; state_file=$2; shift 2 ;;
    --init-state) do_init_state=1; shift ;;
    --reset-state) do_reset_state=1; shift ;;
    --replay) do_replay=1; shift ;;
    --status)    do_status=1; shift ;;
    --dry-run)   dry_run=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -d "$source_dir/.git" ] || die "source not a git repo: $source_dir"
[ -d "$dest_dir" ] || die "destination directory not found: $dest_dir"
git -C "$dest_dir" rev-parse --git-dir >/dev/null 2>&1 || die "destination not a git repo: $dest_dir"

source_abs="$(readlink -f "$source_dir")"
dest_abs="$(readlink -f "$dest_dir")"
skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

default_state_file() {
  local key
  key="$(printf '%s\n%s\n' "$source_abs" "$dest_abs" | git hash-object --stdin)"
  printf '%s/state/reverse_%s.env\n' "$skill_dir" "$key"
}

if [ -z "$state_file" ]; then
  state_file="$(default_state_file)"
fi

current_source_head="$(git -C "$source_dir" rev-parse HEAD)" || die "failed to resolve source HEAD"

write_state() {
  local commit="$1"
  local state_dir tmp_file
  state_dir="$(dirname "$state_file")"
  mkdir -p "$state_dir" || die "failed to create state dir: $state_dir"
  tmp_file="$(mktemp "${TMPDIR:-/tmp}/sync_back_state.XXXXXX")" || die "failed to create temp file"
  {
    printf 'SOURCE_DIR=%q\n' "$source_abs"
    printf 'DEST_DIR=%q\n' "$dest_abs"
    printf 'LAST_SOURCE_COMMIT=%q\n' "$commit"
    printf 'UPDATED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  } > "$tmp_file"
  mv "$tmp_file" "$state_file" || die "failed to write state file: $state_file"
}

load_state() {
  LAST_SOURCE_COMMIT=""
  if [ -f "$state_file" ]; then
    # shellcheck disable=SC1090
    . "$state_file"
  fi
}

if [ "$do_reset_state" -eq 1 ]; then
  write_state "$current_source_head"
  printf 'Reset reverse-sync state: %s -> %s\n' "$state_file" "$current_source_head"
  exit 0
fi

if [ "$do_init_state" -eq 1 ]; then
  if [ -f "$state_file" ]; then
    load_state
    printf 'Reverse-sync state already exists: %s (%s)\n' "$state_file" "${LAST_SOURCE_COMMIT:-unknown}"
    exit 0
  fi
  write_state "$current_source_head"
  printf 'Initialized reverse-sync state: %s -> %s\n' "$state_file" "$current_source_head"
  exit 0
fi

load_state

if [ -n "$from_ref" ]; then
  base_commit="$from_ref"
elif [ "$do_replay" -eq 1 ]; then
  if git -C "$source_dir" rev-parse --verify "${current_source_head}^1" >/dev/null 2>&1; then
    base_commit="$(git -C "$source_dir" rev-parse "${current_source_head}^1")"
  else
    base_commit="4b825dc642cb6eb9a060e54bf8d69288fbee4904"
  fi
else
  [ -n "${LAST_SOURCE_COMMIT:-}" ] || die "reverse-sync state missing; run 'bash $skill_dir/sync_back.sh --source \"$source_abs\" --dest \"$dest_abs\" --init-state'"
  base_commit="$LAST_SOURCE_COMMIT"
fi

git -C "$source_dir" cat-file -e "${base_commit}^{commit}" >/dev/null 2>&1 || die "base commit not available in source repo: $base_commit"

if ! git -C "$source_dir" diff --quiet --ignore-submodules HEAD --; then
  die "source working tree has uncommitted changes; commit/merge/rebase first so reverse-sync has a stable source tree"
fi

tmp_records=""
tmp_filtered=""
tmp_actions=""
tmp_conflicts=""
cleanup() {
  [ -n "$tmp_records" ] && [ -f "$tmp_records" ] && rm -f "$tmp_records"
  [ -n "$tmp_filtered" ] && [ -f "$tmp_filtered" ] && rm -f "$tmp_filtered"
  [ -n "$tmp_actions" ] && [ -f "$tmp_actions" ] && rm -f "$tmp_actions"
  [ -n "$tmp_conflicts" ] && [ -f "$tmp_conflicts" ] && rm -f "$tmp_conflicts"
}
trap cleanup EXIT INT TERM HUP

filter_code_records() {
  awk -F '\t' '
  {
    op = $1
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

    print op "\t" path
  }
  '
}

tree_blob_oid() {
  local repo_dir="$1" treeish="$2" rel_path="$3"
  if git -C "$repo_dir" cat-file -e "${treeish}:${rel_path}" >/dev/null 2>&1; then
    git -C "$repo_dir" rev-parse "${treeish}:${rel_path}"
  else
    printf '%s\n' '__ABSENT__'
  fi
}

worktree_blob_oid() {
  local path="$1"
  if [ -f "$path" ]; then
    git hash-object "$path"
  else
    printf '%s\n' '__ABSENT__'
  fi
}

tmp_records="$(mktemp "${TMPDIR:-/tmp}/sync_back_records.XXXXXX")" || die "failed to create temp file"
tmp_filtered="$(mktemp "${TMPDIR:-/tmp}/sync_back_filtered.XXXXXX")" || die "failed to create temp file"
tmp_actions="$(mktemp "${TMPDIR:-/tmp}/sync_back_actions.XXXXXX")" || die "failed to create temp file"
tmp_conflicts="$(mktemp "${TMPDIR:-/tmp}/sync_back_conflicts.XXXXXX")" || die "failed to create temp file"

git -C "$source_dir" diff --name-status --no-renames "$base_commit" HEAD | awk '
{
  op = $1
  path = $2
  if (op ~ /^D/) print "DELETE\t" path
  else print "SYNC\t" path
}
' > "$tmp_records"

filter_code_records < "$tmp_records" > "$tmp_filtered"
record_count="$(wc -l < "$tmp_filtered" | tr -d ' ')"

if [ "$record_count" -eq 0 ]; then
  echo "No modified code files to sync back."
  if [ "$do_status" -eq 0 ] && [ "$dry_run" -eq 0 ]; then
    write_state "$current_source_head"
    printf 'State advanced to %s\n' "$current_source_head"
  fi
  exit 0
fi

apply_count=0
noop_count=0
conflict_count=0

while IFS=$'\t' read -r op rel_path; do
  base_oid="$(tree_blob_oid "$source_dir" "$base_commit" "$rel_path")"
  source_oid="$(tree_blob_oid "$source_dir" HEAD "$rel_path")"
  dest_oid="$(worktree_blob_oid "$dest_abs/$rel_path")"

  action=""
  reason=""

  if [ "$dest_oid" = "$source_oid" ]; then
    action="NOOP"
    reason="dest already matches source"
    noop_count=$((noop_count + 1))
  elif [ "$dest_oid" = "$base_oid" ]; then
    if [ "$source_oid" = "__ABSENT__" ]; then
      action="DELETE"
      reason="safe delete; dest still at base"
    else
      action="COPY"
      reason="safe update; dest still at base"
    fi
    apply_count=$((apply_count + 1))
  elif [ "$source_oid" = "$base_oid" ]; then
    action="NOOP"
    reason="source equals base"
    noop_count=$((noop_count + 1))
  else
    action="CONFLICT"
    reason="both dest and source diverged from base"
    conflict_count=$((conflict_count + 1))
    printf '%s\t%s\t%s\n' "$action" "$rel_path" "$reason" >> "$tmp_conflicts"
  fi

  printf '%s\t%s\t%s\n' "$action" "$rel_path" "$reason" >> "$tmp_actions"
done < "$tmp_filtered"

if [ "$do_status" -eq 1 ] || [ "$dry_run" -eq 1 ]; then
  echo "Planned reverse-sync actions:"
  printf 'Replay mode: %s\n' "$([ "$do_replay" -eq 1 ] && echo yes || echo no)"
  while IFS=$'\t' read -r action rel_path reason; do
    printf '  %-8s %s  (%s)\n' "$action" "$rel_path" "$reason"
  done < "$tmp_actions"
  printf '\nSummary: %d apply, %d noop, %d conflict\n' "$apply_count" "$noop_count" "$conflict_count"
  printf 'Base commit: %s\n' "$base_commit"
  printf 'Source HEAD: %s\n' "$current_source_head"
  printf 'State file : %s\n' "$state_file"
  exit 0
fi

if [ "$conflict_count" -gt 0 ]; then
  echo "Reverse-sync blocked by real content conflicts:"
  while IFS=$'\t' read -r action rel_path reason; do
    printf '  %-8s %s  (%s)\n' "$action" "$rel_path" "$reason"
  done < "$tmp_conflicts"
  printf '\nResolve those paths manually, or first make %s match the desired base/source state before retrying.\n' "$dest_abs"
  printf 'Base commit: %s\n' "$base_commit"
  printf 'Source HEAD: %s\n' "$current_source_head"
  exit 1
fi

if [ "$apply_count" -eq 0 ]; then
  echo "All changed paths already converge; no file copies needed."
  write_state "$current_source_head"
  printf 'State advanced to %s\n' "$current_source_head"
  exit 0
fi

echo "Applying reverse-sync updates:"
applied=0
while IFS=$'\t' read -r action rel_path reason; do
  src_path="$source_abs/$rel_path"
  dest_path="$dest_abs/$rel_path"

  case "$action" in
    COPY)
      dest_parent="$(dirname "$dest_path")"
      [ -d "$dest_parent" ] || mkdir -p "$dest_parent" || die "mkdir failed: $dest_parent"
      cp "$src_path" "$dest_path" || die "cp failed: $src_path -> $dest_path"
      applied=$((applied + 1))
      printf '\r  [%d/%d] COPY    %s\033[K' "$applied" "$apply_count" "$rel_path"
      ;;
    DELETE)
      if [ -e "$dest_path" ]; then
        rm -f "$dest_path" || die "rm failed: $dest_path"
      fi
      applied=$((applied + 1))
      printf '\r  [%d/%d] DELETE  %s\033[K' "$applied" "$apply_count" "$rel_path"
      ;;
    *)
      ;;
  esac
done < "$tmp_actions"
printf '\r\033[K'

write_state "$current_source_head"
printf 'Applied %d updates into %s\n' "$apply_count" "$dest_abs"
printf 'State advanced to %s\n' "$current_source_head"
printf 'Review with: git -C %s status -sb\n' "$dest_abs"
