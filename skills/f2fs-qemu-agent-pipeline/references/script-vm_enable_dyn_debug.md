# vm_enable_dyn_debug.sh reference

## Path
`scripts/vm_enable_dyn_debug.sh`

## Purpose
Enable dynamic debug specifications inside guest kernel through SSH in one command batch.

## Usage
```bash
bash scripts/vm_enable_dyn_debug.sh 'func foo +p' 'file fs/f2fs/file.c line 100 +p'
```

## Behavior
- Builds remote command sequence:
  - verifies `/sys/kernel/debug/dynamic_debug/control` writable
  - applies each spec with `echo "<spec>" > control`
  - tails and greps control file for common targets
- Executes through `scripts/vm_ssh.sh`.

## Requirements
- debugfs mounted in guest
- dynamic debug enabled in kernel config
- guest reachable over SSH

## Caveats
- Script uses fixed grep targets in tail output; absence there does not always mean spec failed.
- For QGA-only environments, port script logic to `qga_exec.py` path or run equivalent guest commands directly.
