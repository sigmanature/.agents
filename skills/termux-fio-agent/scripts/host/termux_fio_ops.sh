#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  termux_fio_ops.sh check HOST
  termux_fio_ops.sh run HOST -- COMMAND...
  termux_fio_ops.sh push-script HOST LOCAL_SCRIPT [REMOTE_SCRIPT]
  termux_fio_ops.sh pull-latest HOST OUT_DIR
  termux_fio_ops.sh run-fio-and-pull HOST LOCAL_SCRIPT OUT_DIR

Environment for run-fio-and-pull:
  SIZE=256M RUNTIME=10 REPEAT=1 TIME_BASED=0 COOLDOWN=1
USAGE
}

need_host() {
  if [ "$#" -lt 1 ]; then
    usage >&2
    exit 2
  fi
}

check_host() {
  local host="$1"
  ssh "$host" 'id; pwd; command -v fio; command -v python'
}

run_cmd() {
  local host="$1"
  shift
  if [ "$#" -gt 0 ] && [ "$1" = "--" ]; then
    shift
  fi
  if [ "$#" -lt 1 ]; then
    echo "missing command" >&2
    exit 2
  fi
  ssh "$host" "$@"
}

push_script() {
  local host="$1"
  local local_script="$2"
  local remote_script="${3:-~/f2fs_fio_matrix.sh}"
  scp "$local_script" "$host:$remote_script"
  ssh "$host" "sed -i 's/\r$//' $remote_script"
}

pull_latest() {
  local host="$1"
  local out_dir="$2"
  mkdir -p "$out_dir"
  ssh "$host" '
    set -e
    latest=$(ls -td ~/fio-f2fs-test/results-* | head -1)
    tar -C "$(dirname "$latest")" -czf ~/fio-last-result.tgz "$(basename "$latest")"
    echo "LATEST=$latest"
  '
  scp "$host:~/fio-last-result.tgz" "$out_dir/$host-last-result.tgz"
  tar -xzf "$out_dir/$host-last-result.tgz" -C "$out_dir"
  echo "pulled=$out_dir/$host-last-result.tgz"
}

run_fio_and_pull() {
  local host="$1"
  local local_script="$2"
  local out_dir="$3"
  local size_arg="${SIZE:-256M}"
  local runtime_arg="${RUNTIME:-10}"
  local repeat_arg="${REPEAT:-1}"
  local time_based_arg="${TIME_BASED:-0}"
  local cooldown_arg="${COOLDOWN:-1}"

  push_script "$host" "$local_script" "~/f2fs_fio_matrix.sh"
  ssh "$host" "
    set -e
    cd ~
    SIZE=$size_arg RUNTIME=$runtime_arg REPEAT=$repeat_arg TIME_BASED=$time_based_arg COOLDOWN=$cooldown_arg bash ~/f2fs_fio_matrix.sh
    latest=\$(ls -td ~/fio-f2fs-test/results-* | head -1)
    echo \"LATEST=\$latest\"
    if [ -f \"\$latest/median.tsv\" ]; then cat \"\$latest/median.tsv\"; fi
    tar -C \"\$(dirname \"\$latest\")\" -czf ~/fio-last-result.tgz \"\$(basename \"\$latest\")\"
  "
  mkdir -p "$out_dir"
  scp "$host:~/fio-last-result.tgz" "$out_dir/$host-last-result.tgz"
  tar -xzf "$out_dir/$host-last-result.tgz" -C "$out_dir"
  echo "pulled=$out_dir/$host-last-result.tgz"
}

main() {
  if [ "$#" -lt 1 ]; then
    usage >&2
    exit 2
  fi
  local cmd="$1"
  shift
  case "$cmd" in
    check)
      need_host "$@"
      check_host "$1"
      ;;
    run)
      need_host "$@"
      local host="$1"
      shift
      run_cmd "$host" "$@"
      ;;
    push-script)
      if [ "$#" -lt 2 ]; then usage >&2; exit 2; fi
      push_script "$@"
      ;;
    pull-latest)
      if [ "$#" -lt 2 ]; then usage >&2; exit 2; fi
      pull_latest "$@"
      ;;
    run-fio-and-pull)
      if [ "$#" -lt 3 ]; then usage >&2; exit 2; fi
      run_fio_and_pull "$@"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
