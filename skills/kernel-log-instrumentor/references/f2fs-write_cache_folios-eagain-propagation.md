# f2fs `f2fs_write_cache_pages()` vs `f2fs_write_cache_folios()`: `-EAGAIN` 产生点与传播差异

适用代码树：`~/learn_os/pixel/common`（Pixel common kernel tree）

## 速查：本地代码行号锚点（便于对照/打点）

> 说明：行号来自当前本地树用 `nl -ba fs/f2fs/data.c | sed -n ...` 观察到的结果；上游变动后可能漂移。  
> 建议重新定位：`rg -n "f2fs_write_cache_pages|f2fs_write_cache_folios|trylock_op|inode_may_use_large_folio|ret == -EAGAIN" -S fs/f2fs/data.c`

- 选择 large folio 写回路径：`fs/f2fs/data.c:4563-4566`（`mapping_large_folio_support()` && `f2fs_inode_may_use_large_folio()`）
- `f2fs_write_cache_folios()` 入口与 `-EAGAIN` retry 条件：`fs/f2fs/data.c:3919-4176`，其中 retry 判定在 `fs/f2fs/data.c:4108-4118`
- `f2fs_write_single_data_folio()` 中 `-EAGAIN` 产生点：`fs/f2fs/data.c:3704-3715`（OPU 懒获取 `f2fs_trylock_op()` 失败）
- `f2fs_write_cache_pages()` 吞掉 `-EAGAIN`：`fs/f2fs/data.c:4394-4420`，其中 `ret == -EAGAIN` 分支为 `fs/f2fs/data.c:4412-4419`
- page-based 路径 `-EAGAIN` 产生点（`f2fs_trylock_op()` 失败）：`fs/f2fs/data.c:3498-3500`（LOCK_REQ 早返回）与 `fs/f2fs/data.c:3553-3558`（LOCK_RETRY → `err=-EAGAIN`）
- `f2fs_inode_may_use_large_folio()` 的过滤条件：`fs/f2fs/f2fs.h:5024-5044`（排除 inline / compressed / verity / quota）

## 背景：writepages / fsync 链路里 `writepages` 的返回值会向上传播

- `mm/filemap.c:file_write_and_wait_range()` 会调用 `__filemap_fdatawrite_range(..., WB_SYNC_ALL)`，其内部走 `filemap_fdatawrite_wbc()` → `do_writepages(mapping, wbc)`，并把 `do_writepages()` 的返回值作为 `err` 返回（若非 0）。  
  因此：如果 F2FS 的 `->writepages` 返回 `-EAGAIN`，理论上 `fsync()`（F2FS: `f2fs_do_sync_file()`）可直接把 `-EAGAIN` 返回到用户态。

## `-EAGAIN` 产生点（非压缩文件路径）

### 1) page-based 旧路径（`f2fs_write_cache_pages()` → `f2fs_write_single_data_page()` → `f2fs_do_write_data_page()`）

`-EAGAIN` 的“根因”主要是 **`f2fs_trylock_op()` 失败**（为了避免 `page lock` 与 `f2fs_lock_op` 的潜在死锁 / 锁竞争），例如：

- `fs/f2fs/data.c:f2fs_do_write_data_page()`：
  - `if (fio->need_lock == LOCK_REQ && !f2fs_trylock_op(...)) return -EAGAIN;`
  - `if (fio->need_lock == LOCK_RETRY && !f2fs_trylock_op(...)) { err = -EAGAIN; ... }`

`f2fs_write_single_data_page()` 里也会把初始 `err` 设成 `-EAGAIN`，并在 inline-data/正常写回路径之间切换，但最终 `-EAGAIN` 仍然来自上面的 trylock 失败。

### 2) folio-based 新路径（`f2fs_write_cache_folios()` → `f2fs_write_single_data_folio()`）

`-EAGAIN` 的产生点同样是 **`f2fs_trylock_op()` 失败**，但位置变为“按 subpage/dirty-range 写回”的 OPU 分支中 **懒获取 op lock**：

- `fs/f2fs/data.c:f2fs_write_single_data_folio()`：
  - `if (!folio_op_lock_held) { if (!f2fs_trylock_op(sbi)) { err = -EAGAIN; ... } folio_op_lock_held = true; }`

注意：`op_lock_held=true` 时，这个函数后续不会再次 trylock，所以 **`-EAGAIN` 通常发生在 `op_lock_held` 为 false 的时刻**（即首次需要 OPU 的那一刻）。

## `-EAGAIN` 在两条路径的处理差异

### A) `f2fs_write_cache_pages()`：基本“吞掉”`-EAGAIN`（并按 sync_mode 选择是否重试）

关键逻辑（`fs/f2fs/data.c:f2fs_write_cache_pages()`）：

- `ret = f2fs_write_single_data_page(...);`
- `else if (ret == -EAGAIN) {`
  - `ret = 0;`  ← **吞掉**
  - `if (wbc->sync_mode == WB_SYNC_ALL) { sleep; goto retry_write; }`  ← **同步写回会等待并重试同一 folio**
  - `goto next;` ← **WB_SYNC_NONE 直接跳过，留待后续 writeback 再处理（页已被 redirty）**
- `}`

结论：在该实现里，`f2fs_write_cache_pages()` 理论上不会把 `-EAGAIN` 作为返回值向上层传播。

### B) `f2fs_write_cache_folios()`：只在“完全未提交”场景做一次局部重试，其它情况下可能把 `-EAGAIN` 原样返回上层

关键逻辑（`fs/f2fs/data.c:f2fs_write_cache_folios()`）：

- 循环写 dirty-range：
  - `err = f2fs_write_single_data_folio(..., &op_lock_held);`
  - `folio_submitted += submitted;`
- 仅在下面这个条件下重试：
  - `if (err == -EAGAIN && !op_lock_held && !folio_submitted) { unlock; sleep; lock; retry = true; goto retry; }`
- 否则：
  - `if (err) goto out;` → `if (err) break;` → `return err;`

结论：`f2fs_write_cache_folios()` **可能直接返回 `-EAGAIN`**，尤其是当：

- 同一 folio 已经通过某些 dirty-range（例如 IPU 子路径）提交了部分 IO（`folio_submitted > 0`），随后在另一个 dirty-range 遇到 OPU 需要 trylock 但失败，从而得到 `-EAGAIN`；此时不会走“未提交才重试”的分支，最终把 `-EAGAIN` 向上返回。

## 实战推论（调试/复现时的判断点）

- 如果你在用户态看到 `fsync()` 偶发返回 `-EAGAIN`，而磁盘/blk 错误并不明显，优先检查是否落在“large folio + `f2fs_write_cache_folios()`”路径：  
  `fs/f2fs/data.c:__f2fs_write_data_pages()` 里有 `mapping_large_folio_support(...) && f2fs_inode_may_use_large_folio(inode)` 的分支选择。
