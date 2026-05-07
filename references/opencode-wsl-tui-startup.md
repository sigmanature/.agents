# OpenCode WSL TUI Startup Diagnosis

Use this reference when `opencode` starts to a blank or black TUI screen under WSL.

## Quick Checks

1. Confirm the binary and version:
   ```bash
   command -v opencode
   opencode --version
   ```

2. Check terminal basics:
   ```bash
   uname -a
   printf 'TERM=%s\nCOLORTERM=%s\nWT_SESSION=%s\nWSL_DISTRO_NAME=%s\n' "$TERM" "$COLORTERM" "$WT_SESSION" "$WSL_DISTRO_NAME"
   ```

3. Inspect recent logs:
   ```bash
   find ~/.local/share/opencode/log -maxdepth 1 -type f -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' | sort | tail -10
   tail -120 ~/.local/share/opencode/log/*.log
   ```

4. Isolate external plugins:
   ```bash
   opencode --pure --print-logs --log-level DEBUG
   opencode --print-logs --log-level DEBUG
   ```

If `--pure` renders normally but normal startup shows a blank screen or model warning, suspect external plugin configuration first.

## Known Symptom: oh-my-openagent Sisyphus Model Alias

Observed with `opencode` 1.14.x and `oh-my-openagent@latest`:

- Normal startup clears the screen and may appear black for several seconds.
- The TUI can display a warning like:
  ```text
  Agent Sisyphus - Ultraworker's configured model anthropic/claude-opus-4.7 is not valid
  ```
- `opencode --pure` renders the default home screen normally.

Root cause: `oh-my-openagent` can resolve the default Sisyphus model to `anthropic/claude-opus-4.7`, while the current opencode model validation expects a different canonical ID or the provider is unavailable. If the user's `~/.config/opencode/oh-my-openagent.json` overrides other agents but not `sisyphus`, the invalid plugin default can still be selected.

Minimal fix:

```json
{
  "agents": {
    "sisyphus": {
      "model": "opencode/gpt-5-nano"
    }
  }
}
```

Use a model already available in `opencode models opencode` or another configured provider.

## Terminal Raw Mode Error

Logs may contain:

```text
setRawMode failed with errno: 5
```

This can happen when launching the TUI from a non-interactive process without a real TTY. Treat it as a terminal invocation issue unless it also reproduces in an actual WSL terminal. Use a real terminal for TUI verification; use `opencode run`, `opencode debug`, or `opencode --pure` for noninteractive checks.

## Validation

After a config change:

1. Validate JSON:
   ```bash
   node -e "JSON.parse(require('fs').readFileSync(process.env.HOME+'/.config/opencode/oh-my-openagent.json','utf8')); console.log('json ok')"
   ```

2. Confirm the model exists:
   ```bash
   opencode models opencode
   ```

3. Launch the TUI with logs and verify the home screen shows the expected agent/model:
   ```bash
   opencode --print-logs --log-level DEBUG
   ```

