# `pm_critical_info`: `packages.xml` open failed `EINVAL` triage

Symptom (logcat):

- `pm_critical_info: Error reading package manager settings, removing /data/system/packages.xml`
- `java.io.FileNotFoundException: /data/system/packages.xml: open failed: EINVAL (Invalid argument)`
- followed by many `Package ... is unknown` / `Deleting invalid package at /data/app/...`

Key point:

- This is **not** “PackageManager forgot to fsync the APK”.
- It means **`open()` on `packages.xml` failed in the kernel with `-EINVAL`**, then PackageManager deletes/rebuilds its settings, causing “apps disappeared”.

## Very fast discriminator: is it `fs-verity` or `fscrypt`?

On device (root):

```bash
adb shell su -c 'lsattr -a /data/system/packages.xml /data/system/packages.xml.reservecopy'
```

- If you see `V` on the files (fs-verity flag), then `open()` can fail because **fs-verity descriptor validation returned `-EINVAL`**.
- If you see only `E` (encrypted) but not `V`, then fscrypt/key/context problems become more likely.

In kernel logs, fs-verity problems are usually obvious:

```bash
rg -n 'fs-verity \\(.*inode .*\\): Unrecognized descriptor version' kernel_stream.txt | head
```

## Fast evidence checklist (captured logs)

If you used a capture layout like:

`.../session_YYYY-MM-DD_HHMMSS/`
- `logcat_all.txt`
- `kernel_stream.txt`
- `pstore_once.txt`

Then run these on the host:

```bash
rg -n 'pm_critical_info: Error reading package manager settings|packages\\.xml: open failed: EINVAL' logcat_all.txt

# Check early-boot fsck/mount and whether /data needed fsck
rg -n 'fsck\\.f2fs:|Invalid f2fs superblock|mount_with_alternatives\\(\\): skipping mount' kernel_stream.txt | head

# Check for fs-verity descriptor problems during package scan
rg -n 'fs-verity \\(.*inode .*\\): Unrecognized descriptor version' kernel_stream.txt | head

# Check pstore for f2fs warnings during rename/unlink (AtomicFile uses rename)
rg -n 'f2fs_evict_inode|renameat2|do_renameat2' pstore_once.txt | head
```

## Most likely root-cause buckets (ordered)

### 0) fs-verity metadata corruption / incomplete writeback (very likely if `V` flag is set)

If you see both:
- `lsattr` shows `V` for `packages.xml*`, and
- dmesg shows `fs-verity (..., inode ...): Unrecognized descriptor version: 0`

then `open()` is failing because fs-verity reads a descriptor and validates `desc->version == 1`, but it got `0` (often “all zeros”).

One known kernel-side cause (especially with **large folio** changes):
- f2fs stores verity descriptor + Merkle tree **past i_size** (see `fs/f2fs/verity.c`).
- While building verity, F2FS sets `FI_VERITY_IN_PROGRESS` and must write back dirty pages **beyond i_size** before clearing it.
- The 4K writeback path already special-cases `f2fs_verity_in_progress(inode)`, but a folio-based writeback path that clamps to `i_size` can silently skip those pages, leaving the descriptor unwritten on disk.

### 1) Filesystem “crash-recovery / fsck happened” and PackageManager settings got clobbered

Look for:
- `fsck.f2fs:` running at boot
- checkpoint state including `sudden-power-off`

This can revert or invalidate recently updated `packages.xml` / its reserve copy.

### 2) Atomic rename / directory fsync semantics issue

Android writes `packages.xml` using an AtomicFile-like pattern (tmp write + fsync + rename + dir fsync).

If pstore shows **F2FS warnings in rename/unlink/evict**, investigate the exact warning site (use `addr2line` on `f2fs_evict_inode+0x...` against the device `vmlinux`).

### 3) xattr/verity metadata read issues

If `kernel_stream.txt` shows many:
- `fs-verity (...): Unrecognized descriptor version: 0`

that suggests verity metadata reads are returning garbage/zeros, which can make APKs “invalid” and trigger cleanup.

### 4) fscrypt key/context setup failures (less likely if no kernel `fscrypt_warn` seen)

`-EINVAL` can also come from `fscrypt_get_encryption_info()` when:
- encryption context is corrupt/unrecognized, or
- policy is unsupported, or
- (hw-wrapped key case) inline encryption requirements aren't met.

You normally expect a matching kernel log line via `fscrypt_warn()` if this happens; absence of such logs reduces likelihood but doesn't fully rule it out (rate limiting can hide repeats).

**Concrete kernel call path (useful when interpreting `open failed: EINVAL`)**

- `fs/f2fs/file.c`: `f2fs_file_open()` calls `fscrypt_file_open(inode, filp)` first.
- `fs/f2fs/file.c`: `f2fs_file_open()` then calls `fsverity_file_open(inode, filp)`.
- `fs/crypto/hooks.c`: `fscrypt_file_open()` calls `fscrypt_require_key(inode)`.
- `fs/crypto/keysetup.c`: `fscrypt_get_encryption_info()` returns `-EINVAL` specifically when:
  - `fscrypt_policy_from_context()` fails (context is “unrecognized or corrupt”), or
  - `!fscrypt_supported_policy()` (policy unsupported for this inode / config).

So, if you can grab kernel logs around boot, prioritize:

```bash
adb shell su -c 'dmesg -T | rg -i "fscrypt_warn|Error .* getting encryption context|Unrecognized or corrupt encryption context|f2fs|EINVAL" | head -n 200'

# Some builds expose kernel ring via logcat:
adb shell su -c 'logcat -b kernel -d | rg -i "fscrypt|f2fs|EINVAL" | head -n 200'
```
