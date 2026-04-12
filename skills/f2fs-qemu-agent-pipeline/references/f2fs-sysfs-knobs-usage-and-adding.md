# F2FS sysfs knobs：是否影响决策 + 如何新增（代码指引）

面向 Android 上常见的 `/sys/fs/f2fs/<dev>/...`（如 `dm-54`）以及全局的 `/sys/fs/f2fs/features/*`、`/sys/fs/f2fs/tuning/*`。

本仓库里常见有两套内核树，路径不同但结构接近：

- `f2fs/fs/f2fs/...`
- `pixel/common/fs/f2fs/...`

> 注意：Pixel tree 的 `sysfs.c` 里 `struct f2fs_attr` 没有 `size` 字段，generic show/store 走 `unsigned int` 指针路径；如果你的字段不是天然 u32（比如 u64/atomic64_t/bool），强烈建议写自定义 show/store。

---

## 1) 这些 sysfs tunables 会在 F2FS 决策中使用吗？

会。大部分 `/sys/fs/f2fs/<dev>/` 下的 tunables 不是“摆设”，会被运行时读到并影响策略选择、阈值、节流等。

下面给出一些在 `f2fs/` 树里可直接看到的“knob → 决策点”示例（同名在 `pixel/common/` 树里通常也存在对应实现）。

### 1.1 `readdir_ra`：readdir 是否触发 inode readahead

- sysfs 写入：`f2fs/fs/f2fs/sysfs.c:860`（`__sbi_store()` 分支，设置 `sbi->readdir_ra`）
- 决策使用：`f2fs/fs/f2fs/dir.c:981` 起
  - `bool readdir_ra = sbi->readdir_ra;`
  - if (readdir_ra) 做 `blk_start_plug()`，并在遍历目录项时触发 `f2fs_ra_node_page(...)`

结论：这是“开关型”策略 knob，直接改变 readdir 的 IO 行为。

### 1.2 `ra_nid_pages`：NAT readahead 的页数

- backing field：`NM_I(sbi)->ra_nid_pages`（定义见 `f2fs/fs/f2fs/f2fs.h:1073`）
- 决策使用：`f2fs/fs/f2fs/node.c:2653-2655`
  - `f2fs_ra_meta_pages(..., nm_i->ra_nid_pages, META_NAT, false);`

结论：这是“数量型” knob，直接决定 NAT readahead 的力度。

### 1.3 `max_victim_search`：GC 候选扫描上限

- backing field：`sbi->max_victim_search`（定义见 `f2fs/fs/f2fs/f2fs.h:1900`）
- 决策使用：`f2fs/fs/f2fs/gc.c:310-314`
  - 非 FG_GC / 非 urgent / 非 ATGC 等场景下，把 `p->max_search` 截断到 `sbi->max_victim_search`

结论：这是“节流型” knob，控制 GC 扫描成本 / 延迟。

### 1.4 discard 相关：影响 discard policy 的参数

典型 knobs：

- `max_discard_request`、`discard_io_aware_gran`、`discard_urgent_util`、`discard_granularity`、`discard_io_aware`

决策使用在 `f2fs/fs/f2fs/segment.c:1188` 起的 `__init_discard_policy()`：

- `dpolicy->max_requests = dcc->max_discard_request;` (`segment.c:1200`)
- 利用率超过阈值（`utilization(sbi) > dcc->discard_urgent_util`）时，调整 granularity / interval (`segment.c:1214-1219`)
- `dcc->discard_io_aware` 决定 `dpolicy->io_aware` (`segment.c:1208-1211`)

结论：这组 knobs 直接改变后台 discard 行为（并发/粒度/是否 IO-aware/触发时机）。

### 1.5 `reclaim_segments`：prefree segment 回收阈值

- backing field：`SM_I(sbi)->rec_prefree_segments`（定义见 `f2fs/fs/f2fs/f2fs.h:1204`）
- 决策使用：`f2fs/fs/f2fs/segment.h:742-745`
  - `prefree_segments(sbi) > SM_I(sbi)->rec_prefree_segments`

结论：这是“阈值型” knob，用于判断是否出现 excess prefree segments（影响回收策略）。

### 1.6 `allocate_section_policy` + `allocate_section_hint`：新 section/segment 选择策略

- sysfs 写入校验：`f2fs/fs/f2fs/sysfs.c:944-956`（枚举范围校验）
- 决策使用：`f2fs/fs/f2fs/segment.c:2783-2831`（`get_new_segment()`）
  - `alloc_policy`/`alloc_hint` 改变 `hint` 的起点与回绕逻辑，影响 free_secmap 的扫描方向/范围。

结论：这是“分配策略型” knob，会直接影响写入落点/冷热分布等。

### 1.7 `migration_granularity` + `migration_window_granularity`：BG_GC 迁移节奏

- 决策使用：`f2fs/fs/f2fs/gc.c:1791-1805`、`gc.c:1870-1872`
  - BG_GC/one_time 时调整 `end_segno`（window）
  - `migrated >= sbi->migration_granularity` 时跳过继续 migrate（节流）

结论：这是“迁移节流/窗口” knob，会影响 BG_GC 行为与开销。

### 1.8 `reserved_blocks` / `current_reserved_blocks`：影响 statfs 可用空间暴露

- `reserved_blocks` 写入时会重新计算 `current_reserved_blocks`：`f2fs/fs/f2fs/sysfs.c:580-596`
- `f2fs_statfs()` 使用它扣减可用空间：`f2fs/fs/f2fs/super.c:2231-2239`

结论：这组 knobs 会影响用户态看到的空间统计（以及部分路径下的“可用块”判断）。

---

## 2) 如何新增一个 sysfs attr？

先选你要加到哪一类路径：

1) **全局 capability 列表**：`/sys/fs/f2fs/features/<name>`（只读；一般表示“内核支持什么”）  
2) **全局 tuning**：`/sys/fs/f2fs/tuning/<name>`（可读写；通常走 name-dispatch 的 show/store）  
3) **每个挂载实例 tunable**：`/sys/fs/f2fs/<dev>/<name>`（Android 常用；影响运行时行为）  

下面分别给出“最小改动 checklist”（以 `f2fs/fs/f2fs/sysfs.c` 为主，`pixel/common/...` 同理）。

### 2.1 新增 `/sys/fs/f2fs/features/<name>`（全局 feature）

实现都在 `f2fs/fs/f2fs/sysfs.c`：

1. **声明 base attr**
   - 宏：`f2fs/fs/f2fs/sysfs.c:1065` `F2FS_FEATURE_RO_ATTR(name)`
   - 你通常只需要在现有 feature 列表附近加一行：`F2FS_FEATURE_RO_ATTR(my_feature);`

2. **把它注册进 `features` 的 attrs 数组**
   - 数组：`f2fs/fs/f2fs/sysfs.c:1504` `static struct attribute *f2fs_feat_attrs[]`
   - 增加：`BASE_ATTR_LIST(my_feature),`

3. **更新 ABI 文档**
   - `f2fs/Documentation/ABI/testing/sysfs-fs-f2fs:264`（features 列表相关）
   - 若你也维护 Pixel tree：`pixel/common/Documentation/ABI/testing/sysfs-fs-f2fs:264`

> 注意：这里的 `f2fs_feature_show()` 当前是固定打印 `supported`（见 `f2fs/fs/f2fs/sysfs.c:1060`），所以它不是“按条件显示 supported/unsupported”。如果你需要“条件化”，就要改 show 逻辑或为单独 attr 写自定义 show。

### 2.2 新增 `/sys/fs/f2fs/tuning/<name>`（全局 tuning）

1. **声明 base attr**
   - 宏：`f2fs/fs/f2fs/sysfs.c:1097` `F2FS_TUNE_RW_ATTR(name)`
   - 在现有 tuning 列表附近加：`F2FS_TUNE_RW_ATTR(my_knob);`

2. **注册进 `tuning` 的 attrs 数组**
   - `f2fs/fs/f2fs/sysfs.c:1599` `static struct attribute *f2fs_tune_attrs[]`
   - 增加：`BASE_ATTR_LIST(my_knob),`

3. **实现语义：扩展 `f2fs_tune_show()` / `f2fs_tune_store()`**
   - `f2fs/fs/f2fs/sysfs.c:1071` `f2fs_tune_show()`
   - `f2fs/fs/f2fs/sysfs.c:1081` `f2fs_tune_store()`
   - 当前实现是按 `a->attr.name` 做 `strcmp()` 分发；新增 knob 需要新增分支。

4. **更新 ABI 文档**
   - `f2fs/Documentation/ABI/testing/sysfs-fs-f2fs:843`（tuning 项）
   - Pixel tree 对应：`pixel/common/Documentation/ABI/testing/sysfs-fs-f2fs:835`

### 2.3 新增 `/sys/fs/f2fs/<dev>/<name>`（每挂载实例 tunable）

这是 Android 最常用的那堆 knob，核心是 `struct f2fs_attr`（不是 base attr）。

最小 checklist：

1. **决定 backing field 放哪里**
   - 最好复用 `__struct_ptr()` 已支持的组：`F2FS_SBI` / `SM_INFO` / `DCC_INFO` / `NM_INFO` / `GC_THREAD` 等
   - router 在 `f2fs/fs/f2fs/sysfs.c:75` `__struct_ptr()`

2. **声明 attr**
   - 常用宏区：`f2fs/fs/f2fs/sysfs.c:1119` 起（`F2FS_RW_ATTR` 等）
   - 以及各种包装：`f2fs/fs/f2fs/sysfs.c:1194` 起
   - 例子：如果你加的是 `sbi->foo`，通常写 `F2FS_SBI_GENERAL_RW_ATTR(foo);`

3. **把它加入 per-sb attrs 数组**
   - `f2fs/fs/f2fs/sysfs.c:1376` `static struct attribute *f2fs_attrs[]`
   - 增加：`ATTR_LIST(foo),`
   - 忘了这一步：编译能过，但 sysfs 下不会出现文件。

4. **需要校验/副作用时：在 `__sbi_store()` 加 name-based 分支**
   - `f2fs/fs/f2fs/sysfs.c:479` `__sbi_store()`
   - 把 `if (!strcmp(a->attr.name, "foo")) { ... }` 放在最终 generic store 前面。
   - 如果是 GC/线程相关 knob，注意 `f2fs_sbi_store()` 里的 `gc_entry`（`f2fs/fs/f2fs/sysfs.c:982-991`）会尝试拿 `sb->s_umount` 的 trylock。

5. **更新 ABI 文档**
   - `f2fs/Documentation/ABI/testing/sysfs-fs-f2fs:1` 里对应段落

---

## 3) 常见坑（强烈建议你新增前先对照）

- **Pixel tree generic sysfs 是 u32 路径**：非 u32 字段（u64/atomic64/bool/bitmask）建议自定义 show/store，或者你就得接受截断/未定义行为风险。
- **`struct_type` 必须和 `offsetof(struct ...)` 匹配**：`__struct_ptr()` 决定 base pointer，`offsetof` 决定偏移；组选错会读写到错误地址。
- **需要锁 / 唤醒线程 / 更新多字段**：不要只靠 offset store；用 `__sbi_store()` 分支做完整动作（参考 `gc_urgent` / `reserved_blocks` / `extension_list` 等）。

