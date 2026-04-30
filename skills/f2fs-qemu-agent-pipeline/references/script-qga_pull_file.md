# qga_pull_file.py

Pull a file from the guest through QEMU Guest Agent `guest-file-open`,
`guest-file-read`, and `guest-file-close`.

Use this when:

- SSH/scp is unavailable.
- Evidence is guest-local, for example under `/tmp`.
- The file is binary or too large to safely stream through `guest-exec` stdout.
- A 9p/shared directory is mounted read-only or guest writes fail with
  `Operation not permitted`.

Example:

```bash
python3 .agents/tools/qga_pull_file.py \
  --sock /tmp/qga.sock \
  /tmp/test_inline_artifact/f2fs.img \
  /tmp/f2fs.img
```

Validation:

```bash
sha256sum /tmp/f2fs.img
python3 .agents/tools/qga_exec.py --sock /tmp/qga.sock \
  'sha256sum /tmp/test_inline_artifact/f2fs.img; stat -c "%s %n" /tmp/test_inline_artifact/f2fs.img'
```

Notes:

- The script base64-decodes QGA chunks and writes the host file incrementally.
- Default chunk size is 1 MiB and progress is printed every 64 MiB.
- Keep the guest VM alive until size and hash match.
