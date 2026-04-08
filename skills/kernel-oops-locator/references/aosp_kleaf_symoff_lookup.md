# AOSP / Pixel (kleaf) kernel: locate `func+0xOFF` without interactive gdb

This note is for the common “Android kernel” scenario where:

- your log shows a frame like `f2fs_evict_inode+0x5b0/0x7fc`
- you have a built `vmlinux` from **kleaf** (often PIE, LTO, etc.)
- you want to map it to **file:line** and get a small **disassembly window**

## 1) Pick the correct `vmlinux` (match BuildID / version)

On Pixel/kleaf builds in this repo layout, the most useful artifacts are usually:

- `~/learn_os/pixel/out/cache/last_build/common/vmlinux`
- `~/learn_os/pixel/out/cache/last_build/common/System.map`
- `~/learn_os/pixel/out/cache/last_build/common/source -> ~/learn_os/pixel/common`

Quick sanity:

```bash
VMLINUX=~/learn_os/pixel/out/cache/last_build/common/vmlinux
file "$VMLINUX"
strings -a "$VMLINUX" | rg -m 1 '^Linux version'
readelf -n "$VMLINUX" | rg -m 1 'Build ID'
```

If your dmesg includes a BuildID (often shown on the “Tainted” line), match it to `readelf -n`.

## 2) Convert `func+0xOFF` into an absolute linked address

Option A (fast): use `System.map`:

```bash
MAP=~/learn_os/pixel/out/cache/last_build/common/System.map
rg -n '\\bf2fs_evict_inode$' "$MAP"
```

Example output:

```
ffffffc0806c2bb4 T f2fs_evict_inode
```

Then add the offset:

```bash
BASE=0xffffffc0806c2bb4
OFF=0x5b0
printf 'addr=%#x\n' $((BASE + OFF))
```

Option B: use `nm` (works even if `System.map` is missing):

```bash
VMLINUX=~/learn_os/pixel/out/cache/last_build/common/vmlinux
aarch64-linux-gnu-nm -n "$VMLINUX" | rg '\\bf2fs_evict_inode$'
```

## 3) Map address -> file:line (addr2line)

```bash
VMLINUX=~/learn_os/pixel/out/cache/last_build/common/vmlinux
ADDR=0xffffffc0806c3164   # example computed above

aarch64-linux-gnu-addr2line -e "$VMLINUX" -fip "$ADDR"
```

Important caveat:

- If you see `.../include/trace/events/*.h` in the result, that’s often because the PC lands in
  an inlined tracepoint / macro-expanded location. It’s still useful, but you usually want the
  **nearby disassembly with line annotations** to understand the surrounding C code.
- Another common case: the warning you saw was triggered by a `WARN_ON()` at `file.c:LINE`,
  but the **PC** is inside the out-of-line/cold `__WARN()` slowpath. In that case, the backtrace
  shows `func+0xOFF`, but `addr2line` may point somewhere surprising. Use `objdump -dl` and/or
  `gdb -batch 'info line <file>:<line>'` to reconcile.

## 4) Get disassembly *with line annotations* (objdump, no gdb)

This is the most reliable “no-gdb” method in practice:

```bash
VMLINUX=~/learn_os/pixel/out/cache/last_build/common/vmlinux
ADDR=0xffffffc0806c3164
START=$((ADDR - 0x80))
STOP=$((ADDR + 0x120))

aarch64-linux-gnu-objdump -dl \
  --start-address="$START" \
  --stop-address="$STOP" \
  "$VMLINUX" | sed -n '1,160p'
```

What to look for:

- `.../fs/f2fs/inode.c:<line>` annotations near your `ADDR`
- `__warn_printk` + `brk #0x800` indicates a `WARN*()` site
- calls around the PC (e.g. `fscrypt_put_encryption_info`, `clear_inode`, etc.) help identify which branch of the function you’re in

## 5) (Optional) still want gdb, but non-interactive

Even if you don’t want “interactive debugging”, `gdb -batch` is still a great source locator:

```bash
VMLINUX=~/learn_os/pixel/out/cache/last_build/common/vmlinux
/usr/bin/gdb-multiarch -q -batch "$VMLINUX" \
  -ex 'set pagination off' \
  -ex 'info line *f2fs_evict_inode+0x5b0' \
  -ex 'list *f2fs_evict_inode+0x5b0' \
  -ex 'disassemble /m f2fs_evict_inode, +0x650'
```

If you specifically want to check the code for a reported `file.c:LINE` (even when `*ADDR` maps
to a different header due to inlining), use:

```bash
/usr/bin/gdb-multiarch -q -batch "$VMLINUX" \
  -ex 'set pagination off' \
  -ex 'set confirm off' \
  -ex 'set substitute-path /proc/self/cwd/common ~/learn_os/pixel/common' \
  -ex 'info line /proc/self/cwd/common/fs/f2fs/inode.c:871' \
  -ex 'list /proc/self/cwd/common/fs/f2fs/inode.c:860,890'
```
