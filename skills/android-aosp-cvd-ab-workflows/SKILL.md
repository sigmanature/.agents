---
name: android-aosp-cvd-ab-workflows
description: Diagnose AOSP/Android platform build failures and run Cuttlefish ART/runtime A/B workflows, including Soong/Siso/nsjail triage, incremental ART/Bionic APEX rebuilds, custom kernel/DLKM image pairing, permissive CVD launch, manual ADB proxy fallback, and 16KB-alignment A/B image validation.
---

# Android AOSP CVD A/B Workflows

Use this skill for Android platform build failures and for Cuttlefish experiments that swap ART/Bionic runtime payloads. It merges the former `android-aosp-build` and `aosp-incremental-replace-libs` workflows.

## Start Gate

- Governing target: this skill owns AOSP build triage, runtime/APEX replacement, custom CVD kernel image pairing, and ART/runtime A/B testing.
- Default safety: do not edit `Android.bp`, product makefiles, product properties, or `system/sepolicy` merely to validate CVD boot; those can trigger Soong graph analysis.
- Prefer no-Soong validation for CVD image/SELinux/ADB questions. Run `m` only when intentionally rebuilding the Android output.
- Use `rg`/`rg --files` for discovery in large Android trees.

## Workflow Contract

### Highest-Priority Gate

If the only changed artifact is the CVD kernel `bzImage`, launch CVD with
`--kernel_path=$KERNEL_DIST/bzImage` before considering any image composition,
runtime/APEX repack, or run-dir sync. This is the default kernel iteration path
because it preserves userdata overlays and avoids invalidating installed-app
state. Recompose Android images only when ART/Bionic/Scudo/linker/APEX payloads
changed, or when kernel logs prove a guest-loaded module/DLKM image mismatch.

### Main Workflow
1. Classify the task: build failure, runtime/APEX rebuild, CVD custom-kernel launch, or A/B image comparison.
2. For build failures, identify the first failed Siso/Soong/Ninja action before rerunning a broad build.
3. For kernel-only CVD iteration, use the Highest-Priority Gate: direct launch with `--kernel_path=$KERNEL_DIST/bzImage`; do not compose/sync A/B images unless module or DLKM evidence requires it.
4. For runtime changes, rebuild the narrow APEX target, then explicitly repack `system.img` and `super.img` when needed.
5. For custom-kernel CVD with changed guest-loaded modules, verify `boot.img`, `vendor_boot.img`, embedded `system_dlkm_a`/`vendor_dlkm_a`, and matching DLKM vbmeta before diagnosing userspace.
6. For Android17 ashmem policy blocks, use the paired run-dir permissive launch as the temporary experiment path.
7. For A/B experiments, keep the custom kernel/DLKM layer identical and vary only `com.android.runtime.apex` and `com.android.art.apex`.
8. Before booting a hand-composed A/B image, validate the final `system.img` EROFS layout exposes `/system/bin/init` and `/system/apex/*.apex` directly, not under `/system/system/`.
9. Verify with manifest hashes, boot-complete evidence, and either normal CVD ADB or the bundled manual ADB proxy.
10. Report phase reached, decision path, verification evidence, fallback, unresolved blocker, and next step.

### Build Failure Triage

1. Read `out/siso_failed_commands.sh`, `out/siso_output`, or `out/siso.INFO` before rerunning the full build.
2. Classify the failure domain: target compilation, host tool invocation, Soong/Siso orchestration, sandbox startup, or environment limits.
3. Reproduce the smallest failing host-side command when safe.
4. Fix host configuration first for environment failures; edit source/blueprint only when the failure is repository logic.
5. Validate with the smallest failed target, then resume the original build.

For nsjail `MS_PRIVATE` failures, read `references/nsjail-permission-denied.md`.

### Runtime/APEX Replacement

Critical chain:

```text
m com.android.runtime or m com.android.art
m systemimage
m superimage
```

`systemimage` does not automatically refresh `superimage`. If a run dir stores `super.img` as a raw regular file, convert the rebuilt sparse product `super.img` to the run-dir raw image before launch:

```bash
RUN_DIR=/path/to/cf_run
PRODUCT_OUT=out/target/product/<device>
mv -f "$RUN_DIR/super.img" "$RUN_DIR/super.img.prev-$(date +%Y%m%d-%H%M%S)"
out/host/linux-x86/bin/simg2img "$PRODUCT_OUT/super.img" "$RUN_DIR/super.img.new"
mv -f "$RUN_DIR/super.img.new" "$RUN_DIR/super.img"
```

Kernel-only edits are not runtime/APEX changes. Do not run this repack chain just because `bzImage` changed.

### Kernel-Only Direct Launch

For CVD kernel-only iteration, use `--kernel_path` first and preserve userdata overlays:

```bash
KERNEL_DIST=/home/nzzhao/learn_os/pixel/out/kernel_x86_64/dist
HOME=/home/nzzhao/cvd_homes/B \
RUN_DIR=/home/nzzhao/cf_runs/userdebug_B \
PORT=16522 CID=4 ADB_SERIAL=127.0.0.1:16522 \
MEMORY_MB=8192 RESUME=true ADB_READY_TIMEOUT_SEC=240 \
scripts/cvd_launch_with_adb_proxy.sh start -- \
  --base_instance_num=2 --num_instances=1 --vsock_guest_cid=4 \
  --kernel_path="$KERNEL_DIST/bzImage"
```

Validation evidence:

```bash
adb -s 127.0.0.1:16522 shell 'getprop sys.boot_completed; uname -a; cat /proc/cmdline'
rg -n '"kernel_path"|Loaded bzImage' \
  /home/nzzhao/cvd_homes/B/cuttlefish/instances/cvd-2/cuttlefish_config.json \
  /home/nzzhao/cvd_homes/B/cuttlefish/instances/cvd-2/logs/launcher.log
```

Only escalate to image composition when kernel logs show module or DLKM mismatch.

### Custom Kernel + DLKM Pairing

When launching Cuttlefish with a custom Kleaf kernel that changes guest-loaded modules, do not stop at replacing `boot.img` and `vendor_boot.img`:

- `system_dlkm` and `vendor_dlkm` are consumed from inside `super.img`; changing standalone `RUN_DIR/system_dlkm.img` or `RUN_DIR/vendor_dlkm.img` alone is not enough.
- Compose or rebuild `super.img` with matching `system_dlkm_a` and `vendor_dlkm_a` images.
- Regenerate/sync matching `vbmeta_system_dlkm.img` and `vbmeta_vendor_dlkm.img` from the exact DLKM images used in `super.img`.
- Verify kernel logs are clean of `.gnu.linkonce.this_module section size must match`, `dlkm_loader: Failed to insmod`, and `modules from vendor dlkm weren't loaded` before diagnosing later userspace failures.
- If the custom kernel dist lacks compatible `vendor_dlkm.img`, use a deliberate no-load vendor DLKM only as an isolation test, not a final image.

### Default Permissive CVD Launch

For current ART/Scudo/Bionic 16KB alignment experiments, after image pairing is clean, use the bundled idempotent launcher. It starts CVD if needed, applies the temporary Android17 permissive guest setting, preserves app/userdata state by default, and always connects the manual ADB proxy at `127.0.0.1:16520`:

```bash
scripts/cvd_launch_with_adb_proxy.sh start
adb -s 127.0.0.1:16520 shell 'getprop sys.boot_completed; id; cat /proc/meminfo | head -1'
```

Default wrapper settings are `RUN_DIR=/home/nzzhao/cf_runs/userdebug_test`, `RESUME=true`, `MEMORY_MB=8192`, `GUEST_ENFORCE_SECURITY=false`, and `PORT=16520`. This bypasses the Android17 `ashmem_libcutils_device` enforcing denial without editing sepolicy or triggering Soong. If this boots past `ApplicationSharedMemory`, the enforcing-mode failure is policy/memfd related rather than a kernel/DLKM image mismatch.

Use `RESUME=false` only for clean image/kernel/APEX validation where stale instance state must be discarded; for app workload testing, keep `RESUME=true` so installed apps remain in the writable instance state:

```bash
RESUME=false scripts/cvd_launch_with_adb_proxy.sh restart
RESUME=true scripts/cvd_launch_with_adb_proxy.sh start
```

### Run-Dir Cleanup

`super.img.prev-*` and ad-hoc names such as `super.img.before_art_stack` in the CVD run dir are stale raw super-image backups. They can consume many GB and are safe to delete when current `crosvm`/`run_cvd` only references `super.img` and the needed image manifests are preserved elsewhere.

```bash
RUN=/home/nzzhao/cf_runs/userdebug_test
find "$RUN" -maxdepth 1 -type f \( -name 'super.img.prev-*' -o -name 'super.img.before_art_stack' \) -print
find "$RUN" -maxdepth 1 -type f \( -name 'super.img.prev-*' -o -name 'super.img.before_art_stack' \) -delete
```

For `.worklog` disk pressure, stale failed A/B image directories and smoke outputs are safe to remove once the successful run has boot evidence and the current run-dir symlink targets are preserved. Before deleting, list run-dir links into `.worklog` and keep those exact target files:

```bash
find /home/nzzhao/cf_runs/userdebug_test -maxdepth 1 -type l -printf '%p -> %l\n' | rg '\.worklog'
```

Large regenerable intermediates such as `super.img.sparse`, hand-built `system.img`, APEX extraction caches, failed `cvd-ab-images/<timestamp>` directories, and `super.custom.sparse.img` are cleanup candidates. Keep current `vbmeta_system.img`, `vbmeta_*_dlkm.img`, and `vendor_dlkm.empty-load.erofs.img` if the run dir links to them.

### Manual ADB Proxy Fallback

The default launcher calls `scripts/cvd_manual_adb_proxy.sh connect` automatically. If you need to manage only the proxy, use the bundled idempotent proxy directly:

```bash
scripts/cvd_manual_adb_proxy.sh connect
adb -s 127.0.0.1:16520 shell 'getprop sys.boot_completed; id'
scripts/cvd_manual_adb_proxy.sh stop
```

Use the proxy when guest `adbd` is up but host `adb devices` omits the CVD. The known failure shape is an event-gated host `socket_vsock_proxy` waiting for kernel-log event `AdbdStarted`, keyed to exact string `init: starting service 'adbd'...`. Treat this as a host proxy workaround only; it does not change guest images or guest `adbd`.

### A/B Cuttlefish Image Comparison

For ART/Scudo/Bionic 16KB alignment experiments, treat the custom kernel/DLKM image set as the common lower layer and vary only the userspace ART/runtime payload:

- `A`: pristine no-16KB-alignment baseline artifacts from `/home/nzzhao/learn_os/android17/.worklog/pristine-A-base-20260709-162929` when present; the older HDD archive is legacy and must be marker-checked before use.
- `B`: current active Android output under `/home/nzzhao/learn_os/android17/out/target/product/vsoc_x86_64`, or stable B APEX payloads passed with `B_STABLE_DIR` when current `out` has been overwritten by pristine A.
- Common layer: same custom `boot.img`, `vendor_boot.img`, embedded `system_dlkm_a`/`vendor_dlkm_a`, and matching DLKM vbmeta.
- Variant layer: `com.android.runtime.apex` carrying Bionic `libc.so`, and `com.android.art.apex` carrying ART `libart.so`.

Do not compare A using an old baseline `super.img` directly against B when the kernel is custom. Old `super.img` can carry stock or stale DLKM partitions. Build or compose two final image sets where the kernel/DLKM layer is identical and only ART/runtime APEX differs.

Before trusting an archived A baseline, check marker strings and payload hashes; the July 2026 archive path can contain earlier instrumentation such as `[ZZHAO] __libc_init_common` even when it is named baseline. Treat any such archive as “old experimental A”, not a pristine no-change baseline, until `strings`/BuildID/hash evidence proves otherwise:

```bash
strings -a /path/to/libc.so.baseline | rg 'ZZHAO|16K|small_object'
readelf -n /path/to/libc.so.baseline | rg 'Build ID'
sha256sum /path/to/libc.so.baseline /path/to/libart.so.baseline
```

Run preflight without triggering Soong:

```bash
scripts/cvd_ab_preflight.sh
```

When validating hand-composed final images, point preflight at the image directories so it can reject root-layout mistakes before CVD boot:

```bash
A_IMAGE_DIR=/path/to/A B_IMAGE_DIR=/path/to/B scripts/cvd_ab_preflight.sh
```

To compose the current A/B image pair without triggering Soong, use the bundled script. It copies the official shared system-as-root tree, replaces only A's ART/runtime APEX files, builds `system.img` with the official `aosp_shared_system_image/android_common/prop` file, regenerates `vbmeta_system.img`, builds `super.img`, and runs preflight:

```bash
scripts/cvd_compose_ab_images.sh
```

After a successful compose, sync the final A/B images into the paired run dirs with the bundled sync helper instead of hand-editing symlinks. This preserves separate A/B userdata images, refreshes `super.img`/`vbmeta_system.img`/DLKM vbmeta links, and points `boot.img` at the current `KERNEL_DIST/boot.img` while preserving the template `vendor_boot.img` when the x86 kernel dist has no standalone vendor boot image:

```bash
IMAGE_WORK_DIR=/path/to/.worklog/cvd-ab-images/<timestamp>-A-vs-B \
KERNEL_DIST=/home/nzzhao/learn_os/pixel/out/kernel_x86_64/dist \
scripts/cvd_sync_ab_run_dirs.sh sync
```

When current `out` no longer represents B, pass the stable B checkpoint explicitly:

```bash
A_BASE=/home/nzzhao/learn_os/android17/.worklog/pristine-A-base-20260709-162929 \
B_STABLE_DIR=/home/nzzhao/learn_os/android17/.worklog/cvd-ab-images/20260709-124155-B-stable-before-pristine-A \
WORK_DIR=/home/nzzhao/learn_os/android17/.worklog/cvd-ab-images/$(date +%Y%m%d-%H%M%S)-pristine-A-vs-B-stable \
scripts/cvd_compose_ab_images.sh
```

The compose script must build final `A/super.img` and final `B/super.img` with the current common kernel/DLKM lower layer. When `B_STABLE_DIR` is set, treat it as the source of B ART/runtime APEX payloads only; rebuild `B/system.img`, `B/vbmeta_system.img`, and `B/super.img` instead of symlinking an old stable `super.img`. Do not leave B as an accidental symlink to product or stable `super.img` after a custom kernel rebuild; that silently compares a fresh A lower layer against stale B DLKM contents.

Do not build A's `system.img` with `prop_misc_info_pre_processing`; that file lacks the final `mount_point=/` and AVB tool fields. Using it can silently package the tree one level too high so `/system/bin/init` is absent while `/system/system/bin/init` exists.

Each A/B run must record:

```bash
sha256sum super.img boot.img vendor_boot.img vbmeta*.img > manifest.txt
sha256sum com.android.runtime.apex com.android.art.apex >> manifest.txt
adb -s 127.0.0.1:16520 shell 'getprop sys.boot_completed; getprop ro.build.fingerprint; getprop ro.serialno'
adb -s 127.0.0.1:16520 shell 'sha256sum /apex/com.android.runtime/lib64/bionic/libc.so /apex/com.android.art/lib64/libart.so'
rg -n 'FATAL EXCEPTION|Fatal signal|system_server.*died|SIGSEGV|abort message' <captured-cvd-logs>
```

Use APEX and final image hashes as primary identity. Guest-side `.so` hashes are secondary checks because archived standalone `.so` files may come from a different stripped/unstripped path than the mounted APEX payload.

### Multi-Instance ADB Abstraction

For paired A/B CVD app workloads, never make humans remember per-instance adb ports. Treat adb identity as a profile-level property:

```text
profile -> HOME -> cvd-N/cuttlefish_config.json -> adb_host_port + vsock_guest_cid -> adb serial/fallback proxy
```

Default policy:

1. Resolve `adb_host_port` and `vsock_guest_cid` from the profile's active `cuttlefish_config.json`.
2. Try the native CVD adb serial first, e.g. `127.0.0.1:<adb_host_port>`.
3. If the native port is not listening or `adb_connector` loops with `device '0.0.0.0:<port>' not found`, start a profile-scoped manual `socket_vsock_proxy` on a stable alias port.
4. For non-default instances such as `cvd-2`, account for host tools that call `config->ForDefaultInstance()` during logging. If manual proxy aborts while trying to open `<HOME>/cuttlefish/instances/cvd-1/logs/launcher.log`, create the compatibility log directory or use a wrapper that points logging at an existing instance path before starting the proxy.
5. Report only profile names (`A`, `B`) in higher-level scripts. Let helper functions return the actual adb serial.

Current experiment mapping:

| Profile | Instance | Native adb | Vsock cid | Stable fallback |
|---|---:|---|---:|---|
| `A` | `cvd-1` | `127.0.0.1:6520` or existing manual `127.0.0.1:16521` | 3 | `127.0.0.1:16521` |
| `B` | `cvd-2` | `127.0.0.1:6521` | 4 | `127.0.0.1:16522` |

Do not assume `cvd-2` behaves like `cvd-1`: the guest `adbd` can be healthy while the native host adb connector is unusable. In that case, trust the manual vsock proxy after `sys.boot_completed=1` and `adb -s <serial> shell id` succeed.

### Decision Table

| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Host preflight | `launch_cvd` fails before guest kernel, `kernel.log`/`logcat` empty, or `libminijail` reports `unshare(CLONE_NEWNS) failed: Operation not permitted` | Test `unshare -Ur -m true`; on Ubuntu/AppArmor hosts, temporarily run `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` for the experiment session | `unshare -Ur -m true` returns `0`, then `scripts/cvd_launch_with_adb_proxy.sh start` reaches `VIRTUAL_DEVICE_BOOT_COMPLETED` | If `crosvm` still GPFs in host `libc.so.6`, debug crosvm wrapper/`LD_PRELOAD=libdrm.so.2`; do not diagnose Android image first | block guest/kernel/userspace diagnosis until fixed |
| Build | `out/siso_failed_commands.sh` exists | Read it before rerunning the full build | Failed target and full command are identified | Fall back to `out/siso_output` and `out/siso.INFO` | continue |
| Sandbox | `nsjail` `MS_REC|MS_PRIVATE` permission denied | Run minimal `prebuilts/build-tools/linux-x86/bin/nsjail -q -- /bin/true` and inspect sysctls/AppArmor | Minimal command reproduces exit 255 and host state is recorded | If minimal nsjail passes, inspect generated genrule paths | branch |
| Runtime APEX | APEX changed but CVD still boots old library | Rebuild target, `systemimage`, and `superimage`; sync run-dir raw `super.img` | Product/run-dir hashes changed and guest `.so` hash matches | Inspect run-dir symlinks and stale raw `super.img` | retry |
| Custom kernel | Module ABI mismatch strings in kernel log | Fix embedded DLKM partitions and matching vbmeta before userspace diagnosis | Kernel log has zero mismatch strings | Use no-load vendor DLKM only as an isolation test | block later userspace diagnosis |
| Ashmem | `ApplicationSharedMemory.nativeCreate` fails after DLKM is clean | Relaunch same image set through `scripts/cvd_launch_with_adb_proxy.sh start` with `GUEST_ENFORCE_SECURITY=false` | Boot reaches `VIRTUAL_DEVICE_BOOT_COMPLETED` and `adb -s 127.0.0.1:16520 shell getprop sys.boot_completed` returns `1` | Inspect libcutils memfd/ashmem and image freshness | branch to memfd/policy fix |
| ADB | Guest `adbd started`, host CVD missing, or normal `adb devices` omits CVD after boot | Use `scripts/cvd_launch_with_adb_proxy.sh start` for launch, or `scripts/cvd_manual_adb_proxy.sh connect` for proxy-only repair | `adb -s 127.0.0.1:16520 shell 'getprop sys.boot_completed; id'` succeeds | Debug guest adbd/vsock/crosvm if manual proxy also fails | continue validation |
| A/B health | `launch_cvd` reports boot completed | Capture CVD logs and run fatal scan for app/native/system-server crashes | `sys.boot_completed=1`, ADB works, guest `.so` hashes are recorded, and fatal scan is summarized | If only non-critical app crashes occur, report boot success separately from health issues; if `system_server` dies, block A/B success | continue / branch |
| A/B health | Boot completes but fatal scan shows `klass pointer for obj`, `system_server`/zygote `SIGSEGV`, or ART GC verification frames | Pull `/data/tombstones`, save focused logcat excerpts, and classify the run as boot-complete but not normal/healthy | Tombstones contain `libart.so` GC/verification or class-linker frames and logcat shows whether `system_server` restarted | Do not claim A/B success from `sys.boot_completed=1` alone; compare against B only after this crash is understood or intentionally accepted as an A-only failure | block normal-boot claim |
| A/B health | A/B cells run on kernels with experimental order-2 UFFD mfill or COW fast paths | Keep both defaults off for A. Enable only B/full16K from early boot with `--extra_kernel_cmdline='uffd_mfill_order2=1 mthp_cow_order2=1'`, then verify `/proc/cmdline`, `/sys/kernel/debug/uffd_mfill_order2/stats`, and `/sys/module/kernel/parameters/mthp_cow_order2` | A shows UFFD `enabled 0` and `mthp_cow_order2=N`; B/full16K shows both enabled before app startup; all4K cells show both disabled | If either fast path was enabled only by post-boot sysfs/debugfs writes, treat startup attribution as contaminated and reboot with the intended cmdline | branch / block A-vs-B attribution |
| App workload | Installing apps into CVD for stress testing | Keep the same instance and relaunch future runs with `--resume=true`; do not delete `overlay.img` | `pm list packages -3` remains nonzero after restart | If changing system/kernel/APEX images, switch back to `--resume=false` and expect apps to be cleared | branch |
| App workload | Bulk `adb install` in daemon CVD mode | Disable platform verifier before install: `settings put global package_verifier_enable 0`, `settings put global verifier_verify_adb_installs 0`, `settings put global package_verifier_user_consent -1` | `settings get global` returns `0`, `0`, `-1` | If failures are `StorageManagerService.allocateBytes` NPE, `Can't find service: package`, or `Broken pipe`, reboot with `--resume=true` and rerun the idempotent installer; do not diagnose as Google Play Protect on AOSP CVD without GMS packages | continue / retry |
| App workload | `/data` fills during large APK clone | Stop CVD, grow `userdata.img`, run `resize.f2fs`, and relaunch; for very large userdata images, moving `userdata.img` to a large HDD and leaving a symlink is valid when `statfs` on the symlink reports the HDD filesystem | Guest `df -h /data` shows the new size and app install reaches the desired package count | If `launch_cvd` says not enough space for `userdata.img`, move the sparse userdata image to the large disk and keep the run-dir symlink; do not delete the current app overlay unless intentionally resetting | branch |
| A/B compose | Custom kernel was rebuilt | Re-run `scripts/cvd_compose_ab_images.sh`; it must create `A/super.img`, `B/super.img`, and common DLKM vbmeta from the same `KERNEL_DIST` | Preflight passes and manifests show both A/B `super.img` under the new compose dir | If B points to product `super.img`, fix the compose script before booting A/B | block A/B comparison |
| A/B run-dir sync | A/B compose succeeded and CVD run dirs must use the new image pair | Run `IMAGE_WORK_DIR=<compose-dir> scripts/cvd_sync_ab_run_dirs.sh sync`; do not manually edit only `super.img` | `run_dirs_manifest.txt` shows A/B `super.img`, `vbmeta_system.img`, `vbmeta_*_dlkm.img`, and `boot.img` resolve to the intended compose/kernel paths; A/B `userdata.img` resolve to different files | If `prepared_ok` fails, inspect `run_dirs_manifest.txt` and rerun after stopping stale CVD instances | continue to launch |
| CVD launch | App/THP workload should model a normal phone | Use `scripts/cvd_launch_with_adb_proxy.sh start`; its default `MEMORY_MB=8192` passes `--memory_mb=8192` | Guest `MemTotal` is about 8.1 GB through `adb -s 127.0.0.1:16520` | If launch fails from disk preflight, fix image storage first; do not drop memory silently | continue |
| CVD launch | Apps must survive across A/B or kernel/DLKM image rotation | Do not assume `RESUME=true` preserves apps when `super.img`, `vbmeta_system.img`, `boot.img`, or DLKM images change; Cuttlefish wipes the overlay when `--use_overlay=true` and `WillRebuildCompositeDisk()` is true, which happens on composite config mismatch, missing prior composite, or any component image mtime newer than `os_composite.img`; first record `cmd package list packages -3 | wc -l` and snapshot the run-dir/instance userdata state, then launch and recheck the package count | Package count and `/data/app` entries match before/after; launcher output lacks `base images have changed under the overlay, making the overlay incompatible. Wiping the overlay files.`; `os_composite_disk_config.txt` and component mtimes are stable | If count drops to zero, treat the app workload as lost and reinstall from APK corpus before app stress; do not continue app A/B metrics on an empty userdata | block app workload until reinstalled |
| CVD launch | `scripts/cvd_launch_with_adb_proxy.sh stop/status` reports the wrapper lock is active while CVD is merely running | Treat it as inherited lock-fd leakage from an older launcher invocation; use direct run-dir `ANDROID_HOST_OUT=$RUN_DIR ./bin/stop_cvd`, then relaunch with the fixed wrapper that closes fd 9 before `launch_cvd` | `fuser $STATE_DIR/lock` no longer shows `run_cvd`/`crosvm` after relaunch; wrapper `status` works | If old children still hold the lock, stop CVD directly and remove only the runtime lock after processes exit | replace wrapper stop for that one stale generation |
| Run-dir cleanup | Disk pressure and many `super.img.prev-*` files under `cf_runs/userdebug_test` | Verify current process uses `super.img`, then delete stale `super.img.prev-*` and `super.img.before_art_stack` | `du -h /home/nzzhao/cf_runs` drops and current CVD still uses `super.img` | Keep `userdata.img`, current `super.img`, boot/vendor_boot/vbmeta, and named custom-kernel artifacts unless explicitly rotating them | continue |
| Worklog cleanup | `.worklog` contains old failed A/B raw/sparse images or extraction caches | First list run-dir symlinks into `.worklog`; delete only unreferenced failed image dirs, smoke dirs, extraction caches, and regenerable `*.sparse.img`/temporary `system.img` | `missing_linked_targets=0` and `df`/`du` improve | Preserve current run evidence, manifests, and any target file referenced by run-dir symlinks | continue |
| AOSP tree migration | Moving the large Android checkout to a new disk to relieve root SSD pressure | Use ext4, not NTFS, and preserve the original absolute path by mounting the new filesystem at `/home/nzzhao/learn_os/android17` after rsync; if `sudo` is unavailable in the agent, `pkexec` can format/mount after all `/dev/sdXn` mountpoints are explicitly unmounted | `lsblk` shows ext4, `df -hT` has enough free space, write smoke succeeds, and `pwd -P` remains `/home/nzzhao/learn_os/android17` after final mount | If formatting says the device is in use, list `findmnt -rn -S /dev/sdXn` and unmount every target before retrying; keep the old tree as `android17.old` until build/CVD validation passes | branch to migration; do not run Soong just to validate the move |
| A/B | Multiple old B snapshots or stale baseline ambiguity | Keep only one archived A baseline; treat current out as B | `rg --files <archive>` shows only baseline root; preflight manifests pass | Restore historical B only from backup when explicitly needed | continue |
| A/B | Archived A baseline contains marker strings such as `[ZZHAO]` or unexpected 16KB instrumentation | Reclassify the archive as old experimental A; either rebuild a pristine A from clean source or create a new checkpoint with clearly named deltas | `strings`, BuildID, guest `sha256sum`, and input APEX manifests all match the intended variant | Do not attribute A crashes to kernel-only or arch mmap alignment until the userspace payload is proven clean | block pristine A/B claims |
| A/B image layout | CVD reboots after `init first stage started` and `erofs (device dm-9): mounted`, with empty logcat and no second-stage init lines | Inspect final `system.img` with `dump.erofs --path=/system/bin/init`, `/system/apex/com.android.runtime.apex`, and `/system/system/bin/init`; rerun `cvd_ab_preflight.sh` with `A_IMAGE_DIR`/`B_IMAGE_DIR` | `/system/bin/init` is executable, runtime APEX is under `/system/apex`, and `/system/system/bin/init` is absent | Rebuild/recompose with `scripts/cvd_compose_ab_images.sh` or manually use the shared system-as-root input plus `aosp_shared_system_image/android_common/prop`; never use `prop_misc_info_pre_processing` for final image composition | block boot diagnosis |
| A/B adb | Non-default instance such as `cvd-2` boots, but native adb port like `6521` refuses connections or `adb_connector` repeatedly logs `device '0.0.0.0:<port>' not found` | Resolve `vsock_guest_cid` from the profile config and start a manual `socket_vsock_proxy` on a stable alias port such as `16522`; if the proxy aborts on `<HOME>/cuttlefish/instances/cvd-1/logs/launcher.log`, create that compatibility log directory or use a wrapper that supplies an existing log path | `ss -ltnp` shows the alias port, `adb connect 127.0.0.1:<alias>` succeeds, `sys.boot_completed=1`, and `adb shell id` works | Keep the native CVD port as best-effort only; do not block app installation or tests on native adb if the manual proxy is healthy | branch to manual proxy fallback |
| A/B app preload | Installing apps from `package-size.tsv` advances only one package while source and target adb devices are online | Ensure every `adb shell`, `adb pull`, `adb install`, and package-count helper inside the `while read ... < <(sort manifest)` loop uses `</dev/null` so adb cannot consume the manifest stream | `success.tsv` grows beyond the first package and `cmd package list packages -3 | wc -l` rises toward the target | Restart only the preload worker; preserve userdata and skip already installed packages | replace install-loop implementation |
| Kernel-only CVD | Rebuilt CVD x86 `bzImage` from `/home/nzzhao/learn_os/pixel/out/kernel_x86_64/dist` and no module/DLKM mismatch is known | First verify the active profile uses `HOME=/home/nzzhao/cvd_homes/{A,B}` and that run-dir base images are not symlinked to fast-changing `$KERNEL_DIST/boot.img`; then launch with `RESUME=true` and the intended boot cmdline/kernel path | Launch log has no `base images have changed under the overlay`, package count is unchanged, `cuttlefish_config.json` records intended paths/cmdline, and guest reaches `sys.boot_completed=1` | If run-dir `boot.img` points at newly rebuilt `$KERNEL_DIST/boot.img` or `boot_repacked.img`/composite mtime changed, CVD can rebuild the OS composite and wipe overlays even with `RESUME=true`; pin stable base images or reinstall apps after marking the overlay invalid | replace naive `--kernel_path preserves overlay` assumption with preflight over HOME, symlinks, and composite mtime |
| Runtime/APEX | Changed ART, Bionic, Scudo, linker, or APEX payloads | Rebuild `com.android.runtime`/`com.android.art`, then run `m systemimage` and `m superimage`, compose/sync B images, and expect overlay invalidation if base images change | APEX manifests/hashes and guest libraries match the rebuilt product; boot reaches `sys.boot_completed=1` | If only kernel changed, stop and use Kernel-only CVD instead | block direct-kernel shortcut for userspace payload changes |
| ART CMC UFFD 16KB | B image with forced 16KB ART page size crashes during install/runtime with `UFFDIO_COPY` `EEXIST` near the faulting 16KB window | Treat this as kernel-page versus ART-page granularity mismatch first. Fix kernel order-2 `mfill` PTE_EXIST to fall back to order-0 and make ART `CopyIoctl()` account `EEXIST` at kernel-page granularity while returning only full ART-page multiples | Synthetic B installs all 60 APKs, no tombstones, and logs show `eexist-skip-kernel-page` progressing by 4KB without fatal | If still crashing, collect the exact 16KB window, PTE-present subpage trace, and ART `PageState` transition before changing linker/OAT hypotheses | branch before image/oat/linker diagnosis |
| Runtime linker 16KB force | Early boot reaches `apexd-bootstrap`, then `linkerconfig` or `prng_seeder` repeatedly logs `Fatal signal 11 (SIGSEGV), code 2 (SEGV_ACCERR)` | Treat this as a linker mapping/protection bug before ART diagnosis. Do not implement forced 16KB load alignment by setting `should_use_16kib_app_compat_`; keep force alignment on the normal loader's reservation/segment path instead | Kernel log has no `linkerconfig terminated by signal 11`/`prng_seeder` fatal lines, boot reaches `sys.boot_completed=1`, and guest hashes match rebuilt `/apex/com.android.runtime/bin/linker64` | If fatal remains, inspect phdr layout with `readelf -W -l` and compare `p_align`, RELRO, and RW segment boundaries before changing sepolicy | replace app-compat shortcut with normal-loader fix |
| Runtime linker padding | Boot succeeds only with `guest_enforce_security=false` and kernel/logcat show many `avc: denied { execmem } ... permissive=1` after forcing segment padding | Record the build as permissive-only for experiments; do not claim enforcing-mode boot until the mapping strategy avoids anonymous executable padding or policy is deliberately changed | `sys.boot_completed=1`, no `Fatal signal`, `rg 'avc:.*execmem'` count is recorded, and launch command includes `--guest_enforce_security=false` | For enforcing experiments, revert/replace executable anonymous padding before diagnosing unrelated ART/GC faults | branch validation result by SELinux mode |
| Runtime/APEX vbmeta | CVD bootloops before logcat; kernel reaches first-stage init, mounts `dm-9`, then logs `device-mapper: verity ... metadata block ... is corrupted` | Treat this as image/vbmeta descriptor skew, not ART/linker crash. Regenerate top-level `vbmeta.img` from exact vendor_boot/odm/vendor/odm_dlkm images and regenerate `vbmeta_system.img` from exact product/system/system_ext images used in the composed `super.img`; do not trust stale `PRODUCT_OUT/vbmeta*.img` after image rebuilds | `cvd_ab_preflight.sh` hashtree descriptor diffs are empty, `avbtool info_image` root digests match image footers, run-dir links include common `vbmeta.img`, and boot reaches logcat/ADB | Re-run compose after `m systemimage superimage`; if still failing, byte-compare each super partition against its source image and map the failing `254:N` mount against fstab order before ART diagnosis | replace stale-vbmeta reuse with forced common/B vbmeta regeneration |
| Runtime/APEX chained vbmeta | U-Boot logs `vbmeta_system_dlkm_a` or `vbmeta_vendor_dlkm_a` `OK_NOT_SIGNED`, then `Android boot failed, error -1` before the kernel starts | Sign every chained vbmeta image with the same AVB test key used by the chain descriptor; do not compose chained `vbmeta_*` with `--algorithm NONE` | `avbtool info_image` for `vbmeta.img`, `vbmeta_system.img`, `vbmeta_system_dlkm.img`, and `vbmeta_vendor_dlkm.img` shows `SHA256_RSA4096`, and U-Boot proceeds to `Loading kernel` | If using an intentionally unsigned image, remove it from top-level chain and verify fstab does not require it; otherwise treat unsigned chained vbmeta as a boot blocker | block CVD launch until chained vbmeta images are signed |
| Runtime/APEX vendor_boot | U-Boot prints top-level `Verification passed successfully` but then `Android boot failed, error -1` without `Loading kernel`, especially after replacing top-level `vbmeta.img` | Ensure the exact `vendor_boot.img` used by the run dir has an AVB hash footer and is included in top-level `vbmeta.img`; for padded custom vendor_boot images, compact trailing zero padding, add a hash footer with the 64MiB partition size, and sync run dirs to the generated common vendor_boot | `avbtool info_image common/vendor_boot.img` succeeds, top-level `vbmeta.img` includes the vendor_boot descriptor, and U-Boot logs `Loading kernel` | If vendor_boot cannot be footered, fall back to the previously bootable signed vendor_boot and keep `--kernel_path` for bzImage-only replacement | replace unsigned custom vendor_boot symlink during composed A/B runs |

### Output Contract

- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## Key Paths

| Item | Path |
|---|---|
| A baseline archive | `/media/nzzhao/bdb8bfc4-b802-4600-ad17-922826aef12d/android17-ab/aosp-lib-ab-baseline-20260706-210427` |
| Pristine A base | `/home/nzzhao/learn_os/android17/.worklog/pristine-A-base-20260709-162929` |
| Stable B checkpoint | `/home/nzzhao/learn_os/android17/.worklog/cvd-ab-images/20260709-124155-B-stable-before-pristine-A` |
| B product out | `/home/nzzhao/learn_os/android17/out/target/product/vsoc_x86_64` |
| CVD run dir | `/home/nzzhao/cf_runs/userdebug_test` |
| Manual ADB serial | `127.0.0.1:16520` |
| Runtime APEX | `out/target/product/vsoc_x86_64/system/apex/com.android.runtime.apex` |
| ART APEX | `out/target/product/vsoc_x86_64/system/apex/com.android.art.apex` |

## Caveats

- APEX payloads are EROFS-mounted; do not expect `adb remount` to replace mounted APEX libraries safely.
- Product/sepolicy edits can trigger Soong graph analysis; prefer permissive runtime validation while isolating ashmem/memfd root cause.
- `super.img` is the dynamic partition carrier; Cuttlefish does not use standalone `system.img` once booting from `super.img`.
