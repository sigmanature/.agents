# Subagent model override pitfalls

## Symptom

Calling `functions.spawn_agent` with an explicit `model` can fail with errors like:

- `Param Incorrect`
- `Not supported model <name>`

Even when `<name>` is supported for the *main* session.

## Why this happens

Some runtimes restrict the set of models that subagents may use, and that allow-list can differ from the main agent’s model.

## Recommended practice

- Default: **omit** the `model` field in `spawn_agent` so the runtime chooses a supported subagent model.
- Only override `model` if you have an explicit allow-list for subagents in your current environment.

