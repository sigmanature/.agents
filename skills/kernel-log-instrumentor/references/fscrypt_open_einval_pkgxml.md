# Debug recipe: `open()` returns `-EINVAL` for `/data/system/packages.xml` (Pixel / AOSP)

This recipe is for the Android symptom:

- logcat: `packages.xml: open failed: EINVAL (Invalid argument)`
- then PackageManager wipes settings and “apps disappeared”

Key idea:

- This is often an **fscrypt open-time failure**, not “APK didn’t fsync”.
- On F2FS, `f2fs_file_open()` calls `fscrypt_file_open()` first, so errors from fscrypt can surface as `open()` returning `-EINVAL`.

## Why `-EINVAL` happens in fscrypt

In `fscrypt_get_encryption_info()`:

- context read succeeds, but:
  - context is corrupt/unrecognized (`fscrypt_policy_from_context()` fails), or
  - policy exists but is unsupported (`!fscrypt_supported_policy()`), which historically returned `-EINVAL` **silently**

## Minimal, low-noise instrumentation

Goal: when `-EINVAL` happens, print the **path** and enough context to determine which branch it was.

### A) Path-correlation at `fscrypt_file_open()` (has `filp` / `dentry`)

Insert a `WARN`-level log only for `err == -EINVAL`, e.g.:

- `dentry=%pd2` (so we don’t need `/proc/kallsyms`)
- `ino`, `mode`, `sb`

### B) Add a warning in the silent `unsupported policy` branch

In `fscrypt_get_encryption_info()`:

- keep original behavior, but if `!allow_unsupported`, print:
  - `ctx_len`
  - `policy.version`, `contents_encryption_mode`, `filenames_encryption_mode`, `flags`, `log2_data_unit_size` (v2)
  - first bytes of raw context: `%*ph`

This typically tells you immediately whether the context bytes are garbage, or policy fields are simply not supported in the running config.

## Log reading checklist

When you reproduce “install one app, reboot once, PM blows up immediately”, grep kernel logs for:

- `FSCRYPT_OPEN_ERR`
- `Unrecognized or corrupt encryption context`
- `Unsupported fscrypt policy`

Then:

- If the path is `/data/system/packages.xml*`, you have an actionable kernel-side reason for the `EINVAL`.
- If the path is not that file, you’re chasing the wrong object and should add more targeted logging.

