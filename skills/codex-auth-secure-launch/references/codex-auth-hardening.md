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

## Rotating the API key later

### 1. Keyring-backed setups
- Update the stored credential in the system keyring.
- Keep `~/.codex/config.toml` pointed at the same provider and `env_key` unless the provider itself changed.
- Relaunch Codex and verify the new credential works before removing any old backups or revoking the old key.

### 2. Encrypted-file plus wrapper setups
- If the provider and `env_key` stay the same, do not edit `~/.codex/config.toml` or the shell wrapper. Replace only the encrypted key file.
- Back up the current encrypted file first:

```bash
cp ~/.codex/auth.key.enc ~/.codex/auth.key.enc.bak.$(date +%Y%m%d_%H%M%S)
```

- Rebuild the encrypted file from a temporary JSON source without putting the new key into shell history:

```bash
tmp_json="$(mktemp)"
chmod 600 "$tmp_json"
trap 'rm -f "$tmp_json"; unset NEW_KEY' EXIT

read -r -s -p "New API key: " NEW_KEY
echo
jq -n --arg key "$NEW_KEY" '{OPENAI_API_KEY: $key}' > "$tmp_json"
unset NEW_KEY

bash /home/nzzhao/.agents/skills/codex-auth-secure-launch/scripts/codex_secure_launch.sh init \
  --source "$tmp_json" \
  --output ~/.codex/auth.key.enc

rm -f "$tmp_json"
trap - EXIT
```

- `init` prompts for the encryption passphrase. Reuse the old passphrase if you want the same unlock experience, or enter a new one if you also want to rotate the passphrase.
- Validate the normal wrapper path after rotation, for example by running `codex -p high` and confirming the prompt path still works.
- If validation fails, restore the backup encrypted file and re-check the provider name, `env_key`, and wrapper arguments.

### 3. When config changes are actually required
- Update `~/.codex/config.toml` and the shell wrapper only if the provider changed, the environment variable name changed, or the encrypted file path changed.
- Replacing the raw API key alone should not require config edits in a stable `mify` plus `MIFY_API_KEY` setup.

## Limits
- This protects the secret at rest.
- Once Codex is launched, the decrypted key still exists in the launched process environment.
- If someone intentionally bypasses the shell wrapper by running the absolute binary path directly, the program can still start, but without `auth.json` or the needed environment variable it should not have the old plaintext auth path available.
