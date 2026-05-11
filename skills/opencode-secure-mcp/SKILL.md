---
name: opencode-secure-mcp
description: "Use when the user wants a persistent local MCP capability for secure `opencode` task execution, especially when extracting ad-hoc wrappers into a reusable bottom-layer service, running cheap external subagent work, or keeping encrypted-at-rest API keys while exposing `opencode` through stdio MCP."
---

# opencode Secure MCP

Use this skill when the user wants `opencode` exposed as a local MCP-backed capability without weakening the encrypted-at-rest launch boundary.

This skill owns the reusable bottom-layer pieces for secure `opencode` execution:

- the secure launch wrapper
- the stdio MCP server
- local registration and probe scripts

## Security Boundary

- Never read `~/.opencode/pass.txt` from the model layer.
- Never call `openssl` directly from the model layer for `opencode` launch.
- Let `scripts/opencode_secure_run.sh` perform decryption internally and inject only environment variables into the child `opencode` process.
- The MCP server must only orchestrate wrapper calls. It must not reimplement secret handling in Python.

## Primary Workflow

1. Confirm the user actually wants a reusable bottom-layer capability, not just a one-off wrapper launch.
2. For cross-device or cross-vendor migration, register from the top-level manifest through `python3 ~/.agents/install_mcps.py opencode_secure --scope user --vendor codex`; use `--all` or repeat `--vendor` when intentionally installing every manifest/vendor.
3. Use `scripts/register_opencode_secure_mcp.sh` only as the Codex-only fallback when the top-level MCP installer is unavailable.
4. Let the MCP server resolve models before wrapper launch: empty `model` or `auto/default/stable` should follow the most recent validated entry in `~/.local/state/opencode/model.json`.
5. Use the MCP tools for task execution; keep provider secrets encrypted at rest.
6. Use `diagnostics.mode=on_error` for routine probes and switch to `trace` only when you need opencode startup or provider logs.
7. Validate with `scripts/test_opencode_secure_mcp.py` or a targeted probe before claiming the capability works.
8. If Codex TUI hangs on `Booting MCP server: opencode_secure`, check the stdio transport notes before changing wrapper security behavior.
9. If direct `opencode_secure_run.sh` works but `opencode_run_task` times out, check stdin-inheritance notes before blaming the provider/model.
10. When model resolution fails, use the MCP error payload's `candidate_models` list and `model_state_path` instead of guessing from shortened UI display names.

## Workflow Contract

### Main Workflow
1. Install or restore the MCP registration from `~/.agents/mcps/opencode_secure.json` with `python3 ~/.agents/install_mcps.py opencode_secure --scope user --vendor codex`; expand to more vendors only when the user intentionally wants them.
2. Validate the wrapper path first with `diagnostics.mode=on_error`.
3. Prefer omitting `model` unless the caller truly needs a pin; the MCP server resolves omitted `model` or `auto/default/stable` to the most recent validated local success from `~/.local/state/opencode/model.json`.
4. When the caller wants a lighter selector, pass a validated short alias such as `kimi` or a provider-less validated suffix such as `moonshot/kimi-k2.6`; the MCP server should expand it to a full provider/model id and return `resolved_model` plus `resolution_source`.
5. Use `opencode_run_task` only for small interactive probes that should finish within the tool-call window.
6. Use `opencode_submit_task` first for long research, online lookup, multi-agent, or otherwise complex prompts; then poll with `opencode_get_task` and `opencode_collect_artifacts`.
7. On any timeout, inspect `timeout_context` when available, then continue with `opencode_get_task` and `opencode_collect_artifacts` before deciding whether the fault is upstream latency or local orchestration.
8. Report whether the task later converged, failed definitively, or required manual cancellation.

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Registration | Cross-device migration, fresh machine setup, or user asks for cross-vendor MCP install | Use `python3 ~/.agents/install_mcps.py opencode_secure --scope user --vendor codex` for Codex-only restore; use `--all` or additional `--vendor` flags only for intentional broader rollout | `codex mcp list --json` shows `opencode_secure` enabled with the entry script under `~/.agents/skills/opencode-secure-mcp/scripts/` | Fall back to `scripts/register_opencode_secure_mcp.sh` for Codex-only registration, then repair the top-level installer/manifest | branch |
| Probe | Caller omits `model`, or passes `auto` / `default` / `stable` | Resolve to the first validated entry in `~/.local/state/opencode/model.json` `recent`; return the chosen full id as `resolved_model` with `resolution_source=recent_default` | MCP result shows the expected `resolved_model`, and stdout/stderr/artifacts confirm opencode ran with that full id | If there is no local validated candidate, return a structured `model_resolution_failed` error instead of guessing | replace |
| Probe | Caller passes a short alias such as `kimi` or a provider-less validated suffix such as `moonshot/kimi-k2.6` | Match the selector against validated `recent`/`variant` entries server-side, then pass the full provider/model id to the wrapper | MCP result shows `resolved_model` plus `resolution_source=alias_builtin` or `recent_match`, and opencode logs run on that full id | If there is no unambiguous local match, return `candidate_models` from the error payload and let the caller choose a pin | replace |
| Probe | Caller passes a full provider/model string | Pass it through unchanged and record `resolution_source=explicit` | MCP result echoes the same `resolved_model`, and the command line/artifacts use that full id | If the explicit full id fails live after launch, classify it as provider/runtime failure rather than resolver failure | continue |
| Probe | Provider returns quota, billing, or token-limit errors after the model has already resolved to the intended display name | Keep the validated full model string unchanged and treat the result as a provider quota/billing blocker; retry later instead of changing model ids | stderr/artifacts show the intended display name such as `tongyi/deepseek-v4-flash` before the quota failure text | Only revisit model selection when the error is model-resolution related, such as `ProviderModelNotFoundError` | branch |
| Probe | `opencode_run_task` returns `error.code=timeout` with `job_id` and `timeout_context.saw_output=true` | Treat the sync timeout as a handoff; keep the job alive and poll with `opencode_get_task` / `opencode_collect_artifacts` | Job later reaches `succeeded` or `failed`, and artifact tails show model progress | Cancel the job only if it keeps running without converging or exceeds the caller's patience budget | branch |
| Probe | `opencode_run_task` returns `error.code=timeout` with `timeout_context.saw_output=false` | Suspect wrapper/startup/local orchestration first; inspect stderr/stdout tails and compare with direct wrapper execution | Direct wrapper probe reproduces or avoids the silent startup | Check stdio transport and wrapper startup assumptions before blaming the provider | branch |
| Probe | Long research, online source lookup, multi-agent, or complex prompt is expected to exceed the outer MCP tool-call wait window | Start with `opencode_submit_task` instead of synchronous `opencode_run_task`, set `persist_artifacts=true`, then poll the returned `job_id` | `opencode_get_task` reports `running`/terminal state and `opencode_collect_artifacts` shows progress or final output | If a prior synchronous tool call timed out without returning a `job_id`, resubmit once via `opencode_submit_task` and treat the original call as unknown/untracked unless artifacts identify it | replace |
| Validation | Background job is still `running` after sync handoff | Keep using `opencode_get_task` and `opencode_collect_artifacts` instead of re-submitting the same prompt immediately | Artifact tail changes or final status arrives | Cancel stale work and retry with adjusted timeout only after capturing evidence | replace |
| Recovery | MCP server exits or is OOM-killed mid-job | Restart the server and re-query the same `job_id` first; rely on durable completion evidence under the state directory instead of assuming the task was lost | `opencode_get_task` returns a terminal status or updated artifact tails for the original `job_id` | If there is no completion evidence and the process is gone, treat the job as terminal `finished_unknown`, inspect artifacts, then decide whether to retry | branch |

### Output Contract
- phase reached:
- decision path taken:
- requested model:
- resolved model:
- resolution source:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## Tools

The local MCP server exposes a small task-oriented surface:

- `opencode_run_task`
- `opencode_submit_task`
- `opencode_get_task`
- `opencode_cancel_task`
- `opencode_collect_artifacts`

The interface is intentionally task-shaped, not a raw `opencode` CLI passthrough.

## Model Selection Notes

- MCP calls no longer rely on a static default provider/model pairing.
- Empty `model` or `auto/default/stable` resolves to the most recent validated local success from `~/.local/state/opencode/model.json`.
- `search` resolves server-side to `Mify-Mini/azure_openai/gpt-5-mini`.
- Built-in short aliases such as `kimi`, `deepseek`, `qwen`, `glm`, `gpt`, `claude`, and `minimax` resolve server-side against validated local entries.
- Provider-less validated suffixes such as `moonshot/kimi-k2.6` or `deepseek-v4-flash` also resolve server-side when they match local `recent`/`variant` entries.
- Explicit full provider/model ids still pass through unchanged for custom pinning.
- MCP results should expose `requested_model`, `resolved_model`, and `resolution_source`; use those instead of inferring from shortened UI names.
- The direct wrapper fallback is currently `Mify-Moon/moonshot/kimi-k2.6`, but the wrapper fallback is secondary to MCP server-side resolution.
- If a live probe reaches the intended display name and then fails with quota, billing, or token-limit errors, treat that as a provider-side retry-later condition, not a model-selection problem.

## When To Read References

- Read [references/tool-contract.md](references/tool-contract.md) when you need the MCP input/output contract.
- Read [references/model-resolution.md](references/model-resolution.md) when you need the exact selector-resolution rules or want to debug `model_resolution_failed`.
- Read [references/security-boundary.md](references/security-boundary.md) when adjusting how encrypted keys flow into `opencode`.
- Read [references/stdio-transport-troubleshooting.md](references/stdio-transport-troubleshooting.md) when startup succeeds at the process level but Codex never finishes MCP boot.
- Read [references/stdin-inheritance-timeout-troubleshooting.md](references/stdin-inheritance-timeout-troubleshooting.md) when direct wrapper execution works but synchronous MCP calls hang or time out.

## Scripts

- [scripts/opencode_secure_run.sh](scripts/opencode_secure_run.sh)
- [scripts/opencode_secure_job_runner.py](scripts/opencode_secure_job_runner.py)
- [scripts/opencode_secure_mcp_server.py](scripts/opencode_secure_mcp_server.py)
- [scripts/register_opencode_secure_mcp.sh](scripts/register_opencode_secure_mcp.sh)
- [scripts/test_opencode_secure_mcp.py](scripts/test_opencode_secure_mcp.py)
- [/home/nzzhao/.agents/install_mcps.py](/home/nzzhao/.agents/install_mcps.py)
