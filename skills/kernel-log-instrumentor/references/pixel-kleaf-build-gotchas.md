# Pixel Kleaf (Bazel) Build Gotchas (Local Workspace)

This note captures a few recurring pitfalls when building Pixel kernels using the Kleaf/Bazel workflow in a local checkout.

## Symptom: `--config=raviole` not defined

If you run `bazel query --config=raviole ...` you may see:

- `ERROR: Config value 'raviole' is not defined in any .rc file`

Reason:
- In many Pixel workspaces, `raviole` is defined under `build:raviole` (i.e. build command config), not under `query:raviole`.

Fix:
- Omit `--config=raviole` for `query`, or add a `common:raviole` / `query:raviole` config if you own the bazelrc.

## Symptom: Bazel explores generated `out/` dirs or hits symlink loops

If your workspace has an `out/` symlink (or any output directory inside the workspace), Bazel may traverse it and fail with:

- `ERROR: infinite symlink expansion detected ...`

Fix:
- Add a `.bazelignore` at workspace root to exclude output directories, e.g.:
  - `out`
  - `bazel-bin`
  - `bazel-out`
  - `bazel-testlogs`

## Known-working build invocation (raviole/slider example)

In this `learn_os` checkout, a working build command is:

```bash
cd /home/nzzhao/learn_os/pixel
tools/bazel --bazelrc=device.bazelrc build --config=raviole \
  //private/google-modules/soc/gs:slider_dist --noshow_progress
```

Notes:
- `--bazelrc=device.bazelrc` is passed explicitly because the workspace may not have a standard `.bazelrc`.
- `--noshow_progress` keeps logs smaller; drop it for more interactive progress.

