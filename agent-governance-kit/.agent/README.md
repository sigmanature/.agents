# .agent/ (runtime-only)

This directory is created by `agentctl` and should not be committed.

- `state.json` — timestamps and small state used by `audit`.
- `jobs.json` — the job registry (authoritative).
- `jobs/<job_id>.log` — stdout/stderr for background jobs.
- `runs/<timestamp>_<tag>.log` — logs for foreground `agentctl run`.

