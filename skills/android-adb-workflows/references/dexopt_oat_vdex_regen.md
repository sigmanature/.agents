# Deterministic dexopt / oat(vdex) regeneration without reboot

This note focuses on **repeatably regenerating** dexopt artifacts (`*.odex`/`*.vdex`) on a running
device **without reboot**, using stable shell surfaces:

- `adb shell pm ...` (recommended)
- `adb shell cmd package ...` (usually equivalent; can have different help output)

It also covers practical profile control (clear/snapshot/dump) and ways to trigger background
dexopt work.

## Recommended stable repro baseline

For the Huoshan-style “rewrite + cold-start consume” experiments, treat this as the stable default
until you intentionally vary one axis:

- compiler filter: `speed-profile`
- reason: `cmdline`
- compile shape: `pm delete-dexopt` then `pm compile -f -m speed-profile`
- launch pressure: force-stop driven cold starts, not warm foreground/background toggles

If you need filter sweeps, make them explicit and record them as a separate experiment family.
Do not silently mix `verify`/`speed` into the same baseline run.

## Terminology (what you’re regenerating)

- **Primary dex**: dex inside installed APK splits (base + split APKs)
- **Secondary dex**: dex/jar extracted/created under app data at runtime and loaded by custom
  classloaders
- **Artifacts** (most common locations):
  - `/data/app/<pkg-...>/oat/<isa>/` (primary)
  - `*/oat/<isa>/*.odex` and `*/oat/<isa>/*.vdex` (names vary by split)

## How the artifacts are committed on-device (`.tmp` / `.backup` semantics)

When you trigger dexopt (via `pm compile`, background dexopt, install/upgrade, etc.), the
write/commit semantics typically come from **`installd`**, not from `dex2oat` itself:

- `installd` creates **work files** next to the final artifacts:
  - `<artifact>.tmp` is created first (best-effort deletes any stale `.tmp` from prior runs).
  - `dex2oat` is invoked with **output file descriptors** that point at those `.tmp` work files.
  - `dex2oat` is also given `--oat-location=<final path>` so the embedded metadata matches the
    path that will exist after commit.
- After `dex2oat` exits successfully, `installd` performs a 3-step “safe commit”:
  1) If `<artifact>` already exists, rename it to `<artifact>.backup`
  2) Rename `<artifact>.tmp` → `<artifact>` (this is the “commit” point)
  3) Delete `<artifact>.backup`
- If commit fails:
  - Try to rollback: rename `<artifact>.backup` back to `<artifact>`
  - If rollback fails too: delete the whole set (`.tmp`, `.backup`, and the regular file)

Practical consequences for debugging/repro plans:

- Seeing `*.tmp` lingering usually means “dex2oat/installd was killed or failed before commit.”
- Seeing `*.backup` lingering usually means “commit path ran, but cleanup didn’t finish,” or a
  partial rollback scenario.
- For artifacts under `/data/app/.../oat/...`, you may see only a very brief `.backup` window unless
  you’re sampling aggressively (or injecting failures).

### Special-case: boot image change + “in-place” VDEX update

When dexopt is triggered specifically due to a **boot image change**, `installd` can choose an
“update VDEX in place” mode **when all of these are true**:

- The input VDEX path equals the output VDEX path (same location)
- The dexopt reason is `DEX2OAT_FOR_BOOT_IMAGE`
- Not doing profile-guided compilation (because dexlayout needs separate input/output VDEX)

In that mode `installd` first unlinks the existing regular `.vdex`, then uses the same FD for
`--input-vdex-fd` and `--output-vdex-fd` (still writing via the `.tmp` work file, then committing
via rename).

### Special-case: A/B slot artifact moves

There is also a separate unlink+rename pattern used to “move B → A” artifacts based on
`ro.boot.slot_suffix` (i.e., rename `artifact.<slot>` to `artifact` after unlinking any existing
target). This can show up around OTA/slot transitions.

## 0) Quick discovery for big apps (Douyin / Huoshan)

Find the package deterministically:

```bash
chmod +x scripts/adb_pkg.sh
./scripts/adb_pkg.sh --filter aweme listf      # often Douyin
./scripts/adb_pkg.sh --filter huoshan listf    # may vary by channel/build
./scripts/adb_pkg.sh current                   # if the app is in foreground
```

Then export:

```bash
PKG=com.ss.android.ugc.aweme   # example
```

## A) Force recompile even if “up-to-date”

### Option A1 (most deterministic): delete → compile

This guarantees the compile pass must regenerate artifacts:

```bash
adb shell pm delete-dexopt "$PKG"
adb shell pm compile --full -r cmdline -f -m speed-profile "$PKG"
```

### Option A2: compile with force

If you don’t want to delete first:

```bash
adb shell pm compile --full -r cmdline -f -m speed-profile "$PKG"
```

Notes:
- `-f` forces dexopt even when the new filter isn’t “better” than what’s already installed.
- If you pass `-m ...`, modern ART Service also treats it as “force the compiler filter” (i.e.
  don’t silently downgrade because profiles are missing).
  - Practical implication: if you ask for `-m speed-profile`, you’re less likely to get silently
    downgraded to `verify` just because the device thinks profiles are “not ready”.

### Option A3 (aggressive): reset the package’s dexopt state

This is useful when you want a “clean reinstall-like” state without actually reinstalling:

```bash
adb shell pm compile --reset "$PKG"
```

Behavior:
- Clears current profiles + reference profiles + dexopt artifacts (including cloud/SDM artifacts).
- If an external profile exists, ART may recreate reference profiles + regenerate artifacts.
- Ignores all other compile flags when `--reset` is set.

## B) Switch compiler filters to force different outputs

Common filters:
- `speed` (max AOT; slowest compile, fastest runtime)
- `speed-profile` (profile guided)
- `verify` (guaranteed: no compiled code in artifacts)

Examples:

```bash
adb shell pm compile --full -f -m verify "$PKG"
adb shell pm compile --full -f -m speed-profile "$PKG"
adb shell pm compile --full -f -m speed "$PKG"
```

Useful add-on:

```bash
adb shell pm compile --full -f -m speed-profile --force-merge-profile "$PKG"
```

This forces profile merge even if the delta is small, which is handy when you want repeated,
consistent “profile-based” compiles during experiments.

## Scope control (primary vs secondary vs split-only)

By default, if you don’t pass any scope flags, ART Service behaves like:
- `--primary-dex --include-dependencies`

Explicitly set scope for reproducibility:

```bash
# Primary + secondary + deps (stable baseline)
adb shell pm compile --full -f -m speed-profile "$PKG"

# Only primary dex (APK splits)
adb shell pm compile --primary-dex -f -m speed-profile "$PKG"

# Only secondary dex (only has effect if the app generated secondary dex artifacts)
adb shell pm compile --secondary-dex -f -m speed-profile "$PKG"
```

Split-only runs (base APK only):

```bash
adb shell pm compile --split \"\" -f -m speed-profile "$PKG"
```

## C) Profile control: reset / snapshot / dump

### C1) Clear locally collected profiles (keep external/cloud)

```bash
adb shell pm art clear-app-profiles "$PKG"
```

### C2) Snapshot or dump profiles (writes under `/data/misc/profman`)

```bash
adb shell pm snapshot-profile "$PKG"
adb shell pm dump-profiles "$PKG"
adb shell pm dump-profiles --dump-classes-and-methods "$PKG"
```

Treat `pm dump-profiles --dump-classes-and-methods` as a hint, not a proof of artifact membership.
For CNFE/CNDFE triage, verify the generated oat/odex directly with `oatdump --list-classes` against
the current APK + oat pair.

Split-only profile operations:

```bash
adb shell pm snapshot-profile --split split_config.arm64_v8a "$PKG"
```

Practical caveat: pulling `/data/misc/profman` to the host usually requires **root**
(`adb root` on `userdebug/eng`, or `su` on Magisk), because `adb pull` runs as the **shell**
user on most devices.

## D) Trigger background dexopt work

### D1) Start a real background dexopt job now

```bash
adb shell pm bg-dexopt-job
```

Control scheduled behavior:

```bash
adb shell pm bg-dexopt-job --cancel
adb shell pm bg-dexopt-job --disable
adb shell pm bg-dexopt-job --enable
```

### D2) Run batch dexopt for a reason (ART Service)

This is not identical to the “real” background job, but is useful for experiments:

```bash
adb shell pm art dexopt-packages -r bg-dexopt
```

Single-package “bg-dexopt style” compile (often preferable for experiments):

```bash
adb shell pm compile -r bg-dexopt -f -m speed-profile "$PKG"
```

## Verification: confirm what changed

Dump current dexopt state (best first check):

```bash
adb shell pm art dump "$PKG"
```

Treat `pm art dump` as the **source of truth** for the effective result:

- requested filter is just intent
- effective `[status=...] [reason=...]` is what ART actually kept
- if your workflow expects invariants, persist both the requested values and the effective values
  into an experiment manifest

For snapshot-based workflows, prefer these stable anchors over whole-file hashes:

- effective filter / reason from `pm art dump`
- `compiler-filter`, `bootclasspath-checksums`, class-loader-context, ISA/features from `oatdump --header-only`
- VDEX structural fields such as magic/version/section counts/checksum-entry-count/embedded-dex-count

For CNFE-focused verification, add one more check:

- extract suspect classes from crash/logcat lines, then probe the current oat/odex with
  `oatdump --list-classes --dex-file <apk> --class-filter <class> --require-match`
  to separate “class is present in the APK but absent from the compiled artifact” from generic
  packaging/profile noise

Whole-file hash remains useful as a raw artifact fingerprint, but it is **not** the primary
correctness criterion for these experiments because volatile metadata can change while stable
anchors remain healthy.

If you have root, also validate file timestamps / sizes:

```bash
adb shell su -c "ls -la /data/app/*${PKG}*/oat/* 2>/dev/null | head"
```

## Repro guidance for "post-install second rewrite" bugs

If your bug usually appears **after install**, but **not** when you reinstall the APK repeatedly
back-to-back, prefer this shape:

1. install once so the package has an initial valid artifact set
2. keep the existing artifacts in place
3. force another compile by **switching compiler filters**
4. relaunch the app repeatedly **while the compile is running**
5. after compile returns, do **force-stop driven cold starts** at `T+0s`, `T+10s`, `T+30s`

Why this often matches reality better:

- repeated reinstall tends to produce a fresh, clean artifact set each time
- many field failures show up on the **second rewrite / replacement** window, not on the initial
  install
- toggling `speed-profile` <-> `speed` (or `verify` <-> `speed`) keeps the package in a
  "replace existing oat/vdex" regime that is closer to post-install background dexopt behavior
- if the bug depends on a **new process** consuming newly-committed artifacts, force-stop before
  each launch and keep a delayed post-compile cold-start sequence; repeated launcher taps alone
  can collapse into warm/singleTop behavior and under-stress class loading

## Cross-device fairness note: reinstall is a reset of old artifacts, not a byte-identity guarantee

For cross-device comparisons, `adb uninstall <pkg>` followed by reinstalling the same APK is a good
way to discard the **previous installation instance** and its `/data/app/.../oat/...` artifacts.
However, it does **not** guarantee that two devices will regenerate byte-identical `base.odex` or
`base.vdex`.

What reinstall is useful for:
- clearing the previous randomized install directory under `/data/app/~~.../`
- forcing a new install-time dexopt pass
- often normalizing `pm art dump <pkg>` to an install-time state such as `status=verify` and
  `reason=install`

What reinstall does **not** guarantee:
- identical `.odex/.vdex` hashes across devices
- identical compiler output even when the APK bytes are identical
- identical post-install ART state after any background dexopt/profile activity

Practical interpretation:
- if two devices both show `reason=install` and `status=verify` right after reinstall, that means
  the old install state was cleared and both are back at an install-time baseline
- if the resulting `base.odex` / `base.vdex` hashes still differ, treat that as evidence that
  install-time ART output is device-state-dependent, not as proof that the APK payload differs

Same-device repeatability caveat:
- even on the **same device**, uninstall + reinstall of the same APK can still produce different
  `base.odex` / `base.vdex` hashes while `pm art dump <pkg>` continues to report the same
  high-level state such as `status=verify` and `reason=install`
- if that happens, the useful conclusion is not "the APK changed"; it is that byte-level oat/vdex
  output is not stable enough to treat reinstall as a deterministic hash reproducer by itself

Practical starter:

```bash
./scripts/adb_oat_rewrite_capture.sh \
  --serial <SERIAL> \
  --package <pkg.name> \
  --iters 2 \
  --filters speed-profile \
  --post-cold-start-delays 0,10,30
```

Add `--tracefs` only after this succeeds:

```bash
adb shell 'su -c id'
```

## Caveats (userdebug vs user)

- These commands require **shell** (ADB) or **root**. With USB debugging enabled, `adb shell`
  is “shell”, so `pm compile` / `pm delete-dexopt` are typically available on retail builds too.
- **Inspecting** and especially **pulling** internal artifacts and profile dumps often needs root:
  - Prefer the “root copy to `/data/local/tmp` then `adb pull`” pattern from
    `references/adb_execution_reference.md`.
- OEM builds may customize background dexopt policies (idle/charging thresholds, job scheduling).
