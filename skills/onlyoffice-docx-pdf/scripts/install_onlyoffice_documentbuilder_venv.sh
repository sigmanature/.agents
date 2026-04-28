#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install_onlyoffice_documentbuilder_venv.sh

Environment:
  PYTHON_BIN                    Python interpreter used to create the venv. Default: /usr/bin/python3
  INSTALL_ROOT                  User-local install root. Default: ~/.local/opt/onlyoffice-documentbuilder
  VENV_DIR                      Virtualenv directory. Default: $INSTALL_ROOT/venv
  BIN_LINK                      Wrapper path. Default: ~/.local/bin/documentbuilder
  DOCUMENT_BUILDER_PIP_SPEC     pip requirement spec. Default: document-builder==9.3.0.140

Notes:
  - No sudo is required.
  - This is the preferred install path for agent use in this skill.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

[[ $# -eq 0 ]] || {
  usage >&2
  exit 2
}

python_bin="${PYTHON_BIN:-/usr/bin/python3}"
install_root="${INSTALL_ROOT:-$HOME/.local/opt/onlyoffice-documentbuilder}"
venv_dir="${VENV_DIR:-$install_root/venv}"
bin_link="${BIN_LINK:-$HOME/.local/bin/documentbuilder}"
pip_spec="${DOCUMENT_BUILDER_PIP_SPEC:-document-builder==9.3.0.140}"

[[ -x "${python_bin}" ]] || die "python interpreter is not executable: ${python_bin}"
need_cmd install

"${python_bin}" -m venv "${venv_dir}"
"${venv_dir}/bin/pip" install --upgrade pip
"${venv_dir}/bin/pip" install "${pip_spec}"

install -d "$(dirname "${bin_link}")"
cat > "${bin_link}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
venv_python="${venv_dir}/bin/python"
builder_bin="\$("\${venv_python}" - <<'PY'
from pathlib import Path
import docbuilder
print(Path(docbuilder.__file__).resolve().parent / "lib" / "docbuilder")
PY
)"
builder_dir="\$(dirname "\${builder_bin}")"
export LD_LIBRARY_PATH="\${builder_dir}:\${LD_LIBRARY_PATH:-}"
cd "\${builder_dir}"
exec "\${builder_bin}" "\$@"
EOF
chmod 0755 "${bin_link}"

printf 'Installed %s into %s\n' "${pip_spec}" "${venv_dir}"
printf 'Created wrapper: %s\n' "${bin_link}"
printf 'Run this to verify: %s /path/to/script.docbuilder\n' "${bin_link}"
