# Codex Auth Hardening

## Purpose
Protect Codex API keys at rest without breaking normal Codex usage.

## Recommendation order

### 1. Preferred: use the official keyring credential store
- Use this when the desktop or session keyring is available.
- This stays on the supported Codex credential path.

### 2. Good fallback: encrypted key file plus launch-time decryption
- Keep the API key encrypted at rest in a separate file such as `~/.codex/auth.key.enc`.
- Launch Codex through `scripts/codex_secure_launch.sh`.
- Export the decrypted key only into an environment variable such as `OPENAI_API_KEY` or a provider-specific variable such as `MIFY_API_KEY`.

## What not to do
- Do not encrypt `~/.codex/auth.json` in place and expect Codex to keep reading it normally.
- Do not change the structure of `auth.json` unless you are also changing Codex's credential loader.

## Typical migration

### 1. Configure the provider to use an explicit env_key
Example:

```toml
[model_providers.mify]
name = "mify"
base_url = "http://model.mify.ai.srv/v1"
wire_api = "responses"
env_key = "MIFY_API_KEY"
```

### 2. Create the encrypted key file
```bash
bash /home/nzzhao/.agents/skills/codex-auth-secure-launch/scripts/codex_secure_launch.sh init \
  --output ~/.codex/auth.key.enc
```

This prompts for a new passphrase and encrypts the current API key from `~/.codex/auth.json`.

### 3. Launch Codex through the secure path
```bash
bash /home/nzzhao/.agents/skills/codex-auth-secure-launch/scripts/codex_secure_launch.sh run \
  --encrypted-file ~/.codex/auth.key.enc \
  --provider mify \
  --env-key MIFY_API_KEY \
  -- -p high
```

### 4. Keep the normal `codex ...` UX with a shell wrapper
```bash
codex() {
  bash /home/nzzhao/.agents/skills/codex-auth-secure-launch/scripts/codex_secure_launch.sh run \
    --encrypted-file "$HOME/.codex/auth.key.enc" \
    --provider mify \
    --env-key MIFY_API_KEY \
    -- "$@"
}
```

After reloading the shell, `codex -p high` still works, but it flows through the encrypted launch path.

### 5. Move plaintext aside after validation
- Move `~/.codex/auth.json` to:
  - `~/.codex/auth.json.plaintext.bak.YYYYMMDD_HHMMSS`
- Keep it only as a rollback backup until the new path is verified.

## Limits
- This protects the secret at rest.
- Once Codex is launched, the decrypted key still exists in the launched process environment.
- If someone intentionally bypasses the shell wrapper by running the absolute binary path directly, the program can still start, but without `auth.json` or the needed environment variable it should not have the old plaintext auth path available.
