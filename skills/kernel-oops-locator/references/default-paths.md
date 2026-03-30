# Default paths and behavior

This skill is intentionally tuned for one local environment.

## Defaults

- `BASE=~/learn_os`
- `kernelSrcDir=$BASE/f2fs`
- `vmlinuxDir=$BASE/f2fs_upstream`
- `vmlinux=$BASE/f2fs_upstream/vmlinux`
- `compileCommandsDir=$BASE/f2fs`
- `gdbinitPath=$BASE/.gdbinit`
- `gdb=/usr/bin/gdb-multiarch`
- `varsFile=$BASE/.vars.sh`

## Meaning of remote gdbstub

This is the `target remote host:port` style workflow used to attach gdb to a live target such as QEMU, KGDB, or OpenOCD. It is not required for simple offline crash lookup with `vmlinux`.

## Typical local command

```bash
source ~/learn_os/.vars.sh && \
/usr/bin/gdb-multiarch -q -batch "$BASE/f2fs_upstream/vmlinux" \
  -ex 'set pagination off' \
  -ex 'set confirm off' \
  -ex 'set listsize 20' \
  -ex "directory $BASE/f2fs" \
  -ex "source $BASE/.gdbinit" \
  -ex 'info line *f2fs_put_page+0x34' \
  -ex 'list *f2fs_put_page+0x34'
```

## Symbol selection rule

1. Prefer the frame marked `(P)`.
2. Else prefer `PC`, `pc`, `RIP`, or similar instruction pointer lines.
3. Else use the first `func+0xoff/0xlen` inside the crashing section.
4. Strip the trailing `/0xlen` before passing to `list *...`.
