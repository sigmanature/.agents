# Trigger examples

Use this skill when a request involves one or more of these conditions:

- A `learn_os` style kernel workspace with `.vars.sh`
- QEMU boot or reboot orchestration
- Guest access over forwarded SSH
- Guest access via QEMU Guest Agent (QGA), especially when SSH is unavailable/blocked
- F2FS validation inside a guest instead of source-only reasoning
- Kernel build validation that should check changed `.c` files before a full build
- Shared directory, 9p, or mount verification
- Requests where progress must be backed by logs and verified command results

Examples:

- Start the prepared QEMU guest in the background and tell me where the logs are.
- Run the guest command over SSH instead of opening a terminal.
- SSH is unavailable; run the guest command or test script via `scripts/qga_exec.py`.
- Check my changed F2FS files first, then do the full image build.
- Verify whether the required mounts are really present before the test.
- Do not tell me it worked unless the exit code proves it.
