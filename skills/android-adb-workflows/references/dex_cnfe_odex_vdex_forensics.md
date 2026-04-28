# Android CNFE/NCDFE odex-vdex artifact-first triage

Use this workflow when:

- the APK is already assumed intact
- the live question is whether the current odex/vdex artifacts still expose the missing class
- you need to distinguish `header breakage` vs `payload zeroing` vs `torn/partial rewrite`

This is the artifact-first path. If you still need to prove the APK contains the class, use `dex_classnotfound_storage_triage.md` first.

## One command

```bash
chmod +x scripts/adb_cnfe_odex_vdex_triage.sh
./scripts/adb_cnfe_odex_vdex_triage.sh \
  --serial <SERIAL> \
  --package com.google.android.permissioncontroller \
  --class 'com.google.protobuf.ManifestSchemaFactory$1' \
  --class 'androidx.collection.ArraySet'
```

Outputs land under:

- `meta.txt`: resolved package, apk, oat, vdex paths
- `pm_art_dump.txt`: live ART source-of-truth for current artifact location/filter
- `oat.header.txt`: explicit `oatdump --header-only`
- `probe.<class>.txt`: per-class `oatdump --list-classes` output
- `probe.<class>.rc`: `0` means the class probe matched
- `live.odex`, `live.vdex`: pulled current binaries
- `live.vdex.json`: strict `vdexdump_min.py --json --strict`
- `zero_scan.txt`, `zero_scan.json`: max zero-run and full-zero-page scan
- `*.head.hex.txt`, `*.max_zero_run.hex.txt`: xxd snapshots for direct byte inspection
- `summary.txt`: compact decision anchors

## Read order

1. `pm_art_dump.txt`
2. `oat.header.txt`
3. `probe.<class>.rc` and `probe.<class>.txt`
4. `live.vdex.json`
5. `zero_scan.txt`
6. `live.odex.max_zero_run.hex.txt` and `live.vdex.max_zero_run.hex.txt`

## Decision matrix

### 1) Header problem

Strong signals:

- `oat.header.rc != 0`
- `live.vdex.rc != 0`
- `live.vdex.json` shows bad magic, impossible section counts, or section offsets beyond EOF
- `*.head.hex.txt` already looks zeroed or obviously garbage

Interpretation:

- the artifact is already broken at the file header / top-level section-table level
- this is not the “class is present but some later page was damaged” pattern

### 2) Payload zeroing / zero-page damage

Strong signals:

- `oat.header.rc == 0`
- `live.vdex.rc == 0` or at least the top-level structure is sane
- `probe.<class>.rc != 0` for the crash-signature class or some related probe becomes unstable
- `zero_scan.txt` reports one or more full zero pages, or a large zero run in the middle of the file
- `*.max_zero_run.hex.txt` shows a long contiguous `00` region

Interpretation:

- this is the strongest evidence for “some current page content turned into zeros”
- page-aligned zero pages are much stronger than a short trailing zero tail

Important caveat:

- a zero run by itself is not enough; ART artifacts can contain some benign zero regions
- treat it as actionable when it is page-sized, in the middle of the file, or correlates with a class probe failure

Global diff heuristic:

- If bad and good artifacts have the same file size, differ in only a small number of 4KB pages, and each bad page differs across almost the entire page while neighboring pages remain byte-identical, that argues against a whole-file shift or general recompilation drift.
- If those bad 4KB pages also fail to match any other 4KB page in the good artifact, the pattern is more consistent with localized page corruption than with a simple page-copy mixup from another offset.

## Executable-page entropy prefilter

Use this when the suspect file is a compiled ART artifact and you do **not** yet have a trustworthy peer file to diff against.

## Workflow Contract

### Main Workflow
1. Detect the container first with `file` and `readelf -W -S`.
2. If the file is ELF and has one or more executable (`AX`) sections, scan every 4KB page in those sections for cheap byte metrics first: entropy, zero-byte count, and longest zero run.
3. Sort by entropy descending and only run AArch64 disassembly on the top outlier pages plus any immediate neighbors or crash-page candidates.
4. Report whether the top outliers cluster in one region and whether they also look abnormal under disassembly.

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Preflight | `file` says ELF aarch64 and `readelf` shows non-zero `AX` section(s) | Run executable-page entropy scan before asking for a good peer file | Top outlier pages are produced with concrete offsets and section names | If the file is too large to disassemble broadly, keep disassembly limited to the top entropy pages only | branch |
| Preflight | ELF file has zero-sized `.text` or no executable section at all | Do **not** force AArch64 code-page heuristics onto it | `readelf` confirms `.text` size `0` or no `AX` section | Fall back to DEX/VDEX or section-layout analysis | replace |
| Analysis | Top entropy pages cluster and also show AArch64 anomalies such as very high `.inst` / `undefined` counts or `objdump` abort on a page that should be code | Treat that as a strong no-peer signal for executable page corruption | Neighboring normal code pages in the same file keep much lower entropy and mostly decode into common mnemonics | Escalate to peer diff or live swap only after recording the outlier offsets | branch |
| Analysis | Top entropy executable pages stay in the normal code range for that file and disassembly remains mostly common mnemonics with low `.inst` counts | Treat this as **not matching** the known SystemUI bad-code-page pattern | The top pages do not cluster into a narrow abnormal band | Switch to class-layout / dexdump / VDEX hypotheses instead of blaming executable page garbage | replace |

### Output Contract
- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

Practical note:

- In one confirmed SystemUI case, the corrupted executable pages stood out immediately under this prefilter: top `.text` pages were near entropy `7.95`, clustered into a few offsets, had hundreds of `.inst` / `undefined` lines, and some pages made `aarch64-linux-gnu-objdump` abort. By contrast, a current Wellbeing live artifact on the comparison device topped out around entropy `6.34` with low `.inst` counts and normal common-mnemonic density. Treat these numbers as an example of separation, not as a universal hard threshold.

### 3) Torn write / partial rewrite

Strong signals:

- header still parses
- the file size still looks plausible
- class probes fail or become inconsistent
- the bad region is not a clean all-zero hole
- hex around the suspect region looks like mixed old/new structured data, not just zeros

Interpretation:

- this is more consistent with “rewrite got interrupted or readers observed a partially updated artifact”
- use a known-good snapshot for `cmp -l` when available; a bounded mixed-content frontier is stronger than a plain zero page

## Practical notes

- For system or APEX apps, `pm art dump` is the safest source-of-truth for the current odex path.
- In this workflow, `old dex` / `new dex` means the dalvik-cache live compiled artifact named `...@classes.dex`, not the APK-embedded `classes.dex`. For APK-backed app artifacts on modern Android, the file can still be named `classes.dex` even though it is playing the odex-side role in the compiled artifact set.
- A `.vdex` with `dex_section.exists=false` is still useful: it proves top-level VDEX structure and verifier/type-lookup sections are intact, even though it does not embed dex bytes.
- `oatdump --list-classes` is more reliable than raw `strings` for class presence. Treat raw `strings` hits as supporting evidence only.
- If the current package is known to be crash-looping in a fresh compile window, pair this artifact-first workflow with the rewrite-window capture flow from `dexopt_oat_vdex_regen.md`.
- For APEX packages backed by dalvik-cache, temporarily renaming the live `classes.dex` / `.vdex` pair aside can make `pm art dump` fall back to `status=run-from-apk`, `reason=unknown`, `location is error`. If the package then launches cleanly from the same APK, that is strong evidence that the removed compiled artifacts were implicated.

## Drop-cache differential experiment

Use this only as a differential probe when the user wants to separate:

- "runtime only poisoned by current page cache state"
- "persistent backing artifact is bad"

Recommended one-shot sequence:

1. Reproduce the current crash and save `logcat`.
2. Save `pm art dump` so the live odex/vdex path is frozen for this round.
3. `adb shell su -c 'sh -c "sync; echo 3 > /proc/sys/vm/drop_caches"'`
4. Force-stop the package.
5. Launch the same entry point again.
6. Save the second `logcat`.
7. Immediately probe the same live odex with `oatdump --list-classes --class-filter ... --require-match`.

Interpretation rules:

- If `drop_caches` makes the crash disappear, that is strong evidence that the previous failure depended on page-cache state. It still does not prove the backing file is good forever; it only proves the failure was not stable across one forced cache eviction.
- If `drop_caches` does not change the crash, that only proves the failure survives one cache eviction. By itself, this is not enough to claim "persistent on-disk bytes are definitely bad".
- If `drop_caches` does not change the crash and `oatdump` still finds the same class from the current live odex, the state is "runtime CNFE persists while static class visibility still exists". Do not collapse this into a simple "disk bad" verdict.
- If `drop_caches` does not change the crash and class probes fail against the same live artifact, persistent artifact damage becomes a much stronger hypothesis.

Important caveat:

- `drop_caches` only evicts page cache. The next reader can repopulate cache from backing storage, and `oatdump` is a structured parser rather than the ART runtime class loader. Therefore "runtime still crashes after drop" and "oatdump still lists the class after drop" can coexist on the same device and must be reported as a divergence, not forced into a page-cache-only or disk-only binary.

## Tail-page mapped-vs-hole differential

Use this when a suspicious live `.vdex` or `.odex` already shows a final file-valid partial page that reads as all-zero, and you need to know whether the reader is:

- reading a mapped on-disk block whose bytes are zero, or
- hitting an unmapped hole and taking the kernel zero-fill branch

Recommended sequence:

1. Freeze the live artifact path, size, inode, and final page index from `pm art dump` plus `stat`.
2. Narrow F2FS klog to that inode and index range.
3. `adb shell su -c 'sh -c "sync; echo 3 > /proc/sys/vm/drop_caches"'`
4. Use a cold buffered read such as `dd if=<live.vdex> bs=4096 skip=<last_idx> count=1 of=/dev/null`.
5. Inspect F2FS read-side logs for the same inode/index.

Interpretation rules:

- If the log shows `stage=map_result ... map_state=mapped_blk`, the read path saw a mapped data block. In that case, a zero page is much more consistent with mapped content reading back as zeros than with read-side hole zero-fill.
- If the log shows `stage=read_zero_fill` and the preceding `stage=map_result` says `map_state=unmapped_hole_window`, the reader did not get a mapped block for that page; the zeros came from the read-side hole path.
- For `F2FS_GET_BLOCK_DEFAULT`, a hole/default read can still hide two different raw states: `NULL_ADDR` or `NEW_ADDR`. Add focused `f2fs_map_blocks` logging of the raw `blkaddr` before the default-hole `goto sync_out` if you need to separate:
  - `raw_blkaddr=NULL_ADDR`: the tail page was still a plain hole
  - `raw_blkaddr=NEW_ADDR`: the tail page had reached delayed-allocation / reserved state but was still unmapped for the read
- If the read falls into `f2fs_map_no_dnode()`, report that separately. That means the node lookup itself hit `-ENOENT`; do not collapse that into `NULL_ADDR`.

Practical note:

- `dd` is sufficient for this differential because the goal is not to mimic ART's exact `mmap` fault stack; the goal is to cold-read the same cache-miss page and force the filesystem read path to reveal whether the page is mapped or hole-backed.

## Artifact isolation differential

Use this when the user wants a stronger experiment than `drop_caches` and accepts a short live-artifact rename window:

1. Freeze the current live artifact path with `pm art dump`.
2. Pull the current live `classes.dex` / `.vdex` and record hashes before touching anything.
3. Temporarily rename the live pair to reversible backup names in the same directory.
4. Re-check `pm art dump`.
5. Start the same entry point again.

Interpretation rules:

- If `pm art dump` falls to `status=run-from-apk` and the package launches cleanly, the removed compiled artifacts were implicated.
- If the package still crashes in `run-from-apk`, the failure is no longer specific to the removed compiled artifacts.
- If an explicit `pm compile -f -r cmdline -m speed-profile` then regenerates a new live pair that also launches cleanly, keep both old and new files for byte/hash comparison instead of restoring immediately.

Important caveat:

- Treat a **partial isolation** (`classes.dex` moved aside but `.vdex` still live, or the reverse) as inconclusive. In that state, `pm art dump` can pivot to another status such as `verify`/`vdex`, while the process still crash-loops and fresh tombstones can still symbolize frames as `/data/dalvik-cache/...@classes.dex`.
- Do not use a dex-only rename failure to clear the dex side or to blame `/apex` / source APK. First restore a consistent state, then rerun the intended pair-isolation workflow so the conclusion is based on `old+old` versus `none+none`, not on a mixed live pair.

### Fast-path culprit swap when the counterpart already matches

Use this shortcut only when one side is already de-risked by direct byte identity across devices, for example:

- bad-device `classes.vdex` hash matches the good device
- `/apex/.../libc.so` hash matches the good device
- only bad-device `classes.dex` differs

Recommended sequence:

1. Pull the good-device copy of the primary suspect file.
2. Keep the matching counterpart file in place on the bad device.
3. Stop framework/services before replacing the suspect file at the exact live path.
4. Start framework/services again.
5. Immediately re-hash the live file on the bad device to prove the replacement survived startup.
6. Re-check `pm art dump`, `pidof`, and the crash signature.

Interpretation rules:

- If the live hash on the bad device now matches the good device and the target process becomes stable, treat that replaced file as the primary suspect.
- If the target process still crashes with the same signature after the live hash is confirmed to match the good device, the replaced file is not sufficient to explain the failure; escalate to the full mixed-pair matrix or upstream source/APEX inputs.
- If startup silently regenerates or overwrites the replaced file before you can confirm the live hash, the experiment is inconclusive. Fix the workflow first; do not claim the file was exonerated.

## Mixed old/new pair differential

Use this only after both baselines are already known:

- old + old reproduces the same crash signature
- new + new does not reproduce that crash signature

Then test the two mixed pairs separately:

1. old dex + new vdex
2. new dex + old vdex

Interpretation rules:

- If only `old dex + new vdex` reproduces the same crash signature, the old dex side is the primary suspect.
- If only `new dex + old vdex` reproduces the same crash signature, the old vdex side is the primary suspect.
- If both mixed pairs reproduce the same crash signature, treat that as cross-generation pair inconsistency or non-atomic artifact commit, not proof that either file alone is corrupt.
- If neither mixed pair reproduces the same crash signature, the failure likely depends on the matched old+old pair as a unit, or on another co-generated artifact/state not yet swapped.

Important caveat:

- Only treat a mixed-pair result as diagnostic when it reproduces the same crash signature. If the mixed pair crashes differently, report that as a mismatch-induced side effect rather than as a root-cause confirmation for the original failure.

## Old/new VDEX structural diff follow-up

Use this after mixed-pair testing already points to the VDEX side as the primary suspect.

Recommended sequence:

1. Run `vdexdump_min.py --json --strict` on both old and new VDEX files.
2. Confirm whether size, magic, version, section count, section offsets, and checksum-section entries still match.
3. Count byte diffs per section:
   - `checksum`
   - `verifier_deps`
   - `type_lookup_table`
4. Classify each differing byte as:
   - `0 -> non0`
   - `non0 -> 0`
   - `non0 -> non0`
5. If `verifier_deps` is implicated, compare printable string sets before assuming corruption. Local reordering can happen without adding or removing descriptors.
6. If `type_lookup_table` is implicated, inspect the specific page or tail region with `xxd` and look for structured slots that became all-zero on the bad side.

Interpretation rules:

- If header, section layout, and checksum-section entries are identical, this is not a top-level VDEX header failure.
- If `verifier_deps` differences keep the same printable string set and mostly look like local reordering, treat that as weakly diagnostic by itself.
- If `type_lookup_table` differences are dominated by `0 -> non0` when comparing bad-old to good-new, especially in a bounded region, that is stronger evidence that the bad VDEX lost lookup-table payload rather than merely being recompiled with a different ordering.
- If the bounded region is a final partial page instead of a whole interior page, report that precisely. Do not overstate it as a full-page zeroing event.

## Kernel-side mmap interpretation caveats

Use this when reviewing a hypothesis that a bad VDEX came from the mmap write path rather than from buffered `write(2)`.

Important rules:

- ART VDEX payload writes can go through `MAP_SHARED` mappings plus `memcpy()` and later `msync()`, but that does **not** mean "all persistence logic is only `page_mkwrite`". The first store to a read-only mapped page faults through `page_mkwrite`; later persistence still depends on dirty pagecache and writeback.
- For current F2FS large-folio `f2fs_vm_page_mkwrite()`, the explicit zeroing step is for the part of the folio **after EOF** (`folio_zero_segment(offset_in_folio(isize), folio_size)`), not for the in-file valid bytes before EOF.
- Therefore, "the last in-file partial block ended up all zero" is **not** directly explained by the obvious `folio_zero_segment()` call alone.
- A one-shot failure on the first write fault is still a plausible trigger model, because `page_mkwrite` runs when a previously read-only PTE is about to become writable. If setup is wrong on that first fault, later stores to the same page in the same mapping may not re-enter `page_mkwrite`.
- When ART has already set the target VDEX length before mapping and copying the payload, a hypothesis that `page_mkwrite` observed a too-small final EOF because userspace extended the file only later becomes much weaker. Check the exact writer branch first.
