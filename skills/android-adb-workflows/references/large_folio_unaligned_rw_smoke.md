# Large-folio encrypted file: unaligned R/W smoke test (Pixel + Magisk)

Use this when you need a quick sanity check that **large folio** + **(inline) encryption** is safe for:

- read-then-write (pagecache populated, readahead involved)
- unaligned overwrite (offset and length not 4K-aligned)
- unaligned append
- `sync` + `drop_caches` + readback verification
- optional reboot verification

## Run

1) Pick the device serial:

```bash
SERIAL="$FOLIO_S"   # or: adb devices
```

2) Run write-phase (creates the file if missing, then does the R/W patterns + verify):

```bash
~/.agents/skills/android-adb-workflows/scripts/lf_unaligned_rw_smoke.sh \
  --serial "$SERIAL" \
  --phase write
```

3) Reboot once:

```bash
adb -s "$SERIAL" reboot
adb -s "$SERIAL" wait-for-device
adb -s "$SERIAL" shell 'until [ "$(getprop sys.boot_completed)" = "1" ]; do sleep 1; done'
```

4) Run verify-phase (drop_caches + verify the two modified regions):

```bash
~/.agents/skills/android-adb-workflows/scripts/lf_unaligned_rw_smoke.sh \
  --serial "$SERIAL" \
  --phase verify
```

## Notes

- Default directory is `/data/media/0/Download` and file is `lf.c`.
- It stores small payload/meta helper files alongside the target as:
  - `/.lf.c.payload1.bin`, `/.lf.c.payload2.bin`, `/.lf.c.meta`
- If any mismatch happens, immediately capture kernel logs (`dmesg` / `logcat -b kernel`) around the time of the failure.

