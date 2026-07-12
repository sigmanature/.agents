# fscrypt inlinecrypt and bounce-folio diagnosis

Use this note when an F2FS/fscrypt write completion crash appears to involve a
`mapping == NULL` folio, especially during xfstests encryption cases such as
`generic/739`.

## Diagnostic rule

Do not assume every `mapping == NULL` folio in `f2fs_write_end_bio()` is an
fscrypt filesystem-layer bounce folio.

There are two distinct bounce sources:

1. **Filesystem-layer fscrypt bounce page**
   - F2FS uses this only when `fscrypt_inode_uses_inline_crypto(inode)` is false.
   - The page comes from `fscrypt_encrypt_pagecache_blocks()`.
   - Expected marker: `page_private(ciphertext_page)` / `folio->private` points
     back to the original pagecache folio.

2. **blk-crypto software-fallback bounce page candidate**
   - fscrypt may still select inline encryption when `CONFIG_BLK_INLINE_ENCRYPTION_FALLBACK=y`, because `blk_crypto_config_supported()` returns true for raw keys when fallback is enabled.
   - If the real block device does not natively support the requested inline crypto configuration, `__blk_crypto_submit_bio()` calls `blk_crypto_fallback_bio_prep()`.
   - For writes, blk-crypto fallback allocates separate encrypted pages and bios; those pages can have `mapping == NULL` without carrying fscrypt's pagecache-folio private pointer.
   - However, the fallback write completion normally calls `bio_endio()` on the original source bio. Before blaming blk-crypto fallback for an F2FS completion crash, verify which bio reached the filesystem endio path.

## xfstests generic/739 trigger

`generic/739` verifies encrypted files whose crypto data unit size differs from
the filesystem block size. It sets v2 fscrypt policies with `log2_dusize=9` and
`log2_dusize=10`, i.e. 512-byte and 1024-byte data units.

For F2FS this is allowed only because `f2fs_cryptops` advertises
`supports_subblock_data_units = 1`. The test is therefore intentionally a
sub-block-data-unit fscrypt test, not a generic large-folio test.

## Practical check sequence

1. Confirm whether the mounted filesystem used `inlinecrypt`.
2. Check whether `fscrypt_inode_uses_inline_crypto(inode)` is true at the write path.
3. Check whether the target block device natively supports the exact
   `blk_crypto_config` tuple: mode, data unit size, DUN bytes, and key type.
4. If native support is absent and `CONFIG_BLK_INLINE_ENCRYPTION_FALLBACK=y`,
   expect blk-crypto fallback pages in the submitted bio path.
5. When `f2fs_write_end_bio()` treats `mapping == NULL` as fscrypt bounce,
   verify `folio->private` before concluding it is an fscrypt bounce page.

## Large-folio gate rule

When large folios are experimental and only inline encryption is supported, do
not globally disable fscrypt or filesystem-layer encryption. Scope the exclusion
to large folios:

1. In the inode mapping-order gate, use only state that is safe before key setup.
   For example, encrypted regular files without `SB_INLINECRYPT` should not get
   large-folio mapping order. Do not call `fscrypt_inode_uses_inline_crypto()` or
   `fscrypt_inode_uses_fs_layer_crypto()` from this early gate, because those
   helpers require fscrypt inode info to already be set up.
2. In the writeback path, after key setup has happened, add a hard guard that
   rejects filesystem-layer encryption only when the source folio is large and
   `fscrypt_inode_uses_inline_crypto(inode)` is false. This prevents
   `fscrypt_encrypt_pagecache_blocks()` from seeing a large folio while still
   allowing existing order-0 fs-layer fscrypt behavior.
3. Treat `f2fs_write_end_bio()` as a defensive assertion site, not the primary
   policy gate. By endio time the original source-folio context may be obscured
   by bounce pages.

## Interpretation

If a crash shows `mapping == NULL` and `folio->private == NULL` in
`f2fs_write_end_bio()`, first confirm whether the test actually mounted F2FS with
`inlinecrypt`. If not, filesystem-layer fscrypt bounce is expected. If it was
mounted with `inlinecrypt`, treat blk-crypto fallback pages as a candidate only
after verifying that the filesystem endio path is seeing the fallback bio rather
than the original source bio.
