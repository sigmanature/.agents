# apk_batch_install: installing when APKs are split across dirs

## Symptom
`apk_batch_install.py` expects a single directory containing `*.apk` files. Some datasets store APKs split across multiple subdirectories (e.g. `top50/` + `top51_100/`), so passing a parent directory won’t work if the script only globs one level.

Also note: when running long installs from a non-interactive shell, Python stdout can be fully buffered. If you want live progress, run with `PYTHONUNBUFFERED=1` or patch the script to enable line buffering.

## Corrupted/truncated APK detection
If `adb install` fails with `INSTALL_PARSE_FAILED_NOT_APK` and unzip tooling reports:

```
End-of-central-directory signature not found
```

then the `.apk` is very likely truncated/corrupted (missing ZIP central directory). Re-download that APK; retries on the device won’t fix it.

## Recommended pattern (symlink “flat” dir)

Create/refresh a flat directory containing symlinks to all APKs, then install from that directory.

Example (run from repo root):

```bash
cd top100_apks
rm -f *
ln -s ../downloads/top50/top50/*.apk .
ln -s ../downloads/top51_100/top51_100/*.apk .
find . -maxdepth 1 -type l -name '*.apk' | wc -l   # expect 100
```

Then run the batch installer:

```bash
python3 /home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts/apk_batch_install.py \
  top100_apks \
  --serial <SERIAL> \
  --output-dir ./output/apk_install_top100_<ts>_<serial> \
  --gap-s 0.2
```

## Alternative (single command)
If your `ln` supports it, use relative links:

```bash
mkdir -p top100_apks
ln -sr downloads/top50/top50/*.apk downloads/top51_100/top51_100/*.apk top100_apks/
```

