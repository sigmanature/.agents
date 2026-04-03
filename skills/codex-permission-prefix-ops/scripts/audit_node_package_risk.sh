#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 /abs/path/to/node-package-dir" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

pkg_dir="${1%/}"
if [[ ! -d "$pkg_dir" ]]; then
  echo "Package directory not found: $pkg_dir" >&2
  exit 1
fi

pkg_json="$pkg_dir/package.json"
if [[ ! -f "$pkg_json" ]]; then
  echo "Missing package.json: $pkg_json" >&2
  exit 1
fi

declare -a first_party_globs=(
  -g '!README*'
  -g '!LICENSE*'
  -g '!*.md'
  -g '!node_modules/**'
  -g '!**/node_modules/**'
)

echo "== Package =="
sed -n '1,220p' "$pkg_json" | sed -n '/"name"/p;/"version"/p;/"license"/p;/"repository"/,/\}/p;/"bin"/,/\}/p;/"scripts"/,/\}/p'
echo

echo "== Lifecycle Hooks =="
rg -n '"(preinstall|install|postinstall|prepare)"\s*:' "$pkg_json" || true
echo

echo "== Native Artifacts =="
find "$pkg_dir" -type f \( -name '*.node' -o -name '*.so' -o -name '*.dylib' -o -name '*.dll' -o -name '*.exe' \) | sort || true
find "$pkg_dir" -type f | xargs file 2>/dev/null | rg 'ELF|Mach-O|PE32' || true
echo

echo "== Suspicious Capability Scan =="
rg -n 'child_process|spawn\(|spawnSync|exec\(|execSync|eval\(|Function\(|vm\.runInContext|new VM|writeFile|appendFile|createWriteStream|unlinkSync|rmSync|rmdirSync|listen\(|0\.0\.0\.0|access-control-allow-origin|cors\(|http://|https://' \
  "$pkg_dir" "${first_party_globs[@]}" || true
echo

echo "== Broad Listener / Local Exposure =="
rg -n '0\.0\.0\.0|listen\(|cors\(|origin:\s*["'\'']\*["'\'']|ENABLE_CORS|CORS_ORIGIN' "$pkg_dir" "${first_party_globs[@]}" || true
echo

echo "== Filesystem Write / Delete =="
rg -n 'writeFile|appendFile|createWriteStream|mkdir|mkdtemp|chmod|unlink|rmSync|rmdirSync|rename|copyFile' "$pkg_dir" "${first_party_globs[@]}" || true
echo

echo "== Environment Variables =="
rg -n 'process\.env\.[A-Z0-9_]+|process\.env\[['"'"'\"'][A-Z0-9_]+'"'\"']\]' "$pkg_dir" "${first_party_globs[@]}" || true
echo

echo "== Dependencies (top-level) =="
if [[ -d "$pkg_dir/node_modules" ]]; then
  find "$pkg_dir/node_modules" -mindepth 1 -maxdepth 1 -type d | wc -l | sed 's/^/top-level node_modules dirs: /'
  find "$pkg_dir/node_modules" -mindepth 1 -maxdepth 2 -type d | sed -n '1,120p'
else
  echo "No nested node_modules directory"
fi
echo

echo "== Quick Questions =="
echo "- Does it auto-run code at install time?"
echo "- Does it download binaries?"
echo "- Does it expose HTTP on 0.0.0.0?"
echo "- Does it execute child processes?"
echo "- Does it contain native binaries you did not build?"
echo "- Does it access cookies, credentials, or browser state?"
