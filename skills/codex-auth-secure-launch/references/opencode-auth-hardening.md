# opencode Auth Hardening

## Purpose

Protect `opencode` API credentials at rest while keeping `opencode run ...` usable from a wrapper.

## Recommendation

### Preferred path for shared encrypted credentials

- Keep the raw API key encrypted at rest in a separate file such as `~/.opencode/auth.key.enc`.
- Keep the decryption passphrase in `~/.opencode/pass.txt` when the user accepts that local tradeoff.
- Decrypt it only inside the wrapper and inject environment variables only for the child `opencode` process.
- Prefer explicit `-m provider/model` during validation so provider selection is not ambiguous.

## Model-side boundary

- The model must not read `~/.opencode/pass.txt` directly.
- The model must not run `openssl` directly for `opencode` launch.
- The approved paths are:
  - call `scripts/opencode_secure_run.sh` for direct wrapper compatibility
  - or call the local `opencode-secure-mcp` server, which still routes execution through that wrapper boundary

## What not to do

- Do not rewrite `~/.local/share/opencode/auth.json` into a custom encrypted format and expect `opencode` to keep reading it.
- Do not validate the env-only path while leaving existing native auth storage in place without isolation. `opencode` may still succeed by reading its own auth store.
- Do not print the decrypted key, echo it in shell traces, or write it to a temporary `.env` file unless the user explicitly accepts that tradeoff.
- Do not open `~/.opencode/pass.txt` from the model layer just because the wrapper failed.

## Minimal env-only wrapper

Use `scripts/opencode_secure_run.sh` or the `opencode-secure-mcp` MCP server:

```bash
bash /home/nzzhao/.agents/skills/opencode-secure-mcp/scripts/opencode_secure_run.sh \
  --model "Mify-Kimi/Pro/moonshotai/Kimi-K2.5" \
  -- "用一句话确认 secure wrapper 生效"
```

The wrapper:

- passes `~/.opencode/pass.txt` to `openssl` when `CODEX_AUTH_PASSPHRASE` is unset
- decrypts the key from the encrypted file
- accepts both OpenSSL base64 output (`-a`) and raw binary `Salted__...` output
- injects the same value into a small set of provider-style env vars
- launches `opencode run -m <model> ...`
- never writes the plaintext key to disk

By default it injects:

- `MIFY_API_KEY`
- `OPENAI_API_KEY`
- `KIMI_API_KEY`
- `MOONSHOT_API_KEY`
- `MINIMAX_API_KEY`
- `ZHIPU_API_KEY`

Add more names with repeated `--env-key <NAME>` when a custom provider expects a different variable.

## Validation

Do not use temporary `XDG_DATA_HOME` isolation as the first-line validation for `opencode`. In practice it changes more than auth storage and can create false negatives unrelated to the encrypted key path.

Preferred validation order:

1. Run a manual env-based launch under the normal user data directory.
2. Run the wrapper under the same normal user data directory.
3. Compare the results.

Example manual env launch:

```bash
MIFY_API_KEY='your-known-good-key' \
opencode run \
  --model "Mify-Kimi/Pro/moonshotai/Kimi-K2.5" \
  -- "回复 ok"
```

Example wrapper launch:

```bash
bash /home/nzzhao/.agents/skills/opencode-secure-mcp/scripts/opencode_secure_run.sh \
  --model "Mify-Kimi/Pro/moonshotai/Kimi-K2.5" \
  -- "回复 ok"
```

If the manual env launch succeeds and the wrapper launch also succeeds under the same normal XDG layout, the encrypted key path is working.

If the manual env launch succeeds but the wrapper launch returns an upstream `401 Invalid API Key`, then:

- the model, provider, and runtime state are fine
- the encrypted key contents differ from the validated manual env value
- the next step is to rebuild `~/.opencode/auth.key.enc` from the known-good key

If the wrapper fails before `opencode` starts and `openssl` reports it cannot read the encrypted input file cleanly, treat that as a format mismatch first. Typical causes:

- the encrypted file was created with a different cipher than `aes-256-cbc`
- `-pbkdf2` or the iteration count differs from the wrapper expectation
- the file was created without `-a` base64 mode while the wrapper expects base64 input

In that case, rebuild `~/.opencode/auth.key.enc` with the same parameters as the wrapper instead of debugging provider auth first.

Current wrapper expectation:

- cipher: `aes-256-cbc`
- KDF: `-pbkdf2`
- iteration count: `200000`
- output format: either with `-a` or without `-a`

## Rotation

- If the provider wiring and model names stay the same, replace only the encrypted key file.
- Keep the wrapper and `opencode` config stable unless the provider's expected env var names change.
- Re-run the isolated validation path before retiring any older encrypted backup.
