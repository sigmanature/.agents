---
name: kernel-oops-locator
description: analyze linux kernel panic, oops, null pointer, unable to handle, or call trace text and map the likely crash site to nearby source lines using non-interactive gdb-multiarch commands against vmlinux. use when the user provides panic text, a panic log, a p-marked frame, a function+offset like foo+0x12, or asks to locate code near a crash site in a kernel tree. prefer the hardcoded ~/learn_os defaults in this skill unless the user explicitly overrides them.
---

# Kernel Oops Locator

Use this skill for **offline kernel crash source lookup**, not for live breakpoint debugging.

## Default environment

Unless the user explicitly overrides them, assume these defaults:

- kernel source tree: `~/learn_os/f2fs`
- vmlinux path: `~/learn_os/f2fs_upstream/vmlinux`
- compile commands dir: `~/learn_os/f2fs`
- gdb init: `~/learn_os/.gdbinit`
- debugger: `/usr/bin/gdb-multiarch`
- optional shell vars file: `~/learn_os/.vars.sh`

Read `references/default-paths.md` if you need the exact defaults or an example command.

## Workflow

1. Identify the input mode.
   - **panic text pasted in chat**: extract the best crash location.
   - **panic log file**: read the file content, then extract the best crash location.
   - **explicit symbol**: use the provided `func+0xoff` or absolute address directly.

2. Choose the best lookup target.
   - First choice: the frame explicitly marked **`(P)`**.
   - Fallback: a `PC is at ...`, `pc : ...`, `lr : ...`, or `RIP:` style location.
   - Fallback: the first `func+0xoff/0xlen` in the crashing section.
   - Normalize results to `func+0xoff` when possible.

3. Prefer **non-interactive** gdb usage.
   - Do **not** assume terminal keyboard interaction.
   - Prefer `gdb-multiarch -q -batch` plus `-ex` commands.
   - Use `scripts/make_gdb_command.py` to generate the exact shell command or gdb command file text.

4. Produce readable code context.
   - Default to `info line` and `list *location`.
   - Show a readable window around the location.
   - Add `disassemble /s` only when the user asks for asm mixed output or when source mapping looks ambiguous.

5. Be honest about execution context.
   - If you cannot access the user’s local kernel tree from the current environment, still provide the exact command to run locally.
   - Do not pretend that the command was executed if it was not.

## Preferred command shape

Default command pattern:

```bash
/usr/bin/gdb-multiarch -q -batch ~/learn_os/f2fs_upstream/vmlinux \
  -ex 'set pagination off' \
  -ex 'set confirm off' \
  -ex 'set listsize 20' \
  -ex 'directory ~/learn_os/f2fs' \
  -ex 'source ~/learn_os/.gdbinit' \
  -ex 'info line *f2fs_put_page+0x34' \
  -ex 'list *f2fs_put_page+0x34'
```

If the user wants a shell snippet that honors `~/learn_os/.vars.sh`, prefer:

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

## Output expectations

When helping the user, prefer this structure:

1. `picked location`: the exact symbol or address chosen, and why.
2. `local command`: the exact non-interactive gdb command.
3. `expected effect`: what the command prints, such as source line mapping and nearby code.
4. `fallbacks`: alternate symbol candidates if the panic text is ambiguous.

## Resources

- Use `scripts/parse_oops_target.py` to extract the best location from panic text.
- Use `scripts/make_gdb_command.py` to generate a ready-to-run command.
- Use `references/default-paths.md` for the baked-in environment defaults and examples.

## Scope boundary

This skill is **not** primarily for `target remote localhost:1234` or live attach sessions. Only discuss remote gdbstub / attach flows if the user explicitly asks to connect to a live target, qemu, kgdb, or a gdb server.
