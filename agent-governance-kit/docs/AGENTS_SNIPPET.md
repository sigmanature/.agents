# Snippet to paste into AGENTS.md

## Enforcement (hard gates)

- Run potentially-failing commands via: `tools/agentctl run --tag <tag> -- <cmd...>`
  - On failure, `agentctl` auto-creates a **worklog item** that must be triaged before completion.
- Start any long-running/background job via: `tools/agentctl start --tag <tag> -- <cmd...>`
  - The job must be **polled** and **consumed** before claiming completion.
- Before concluding, run:
  - `tools/agentctl close` (must pass)
  - `tools/agentctl audit` (must PASS)

If a gate fails, fix all reported items (consume jobs / triage-promote worklog) before stopping.

