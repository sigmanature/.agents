# MCP Audit Checklist

This checklist is for "can I trust this MCP enough to run it on my machine?" decisions.

## Threat model

An MCP can hurt you even if it is not overt malware.

Common risk classes:

- credential access
- local file exfiltration
- shell command execution
- silent network listeners
- browser cookie/session extraction
- supply-chain substitution during install
- remote auto-download of unsigned binaries

## Practical audit order

1. Package metadata
   - Check name, version, license, repository, bin entry.
   - Treat missing repository/license as a negative signal.
2. Lifecycle hooks
   - Inspect `preinstall`, `install`, `postinstall`, `prepare`.
   - These are the highest-risk auto-execution points.
3. Capability surface
   - Search for `child_process`, `spawn`, `exec`, `eval`, `Function`, filesystem writes, and network listeners.
4. Local attack surface
   - Check whether it binds to `0.0.0.0`, enables permissive CORS, or exposes HTTP endpoints by default.
5. Network boundaries
   - Check whether it blocks localhost/private IP targets or can reach arbitrary URLs.
6. Native code
   - Embedded ELF/exe binaries or `.node` addons raise the bar for trust.
   - Prefer source build or reproducible hashes when possible.
7. Dependency sprawl
   - Large transitive dependency trees increase review cost and supply-chain risk.
8. Runtime validation
   - Run under least privilege, on a clean test account or isolated environment first.

## Risk grading

Use this rough scale:

- Low:
  - no lifecycle hooks
  - pure JS/TS
  - narrow tool surface
  - no local listener by default
- Medium:
  - expected network access
  - optional browser automation
  - manageable dependency tree
  - no obvious shell execution or destructive file ops
- High:
  - install hooks downloading binaries
  - default `0.0.0.0` listeners
  - browser/cookie/session handling
  - child-process execution
  - native binary blobs you did not build
- Critical:
  - obfuscated code
  - hidden credential collection
  - shell execution unrelated to declared purpose
  - writes outside declared data dirs
  - silent outbound exfiltration

## What the local audit script cannot prove

The script will not prove a package is safe. It only reduces blind spots.

It will miss:

- malicious logic hidden in native binaries
- dormant code paths not matched by grep patterns
- compromised upstream releases that still "look normal"
- behavior only triggered by specific MCP requests

## Containment recommendations

Even after a clean audit:

- run new MCPs under a separate user or container first
- avoid giving them browser profiles or long-lived tokens initially
- prefer STDIO-only over open HTTP listeners when possible
- bind HTTP listeners to `127.0.0.1`
- disable permissive CORS unless needed
- keep API keys in a dedicated env file, not your general shell profile
