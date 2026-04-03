# open-websearch Sandbox Notes

## What to expect in a network-restricted sandbox

- The process can still start locally.
- Local loopback probes to `127.0.0.1` can still succeed.
- Real web search will fail when the sandbox blocks outbound network access.

This means "the MCP server is up" and "the MCP can actually search the web" are separate checks.

## HTTP exposure

The installed package binds the HTTP server to `0.0.0.0`.

That does not create a reverse tunnel by itself, but it does expose the port to:

- other local processes
- other machines on the same network, if routing/firewall allows it
- public clients, if the host is directly reachable or the port is forwarded

## Safer local usage

Prefer STDIO-only startup:

```bash
bash scripts/open_websearch_stdio_safe.sh
```

If HTTP is required, treat it as a local service that should be additionally contained by:

- host firewall rules
- loopback-only binding via code patch or wrapper proxy
- a disposable environment for first-run testing

## Tested query quality in this environment

Real MCP `search` calls succeeded in this environment, but engine quality differed materially:

- `duckduckgo`
  - succeeded for `OpenAI Responses API docs`
  - returned an OpenAI docs result at `developers.openai.com/api/reference/responses/overview`
  - succeeded for `LWN io_uring kernel`
  - returned `https://lwn.net/Articles/810414/`
- `bing`
  - the MCP call itself succeeded
  - result quality was poor for these technical queries
  - `OpenAI Responses API docs` drifted to Zhihu/OpenAI discussion pages
  - `LWN io_uring kernel` drifted to unrelated `LWN` acronym results
- `startpage`
  - returned zero results for the tested constrained queries

Practical takeaway:

- prefer `duckduckgo` first for technical documentation/article discovery in this environment
- do not assume `bing` wrapper quality is acceptable just because the MCP call succeeds
