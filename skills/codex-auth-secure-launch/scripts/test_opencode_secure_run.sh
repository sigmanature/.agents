#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="${ROOT_DIR}/scripts/opencode_secure_run.sh"
CODEX_WRAPPER="${ROOT_DIR}/scripts/codex_secure_launch.sh"

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmpdir}"
}
trap cleanup EXIT

cat >"${tmpdir}/auth.json" <<'EOF'
{"OPENAI_API_KEY":"dummy-test-key"}
EOF

cat >"${tmpdir}/pass.txt" <<'EOF'
test-passphrase
EOF

export CODEX_AUTH_PASSPHRASE="test-passphrase"
bash "${CODEX_WRAPPER}" init \
  --source "${tmpdir}/auth.json" \
  --output "${tmpdir}/auth.key.enc" >/dev/null
unset CODEX_AUTH_PASSPHRASE

mkdir -p "${tmpdir}/bin"
cat >"${tmpdir}/bin/opencode" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "run" ]] || { echo "expected subcommand run" >&2; exit 10; }
shift

model=""
message=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--model)
      model="${2:-}"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      message="${1:-}"
      shift
      break
      ;;
  esac
done

[[ "${model}" == "Mify-Kimi/Pro/moonshotai/Kimi-K2.5" ]] || {
  echo "unexpected model: ${model}" >&2
  exit 11
}

for key_name in MIFY_API_KEY OPENAI_API_KEY KIMI_API_KEY MOONSHOT_API_KEY MINIMAX_API_KEY ZHIPU_API_KEY; do
  [[ -n "${!key_name:-}" ]] || {
    echo "missing env ${key_name}" >&2
    exit 12
  }
done

[[ "${OPENAI_API_KEY}" == "dummy-test-key" ]] || {
  echo "unexpected OPENAI_API_KEY value" >&2
  exit 13
}

[[ "${message}" == "hello from test" ]] || {
  echo "unexpected message: ${message}" >&2
  exit 14
}

printf 'ok model=%s message=%s\n' "${model}" "${message}"
EOF
chmod 700 "${tmpdir}/bin/opencode"

PATH="${tmpdir}/bin:${PATH}" \
  XDG_DATA_HOME="${tmpdir}/xdg-data" \
  bash "${WRAPPER}" \
    --encrypted-file "${tmpdir}/auth.key.enc" \
    --pass-file "${tmpdir}/pass.txt" \
    --model "Mify-Kimi/Pro/moonshotai/Kimi-K2.5" \
    -- "hello from test"

rm -f "${tmpdir}/auth.key.enc"
export CODEX_AUTH_PASSPHRASE="test-passphrase"
printf '%s' 'dummy-test-key' | \
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
    -pass "pass:${CODEX_AUTH_PASSPHRASE}" -out "${tmpdir}/auth.key.enc"
unset CODEX_AUTH_PASSPHRASE

PATH="${tmpdir}/bin:${PATH}" \
  XDG_DATA_HOME="${tmpdir}/xdg-data-2" \
  bash "${WRAPPER}" \
    --encrypted-file "${tmpdir}/auth.key.enc" \
    --pass-file "${tmpdir}/pass.txt" \
    --model "Mify-Kimi/Pro/moonshotai/Kimi-K2.5" \
    -- "hello from test"
