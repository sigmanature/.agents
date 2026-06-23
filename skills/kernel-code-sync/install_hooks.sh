#!/usr/bin/env bash
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

usage() {
  cat <<'EOF'
Usage: install_hooks.sh [options]

Install forward and reverse sync hooks:
  forward: common_my_dec     -> common_kernel_code
  reverse: common_kernel_code -> common_my_dec

Hooks installed:
  - post-commit
  - post-merge
  - post-rewrite

Options:
  -p, --project DIR         Project root. Default: ~/learn_os/pixel
      --forward-source DIR  Forward source worktree
      --forward-dest DIR    Forward shadow repo
      --forward-log FILE    Forward hook log file
      --reverse-source DIR  Reverse source shadow repo
      --reverse-dest DIR    Reverse destination worktree
      --reverse-log FILE    Reverse hook log file
      --skip-forward        Do not install forward hooks
      --skip-reverse        Do not install reverse hooks
      --reset-reverse-state Reset reverse-sync state to current reverse source HEAD
  -h, --help                Show this help
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

project_root="${HOME}/learn_os/pixel"
forward_source_dir=""
forward_dest_dir=""
forward_log_file="${TMPDIR:-/tmp}/sync_kernel_code_hooks.log"
reverse_source_dir=""
reverse_dest_dir=""
reverse_log_file="${TMPDIR:-/tmp}/sync_kernel_code_reverse_hooks.log"
skip_forward=0
skip_reverse=0
reset_reverse_state=0
have_forward_source=0
have_forward_dest=0
have_reverse_source=0
have_reverse_dest=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -p|--project) [ "$#" -ge 2 ] || die "$1 requires a value"; project_root=$2; shift 2 ;;
    --forward-source) [ "$#" -ge 2 ] || die "$1 requires a value"; forward_source_dir=$2; have_forward_source=1; shift 2 ;;
    --forward-dest) [ "$#" -ge 2 ] || die "$1 requires a value"; forward_dest_dir=$2; have_forward_dest=1; shift 2 ;;
    --forward-log) [ "$#" -ge 2 ] || die "$1 requires a value"; forward_log_file=$2; shift 2 ;;
    --reverse-source) [ "$#" -ge 2 ] || die "$1 requires a value"; reverse_source_dir=$2; have_reverse_source=1; shift 2 ;;
    --reverse-dest) [ "$#" -ge 2 ] || die "$1 requires a value"; reverse_dest_dir=$2; have_reverse_dest=1; shift 2 ;;
    --reverse-log) [ "$#" -ge 2 ] || die "$1 requires a value"; reverse_log_file=$2; shift 2 ;;
    --skip-forward) skip_forward=1; shift ;;
    --skip-reverse) skip_reverse=1; shift ;;
    --reset-reverse-state) reset_reverse_state=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

if [ "$have_forward_source" -eq 0 ]; then
  forward_source_dir="$project_root/common_my_dec"
fi
if [ "$have_forward_dest" -eq 0 ]; then
  forward_dest_dir="$project_root/common_kernel_code"
fi
if [ "$have_reverse_source" -eq 0 ]; then
  reverse_source_dir="$project_root/common_kernel_code"
fi
if [ "$have_reverse_dest" -eq 0 ]; then
  reverse_dest_dir="$project_root/common_my_dec"
fi

sync_script="$HOME/.agents/skills/kernel-code-sync/sync.sh"
sync_back_script="$HOME/.agents/skills/kernel-code-sync/sync_back.sh"
[ -x "$sync_script" ] || die "forward sync script not executable: $sync_script"
[ -x "$sync_back_script" ] || die "reverse sync script not executable: $sync_back_script"

install_triplet() {
  local repo_dir="$1" payload="$2" desc="$3"
  local git_common_dir hooks_dir hook_path hook_name

  git -C "$repo_dir" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $repo_dir"
  git_common_dir="$(git -C "$repo_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || git -C "$repo_dir" rev-parse --absolute-git-dir 2>/dev/null || true)" || die "failed to resolve git common dir for $repo_dir"
  [ -n "$git_common_dir" ] || die "failed to resolve git common dir for $repo_dir"
  hooks_dir="$(readlink -f "$git_common_dir/hooks" 2>/dev/null || true)"
  if [ -z "$hooks_dir" ]; then
    hooks_dir="$git_common_dir/hooks"
  fi
  mkdir -p "$hooks_dir" || die "failed to create hooks dir: $hooks_dir"

  for hook_name in post-commit post-merge post-rewrite; do
    hook_path="$hooks_dir/$hook_name"
    printf '%s\n' "$payload" > "$hook_path" || die "failed to write $hook_path"
    chmod +x "$hook_path" || die "failed to chmod $hook_path"
  done

  printf 'Installed %s hooks in %s\n' "$desc" "$hooks_dir"
}

if [ "$skip_forward" -eq 0 ]; then
  [ -d "$forward_source_dir" ] || die "forward source not found: $forward_source_dir"
  [ -d "$forward_dest_dir/.git" ] || die "forward destination repo not found: $forward_dest_dir"

  forward_payload=$(cat <<EOF
#!/usr/bin/env bash
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

hook_name="\$(basename "\$0")"
worktree_root="\$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ "\$worktree_root" = "$(readlink -f "$forward_source_dir")" ] || exit 0

sync_script="$sync_script"
source_dir="$(readlink -f "$forward_source_dir")"
dest_dir="$(readlink -f "$forward_dest_dir")"
log_file="$forward_log_file"

mkdir -p "\$(dirname "\$log_file")" 2>/dev/null || true

run_forward() {
  local from_ref="\$1"
  local label="\$2"
  local ts
  ts="\$(date '+%Y-%m-%d %H:%M:%S')"
  {
    printf '[%s] %s: start (%s)\n' "\$ts" "\$hook_name" "\$label"
    if [ -n "\$from_ref" ]; then
      bash "\$sync_script" --source "\$source_dir" --dest "\$dest_dir" --from "\$from_ref" --push
    else
      bash "\$sync_script" --source "\$source_dir" --dest "\$dest_dir" --push
    fi
    rc=\$?
    printf '[%s] %s: end rc=%s (%s)\n' "\$(date '+%Y-%m-%d %H:%M:%S')" "\$hook_name" "\$rc" "\$label"
    exit "\$rc"
  } 2>&1 | tee -a "\$log_file"
}

case "\$hook_name" in
  post-commit)
    run_forward "HEAD~1" "forward sync committed delta"
    ;;
  post-merge)
    run_forward "" "forward sync after merge/pull"
    ;;
  post-rewrite)
    case "\${1:-}" in
      rebase|amend) run_forward "" "forward sync after rewrite:\${1:-unknown}" ;;
      *) exit 0 ;;
    esac
    ;;
  *)
    exit 0
    ;;
esac
EOF
)

  install_triplet "$forward_source_dir" "$forward_payload" "forward"
fi

if [ "$skip_reverse" -eq 0 ]; then
  [ -d "$reverse_source_dir/.git" ] || die "reverse source repo not found: $reverse_source_dir"
  [ -d "$reverse_dest_dir" ] || die "reverse destination not found: $reverse_dest_dir"

  if [ "$reset_reverse_state" -eq 1 ]; then
    bash "$sync_back_script" --source "$reverse_source_dir" --dest "$reverse_dest_dir" --reset-state >/dev/null
  else
    bash "$sync_back_script" --source "$reverse_source_dir" --dest "$reverse_dest_dir" --init-state >/dev/null
  fi

  reverse_payload=$(cat <<EOF
#!/usr/bin/env bash
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

hook_name="\$(basename "\$0")"
worktree_root="\$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ "\$worktree_root" = "$(readlink -f "$reverse_source_dir")" ] || exit 0

sync_back_script="$sync_back_script"
source_dir="$(readlink -f "$reverse_source_dir")"
dest_dir="$(readlink -f "$reverse_dest_dir")"
log_file="$reverse_log_file"

mkdir -p "\$(dirname "\$log_file")" 2>/dev/null || true

run_reverse() {
  local label="\$1"
  local ts
  ts="\$(date '+%Y-%m-%d %H:%M:%S')"
  {
    printf '[%s] %s: start (%s)\n' "\$ts" "\$hook_name" "\$label"
    bash "\$sync_back_script" --source "\$source_dir" --dest "\$dest_dir"
    rc=\$?
    printf '[%s] %s: end rc=%s (%s)\n' "\$(date '+%Y-%m-%d %H:%M:%S')" "\$hook_name" "\$rc" "\$label"
    exit "\$rc"
  } 2>&1 | tee -a "\$log_file"
}

case "\$hook_name" in
  post-commit)
    run_reverse "reverse sync committed delta"
    ;;
  post-merge)
    run_reverse "reverse sync after merge/pull"
    ;;
  post-rewrite)
    case "\${1:-}" in
      rebase|amend) run_reverse "reverse sync after rewrite:\${1:-unknown}" ;;
      *) exit 0 ;;
    esac
    ;;
  *)
    exit 0
    ;;
esac
EOF
)

  install_triplet "$reverse_source_dir" "$reverse_payload" "reverse"
fi

printf 'Forward log: %s\n' "$forward_log_file"
printf 'Reverse log: %s\n' "$reverse_log_file"
