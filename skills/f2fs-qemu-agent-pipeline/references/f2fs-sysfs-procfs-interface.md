# F2FS: sysfs / procfs 暴露的可调节接口（代码定位）

本仓库里有不止一份 F2FS 源码树：

- 主线/镜像：`f2fs/fs/f2fs/...`
- Pixel common 内核树：`pixel/common/fs/f2fs/...`

Android 设备上你看到的：

- `/sys/fs/f2fs/<dev>/...`（例如截图里的 `dm-54`）
- `/proc/fs/f2fs/<dev>/...`

对应的实现入口基本都在同一个文件（不同源码树路径不同）：

- `f2fs/fs/f2fs/sysfs.c`
- `pixel/common/fs/f2fs/sysfs.c`

---

## 1) sysfs：`/sys/fs/f2fs/...` 的实现位置

### 1.1 目录树是怎么创建出来的

- 全局 sysfs 根：`/sys/fs/f2fs/`
  - `f2fs/fs/f2fs/sysfs.c:1957` `int __init f2fs_init_sysfs(void)`
    - `kset_register(&f2fs_kset)`，并把它挂在 `fs_kobj` 下（所以落在 `/sys/fs/`）
    - 创建两个全局 kobject：
      - `/sys/fs/f2fs/features/`（`kobject_init_and_add(..., "features")`）
      - `/sys/fs/f2fs/tuning/`（`kobject_init_and_add(..., "tuning")`）
  - `f2fs/fs/f2fs/sysfs.c:1992` `void f2fs_exit_sysfs(void)` 负责销毁

- 每个挂载实例的目录：`/sys/fs/f2fs/<sb->s_id>/`
  - `f2fs/fs/f2fs/sysfs.c:2001` `int f2fs_register_sysfs(struct f2fs_sb_info *sbi)`
    - `kobject_init_and_add(&sbi->s_kobj, ..., "%s", sb->s_id)`
    - 子目录：
      - `/sys/fs/f2fs/<sbid>/stat/`（`sbi->s_stat_kobj`）
      - `/sys/fs/f2fs/<sbid>/feature_list/`（`sbi->s_feature_list_kobj`）
  - `f2fs/fs/f2fs/sysfs.c:2067` `void f2fs_unregister_sysfs(struct f2fs_sb_info *sbi)` 负责卸载时清理

说明：Android 上你看到 `dm-54` 这类名字，就是 `sb->s_id`，因此 sysfs 目录名跟 block 设备名对得上。

### 1.2 “这些 sysfs 文件名”是如何映射到代码的

sysfs 的读/写分发链路都在 `f2fs/fs/f2fs/sysfs.c`：

- `f2fs/fs/f2fs/sysfs.c:996` `f2fs_attr_show()` / `f2fs_attr_store()`（kobject sysfs ops）
- 大多数 per-superblock tunable 最终会走：
  - `f2fs/fs/f2fs/sysfs.c:370` `f2fs_sbi_show()`
  - `f2fs/fs/f2fs/sysfs.c:977` `f2fs_sbi_store()` → `f2fs/fs/f2fs/sysfs.c:479` `__sbi_store()`
- `__sbi_store()` 里对部分 knob 有“按文件名特殊解释”的分支（例如 `gc_urgent`、`gc_idle` 这种）

属性定义本身主要由一组宏生成（也在 `f2fs/fs/f2fs/sysfs.c`）：

- `f2fs/fs/f2fs/sysfs.c:1119` `F2FS_ATTR_OFFSET` / `F2FS_RO_ATTR` / `F2FS_RW_ATTR`
- `f2fs/fs/f2fs/sysfs.c:1141` `F2FS_GENERAL_RO_ATTR`
- `f2fs/fs/f2fs/sysfs.c:1194` 起的一大段 `*_ATTR(...)` 列表（把 sysfs 文件名绑定到对应结构体字段）

与截图里名字一致的典型例子（都在 `f2fs/fs/f2fs/sysfs.c` 的属性定义区）：

- `gc_idle`、`gc_urgent` → `F2FS_SBI_RW_ATTR(..., gc_mode)`（见 `f2fs/fs/f2fs/sysfs.c:1234-1235`，写入语义在 `__sbi_store()`）
- `reclaim_segments` → `SM_INFO_RW_ATTR(reclaim_segments, rec_prefree_segments)`（见 `f2fs/fs/f2fs/sysfs.c:1205`）
- `max_discard_request` / `discard_granularity` / `discard_io_aware` 等 → `DCC_INFO_GENERAL_RW_ATTR(...)`（见 `f2fs/fs/f2fs/sysfs.c:1216-1224`）
- `ra_nid_pages` → `NM_INFO_GENERAL_RW_ATTR(ra_nid_pages)`（见 `f2fs/fs/f2fs/sysfs.c:1229`）
- `reserved_blocks` → `RESERVED_BLOCKS_GENERAL_RW_ATTR(reserved_blocks)`（见 `f2fs/fs/f2fs/sysfs.c:1308`）
- 只读统计：`dirty_segments` / `free_segments` / `features` 等 → `F2FS_GENERAL_RO_ATTR(...)`（见 `f2fs/fs/f2fs/sysfs.c:1319+`）

---

## 2) procfs：`/proc/fs/f2fs/...` 的实现位置

procfs 的创建/删除也在 `f2fs/fs/f2fs/sysfs.c`：

- `/proc/fs/f2fs` 根目录
  - `f2fs/fs/f2fs/sysfs.c:1977` `f2fs_proc_root = proc_mkdir("fs/f2fs", NULL);`
  - `f2fs/fs/f2fs/sysfs.c:1997` `remove_proc_entry("fs/f2fs", NULL);`

- `/proc/fs/f2fs/<sb->s_id>/` 每个挂载实例的子目录 + 文件
  - `f2fs/fs/f2fs/sysfs.c:2028` `sbi->s_proc = proc_mkdir(sb->s_id, f2fs_proc_root);`
  - `f2fs/fs/f2fs/sysfs.c:2034` 起注册的一组 `proc_create_single_data(...)`：
    - `segment_info`
    - `segment_bits`
    - `victim_bits`
    - `discard_plist_info`
    - `disk_map`
    - `donation_list`
    - `iostat_info`（`CONFIG_F2FS_IOSTAT`）
    - `inject_stats`（`CONFIG_F2FS_FAULT_INJECTION`）
  - `f2fs/fs/f2fs/sysfs.c:2069` `remove_proc_subtree(sbi->sb->s_id, f2fs_proc_root);`

---

## 3) 这些接口什么时候注册（调用点）

- 模块 init/exit：
  - `f2fs/fs/f2fs/super.c:5576` `f2fs_init_sysfs();`
  - `f2fs/fs/f2fs/super.c:5631` `f2fs_exit_sysfs();`
- 挂载时注册 per-superblock 目录：
  - `f2fs/fs/f2fs/super.c:5210` `f2fs_register_sysfs(sbi);`

---

## 4) 快速检索命令（在本仓库里）

```bash
# sysfs / procfs 注册入口
rg -n "f2fs_init_sysfs\\(|f2fs_register_sysfs\\(|proc_mkdir\\(|proc_create_single_data\\(" f2fs/fs/f2fs/sysfs.c

# sysfs read/write 分发链路
rg -n "f2fs_attr_show\\(|f2fs_attr_store\\(|f2fs_sbi_show\\(|f2fs_sbi_store\\(|__sbi_store\\(" f2fs/fs/f2fs/sysfs.c

# 找到某个具体 knob（比如 gc_idle / max_discard_request）
rg -n "gc_idle|max_discard_request|reserved_blocks|ra_nid_pages" f2fs/fs/f2fs/sysfs.c
```
