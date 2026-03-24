---
name: pixel6-vendorboot-magisk-root
description: "patch pixel 6 (oriole) android 16 aosp-built images for magisk root by injecting magisk into vendor_boot v4 main vendor ramdisk fragment 00 (fastboot syntax vendor_boot: with empty suffix). use when there is no init_boot and magisk-patching boot.img breaks boot, but your vendor_boot:dlkm workflow still boots. outputs vendor_ramdisk.magisk.lz4 and flash commands while preserving dlkm."
---

# Pixel 6 vendor_boot Magisk root

## Key invariant (do not generalize)

- **Target is `vendor_boot:` (colon with nothing after it)**.
  - In fastboot v4 vendor ramdisk fragments, `vendor_boot:foo` maps to a fragment named `ramdisk_<foo>`.
  - Therefore **`vendor_boot:` (empty `<foo>`) targets fragment name `ramdisk_`**, which is typically **index 00** / the platform vendor ramdisk on Pixel 6.
- **Do not touch the dlkm fragment**; keep using your existing workflow for modules:
  - `vendor_boot:dlkm` + `--dtb ...` + `initramfs.img`

## What this skill outputs

1) `vendor_ramdisk.magisk.lz4` (legacy lz4) that you flash via **`fastboot flash vendor_boot:`**
2) (optional) a text snippet of the exact fastboot commands for your case

## Inputs expected

- `vendor_boot.img` from your current AOSP build (or factory/OTA extraction)
- `magisk` artifacts directory containing:
  - `magiskboot` (host-runnable)
  - `magiskinit`
  - `magisk64.xz` (pixel 6 is arm64)
  - `init-ld.xz`
  - `stub.apk`
- host tools available:
  - `python3`
  - `lz4`
  - `xz`

If `unpack_bootimg.py` (AOSP host tool) is available, provide its path. Otherwise this skill will attempt a best-effort fallback.

## Workflow

### Step 1 — unpack vendor_boot to locate fragment 00

Run:

```bash
python scripts/unpack_vendor_boot.py \
  --vendor-boot /path/to/vendor_boot.img \
  --out out_vendor_boot \
  --unpack-tool /path/to/unpack_bootimg.py
```

This creates `out_vendor_boot/vendor-ramdisk-by-name/ramdisk_` → `vendor_ramdisk00` mapping.

### Step 2 — patch vendor_ramdisk00 with magisk and recompress

```bash
python scripts/patch_vendor_ramdisk00_magisk.py \
  --vendor-boot-out out_vendor_boot \
  --magisk-dir /path/to/magisk_files \
  --out out_magisk
```

Outputs:
- `out_magisk/vendor_ramdisk.magisk.lz4`

### Step 3 — flash

Minimum working sequence (keep your dlkm workflow):

```bash
# main vendor ramdisk (fragment 00)
fastboot flash vendor_boot: out_magisk/vendor_ramdisk.magisk.lz4

# dlkm fragment + dtb (your existing command)
fastboot flash --dtb out/DEVICE/dist/dtb.img vendor_boot:dlkm out/slider/dist/initramfs.img

fastboot reboot
```

## Troubleshooting

- If `vendor_boot:` flash fails: confirm you are using **platform-tools / fastboot new enough** to support vendor_boot fragment flashing.
- If the device boots but Magisk shows “not installed”: ensure Magisk app is installed, open it once, and confirm it detects the patched image.
- If you previously used `vendor_boot:default`: avoid it; it can rebuild the fragment table and drop `dlkm`.

## References

- `references/chat_excerpt_vendorboot_magisk.md` contains the exact proven commands and the `vendor_boot:` empty-suffix rule from your working log.
