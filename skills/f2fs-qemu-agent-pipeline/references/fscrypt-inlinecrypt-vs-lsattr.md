# fscrypt inline encryption vs `lsattr` flags (F2FS)

`lsattr` output is often misread as “proof of inline encryption”.  On Linux, **inline encryption** means using **block-layer encryption** (blk-crypto / `-o inlinecrypt`) for regular file contents.  That is *not* the same thing as the `I` attribute shown by `lsattr`.

## What `lsattr` shows on F2FS

F2FS exposes `lsattr` flags via `f2fs_fileattr_get()`:

- `E` is set when `IS_ENCRYPTED(inode)` is true → exposed as `FS_ENCRYPT_FL`.
- `I` is set when either `f2fs_has_inline_data(inode)` **or** `f2fs_has_inline_dentry(inode)` is true → exposed as `FS_INLINE_DATA_FL`.

So on an encrypted directory, `E + I` frequently means:

- **`E`**: this directory is encrypted with fscrypt (filenames encrypted)
- **`I`**: directory uses inline dentry (or inline data), unrelated to blk inline crypto

## When a file uses “inline encryption” (blk-crypto)

fscrypt selects inline encryption per-inode. Key conditions include:

- filesystem mounted with `-o inlinecrypt` (`SB_INLINECRYPT`), and
- underlying block devices support the needed crypto config.

F2FS then checks `fscrypt_inode_uses_inline_crypto(inode)` and skips pagecache encryption when it is true.

## Quick guest-side checks

Inside the guest:

```bash
# Confirm the f2fs mount has inlinecrypt enabled (if you expect blk inline crypto)
cat /proc/mounts | rg ' f2fs ' | rg 'inlinecrypt' || true

# Look for fscrypt info logs about blk-crypto usage (mode-dependent)
dmesg | rg 'fscrypt: .*blk-crypto' || true
```

