# worklog/

This directory is intended to be **committed**.

- `governance.jsonl` — structured items (authoritative for gates)
- `governance-notes.md` — optional human notes

A worklog item is considered **closed** only when:
- `status` is one of: `discarded`, `deferred`, `promoted`
- if deferred: `defer_reason` must be non-empty
- if promoted: `promoted_paths` must include at least 1 file path

