# opencode Secure MCP Security Boundary

## What stays in the wrapper

- passphrase lookup
- `openssl` invocation
- encrypted-file decoding
- environment-variable injection into the child `opencode` process

## What stays in the MCP server

- task schema validation
- subprocess orchestration
- job bookkeeping
- status lookup
- cancellation
- artifact collection

## Why this split matters

If the MCP server learns how to decrypt secrets itself, the reusable bottom-layer capability becomes harder to audit and easier to misuse.

Keep the server dumb about secrets and smart about task orchestration.
