# Agent Governance Kit (job registry + close gate + narrow auditor)

This kit adds **hard enforcement** on top of `AGENTS.md`:

1) **Job registry**: background jobs are started, tracked, polled, and must be **consumed**.
2) **Close gate**: the agent is not allowed to "finish" while:
   - there are running jobs,
   - finished-but-unconsumed jobs,
   - open/triaged-but-unpromoted worklog items.
3) **Narrow auditor**: produces a small verdict (PASS / FAIL + reasons) without reading hidden thoughts.

> Designed for Codex-style long-horizon tasks where model context may evaporate.
> The enforcement is file-based, so it survives restarts.

## What gets created

- `tools/agentctl.py` — the CLI.
- `worklog/` — **committable** trial-and-error staging queue (JSONL + notes).
- `.agent/` — **runtime-only** (job logs, state, temp). Add to `.gitignore`.

## Quick start

```bash
python3 tools/agentctl.py init

# run commands in foreground and auto-capture failures into worklog
python3 tools/agentctl.py run --tag build -- make test

# start a long job in background
python3 tools/agentctl.py start --tag install -- bash scripts/big_install.sh

# poll jobs (updates status)
python3 tools/agentctl.py poll

# consume completed job output (marks "consumed")
python3 tools/agentctl.py consume <job_id>

# capture a manual trial/error entry
python3 tools/agentctl.py capture --kind trial_error --summary "foo fails on mac" --details "permission denied ..." --reusable yes

# triage + promote (record what you changed)
python3 tools/agentctl.py triage <worklog_id> --outcome reference --promoted references/known-pitfalls.md
python3 tools/agentctl.py triage <worklog_id> --outcome skill --promoted skills/python/skill.md

# final gate (fails with exit code != 0 if incomplete)
python3 tools/agentctl.py close

# narrow auditor report (good to feed back to the main agent)
python3 tools/agentctl.py audit
```

## Recommended integration patterns

### A) "No-exit until gated" wrapper (manual)
When the agent wants to conclude, run:
```bash
python3 tools/agentctl.py audit --json > .agent/audit.json || true
cat .agent/audit.json
```
If FAIL, paste the `fix_instructions` back to the agent and continue.

### B) Git pre-commit / CI gates (optional)
```bash
bash scripts/install-git-hooks.sh
# or in CI:
bash scripts/ci-close-gate.sh
```

## Suggested additions to AGENTS.md

Copy-paste:

- Use `agentctl run` for commands that may fail.
- Any background job must be started via `agentctl start`.
- Before claiming completion, run `agentctl close` and ensure it passes.
- If `agentctl audit` fails, fix all listed items before stopping.

See `docs/AGENT_GOVERNANCE.md` for the full policy text.

