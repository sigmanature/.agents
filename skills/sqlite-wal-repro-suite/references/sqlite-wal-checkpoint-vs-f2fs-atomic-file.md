# SQLite WAL checkpoint vs F2FS atomic-file ioctls (Android / Pixel)

This note clarifies a very common confusion: **SQLite “atomic transactions”** (WAL/journal) vs **F2FS “atomic file”** (opt-in ioctl feature, COW+commit).

If you see kernel logs like `fn=__replace_atomic_write_block`, you are looking at the **F2FS atomic-file commit/recovery path**, not “SQLite WAL checkpoint” by itself.

---

## 1) What SQLite WAL is (3 files)

When a SQLite DB is in WAL mode, it typically uses:

- `<db>`: main database file (page-based)
- `<db>-wal`: write-ahead log (append-only frames)
- `<db>-shm`: shared-memory index (coordination between connections)

Writes usually land in `<db>-wal` first. Readers may read from `<db>` plus `<db>-wal` depending on page versions.

---

## 2) What a WAL checkpoint does (what gets written)

A **WAL checkpoint** is “fold committed WAL frames back into the main `<db>` file”.

Conceptually:

1) Read committed frames from `<db>-wal`
2) For each affected page number:
   - `pwrite()` the page into `<db>` (random page writes)
3) `fdatasync()` / `fsync()` the `<db>` file (durability)
4) Optionally “reset” the WAL:
   - depends on checkpoint mode (PASSIVE/FULL/RESTART/TRUNCATE)
   - can include `ftruncate(<db>-wal, 0)` in TRUNCATE-like behavior

So a checkpoint can generate **heavy buffered page writes + fsync + truncate**.

Important: this is still “normal file IO” (write/pwrite/fsync/ftruncate). It does **not** automatically call any F2FS atomic-write ioctls.

---

## 3) What F2FS “atomic file” means

F2FS has an **atomic file update** feature, which is **opt-in** per file via ioctls:

- `F2FS_IOC_START_ATOMIC_WRITE`
- `F2FS_IOC_COMMIT_ATOMIC_WRITE`
- `F2FS_IOC_ABORT_ATOMIC_WRITE`

Once started, F2FS creates/uses a **COW inode** (often called `cow_inode` / tmpfile). Writes go to the COW inode, and **commit** swaps block mappings into the original inode.

The “swap the mapping” part is where `__replace_atomic_write_block()` shows up.

---

## 4) Why you might see `__replace_atomic_write_block()` but not `ioc_start_*`

Typical explanations:

1) **Instrumentation coverage**: your printk/klog macro is placed in `__replace_atomic_write_block()` only, so you only see the “commit/recover” phase.
2) **Ring buffer timing**: `start_atomic_write` can happen much earlier than `commit`. By the time you look at `dmesg`, older `start` logs are overwritten.
3) **Recovery path after reboot**: F2FS can replay/complete an atomic state during mount/recovery (the same helper `__replace_atomic_write_block(... recover=true)` is used in some flows). In that case, the original `start` happened *before* reboot, so you won’t see it in current-boot logs.
4) **It’s not SQLite-driven**: the atomic-file user might be another component (GMS/keystore/statsd/magiskd/etc.) doing its own atomic updates around the same time as your SQLite workload.

Practical rule:
- If the atomic log includes an inode number (`ino=<N>`), always resolve it (`find /data -xdev -inum <N>`) to confirm which file actually hit atomic commit/recover.

---

## 5) Expected kernel call chains (from code)

The function names below match the kernel sources under `~/learn_os/f2fs`:

### 5.1 Atomic-file ioctls

Userspace:
- `ioctl(fd, F2FS_IOC_START_ATOMIC_WRITE, ...)`
- `ioctl(fd, F2FS_IOC_COMMIT_ATOMIC_WRITE, ...)`
- `ioctl(fd, F2FS_IOC_ABORT_ATOMIC_WRITE, ...)`

Kernel high-level path:
- `__arm64_sys_ioctl`
- `do_vfs_ioctl`
- `vfs_ioctl`
- `f2fs_ioctl` → `__f2fs_ioctl()` (dispatch)

Dispatch to helpers (in `fs/f2fs/file.c`):
- `F2FS_IOC_START_ATOMIC_WRITE` → `f2fs_ioc_start_atomic_write(filp, truncate=false)`
  - `filemap_write_and_wait_range(inode->i_mapping, ...)`
  - `f2fs_get_tmpfile(...)` (create `cow_inode` tmpfile) when needed
  - `set_inode_flag(inode, FI_ATOMIC_FILE)`
  - `fi->original_i_size = i_size_read(inode)`
  - `f2fs_i_size_write(fi->cow_inode, isize)`
  - `fi->atomic_write_task = current`
- `F2FS_IOC_COMMIT_ATOMIC_WRITE` → `f2fs_ioc_commit_atomic_write(filp)`
  - `f2fs_commit_atomic_write(inode)`
  - `f2fs_do_sync_file(... atomic=true)` (durability/cp semantics)
  - `f2fs_abort_atomic_write(inode, ret)` (cleanup on success/fail)
- `F2FS_IOC_ABORT_ATOMIC_WRITE` → `f2fs_ioc_abort_atomic_write(filp)`
  - `f2fs_abort_atomic_write(inode, ...)`

### 5.2 Where `__replace_atomic_write_block()` fits

Commit path (in `fs/f2fs/segment.c`):
- `f2fs_commit_atomic_write(inode)`
  - `filemap_write_and_wait_range(inode->i_mapping, ...)` (flush dirty data)
  - `__f2fs_commit_atomic_write(inode)`
    - iterate COW inode blocks
    - for each valid `blkaddr`:
      - `__replace_atomic_write_block(inode, index, blkaddr, &old_addr, recover=false)`
        - `f2fs_get_dnode_of_data(...)`
        - `f2fs_replace_block(...)` (swap mapping)
        - `trace_f2fs_replace_atomic_write_block(...)`

Recovery-ish usage (still in `fs/f2fs/segment.c`):
- `__complete_revoke_list(... revoke=true)` calls:
  - `__replace_atomic_write_block(... recover=true)` for entries it needs to revoke.

---

## 6) How this relates to SQLite corruption reports

SQLite `SQLITE_CORRUPT` means “SQLite parsed inconsistent page structure” and can be caused by:
- true storage corruption (torn writes / wrong data returned / media errors)
- wrong fsync/durability behavior
- a kernel/filesystem bug in writeback/readahead/truncate/race paths
- memory corruption (less common but possible)

But: “WAL checkpoint happened” **does not automatically imply** “F2FS atomic-file ioctl path happened”.

