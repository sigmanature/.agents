# F2FS：fs-verity 文件的「buffered 写 → writeback → bio 落盘」全链路（代码导向速查）

## 适用场景与关键点

- **正常 data 写**：用户态对普通文件的 buffered write（pagecache dirty），随后由回写线程/`fsync()`/`filemap_write_and_wait()` 触发 writeback，最终 `submit_bio()` 落盘。
- **fs-verity enable 阶段的“写”**：`FS_IOC_ENABLE_VERITY` 会在 **同一个 inode** 的 **EOF 之后**（按 64KiB 对齐）写入 Merkle tree 与 descriptor。对 f2fs 来说这也是 **走 address_space aops 的 buffered 写**，但 **不会更新 i_size**（靠 `FI_VERITY_IN_PROGRESS` 保护），并且必须在清除该标志前把这些“超出 i_size 的脏页”写回磁盘。

本速查以本仓库内核树 `f2fs/` 为准：

- `f2fs/fs/f2fs/file.c`：`f2fs_file_write_iter()`
- `f2fs/fs/f2fs/data.c`：`f2fs_write_begin()` / `f2fs_write_end()` / `f2fs_write_data_pages()` / `f2fs_write_single_data_page()` / `f2fs_submit_page_bio()` / `f2fs_submit_page_write()` / `f2fs_write_end_io()`
- `f2fs/fs/f2fs/verity.c`：`pagecache_write()` / `f2fs_write_merkle_tree_block()` / `f2fs_end_enable_verity()`
- `f2fs/fs/f2fs/segment.c`：`f2fs_inplace_write_data()`

## 1) buffered write（用户态）到 dirty folio

入口：

- VFS：`__vfs_write()` → `file->f_op->write_iter()`
- f2fs：`f2fs_file_write_iter()`（`file.c`）

在 buffered 路径里会进入：

- `f2fs_buffered_write_iter()` → `generic_perform_write()`（VFS 通用）  
  然后回调到 aops：
  - `aops->write_begin()` → `f2fs_write_begin()`（`data.c`）
  - `aops->write_end()` → `f2fs_write_end()`（`data.c`）

`f2fs_write_begin()` 关键动作：

- 处理 inline data 转换、（可选）压缩 overwrite 准备。
- `f2fs_filemap_get_folio(... FGP_LOCK | FGP_WRITE | FGP_CREAT ...)` 拿到并锁住 folio。
- `prepare_write_begin()`（或 atomic 的 `prepare_atomic_write_begin()`）：
  - 通过 `dnode_of_data` lookup / reserve，得到 `blkaddr`（`NULL_ADDR`/`NEW_ADDR`/已有块）
  - 对 hole / 扩展写（`pos >= i_size`）会持 `f2fs_map_lock()` 以避免与 checkpoint / inline 转换等竞态
- `f2fs_folio_wait_writeback()`：避免覆写正在回写的页
- 若是部分写且页不 uptodate：
  - 若已有块：`f2fs_submit_page_read()` 先把旧数据读上来
  - 若新块：对整页 zero + uptodate
- 特殊：当 `!f2fs_verity_in_progress(inode)` 时，对 “写到 EOF 附近的尾部” 会做 zero tail；verity enable 阶段要避免破坏元数据区的构建语义。

`f2fs_write_end()` 关键动作：

- 把写入范围标记 dirty（`folio_mark_dirty()`；大 folio 还会按 subrange dirty）
- 正常情况下若 `pos+copied > i_size`：更新 `i_size`
- **但当 `f2fs_verity_in_progress(inode)` 时不会更新 `i_size`**（因为 verity 元数据写在 i_size 之后、但不能向用户可见）

## 2) verity enable 阶段：写入 Merkle tree / descriptor（仍是 buffered 写）

入口：

- `FS_IOC_ENABLE_VERITY` → fs/verity 核心调用 filesystem 的 `fsverity_operations`
- f2fs：`f2fs_begin_enable_verity()` / `f2fs_write_merkle_tree_block()` / `f2fs_end_enable_verity()`（`verity.c`）

关键点：

- f2fs 采用 “**把 verity 元数据放到 EOF 之后**” 的方案：起始 offset 为 `round_up(i_size, 65536)`。
- `f2fs_write_merkle_tree_block()`：
  - 计算 `pos += f2fs_verity_metadata_pos(inode)` 后调用 `pagecache_write()`
- `pagecache_write()`：
  - 直接调用 `mapping->a_ops->write_begin/write_end`（也就是 `f2fs_write_begin/end`）
  - 这就是典型的 pagecache dirty 路径，只是 **写入的位置超出 i_size**
- `f2fs_end_enable_verity()`：
  - 写入 descriptor（同样走 `pagecache_write()`）
  - `filemap_write_and_wait(inode->i_mapping)`：**强制把 data + verity metadata 的脏页全部写回**
  - 之后才 set verity xattr、设置 inode verity flag，并清除 `FI_VERITY_IN_PROGRESS`

## 3) writeback：从 dirty folio 到 f2fs 的写入路径

触发来源：

- 后台回写：`wb_workfn()` / flusher threads
- 显式同步：`fsync()` / `filemap_write_and_wait()` / `syncfs()` 等

VFS → fs：

- `do_writepages()` → `mapping->a_ops->writepages()`  
  f2fs data mapping：`f2fs_write_data_pages()`（`data.c`）

`f2fs_write_data_pages()`：

- 进入 `__f2fs_write_data_pages()`，再调用：
  - `f2fs_write_cache_pages()`（order-0 pages）
  - 或 `f2fs_write_cache_folios()`（large folio support）
- 它们都会遍历 tag 为 DIRTY/TOWRITE 的 folio/page，然后对每个 dirty folio 走：
  - `folio_lock()` → wait writeback（必要时）→ `folio_clear_dirty_for_io()`  
  - → `f2fs_write_single_data_page(folio, ...)`

`f2fs_write_single_data_page()` 关键点（`data.c`）：

- 建立 `struct f2fs_io_info fio`（包含 inode/folio/idx、io_type、wbc flags、bio merge 相关字段）
- **对超出 i_size 的页**：
  - 普通文件：会 zero tail / 或直接跳过不写
  - **verity enable 阶段**：由于 `f2fs_verity_in_progress(inode)` 为真，会 bypass 掉 “超出 i_size 不写” 的逻辑，使 verity 元数据页能够被回写
- 写入策略分叉：
  - IPU（in-place update）：走 `f2fs_inplace_write_data()`（`segment.c`）
  - OPU（out-place update，log-structured）：走 `f2fs_outplace_write_data()`（`segment.c`）
  两条路最终都到提交 bio（见下节）

## 4) 提交 bio：merge + submit_bio()

### OPU（out-place）

- `f2fs_outplace_write_data()` → `do_write_page()`（分配新块、写 summary、更新 dnode 地址等）
- 最终 `f2fs_submit_page_write(&fio)`（`data.c`）：
  - 维护 `sbi->write_io[type][temp]` 的 merge bio
  - `bio_add_folio()` 能合并就合并
  - 否则 `__submit_merged_bio()` → `f2fs_submit_write_bio()` → `submit_bio()`

### IPU（in-place）

- `f2fs_inplace_write_data()`（`segment.c`）：
  - `fio->new_blkaddr = fio->old_blkaddr`
  - 走 `f2fs_merge_page_bio(fio)`（可选）或 `f2fs_submit_page_bio(fio)`
- `f2fs_submit_page_bio()`（`data.c`）：
  - `__bio_alloc()` → `bio_add_folio_nofail()` → `f2fs_submit_write_bio()` → `submit_bio()`

## 5) bio 完成：end_io → folio_end_writeback

- `bio->bi_end_io = f2fs_write_end_io`（`data.c`）
- `f2fs_write_end_io()`：
  - 遍历 bio 内所有 folio，处理 bounce page / 压缩等后置逻辑
  - 若 IO error：`mapping_set_error()` 并可能 `f2fs_stop_checkpoint()`
  - 对完成的 folio：`folio_end_writeback()`，并更新 f2fs 计数器/唤醒 checkpoint 等待者

## 备注：为什么 verity 元数据必须在清除 FI_VERITY_IN_PROGRESS 前写回？

- verity 元数据页的 index 位于 `i_size` 之后；若把 inode 当作 “普通 readonly 文件”，许多路径会按 `i_size` 裁剪/跳过超出 EOF 的页。
- f2fs 在多个位置用 `f2fs_verity_in_progress(inode)` 绕开这些 “EOF 裁剪” 行为，保证 `filemap_write_and_wait()` 能把这些页刷到盘上。

