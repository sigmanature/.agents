# `multi_tool_use.parallel` Quick Reference

This reference is owned by the `dispatching-parallel-agents` skill.

## What it is

`multi_tool_use.parallel` runs multiple **developer tools** concurrently.

Typical usage: execute multiple independent `functions.exec_command` calls in parallel to reduce wall-clock time spent waiting on I/O.

## What to parallelize

### ✅ Safe / common

- Multiple `rg` queries for different symbols or directories
- Multiple reads of known file paths (`cat`, `sed -n`, `ls -la`)
- Multiple “context snapshot” commands (`git status`, `git rev-parse`, `python --version`)

### ⚠️ Sometimes safe (be explicit)

- Multiple targeted tests if they don’t contend on the same resources (ports, device locks, shared temp dirs)
- Multiple build commands if they write to different output directories (worktrees / separate `O=` directories)

### ❌ Avoid / serialize

- Any `apply_patch` operations
- Multiple commands that mutate the same working tree state (`git checkout/reset/clean`, mass deletes)
- Commands that will fight over the same output log file or port
- Any command chain where the next command depends on the previous output

## Dependency checklist (quick)

Before batching commands together, ask:

1. Does any command **write** to the same files/directories as another command?
2. Do any commands share a **log file path** / temp file path?
3. Do any commands need the same **port** / lock / device?
4. Does any command require **output** from another command?

If any answer is “yes”, do not parallelize that pair.

## Example batch (repo inspection)

```json
{
  "tool_uses": [
    {
      "recipient_name": "functions.exec_command",
      "parameters": { "cmd": "rg -n \"TODO\\(\" -S src" }
    },
    {
      "recipient_name": "functions.exec_command",
      "parameters": { "cmd": "rg -n \"panic\\(\" -S src" }
    },
    {
      "recipient_name": "functions.exec_command",
      "parameters": { "cmd": "ls -la && git status --porcelain" }
    }
  ]
}
```

## Pattern: parallelize “discover”, serialize “edit”

Most flows naturally break into:

1. **Discover** (parallel): find files, grep symbols, read logs
2. **Decide** (serial): choose one next action based on evidence
3. **Edit** (serial): apply patches in a controlled order
4. **Verify** (maybe parallel): run independent checks/tests where safe

