---
name: codex-auth-secure-launch
description: "protect Codex CLI API keys at rest without breaking normal usage. use this whenever the user asks to encrypt ~/.codex/auth.json, stop leaving Codex API keys in plaintext, migrate Codex auth from auth.json to keyring or env_key, force Codex launches through a wrapper, or keep the usual `codex -p high` UX while using an encrypted key file. includes migration, backup, wrapper, and rollback steps."
---

# Codex Auth Secure Launch

Use this skill when the user wants to harden Codex credential handling but still keep Codex usable.

The critical boundary is:

- do not invent a custom encrypted format for `~/.codex/auth.json` and expect Codex to read it directly
- either use the supported keyring path, or keep the secret encrypted at rest and decrypt it only into an environment variable at launch time

## What to do

1. Identify the current auth path.
   - `auth.json`
   - keyring
   - provider-specific `env_key`
2. Choose the safest supported target.
   - prefer keyring when it is available and fits the user's workflow
   - otherwise use the encrypted-at-rest launcher in `scripts/codex_secure_launch.sh`
3. Preserve usability.
   - keep `codex ...` invocation habits working by using a shell wrapper when requested
   - preserve user flags such as `-p high` by forwarding `"$@"`
4. Always back up important config files before editing them.
   - especially `~/.codex/config.toml`
   - and shell init files such as `~/.bashrc`

## Workflow

### 1. Inspect current state
- Check whether `~/.codex/auth.json` exists and whether it contains only a simple API key.
- Check whether the active provider already supports `env_key`.
- Check whether a shell wrapper already exists.

### 2. Choose migration mode

#### A. Preferred when possible: keyring
- Keep the solution on the supported Codex credential path.
- Avoid plaintext `auth.json`.

#### B. Good fallback: encrypted key file plus launcher
- Store only the raw API key in an encrypted file such as `~/.codex/auth.key.enc`.
- Launch Codex through `scripts/codex_secure_launch.sh`.
- Export the decrypted value only into the chosen environment variable before `exec codex`.

### 3. Preserve the user experience
- If the user normally types `codex -p high`, add a shell function wrapper like:

```bash
codex() {
  bash /home/nzzhao/.agents/skills/codex-auth-secure-launch/scripts/codex_secure_launch.sh run \
    --encrypted-file "$HOME/.codex/auth.key.enc" \
    --provider mify \
    --env-key MIFY_API_KEY \
    -- "$@"
}
```

- This keeps the usual CLI habit while forcing auth through the secure path.

### 4. Retire plaintext carefully
- After validating the new path, move `~/.codex/auth.json` aside to a timestamped backup.
- Do not silently delete it unless the user explicitly asks.

## References
- [references/codex-auth-hardening.md](references/codex-auth-hardening.md)

## Scripts
- [scripts/codex_secure_launch.sh](scripts/codex_secure_launch.sh)
