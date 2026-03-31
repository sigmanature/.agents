---
name: stack-trace-md
description: Extract Linux kernel or similar stack traces from pasted logs or log files and return a Markdown report. Use this when the user asks to analyze a crash log, hung task log, deadlock, panic, oops, or call trace. Do not use it for general application logs that do not contain stack traces.
---

# Stack Trace Markdown Reporter

Use this skill when the user provides or references logs that contain one or more of the following:

- `Call trace:`
- `task: ... state: ... pid: ...`
- `BUG:`
- `Oops:`
- `Unable to handle`
- `Kernel panic`
- `panic`

## Goal

Turn raw stack-like logs into a readable Markdown incident report.

## Workflow

1. Find the log source.
   - If the user gave you a file path, use that file.
   - If the user pasted log text into the chat, save it to a temporary file in the workspace, for example `./tmp_stack_trace.log`.
2. Run the helper:

```bash
python3 .agents/skills/stack-trace-md/scripts/stack_trace_md.py <path-to-log>
```

Or use the wrapper:

```bash
.agents/skills/stack-trace-md/scripts/stack_trace_md <path-to-log>
```

3. Read the Markdown output.
4. In the final answer:
   - Start with one short sentence explaining that you extracted the stack report.
   - Then paste the generated Markdown report verbatim.
   - If the user asked for diagnosis, add a short diagnosis after the Markdown.

## Output rules

- Preserve the Markdown structure from the script output.
- Do not collapse the code fences.
- Do not rewrite function names.
- If no incident is found, say that no matching stack incident was detected and ask for a fuller log only if needed.

## Notes

- The script supports reading from a file path.
- The script also supports stdin by passing `-` as the log path.
- Prefer the script output over hand-formatting, so the report stays consistent.
