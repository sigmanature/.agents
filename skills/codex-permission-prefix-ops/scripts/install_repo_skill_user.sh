#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 /abs/or/relative/path/to/repo-skill-dir" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

src="${1%/}"
if [[ ! -d "$src" ]]; then
  echo "Source skill directory not found: $src" >&2
  exit 1
fi

if [[ ! -f "$src/SKILL.md" && ! -f "$src/skill.md" ]]; then
  echo "Source skill directory must contain SKILL.md or skill.md: $src" >&2
  exit 1
fi

installer="$HOME/.agents/install_skills.py"
if [[ ! -f "$installer" ]]; then
  echo "Installer not found: $installer" >&2
  exit 1
fi

tmp_root="$(mktemp -d /tmp/repo-skill-install.XXXXXX)"
cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

skill_name="$(basename "$src")"
cp -a "$src" "$tmp_root/$skill_name"

python3 "$installer" "$tmp_root/$skill_name" --scope user --force
