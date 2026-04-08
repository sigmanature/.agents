# QEMU CoW Multi-Instance Workflow (User-Level)

## Purpose

Normalize the contract for `myscripts/qemu_start_ubuntu.sh` so automation can target a specific VM instance without guessing ports, sockets, or log paths.

## Entry point

`myscripts/qemu_start_ubuntu.sh`

## Supported commands

- `start <instance>`
- `stop <instance>`
- `status <instance>`
- `cleanup <instance>`

## Multi-instance contract

Each instance owns a metadata file:

`myscripts/vm_instances/<instance>/instance.env`

Fields (subset):

- `VM_SSH_PORT`
- `VM_HTTP_PORT`
- `VM_GDB_PORT`
- `VM_QGA_SOCK`
- `VM_QMP_SOCK`
- `VM_CONSOLE_LOG`
- `VM_PID_FILE`
- `VM_KERNEL_DIR`
- `VM_MEM`

Automation should prefer reading this file or `status <instance>` instead of inferring ports or socket names.

## Default naming rules

- Instance root: `myscripts/vm_instances/<instance>/`
- Root overlay: `root.qcow2`
- F2FS overlay: `f2fs.qcow2`
- QGA socket: `/tmp/qga.<instance>.sock`
- QMP socket: `/tmp/qemu-qmp.<instance>.sock`

If the instance name ends with digits and no explicit ports are given:

- `ssh_port = 5022 + suffix - 1`
- `http_port = 5080 + suffix - 1`
- `gdb_port = 1234 + suffix - 1`

Examples:

- `vm1` -> ssh `5022`, http `5080`, gdb `1234`
- `vm2` -> ssh `5023`, http `5081`, gdb `1235`

If the instance name has no numeric suffix, callers must provide explicit ports or `--port-offset`.

## Safe validation pattern

Use `--dry-run` first when adding a new instance:

```bash
bash myscripts/qemu_start_ubuntu.sh start vm2 --dry-run
```

This prepares overlays and metadata, prints the resolved QEMU command, but does not boot the VM.

## Shared directory: one copy vs per-instance

Current safe default is per-instance `shared_with_qemu` under the instance directory (isolates concurrent test outputs).

To truly share one copy of scripts across many VMs, the recommended direction is:

- mount a shared base directory read-only (common scripts)
- mount a per-instance writable work directory (results/logs)

This is a workflow-level change and usually requires guest-side scripts to write into the work directory instead of the shared base.

Launcher support:

- `myscripts/qemu_start_ubuntu.sh` supports `--share-mode copy` (default) and `--share-mode shared-ro`.
- `shared-ro` mounts the shared base as `hostshare` with `readonly=on` and mounts a per-instance writable `workshare` directory.
