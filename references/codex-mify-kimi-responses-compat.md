# Codex Mify Kimi Responses Compatibility

Date verified: 2026-05-14

## Finding

Codex CLI 0.130.0 no longer accepts `wire_api = "chat"` for custom model
providers. The CLI reports:

```text
Error: `wire_api = "chat"` is no longer supported.
How to fix: set `wire_api = "responses"` in your provider config.
```

With the local Mify provider at `http://model.mify.ai.srv/v1`,
`wire_api = "responses"` works for `azure_openai/*` models but rejects Kimi
model ids:

```text
/v1/responses only supports provider `azure_openai`, but got `moonshot`
```

## Verified Details

- `moonshot/kimi-k2.6` via Codex/Mify Responses fails.
- `moonshot/kimi-k2.6` via direct Mify `/v1/responses` fails with the same
  provider restriction.
- `kimi-k2.6` via direct Mify `/v1/responses` fails with `Not supported model`.
- `azure_openai/kimi-k2.6` via direct Mify `/v1/responses` fails with
  `Not supported model`.
- `moonshot/kimi-k2.5` via Codex/Mify Responses fails.
- `kimi-k2.5` via Codex/Mify Responses fails with `Not supported model`.
- `/v1/models` lists `moonshot/kimi-k2.6`.
- `/v1/chat/completions` accepts `moonshot/kimi-k2.6`, but Codex 0.130.0
  cannot use a chat-completions custom provider.
- `/v1/responses` was separately verified to work with `azure_openai/gpt-5.5`,
  so the failure is specific to Kimi/provider routing rather than auth or
  generic Responses availability.

## Workflow Impact

Do not add an active Codex profile for Kimi against the current Mify provider
unless one of these changes is true:

1. Mify exposes Kimi through `/v1/responses`.
2. A local Responses-to-Chat proxy is used and configured as the Codex provider.
3. Codex regains support for custom Chat Completions providers.
