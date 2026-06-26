# mmap-16k-unaligned-scan

扫描 Android /proc/<pid>/maps 中所有匿名 VMA，统计 16KB 不对齐的比例，按完整 VMA 名称分组。

## 触发条件

- "扫描不对齐的 VMA"
- "看看进程里有哪些 VMA 不是 16KB 对齐的"
- "统计匿名映射的 16KB 对齐情况"
- "check 16KB unaligned anonymous VMAs"
- 检查 mmap MAP_FIXED 强制 16KB 对齐后的残留不对齐 VMA

## 一行命令

```bash
python3 /home/nzzhao/learn_os/scripts/mmap_unaligned_anon_scan.py \
  --pid-names "875:zygote64,1534:system_server" -o /tmp/report.md
```

### 常用参数组合

```bash
# 扫描指定 PID 和名称
python3 <script> --pid-names "875:zygote64,1534:system_server,6522:toutiao"

# 按进程名查找并扫描
python3 <script> --names "zygote64,system_server,com.android.systemui"

# 扫描所有用户应用进程
python3 <script> --all-user

# CSV 格式输出
python3 <script> --pid-names "875:zygote64" -f csv

# 查看更多进程
python3 <script> --pid-names "875:zygote64,1534:system_server,2009:systemui,2534:gms,6522:toutiao,14406:taobao,10863:wework,23820:cloudmusic,9342:youtube,15018:kuaishou"
```

## 前置条件

- `adb` 可用，设备已连接且 root (`su`)
- Python 3

## 脚本路径

`/home/nzzhao/learn_os/scripts/mmap_unaligned_anon_scan.py`

## 输出解读

报告包含两部分：

### 1. 跨进程汇总 (按 VMA 类别折叠)

| VMA 类别 | 含义 |
|----------|------|
| `[anon]` | 无名称的匿名映射 (glibc/bionic mmap, malloc arena 等) |
| `[anon:.bss]` | ELF 文件的 BSS 段 (linker 通过 MAP_FIXED 映射) |
| `[anon:stack_and_tls]` | 线程栈 + TLS (pthread 创建，guard page + stack) |
| `[anon:thread signal stack]` | 线程信号栈 (3 pages: guard + stack) |
| `[anon:scudo:primary]` | Android scudo 分配器的 primary 区域 |
| `[anon:scudo:primary_reserve]` | scudo primary 的 guard 保留区 |
| `[anon:scudo:secondary]` | scudo secondary (大块分配 mmap) |
| `[anon:partition_alloc]` | Chromium 的 PartitionAlloc (仅 Chromium-based app) |
| `[anon:cfi shadow]` | CFI (Control Flow Integrity) shadow |
| `[anon:dalvik-*]` | ART 虚拟机内部数据结构 |

### 2. 各进程详情

按完整 VMA 名列出每个进程内不对齐的具体情况。

## 关键发现（当前设备）

在 10 个进程（zygote64, system_server, systemui, gms, toutiao, taobao, wework, cloudmusic, youtube, kuaishou）上的统计：

- **总是 100% 不对齐**: `[anon:stack_and_tls]`, `[anon:thread signal stack]`, `[anon:scudo:secondary]`, `[anon:dalvik-Boot image reservation]`, `[stack]`, `[vdso]`
- **高度不对齐 (>70%)**: `[anon:.bss]`(98.9%), `[anon:scudo:primary_reserve]`(88.2%), `[anon:scudo:primary]`(73%), `[anon:cfi shadow]`(98.1%)
- **总是对齐 (0%)**: `[anon:dalvik-main space]`, `[anon:dalvik-*art]`(boot image), `[anon:dalvik-zygote space]`

其中 scudo 相关的不对齐来自分配器自身的 mmap 调用，.bss 来自 linker 的 MAP_FIXED，stack_and_tls 来自 pthread 创建线程时的 mmap。
