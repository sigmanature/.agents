#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  sync_user_skill_links.sh <skill-name> [<skill-name> ...]
  sync_user_skill_links.sh --all
  sync_user_skill_links.sh --all --force
EOF
}

force=0
sync_all=0
declare -a skill_names=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      force=1
      shift
      ;;
    --all)
      sync_all=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      skill_names+=("$1")
      shift
      ;;
  esac
done

agents_root="$HOME/.agents/skills"
if [[ ! -d "$agents_root" ]]; then
  echo "Missing canonical skill root: $agents_root" >&2
  exit 1
fi

if [[ $sync_all -eq 1 && ${#skill_names[@]} -gt 0 ]]; then
  echo "Use either --all or explicit skill names, not both." >&2
  exit 2
fi

if [[ $sync_all -eq 1 ]]; then
  while IFS= read -r name; do
    skill_names+=("$name")
  done < <(find "$agents_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
fi

if [[ ${#skill_names[@]} -eq 0 ]]; then
  usage
  exit 2
fi

declare -a vendor_roots=(
  "$HOME/.codex"
  "$HOME/.claude"
  "$HOME/.roo"
  "$HOME/.cline"
  "$HOME/.tabby"
  "$HOME/.ollama"
)

linked=0
skipped=0
failed=0

for vendor_root in "${vendor_roots[@]}"; do
  [[ -d "$vendor_root" ]] || continue
  skills_dir="$vendor_root/skills"
  mkdir -p "$skills_dir"

  for skill_name in "${skill_names[@]}"; do
    src="$agents_root/$skill_name"
    dst="$skills_dir/$skill_name"

    if [[ ! -d "$src" ]]; then
      echo "[MISS] $src" >&2
      failed=$((failed + 1))
      continue
    fi

    if [[ -L "$dst" ]]; then
      current_target="$(readlink -f "$dst" || true)"
      expected_target="$(readlink -f "$src" || true)"
      if [[ "$current_target" == "$expected_target" ]]; then
        echo "[SKIP] $dst already linked"
        skipped=$((skipped + 1))
        continue
      fi
    elif [[ -e "$dst" ]]; then
      if [[ $force -ne 1 ]]; then
        echo "[SKIP] $dst exists (use --force to replace)"
        skipped=$((skipped + 1))
        continue
      fi
    fi

    if [[ -e "$dst" || -L "$dst" ]]; then
      rm -rf "$dst"
    fi

    ln -s "$src" "$dst"
    echo "[LINK] $dst -> $src"
    linked=$((linked + 1))
  done
done

echo "[SUMMARY] linked=$linked skipped=$skipped failed=$failed"
if [[ $failed -ne 0 ]]; then
  exit 1
fi
