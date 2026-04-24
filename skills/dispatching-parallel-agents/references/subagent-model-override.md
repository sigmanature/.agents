# Subagent model override pitfalls

## Symptom

Calling `functions.spawn_agent` with an explicit `model` can fail with errors like:

- `Param Incorrect`
- `Not supported model <name>`
- `Unknown model <name> for spawn_agent`

Even when `<name>` is supported for the *main* session.

## Why this happens

There can be two different gates:

1. The **Codex tool layer** may only allow a fixed set of `spawn_agent.model` values.
2. The **provider/runtime layer** may further restrict which of those allowed values are actually accepted for subagents.

So a model can fail either because:

- the tool refuses to send it at all (`Unknown model ... for spawn_agent`), or
- the provider rejects it after the request is sent (`Not supported model ...`).

## Recommended practice

- If you do not care which model the subagent uses, **omit** the `model` field in `spawn_agent` so the runtime chooses a supported subagent model.
- If you need a cheaper or otherwise different subagent model, **prefer a named custom agent** in Codex config and pin the model there instead of relying on `spawn_agent(model=...)`.
- Treat per-call `model` overrides as opportunistic only. They are not the most reliable way to enforce subagent model choice across runtimes.

## Stable workaround for cheaper subagents

Use a custom agent entry in your Codex config, then put the cheaper model in that agent's own TOML file.

Main config example:

```toml
[agents.cheap_worker]
config_file = "agents/cheap-worker.toml"
```

Custom agent file example:

```toml
name = "cheap_worker"
model = "gpt-5.4-mini"
model_reasoning_effort = "low"
```

This avoids depending on the runtime honoring `spawn_agent(model=...)` on each call.

## Practical implication

If you are using a non-default provider and want a provider-specific model ID such as `tongyi/...`, do not assume `spawn_agent(model=...)` can forward that raw ID. The tool layer may reject it before the provider sees it.
