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
| W1a | Retry after partial large-folio submission | Would explain a high-order folio being partially submitted, unlocked, and later retried with inconsistent state | Code in `f2fs_write_cache_folios()` only drops writeback/unlocks/retries on `err == -EAGAIN && !op_lock_held && !folio_submitted`; once any subpage was submitted, the retry branch is not taken | excluded | None for this exact subcase | Static code condition excludes retry-after-partial-submit unless the condition is changed |
| W1b | Two `write_cache_folios` passes own the same folio/page concurrently | Could race subpage dirty state or node update for the same page | `writeback_iter()` locks the folio, `folio_prepare_writeback()` skips or waits on existing writeback, and `f2fs_write_cache_folios()` holds folio ownership through submission/cleanup | excluded for same folio | None for same-folio concurrency | Static lock/writeback path excludes concurrent ownership of the same folio |
| W1c | Different folios from the same inode interleave allocation and produce a `+2` pblk pattern | Allocation interleaving can explain mapping shape without same-folio races | Does not by itself explain high-entropy garbage; content must already be wrong before submit or a lower layer must write wrong bytes | open | Allocation provenance: logical index, old pblk, new pblk, segment type, writer context, and pre-submit digest | Exclude if bad pages are shown to be allocated and submitted by one serialized context with correct pre-submit digest |
| W2 | Background writeback versus `fdatasync` / fsync-triggered writeback for the same page | Same dirty inode can be reached through different writeback triggers | `f2fs_do_sync_file()` sets `FI_NEED_IPU` then calls `file_write_and_wait_range()`; `__f2fs_write_data_pages()` increments `wb_sync_req[DATA]` for `WB_SYNC_ALL`, causing later WB_SYNC_NONE writers to skip; if background writeback already owns the folio, sync writeback waits on folio writeback | excluded as same-page race | None for same-page race | Static ordering excludes simultaneous background and fdatasync ownership of the same folio; fdatasync IPU also does not allocate new `+2` pblks |
| W3 | `write_cache_folios` versus `gc_data_segment` / `move_data_block` for the same logical page | Encrypted files use `move_data_block`; GC can update node block addresses | `move_data_block()` grabs and locks the data folio for `bidx`, waits data folio writeback, waits old block writeback in `META_MAPPING`, writes raw encrypted data, then updates the node while still holding the folio; writeback also requires the same folio lock | excluded for same-page race; open only for allocation/provenance side effects | If kept open, log GC source/dest pblk and writeback new pblk only to prove whether GC was present, not because same-page locking is unclear | Same-page data/writeback race is statically excluded by folio lock plus block writeback wait |
| W4 | Zero-order folio fallback / 4K-page path producing dense `+2` allocation | Repeated single-page allocation could theoretically produce dense `+2` pblk sequences under fragmentation or allocator behavior | It explains mapping shape more readily than data decryption failure, but does not explain high-entropy wrong plaintext | weakened | Compare allocation traces for good low-entropy pages and bad high-entropy pages under the same workload; log allocation source and old/new pblk | If `+2` appears without corruption in clean samples, demote `+2` from cause to symptom unless paired with content evidence |

### Layer 2: Bio Submission / F2FS IO Descriptor State

This layer asks whether the bio contains the wrong page, wrong logical index, or
mixed data even though the node mapping later looks internally consistent.

| ID | Suspect | Why It Fits | Current Tension | Status | Needed Evidence | Exclusion Criteria |
|---|---|---|---|---|---|---|
| B1 | Bio contains multiple unrelated folio/subpage entries with a wrong logical-index basis | Mixed bio construction could write ciphertext for one DUN to a pblk later referenced by a different page index | In the large-folio write path, each subpage builds `fio.idx = i` and `fio.cnt = 1`; `f2fs_submit_page_write()` uses `folio->index + fio->idx` for the crypt index and `bio_add_folio(..., fio->idx << PAGE_SHIFT)` for the source offset | excluded for normal large-folio data writeback | None for this normal path; only revisit if another path feeds a different `fio` shape | Static dataflow shows matching source offset and DUN index |
| B2 | Missing explicit `.idx` assignment in `struct f2fs_io_info` after adding `fio->idx` | If a path relies on default zero, page-0 semantics could accidentally be used where a subpage index was required | Current Pixel build has `CONFIG_INIT_STACK_ALL_ZERO=y` and compile commands include `-ftrivial-auto-var-init=zero`; normal order-0 writeback intentionally uses zero, large-folio subpage writeback explicitly sets `.idx = i`, and GC raw-block paths use zero offset with `encrypted_page` and no fscrypt DUN | excluded for current bad-path candidates | None unless a new path is found where `idx` affects a non-zero subpage but remains default-zero | Static audit of the relevant paths excludes random stack garbage and wrong default-zero for this sample |
| B3 | Bio merge crosses a boundary where pblk sequence and DUN sequence diverge | Could make one bio contain pages whose physical and logical sequences disagree | `page_is_mergeable()` requires `last_blkaddr + 1 == cur_blkaddr`; `f2fs_crypt_mergeable_bio()` verifies fscrypt mergeability for the next logical index; the observed bad mapping is local `+2`, so those pages cannot be merged with their neighbors in one sequential bio | excluded for the `+2` bad pages | None for the observed `+2` pages | Static merge predicates exclude a single merged bio spanning the local `+2` gaps |

### Layer 3: Buffered Write / Page-Writeback Concurrency

This layer is not currently well connected to local `+2`, but remains a possible
data-source corruption path.

| ID | Suspect | Why It Fits | Current Tension | Status | Needed Evidence | Exclusion Criteria |
|---|---|---|---|---|---|---|
| P1 | Buffered write modifies folio while writeback observes or submits stale/mixed subpages | Could create high-entropy garbage if plaintext page contents are not what writeback expects | Buffered write holds the target folio lock through `write_begin`/`write_end`; `f2fs_write_begin()` waits existing writeback; writeback owns the same folio lock through `writeback_iter()` before submit | excluded as same-folio data race | None for same-folio overlap | Static folio locking and writeback wait exclude concurrent modification of the same folio during submit |
| P2 | Page-cache read/retry path returns bytes decrypted under an inconsistent index | Would match high entropy at read time without requiring disk content to be wrong | Raw pblk probe of disk bytes reproduced the bad page for center pblk with `bio_crypt=1`, and preserved host bytes matched the raw probe center page | excluded for this sample | None for the tested Gmail VDEX pages | Raw disk read with correct inode/lblk proves the bad bytes are on disk, not only in page cache |

## Immediate Next Direction

After static code analysis, most concurrency and bio-merge explanations are
excluded for the tested path. Prioritize the remaining write-time provenance
questions:

1. At `f2fs_outplace_write_data()` / `do_write_page()`, capture logical index,
   old pblk, allocated new pblk, segment type, and a pre-submit page digest.
2. At `f2fs_submit_page_write()`, capture `folio->index`, `fio->idx`,
   `fio->cnt`, source offset, crypt logical index, destination pblk, and sector.
3. At `f2fs_update_data_blkaddr()`, capture node old/new pblk and logical index
   so allocation, bio submit, and node update can be joined.
4. Keep lightweight GC `move_data_block()` overlap logs as context, but do not
   treat same-page GC/writeback race as a leading theory unless new code evidence
   contradicts the folio-lock analysis.

The next useful proof is not another raw-decrypt sweep; it is a write-time
provenance trace for a future sample that captures:

```text
logical page -> submitted data digest -> bio DUN/index -> destination pblk -> node address update
```
