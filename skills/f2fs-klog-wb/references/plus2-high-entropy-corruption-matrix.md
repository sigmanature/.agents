# F2FS `+2` High-Entropy Corruption Matrix

## Scope

This reference tracks the investigation branch where preserved encrypted `/data`
artifacts show a local physical-block delta of `+2` together with high-entropy
garbage pages. The working example is the preserved Gmail VDEX sample from
device `18281FDF6007HB`.

The primary invariant is:

```text
effective_pblk = m_pblk + (index - m_lblk)
```

Do not reason from raw `m_pblk` alone.

## Locked Evidence

### Gmail VDEX Sample

Bad sample:

```text
device: 18281FDF6007HB
path: /data/dalvik-cache/arm64/product@app@PrebuiltGmail@PrebuiltGmail.apk@classes.vdex
inode: 24220
host blob: output/gmail_suspect_preserve_18281FDF6007HB_20260428_210916/classes.vdex/f2fs_preserve_18281FDF6007HB_20260428_210954/preserved_inode_24220.blob
```

Good reference:

```text
device: 21121FDF600C4G
path: /data/dalvik-cache/arm64/product@app@PrebuiltGmail@PrebuiltGmail.apk@classes.vdex
inode: 44639
host blob: output/gmail_good_reference_21121FDF600C4G_20260429_103509/preserved_inode_44639.blob
```

The two files are not byte-identical across the whole file, so the reference is
used for page-shape comparison rather than whole-file equality.

### Local Bad Region

For the bad sample:

```text
page 48 -> pblk 4536054, delta +1, low entropy
page 49 -> pblk 4536055, delta +1, low entropy
page 50 -> pblk 4536056, delta +1, low entropy
page 51 -> pblk 4536058, delta +2, high entropy
page 52 -> pblk 4536060, delta +2, high entropy
page 53 -> pblk 4536062, delta +2, high entropy
page 54 -> low entropy again
page 55 -> low entropy again
```

Good-reference page-shape comparison:

```text
idx  bad18_entropy  bad18_max%  good21_entropy  good21_max%
50   5.162939       30.3223     5.163443        30.3223
51   7.954389        0.7568     4.999581        31.4697
52   7.951349        0.7812     5.991616        16.4795
53   7.952927        0.7568     5.133372        22.3633
54   5.104066       25.7812     5.104066        25.7812
```

This makes pages `51..53` the current focused bad region.

## Raw Probe Results

### Hypothesis 1: Orphan Original Physical Block

Claim tested:

```text
The expected +1 physical block still contains the original ciphertext and can be
decrypted with the damaged file inode plus the original bad page index.
```

Current result: **not supported by this sample**.

Evidence:

```text
page 51 expected/orphan pblk 4536057 with lblk 51:
  max=27/4096, first32=2b7a1a7fc0ca5ee901b592e27aba48054004553202eb8e0058368a27d75fed07

page 52 neighborhood 4536057..4536063 with lblk 52:
  all candidates stayed high-entropy-shaped; center pblk 4536060 matched the bad page bytes.
```

The probe was valid for inline encryption:

```text
needs_crypt=1 has_key=1 inline=1 bio_crypt=1
```

### Hypothesis 2: Wrong Neighbor Page Index / DUN

Claim tested:

```text
The bad physical blocks contain nearby valid page data, but the read path used
the wrong page index / DUN, so retrying neighboring lblk values should recover
structured plaintext.
```

Current result: **not supported by this sample for lblk 48..56**.

Test:

```text
fixed pblk 4536058, sweep lblk 48..56
fixed pblk 4536060, sweep lblk 48..56
fixed pblk 4536062, sweep lblk 48..56
```

Best observed byte-frequency peaks:

```text
pblk 4536058: best lblk=51 max=31/4096 = 0.7568%
pblk 4536060: best lblk=50 max=33/4096 = 0.8057%
pblk 4536062: best lblk=53 max=31/4096 = 0.7568%
```

Normal reference pages in the same area have max-byte percentages around
`16%..31%`, so the sweep did not recover normal VDEX-shaped plaintext.

## Investigation Matrix

Status vocabulary:

```text
open        still plausible; needs direct evidence
weakened    plausible but current evidence or invariants argue against it
excluded    ruled out for the current sample by a specific test
not-tested  listed but not yet instrumented
```

### Layer 1: Page Writeback / Mapping Mutation

This layer is anchored on the strongest repeated phenomenon: local `+2`
physical-block deltas aligned with high-entropy garbage pages.

| ID | Suspect | Why It Fits `+2` | Current Tension | Status | Needed Evidence | Exclusion Criteria |
|---|---|---|---|---|---|---|
| W1 | `write_cache_folios` versus another `write_cache_folios` pass | Two writeback paths might race around subpages or folio completion and produce skipped/duplicated block allocation patterns | Current design intends unlock + end-writeback retry only when no subpage was submitted, so retry should not leave a partially submitted high-order folio in flight | open | Per-folio logs with folio index, subpage index range, submitted-count, retry reason, and node-block update pblk for the same inode/page range | For bad-region pages, prove only one writeback pass owned each folio until all submitted subpages completed, with no competing node update |
| W2 | Background writeback versus `fdatasync` / fsync-triggered writeback | Same dirty inode can be reached through different writeback triggers; local `+2` could reflect interleaved allocation or node-address updates | Locks should serialize page content and node address updates, but trigger concurrency can still expose state-machine bugs | open | Correlate `writepages` reason/context, `wbc->sync_mode`, `for_reclaim`, current task, inode, folio, and exact logical page range | Show all bad pages came from a single serialized writeback context and no overlapping fdatasync/fsync writeback touched the same folio/range |
| W3 | `write_cache_folios` versus `gc_data_segment` / `move_data_block` | Encrypted files use `move_data_block`; GC could allocate/move data while foreground writeback updates node addresses | Both paths should take folio lock and node lock before changing node block addresses; high-order folio regular `+2` remains surprising | open | Log GC move source/dest pblk, inode, page index, node lock acquisition, folio lock acquisition, and overlap with writeback pages `51..53` | Prove no GC move touched the inode or pblk neighborhood during generation of the bad artifact |
| W4 | Zero-order folio fallback / 4K-page path producing dense `+2` allocation | Repeated single-page allocation could theoretically produce dense `+2` pblk sequences under fragmentation or allocator behavior | It explains mapping shape more readily than data decryption failure, but does not by itself explain high-entropy wrong plaintext | weakened | Compare allocation traces for good low-entropy pages and bad high-entropy pages under the same workload; log allocation source and old/new pblk | If `+2` appears without corruption in clean samples, demote `+2` from cause to symptom unless paired with content evidence |

### Layer 2: Bio Submission / F2FS IO Descriptor State

This layer asks whether the bio contains the wrong page, wrong logical index, or
mixed data even though the node mapping later looks internally consistent.

| ID | Suspect | Why It Fits | Current Tension | Status | Needed Evidence | Exclusion Criteria |
|---|---|---|---|---|---|---|
| B1 | Bio contains multiple unrelated folio/subpage entries with a wrong logical-index basis | Mixed bio construction could write ciphertext for one DUN to a pblk later referenced by a different page index | Most submit paths derive the page index from folio/subpage state; the new `fio->idx` variable must be audited everywhere it is initialized | open | Audit every `struct f2fs_io_info` initializer and every submit path; log `folio->index`, subpage offset, `fio->idx`, target pblk, bio sector, and fscrypt DUN at submission | Prove every write bio for bad pages used the intended folio index/subpage index and matching bio crypt DUN |
| B2 | Missing explicit `.idx` assignment in `struct f2fs_io_info` after adding `fio->idx` | If a path relies on the default zero, page-0 semantics could accidentally be used where a subpage index was required | Current Pixel build has `CONFIG_INIT_STACK_ALL_ZERO=y` and compile commands include `-ftrivial-auto-var-init=zero`, so automatic stack `struct f2fs_io_info fio;` storage is zero-filled in this build; most visible F2FS local initializers also use `= { ... }` | weakened | Code audit plus runtime logging: every IO submit should print whether `fio->idx` was explicitly assigned or only default-zero, and whether that is correct for the path | Exclude after proving every path where `idx` affects submitted data, pblk, or crypto context either sets it explicitly or intentionally uses zero |
| B3 | Bio merge crosses a boundary where pblk sequence and DUN sequence diverge | Could make a local region look regular in pblk while decrypted bytes are wrong | Raw probe with neighboring DUNs did not recover valid plaintext for pages `51..53`, weakening a simple DUN-offset explanation | weakened | Log every bio vector in the submitted bio: inode, logical page/subpage, pblk, sector, DUN/index, and first-page digest before submit | If the submitted bio vectors show correct one-to-one pblk/DUN ordering for bad pages, exclude this path |

### Layer 3: Buffered Write / Page-Writeback Concurrency

This layer is not currently well connected to local `+2`, but remains a possible
data-source corruption path.

| ID | Suspect | Why It Fits | Current Tension | Status | Needed Evidence | Exclusion Criteria |
|---|---|---|---|---|---|---|
| P1 | Buffered write modifies folio while writeback observes or submits stale/mixed subpages | Could create high-entropy garbage if plaintext page contents are not what writeback expects | It does not naturally explain regular local `+2` physical-block deltas | weakened | Log `write_begin`, `write_end`, dirtying, lock state, writeback start/end, and page digest around bad indexes | Exclude when folio lock/writeback ordering proves no writer overlapped bad-page writeback |
| P2 | Page-cache read/retry path returns bytes decrypted under an inconsistent index | Would match high entropy at read time without requiring disk content to be wrong | Raw pblk probe of disk bytes reproduced the bad page for center pblk, so this is not just page-cache presentation for the tested sample | weakened | Compare drop-caches reads, raw pblk probe, and preserved host bytes for the same page | If raw pblk probe with correct inode/lblk matches host bad bytes after drop-caches, page-cache-only explanation is excluded for that page |

## Immediate Next Direction

Prioritize Layer 1 and Layer 2 together:

1. Instrument the write path at the point where a logical page/subpage becomes a
   pblk and where the node address is updated.
2. Instrument the final bio submission path to print the same logical page,
   `folio->index`, subpage offset, `fio->idx`, pblk, sector, and inline-crypto
   DUN/index.
3. Add explicit logs around retry/unlock/end-writeback decisions so a later
   sample can prove whether a partially submitted high-order folio ever becomes
   retry-eligible.
4. Add GC `move_data_block` overlap logs for encrypted files, including source
   pblk, destination pblk, inode, page index, folio lock, and node lock.

The next useful proof is not another raw-decrypt sweep; it is a write-time
provenance trace for a future sample that captures:

```text
logical page -> submitted data digest -> bio DUN/index -> destination pblk -> node address update
```
