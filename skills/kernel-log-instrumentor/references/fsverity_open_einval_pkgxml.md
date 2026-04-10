# Debug recipe: `open()` returns `-EINVAL` for `/data/system/packages.xml` due to **fs-verity**

Symptom (logcat):

- `packages.xml: open failed: EINVAL (Invalid argument)`
- then `pm_critical_info: Error reading package manager settings, removing /data/system/packages.xml`

Fast discriminator:

- If `adb shell su -c 'lsattr -a /data/system/packages.xml'` shows `V`, then PackageManager is opening a **verity file**.
- In that case, `open()` can fail because fs-verity descriptor validation returned `-EINVAL`.

Kernel evidence often looks like:

- `fs-verity (dm-XX, inode NNN): Unrecognized descriptor version: 0`

## Kernel call path (what actually returns `-EINVAL`)

- `fs/f2fs/file.c`: `f2fs_file_open()` calls:
  - `fscrypt_file_open(inode, filp)` first, then
  - `fsverity_file_open(inode, filp)`
- `fs/verity/open.c`: `__fsverity_file_open()` → `ensure_verity_info()` → `fsverity_get_descriptor()`
  - reads the verity descriptor via `inode->i_sb->s_vop->get_verity_descriptor()`
  - validates `desc->version == 1`
  - returns `-EINVAL` if invalid
- `fs/f2fs/verity.c`: `f2fs_get_verity_descriptor()` implements the filesystem callback:
  - reads verity xattr `F2FS_XATTR_NAME_VERITY` to get `(pos, size)`
  - reads descriptor bytes from file data at `pos`

## Why large-folio changes can break verity

F2FS stores the verity metadata **past `i_size`** (Merkle tree + descriptor).

During `FS_IOC_ENABLE_VERITY`, f2fs does:

- write descriptor past EOF (see `fs/f2fs/verity.c:71` `pagecache_write()`)
- `filemap_write_and_wait(inode->i_mapping)` (must flush both data and verity metadata)
- set verity xattr and set inode verity flag

To make this crash-consistent, f2fs sets `FI_VERITY_IN_PROGRESS` and writeback must allow
dirty pages **beyond `i_size`** to be written while this flag is set.

The classic 4K writeback path already checks `f2fs_verity_in_progress(inode)` (see `fs/f2fs/data.c:3590`).

But a folio-based writeback path that clamps to `i_size` (e.g. `f2fs_write_cache_folios()`)
can silently skip those pages, leaving the descriptor unwritten (often all zeros).
Next boot, opening the file triggers fs-verity descriptor validation and fails with `-EINVAL`.

## Minimal instrumentation (low noise)

### A) Correlate which stage fails in `f2fs_file_open()`

Log only when `dentry` is `packages.xml*` or when `err != 0`:

- `stage=fscrypt_file_open` / `stage=fsverity_file_open`
- `dentry=%pd2`, `ino=%lu`, `err=%d`

### B) Log verity descriptor location fields

In `fs/f2fs/verity.c:f2fs_get_verity_descriptor()`:

- if `dentry==packages.xml*` or `inode->i_ino==<target ino>`:
  - dump `dloc.version`, `dloc.size`, `dloc.pos`, `inode->i_size`
  - if invalid, print which check failed

### C) Verify writeback doesn’t skip pages beyond `i_size`

In `fs/f2fs/data.c:f2fs_write_cache_folios()`:

- log when `f2fs_verity_in_progress(inode)` is true:
  - `folio_index`, `pos`, `end_pos`, `isize`, `nr_to_write`
  - whether it’s skipping due to end_index logic

## Reading checklist (what “good” looks like)

After applying the fix/instrumentation and reproducing “install → reboot”:

- There should be **no** `fs-verity ... Unrecognized descriptor version` lines for `packages.xml*`
- `f2fs_file_open()` should not return `-EINVAL` at `stage=fsverity_file_open` for `packages.xml*`

