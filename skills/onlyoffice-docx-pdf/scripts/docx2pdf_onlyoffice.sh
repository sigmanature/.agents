#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  docx2pdf_onlyoffice.sh INPUT.docx [OUTPUT.pdf]

Environment:
  ONLYOFFICE_DOCUMENTBUILDER_BIN  Optional explicit path to the documentbuilder executable.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

resolve_abs_path() {
  local target="$1"
  if command -v realpath >/dev/null 2>&1; then
    realpath "$target"
  else
    python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$target"
  fi
}

resolve_documentbuilder() {
  local candidate

  if [[ -n "${ONLYOFFICE_DOCUMENTBUILDER_BIN:-}" ]]; then
    [[ -x "${ONLYOFFICE_DOCUMENTBUILDER_BIN}" ]] || die "ONLYOFFICE_DOCUMENTBUILDER_BIN is set but not executable: ${ONLYOFFICE_DOCUMENTBUILDER_BIN}"
    printf '%s\n' "${ONLYOFFICE_DOCUMENTBUILDER_BIN}"
    return 0
  fi

  for candidate in \
    "${HOME}/.local/bin/documentbuilder" \
    /usr/bin/documentbuilder
  do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  for candidate in documentbuilder docbuilder; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return 0
    fi
  done

  return 1
}

run_documentbuilder() {
  local builder_invocation="$1"
  shift

  local real_builder builder_dir
  real_builder="$(resolve_abs_path "${builder_invocation}")"
  builder_dir="$(dirname "${real_builder}")"

  if [[ -f "${builder_dir}/libdoctrenderer.so" ]]; then
    (
      cd "${builder_dir}"
      export LD_LIBRARY_PATH="${builder_dir}:${LD_LIBRARY_PATH:-}"
      exec "${real_builder}" "$@"
    )
    return
  fi

  "${builder_invocation}" "$@"
}

has_license_error() {
  local text="$1"
  [[ "${text}" == *"license is invalid"* || "${text}" == *"license error"* || "${text}" == *"license"* ]]
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

[[ $# -ge 1 && $# -le 2 ]] || {
  usage >&2
  exit 2
}

input_path="$1"
[[ -f "${input_path}" ]] || die "input file does not exist: ${input_path}"
[[ -r "${input_path}" ]] || die "input file is not readable: ${input_path}"

builder_bin="$(resolve_documentbuilder || true)"
[[ -n "${builder_bin}" ]] || die "ONLYOFFICE Document Builder is not installed or not discoverable. Install documentbuilder, or set ONLYOFFICE_DOCUMENTBUILDER_BIN. Desktop Editors and its bundled x2t converter are not the supported local automation path here."

input_abs="$(resolve_abs_path "${input_path}")"

if [[ $# -eq 2 ]]; then
  output_path="$2"
else
  case "${input_path}" in
    *.*) output_path="${input_path%.*}.pdf" ;;
    *) output_path="${input_path}.pdf" ;;
  esac
fi

output_dir="$(dirname "${output_path}")"
mkdir -p "${output_dir}"
output_abs="$(resolve_abs_path "${output_dir}")/$(basename "${output_path}")"

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

script_path="${tmpdir}/docx2pdf.docbuilder"
python3 - <<'PY' "${script_path}" "${input_abs}" "${output_abs}"
from pathlib import Path
import json
import sys

script_path = Path(sys.argv[1])
input_abs = sys.argv[2]
output_abs = sys.argv[3]

def js_quote(value: str) -> str:
    return json.dumps(value)

script_path.write_text(
    "builder.OpenFile(" + js_quote(input_abs) + ", \"\");\n"
    "builder.SaveFile(\"pdf\", " + js_quote(output_abs) + ");\n"
    "builder.CloseFile();\n",
    encoding="utf-8",
)
PY

builder_output=""
builder_status=0
if ! builder_output="$(run_documentbuilder "${builder_bin}" "${script_path}" 2>&1)"; then
  builder_status=$?
fi

if [[ -n "${builder_output}" ]]; then
  printf '%s\n' "${builder_output}" >&2
fi

if [[ ${builder_status} -ne 0 ]]; then
  if has_license_error "${builder_output}"; then
    die "ONLYOFFICE Document Builder reported a license problem and could not convert the document."
  fi
  die "documentbuilder failed while converting ${input_abs}"
fi

if [[ ! -s "${output_abs}" ]]; then
  if has_license_error "${builder_output}"; then
    die "ONLYOFFICE Document Builder ran but did not produce a PDF because the installed runtime reported a license problem."
  fi
  die "documentbuilder finished but no PDF was produced at ${output_abs}"
fi
printf '%s\n' "${output_abs}"
