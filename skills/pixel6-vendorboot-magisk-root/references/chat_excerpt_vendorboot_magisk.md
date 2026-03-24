# Chat excerpt: magisk patch vendor_boot main fragment (pixel 6)

This excerpt is copied from the user's working log (fastboot export). It captures the exact invariants and commands that succeeded.

## 1) Magisk ramdisk injection commands (boot_patch.sh style)

```bash
./magiskboot cpio vr.cpio \
  "add 0750 init magiskinit" \
  "mkdir 0750 overlay.d" \
  "mkdir 0750 overlay.d/sbin" \
  "add 0644 overlay.d/sbin/magisk.xz magisk.xz" \
  "add 0644 overlay.d/sbin/stub.xz stub.xz" \
  "add 0644 overlay.d/sbin/init-ld.xz init-ld.xz" \
  "patch" \
  "backup vr.cpio.orig" \
  "mkdir 000 .backup" \
  "add 000 .backup/.magisk config"
```

Config minimal values used:

```text
KEEPVERITY=true
KEEPFORCEENCRYPT=true
RECOVERYMODE=false
```

## 2) Recompress as legacy lz4

```bash
lz4 -l -c vr.cpio > vendor_ramdisk.magisk.lz4
```

## 3) Critical fastboot rule: vendor_boot: (empty suffix) = fragment 00

- `vendor_boot:foo` targets fragment name `ramdisk_<foo>`.
- Therefore **`vendor_boot:` (nothing after the colon) targets `ramdisk_`**, which maps to **vendor_ramdisk00 (index 00)**.

Working flash command:

```bash
# note: nothing after ':'
fastboot flash vendor_boot: vendor_ramdisk.magisk.lz4
```

Keep your DLKM workflow unchanged:

```bash
fastboot flash --dtb out/slider/dist/dtb.img vendor_boot:dlkm out/slider/dist/initramfs.img
```

