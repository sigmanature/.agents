# opencode Secure MCP Model Resolution

## Purpose

Avoid spending agent context on provider/model discovery before every `opencode` task. The MCP server resolves lightweight selectors into full provider/model ids before it launches the secure wrapper.

## Resolution Order

1. `search`
   - Resolve to `Mify-Mini/azure_openai/gpt-5-mini`.
   - Report `resolution_source=search_alias`.
2. Empty `model`, `auto`, `default`, `stable`, or `recent`
   - Resolve to the first entry in `~/.local/state/opencode/model.json` `recent`.
   - Report `resolution_source=recent_default`.
3. Built-in short aliases
   - Supported aliases currently include `kimi`, `deepseek`, `qwen`, `glm`, `gpt`, `claude`, and `minimax`.
   - Match aliases against validated local `recent` then `variant` entries.
   - Report `resolution_source=alias_builtin`.
4. Provider-less validated suffixes
   - Examples: `moonshot/kimi-k2.6`, `deepseek-v4-flash`.
   - Match against validated local `recent` then `variant` entries.
   - Report `resolution_source=recent_match`.
4. Explicit full provider/model ids
   - Pass through unchanged, even if they are not present in the local validated cache.
   - Report `resolution_source=explicit`.

## Local Validation Source

- Primary file: `~/.local/state/opencode/model.json`
- Fields used:
  - `recent`: ordered recent successful provider/model pairs
  - `variant`: validated full provider/model ids already known locally

The resolver prefers `recent` ordering so the most recently successful provider/model pair wins when multiple local candidates contain the same alias token.

## Output Contract Additions

Successful `opencode_run_task` and `opencode_submit_task` responses should include:

- `requested_model`
- `resolved_model`
- `resolution_source`

Persisted job metadata returned by `opencode_get_task` should also include:

- `requested_model`
- `resolved_model`
- `resolution_source`

## Failure Mode

If the resolver cannot match an omitted or shortened selector against local validated entries, it must not guess. Return:

- `error.code=model_resolution_failed`
- `candidate_models`: recent validated full ids to choose from
- `model_state_path`: the local model cache used for resolution

This is a caller correction path, not a provider-runtime failure.
