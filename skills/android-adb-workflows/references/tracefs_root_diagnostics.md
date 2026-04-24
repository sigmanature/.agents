# Tracefs root diagnostics on rooted retail devices

Use this when `tracefs` exists but your host-side root commands still fail from `adb shell`.

## Symptom pattern

- `adb root` prints `adbd cannot run as root in production builds`
- `adb shell command -v su` finds a binary
- `adb shell su -c id` fails with `Permission denied`
- `adb shell mount | grep tracing` still shows `tracefs on /sys/kernel/tracing`

This means the device likely has a root solution installed, but the **shell user has not actually
been granted superuser access yet**. In that state:

- non-root commands such as `pm compile`, `pm art dump`, `monkey`, `cmd package ...` still work
- tracefs writes, `/data/app/.../oat/...` inspection, and root-only pulls still do **not** work

## Fast diagnosis

Run these in order:

```bash
adb shell 'command -v su; which su 2>/dev/null'
adb shell 'su -c id'
adb shell 'su -c getenforce'
adb shell 'su -c "sh -c \"cat /sys/kernel/tracing/tracing_on\""' 
```

Interpretation:

- If the first line finds `su` but the second line fails with `Permission denied`, stop debugging
  your tracefs script. Root itself is not available to the shell user yet.
- If `su -c id` succeeds but `su -c 'echo 1 > ...'` fails, you likely have a quoting/redirection
  issue instead; use `su -c 'sh -c "echo 1 > ..."'`.

## What to fix on-device

On Magisk-style setups, grant superuser to the `shell` caller that comes from `adb shell`.

Practical checks:

- Open the root manager UI and confirm the shell caller is not denied.
- If a previous deny decision was remembered, clear it and retry `adb shell su -c id`.
- If there is a denylist or per-app block that includes the shell/adb path, remove that block.

Do not continue with tracefs capture until this succeeds:

```bash
adb shell 'su -c id'
```

## After root is working

These become the stable patterns:

```bash
adb shell su -c 'sh -c "echo 0 > /sys/kernel/tracing/tracing_on"'
adb shell su -c 'sh -c "echo 1 > /sys/kernel/tracing/events/raw_syscalls/sys_enter/enable"'
adb exec-out su -c 'cat /sys/kernel/tracing/trace_pipe' > trace_pipe.txt
```

## Related assets

- `scripts/adb_oat_rewrite_capture.sh`
- `scripts/adb_dexopt_regen_loop.sh`
- `references/dexopt_oat_vdex_regen.md`
- `references/adb_execution_reference.md`
