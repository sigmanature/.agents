---
name: mmap-lock-contention-analysis
description: "Use when analyzing Android kernel mmap_lock or per-VMA lock contention, especially to validate rip-out patches, tracepoint capture workflows, and kernel or user stack collection on rooted Pixel devices."
---

# mmap_lock Contention Analysis Skill

用于分析 Android 内核中 mmap_lock / per-VMA lock 的竞争，特别是验证 rip-out patch 是否导致优先级反转。

## 前置条件

- Pixel 6/7 设备，已 root
- Kernel 已启用 FAULT_FLAG_VMA_LOCK（per-VMA fault lock）
- 已编译自定义 tracepoint（filemap_fault, vma_start_write, mmap_lock）
- adb 可连接设备

## 快速开始

### 1. 刷入 patch 并启用

```bash
bash private/google-modules/soc/gs/build_slider.sh
fastboot flash boot out/slider/dist/boot.img

# 启用 no-retry 模式
echo 1 > /proc/sys/vm/filemap_fault_no_retry
```

### 2. 配置 tracepoints

使用脚本一键配置：

```bash
cd .agents/skills/mmap-lock-contention-analysis
bash scripts/setup_tracepoints.sh [SERIAL]
```

或手动配置：

```bash
adb shell "su -c 'mount -t debugfs debugfs /sys/kernel/debug'"

for evt in filemap_fault_begin filemap_fault_wait_start filemap_fault_wait_end \
           filemap_fault_retry filemap_fault_end \
           vma_start_write_begin vma_start_write_wait_start \
           vma_start_write_wait_end vma_start_write_done \
           vma_start_read_fail fault_mmap_lock_fallback \
           mmap_lock_wait_start mmap_lock_wait_end \
           mmap_lock_hold_start mmap_lock_hold_end; do
    adb shell "su -c 'echo 1 > /sys/kernel/debug/tracing/events/$evt/enable'"
done

adb shell "su -c 'echo 0 > /sys/kernel/debug/tracing/events/sched/sched_switch/enable'"
adb shell "su -c 'echo 32768 > /sys/kernel/debug/tracing/buffer_size_kb'"
adb shell "su -c 'echo > /sys/kernel/debug/tracing/trace'"
adb shell "su -c 'echo 1 > /sys/kernel/debug/tracing/tracing_on'"
```

### 3. 开启调用栈（零代码改动，运行时生效）

#### 3.1 Kernel Stacktrace（ftrace，推荐）

不需要改 tracepoint 定义，运行时开启即可。每条 event 后面自动附带 8~16 层内核栈：

```bash
# 为关键 event 开启 stacktrace
for evt in \
    mmap_lock/vma_start_write_begin \
    mmap_lock/vma_start_write_wait_start \
    mmap_lock/vma_start_write_wait_end \
    mmap_lock/vma_start_write_done \
    mmap_lock/mmap_lock_wait_start \
    mmap_lock/mmap_lock_wait_end \
    mmap_lock/mmap_lock_hold_start \
    mmap_lock/mmap_lock_hold_end \
    filemap/filemap_fault_begin \
    filemap/filemap_fault_wait_start \
    filemap/filemap_fault_wait_end \
    filemap/filemap_fault_retry \
    filemap/filemap_fault_end; do
    adb shell "su -c 'echo 1 > /sys/kernel/debug/tracing/events/$evt/stacktrace'"
done
```

**效果示例**：

```
app_process-12345  [001] ...1  1234.567: vma_start_write_begin: pid=... caller=mmap_region+0x3c4 ...
        => do_mmap
        => sys_mmap
        => el0_svc_common
        => el0_svc
```

> **注意**：kernel stacktrace 只能看到内核路径。如果 caller 显示 `mmap_region`，说明用户态走了 `mmap()` syscall；如果显示 `mprotect_fixup`，说明走了 `mprotect()` syscall。这已经足够区分绝大多数调用理由（mmap/munmap/mprotect/mremap/brk）。

#### 3.2 User Stacktrace（用户态调用链）

Kernel stacktrace 只能看到 syscall 入口，**看不到用户态调用链**（如 `dlopen -> Linker::LoadSegments -> mmap`）。获取用户栈有三种方案：

**方案 A：Perfetto（推荐，事件触发 + 时间轴天然对齐）**

Perfetto 能把 `ftrace` 内核事件和 `perf` callstack sampling 放在**同一条时间线**上。Android 官方明确说明 Perfetto 可从 ftrace、atrace、heapprofd 等数据源收集数据并组合成同一个 timeline trace。([developer.android.google.cn](https://developer.android.google.cn/tools/perfetto?hl=zh-cn))

关键点：
- `linux.ftrace` 采集内核事件（如 `syscalls/sys_enter_mprotect`, `mm/vma_start_write`）
- `linux.perf` 在 tracepoint 触发时抓 callstack，而非周期采样
- 时间戳天然对齐，无需手动 offset 计算
- 支持按 PID/TID/comm filter，避免全系统炸掉

```protobuf
duration_ms: 30000
buffers { size_kb: 65536 fill_policy: DISCARD }

data_sources {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "syscalls/sys_enter_mprotect"
      ftrace_events: "syscalls/sys_exit_mprotect"
      ftrace_events: "syscalls/sys_enter_mmap"
      ftrace_events: "syscalls/sys_exit_mmap"
      # 如果 kernel 里有自定义 tracepoint：
      # ftrace_events: "mm/vma_start_write"
      ftrace_events: "sched/sched_switch"
      buffer_size_kb: 8192
    }
  }
}

data_sources {
  config {
    name: "linux.perf"
    perf_event_config {
      timebase {
        period: 1
        tracepoint {
          name: "syscalls/sys_enter_mprotect"
          # 建议加 filter 避免全系统高频炸：
          # filter: "common_pid == 12345"
        }
        timestamp_clock: PERF_CLOCK_MONOTONIC
      }
      callstack_sampling {
        kernel_frames: true
        user_frames: true
      }
      ring_buffer_pages: 2048
    }
  }
}

data_sources {
  config {
    name: "linux.process_stats"
    process_stats_config { scan_all_processes_on_start: true }
  }
}
```

启动：
```bash
adb push /path/to/perfetto_config.pbtxt /data/local/tmp/
adb shell "perfetto -c /data/local/tmp/perfetto_config.pbtxt -o /data/local/tmp/perfetto_trace.pb"
```

解析：
```bash
adb pull /data/local/tmp/perfetto_trace.pb /tmp/
# 用 Perfetto UI 打开，或用 trace_processor 查询
```

**方案 B：simpleperf 事件触发（精确事件采样，非周期采样）**

不要用 `simpleperf record -a -e cycles -g`（周期采样无法精确对应每个事件）。改用 tracepoint 触发：

```bash
# 检查设备支持的 tracepoint
adb shell su 0 simpleperf list | grep -E 'mprotect|vma|syscalls|mm'

# 按事件触发（-c 1 表示每次事件触发一个样本）
adb shell su 0 simpleperf record \
  -p <pid> \
  -e syscalls:sys_enter_mprotect \
  -c 1 \
  -g --call-graph dwarf \
  --duration 10 \
  -o /data/local/tmp/mprotect.data

# 解析必须用 Android NDK 里的 simpleperf/report，不要优先用 host Linux perf
adb pull /data/local/tmp/mprotect.data .
./report.py -i mprotect.data -g
./report_html.py -i mprotect.data
```

如果 kernel 暴露了 `mm:vma_start_write`：
```bash
adb shell su 0 simpleperf record \
  -p <pid> \
  -e mm:vma_start_write \
  -c 1 \
  -g --call-graph dwarf \
  --duration 10 \
  -o /data/local/tmp/vma.data
```

注意事项：
- `simpleperf report` 解析需要设备上的符号（unstripped .so）
- APK 里的 native libs 通常被 Android Studio strip，需 unstripped native libs 建 binary_cache
- Java/Kotlin 被 R8/ProGuard 混淆后，需要 mapping.txt 还原符号

**方案 C：在 tracepoint 中内联 user PC（需要改代码）**

如果前两种方案的数据量或精度不够，可以在 tracepoint 的 `TP_fast_assign` 中加入：

```c
#ifdef CONFIG_ARM64
__entry->user_pc = current_pt_regs()->pc;
#else
__entry->user_pc = 0;
#endif
```

然后用 `addr2line` 解析 `user_pc` 到用户态符号。但这需要修改 `filemap.h`/`mmap_lock.h` 并重新编译内核，**仅在 simpleperf/perfetto 无法提供足够精度时使用**。

#### 3.3 结合使用：Kernel Stack + User Stack

**推荐组合（Perfetto 方案）**：

```bash
# 1. 开启 kernel stacktrace（零代码改动）
for evt in mmap_lock/vma_start_write_begin filemap/filemap_fault_begin; do
    adb shell "su -c 'echo 1 > /sys/kernel/debug/tracing/events/$evt/stacktrace'"
done

# 2. 同时启动 Perfetto（内含 ftrace + perf callstack）
adb push /path/to/perfetto_config.pbtxt /data/local/tmp/
adb shell "perfetto -c /data/local/tmp/perfetto_config.pbtxt -o /data/local/tmp/perfetto_trace.pb" &

# 3. 启动 trace_pipe 采集内核 event（作为备份/补充）
python3 scripts/capture_trace_pipe.py --outdir "$OUTDIR" ...
```

**关联方法**：
- `trace_pipe` 中的 `caller=%pS` 告诉你 kernel 层面的 syscall（mmap/mprotect/munmap）
- Perfetto 中的 `linux.perf` callstack 在同一时间轴上展示用户态调用链
- 两者在 Perfetto UI 中**天然对齐**，无需手动 pid + timestamp 关联

```
Perfetto timeline:
  [100.123s] sched_switch: app_process -> kworker
  [100.124s] ftrace: sys_enter_mprotect (pid=12345)
  [100.124s] perf callstack: libart.so art::JNI::CallVoidMethodA
                                => libapp.so Java_com_example_loadLib
                                => libc.so mprotect
  [100.125s] ftrace: vma_start_write_begin (caller=mprotect_fixup)
```

**simpleperf 关联方法**（当使用 simpleperf 而非 Perfetto 时）：
- trace 中的 `mmap_lock_wait_start` 告诉你内核层面何时等待
- simpleperf 的调用栈告诉你这个 mmap 是从哪个 Java/Native 函数调下来的
- 两者通过 **pid + 时间戳** 关联
- **注意**：trace clock 和 simpleperf clock 可能使用不同基准，需要验证对齐方式

**方案 B：perfetto（轻量，可与 trace 并行）**

在 perfetto config 中加入 callstack sampling：

```protobuf
buffers: { size_kb: 65536 }
data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "raw_syscalls/sys_enter"
      ftrace_events: "raw_syscalls/sys_exit"
      buffer_size_kb: 8192
    }
  }
}
data_sources: {
  config {
    name: "linux.perf"
    perf_config {
      all_cpus: true
      sampling_frequency: 1000
      callstack_sampling {
        kernel_frames: true
        user_frames: true
      }
    }
  }
}
data_sources: {
  config {
    name: "linux.process_stats"
    process_stats_config {
      scan_all_processes_on_start: true
    }
  }
}
```

**方案 C：在 tracepoint 中内联 user PC（需要改代码）**

如果前两种方案的数据量或精度不够，可以在 tracepoint 的 `TP_fast_assign` 中加入：

```c
#ifdef CONFIG_ARM64
__entry->user_pc = current_pt_regs()->pc;
#else
__entry->user_pc = 0;
#endif
```

然后用 `addr2line` 解析 `user_pc` 到用户态符号。但这需要修改 `filemap.h`/`mmap_lock.h` 并重新编译内核，**仅在 simpleperf/perfetto 无法提供足够精度时使用**。

#### 3.3 结合使用：Kernel Stack + User Stack

**推荐组合**：

```bash
# 1. 开启 kernel stacktrace（零代码改动）
for evt in mmap_lock/vma_start_write_begin filemap/filemap_fault_begin; do
    adb shell "su -c 'echo 1 > /sys/kernel/debug/tracing/events/$evt/stacktrace'"
done

# 2. 同时启动 simpleperf 采集用户栈（60 秒窗口）
adb shell "nohup simpleperf record -g -e raw_syscalls:sys_enter_mmap,raw_syscalls:sys_enter_mprotect -a --duration 60 -o /data/local/tmp/simpleperf_$(date +%s).data > /dev/null 2>&1 &"

# 3. 启动 trace_pipe 采集内核 event
python3 scripts/capture_trace_pipe.py --outdir "$OUTDIR" ...
```

**关联方法**：
- `trace_pipe` 中的 `caller=%pS` 告诉你 kernel 层面的 syscall（mmap/mprotect/munmap）
- `simpleperf.data` 中的用户栈告诉你这个 syscall 是从哪个 so/函数调用的（如 `linker::LoadSegments`）
- 两者通过 **pid + 时间戳** 关联

### 4. 启动 trace_pipe（实时流，不丢数据）

#### 方案 A：Python 智能分片（推荐）

```bash
OUTDIR="/tmp/mmap_lock_test_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

python3 scripts/capture_trace_pipe.py \
  --outdir "$OUTDIR" \
  --chunk-lines 500000 \
  --chunk-size-mb 500 \
  --compress \
  > "$OUTDIR/capture.log" 2>&1 &
CAPTURE_PID=$!
```

特性：
- 按行数和文件大小双重限制分片
- 可选 gzip 压缩（level=1，速度优先）
- 保证行完整性（不会在行中间切断）
- 优雅处理 SIGINT/SIGTERM
- 支持指定 ADB serial

#### 方案 B：简单 split（按大小）

```bash
OUTDIR="/tmp/mmap_lock_test_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

(
    count=1
    while true; do
        adb -s $SERIAL shell "su -c 'cat /sys/kernel/debug/tracing/trace_pipe'" | \
            split -b 500m - "$OUTDIR/trace_stream_${count}_"
        count=$((count+1))
    done
) &
```

注意：`split -b` 按字节分片，可能会在行中间切断。分析前需要处理。

#### 方案 C：按时间窗口轮转

```bash
OUTDIR="/tmp/mmap_lock_test_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

while true; do
  ts=$(date +%Y%m%d_%H%M%S)
  timeout 300 adb shell "su -c 'cat /sys/kernel/debug/tracing/trace_pipe'" > \
    "$OUTDIR/trace_stream_${ts}.txt" 2>/dev/null
done &
```

注意：`timeout` 会中断 adb 连接，可能丢数据。不推荐长时间采集。

### 5. 启动 reclaim 压力

```bash
nohup bash -c "count=0; while true; do sleep 15; count=\$((count+1)); adb shell 'su -c \"echo 3 > /proc/sys/vm/drop_caches\"' > /dev/null 2>&1; adb shell 'su -c \"echo 1 > /proc/sys/vm/compact_memory\"' > /dev/null 2>&1; echo \"reclaim #\$count\" >> $OUTDIR/reclaim.log; done" > /dev/null 2>&1 &
```

### 6. 启动 memstress（高频 app 轮转）

```bash
cd .agents/skills/android-thp-fallback-sampler
nohup python3 scripts/run_memstress_and_collect_logs.py \
  --serial <SERIAL> \
  --max-cycles 10000 \
  --interval-s 60 \
  --package-file top98_packages.txt \
  --heavy-package com.tencent.tmgp.pubgmhd \
  --heavy-package com.tencent.tmgp.sgame \
  --heavy-package tv.danmaku.bili \
  --burst-size 6 \
  --heavy-per-burst 4 \
  --hold-ms 500 \
  --launch-gap-ms 200 \
  --cycle-sleep-ms 500 \
  --use-su \
  --no-thp-ensure \
  --out-dir "$OUTDIR/memstress" \
  > "$OUTDIR/memstress.log" 2>&1 &
```

### 7. 同时捕获 logcat

```bash
adb -s $SERIAL logcat -v threadtime > "$OUTDIR/logcat.txt" &
```

### 8. 同时捕获 perfetto

```bash
cat > /tmp/perfetto_config.pbtxt << 'EOF'
buffers: { size_kb: 65536 }
data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "raw_syscalls/sys_enter"
      ftrace_events: "raw_syscalls/sys_exit"
      buffer_size_kb: 8192
    }
  }
}
data_sources: {
  config {
    name: "linux.process_stats"
    process_stats_config {
      scan_all_processes_on_start: true
    }
  }
}
EOF

adb -s $SERIAL push /tmp/perfetto_config.pbtxt /data/local/tmp/
adb -s $SERIAL shell "perfetto -c /data/local/tmp/perfetto_config.pbtxt -o /data/local/tmp/perfetto_trace.pb" &
```

## 增强 Tracepoint

### vma_start_write_begin_v2

在 `include/trace/events/mmap_lock.h` 中新增：

```c
TRACE_EVENT(vma_start_write_begin_v2,
    TP_PROTO(struct vm_area_struct *vma, unsigned long vm_flags,
             unsigned long caller_ip),
    TP_ARGS(vma, vm_flags, caller_ip),
    
    TP_STRUCT__entry(
        __field(int, pid)
        __field(int, tgid)
        __field(unsigned long, vm_start)
        __field(unsigned long, vm_end)
        __field(unsigned long, vm_flags)
        __field(unsigned long, caller_ip)
        __field(unsigned long, ino)
        __field(unsigned int, major)
        __field(unsigned int, minor)
        __array(char, comm, TASK_COMM_LEN)
    ),
    
    TP_fast_assign(
        __entry->pid = current->pid;
        __entry->tgid = current->tgid;
        __entry->vm_start = vma->vm_start;
        __entry->vm_end = vma->vm_end;
        __entry->vm_flags = vm_flags;
        __entry->caller_ip = caller_ip;
        memcpy(__entry->comm, current->comm, TASK_COMM_LEN);
        
        if (vma->vm_file && vma->vm_file->f_inode) {
            __entry->ino = vma->vm_file->f_inode->i_ino;
            __entry->major = MAJOR(vma->vm_file->f_inode->i_sb->s_dev);
            __entry->minor = MINOR(vma->vm_file->f_inode->i_sb->s_dev);
        } else {
            __entry->ino = 0;
            __entry->major = 0;
            __entry->minor = 0;
        }
    ),
    
    TP_printk("pid=%d tgid=%d comm=%s vm_start=%lx vm_end=%lx "
              "flags=%lx caller=%pS ino=%lu dev=%u:%u",
        __entry->pid, __entry->tgid, __entry->comm,
        __entry->vm_start, __entry->vm_end,
        __entry->vm_flags, (void *)__entry->caller_ip,
        __entry->ino, __entry->major, __entry->minor)
);
```

### 竞争配对 Tracepoint

```c
TRACE_EVENT(vma_start_write_blocked,
    TP_PROTO(struct vm_area_struct *vma, unsigned long wait_ns,
             int fault_pid, int fault_tgid),
    TP_ARGS(vma, wait_ns, fault_pid, fault_tgid),
    
    TP_STRUCT__entry(
        __field(int, write_pid)
        __field(int, write_tgid)
        __field(int, fault_pid)
        __field(int, fault_tgid)
        __field(unsigned long, wait_ns)
        __field(unsigned long, vm_start)
        __field(unsigned long, vm_end)
        __array(char, write_comm, TASK_COMM_LEN)
        __array(char, fault_comm, TASK_COMM_LEN)
    ),
    
    TP_fast_assign(
        __entry->write_pid = current->pid;
        __entry->write_tgid = current->tgid;
        __entry->fault_pid = fault_pid;
        __entry->fault_tgid = fault_tgid;
        __entry->wait_ns = wait_ns;
        __entry->vm_start = vma->vm_start;
        __entry->vm_end = vma->vm_end;
        memcpy(__entry->write_comm, current->comm, TASK_COMM_LEN);
    ),
    
    TP_printk("write_pid=%d write_tgid=%d write_comm=%s "
              "fault_pid=%d fault_tgid=%d fault_comm=%s "
              "wait_ns=%lu vm=[%lx,%lx]",
        __entry->write_pid, __entry->write_tgid, __entry->write_comm,
        __entry->fault_pid, __entry->fault_tgid, __entry->fault_comm,
        __entry->wait_ns, __entry->vm_start, __entry->vm_end)
);
```

### mmap_lock 阻塞检测

```c
TRACE_EVENT(mmap_lock_write_blocked,
    TP_PROTO(struct mm_struct *mm, int waiter_pid, unsigned long wait_ns),
    TP_ARGS(mm, waiter_pid, wait_ns),
    
    TP_STRUCT__entry(
        __field(int, holder_pid)
        __field(int, holder_tgid)
        __field(int, waiter_pid)
        __field(unsigned long, wait_ns)
        __array(char, holder_comm, TASK_COMM_LEN)
        __array(char, waiter_comm, TASK_COMM_LEN)
    ),
    
    TP_fast_assign(
        __entry->holder_pid = current->pid;
        __entry->holder_tgid = current->tgid;
        __entry->waiter_pid = waiter_pid;
        __entry->wait_ns = wait_ns;
        memcpy(__entry->holder_comm, current->comm, TASK_COMM_LEN);
    ),
    
    TP_printk("holder_pid=%d holder_tgid=%d holder_comm=%s "
              "waiter_pid=%d waiter_comm=%s wait_ns=%lu",
        __entry->holder_pid, __entry->holder_tgid, __entry->holder_comm,
        __entry->waiter_pid, __entry->waiter_comm,
        __entry->wait_ns)
);
```

## 分析 trace 数据

### 一键分析

```bash
python3 scripts/analyze_contention.py "$OUTDIR/trace_stream.txt"
```

输出：
- 终端统计摘要
- `$OUTDIR/trace_stream.summary.json` 结构化数据

### 进程生命周期分析

分析每个进程的 mmap/munmap/mprotect/fork/fault 时间轴，识别 syscall 生命周期阶段和 refault 候选：

```bash
python3 scripts/analyze_lifecycle.py "$OUTDIR/trace/"
```

输出：
- 每个 TGID 的时间线（前/中/后期事件分布）
- Syscall 分布（fork/mmap/munmap/mprotect/split/expand）
- File vs Anonymous VMA 比例
- Refault 候选（同一地址在多个时间窗口 fault）
- Filemap fault 统计（等待时间、进程分布）

示例输出：
```
=== main (TGID=896, events=30048, span=0.331s) ===
Early (0-20%): 3251 events
  dup_mmap (fork)
  dup_mmap (fork)
Middle (20-80%): 0 events
Late (80-100%): 260 events
  dup_mmap

=== pidof (TGID=6293, events=9470, span=0.455s) ===
Early: munmap/exit
Mid: faults: 10, vma_writes: 924
Late: mmap

REFAULT ANALYSIS:
  addr=0x7bf56fc000 count=3 span=0.300s
  addr=0x7bf56fb000 count=3 span=0.235s
```

### 手动分析片段

#### 统计事件数量

```python
import re
from collections import defaultdict, Counter

OUTDIR = "/tmp/mmap_lock_test_xxx"

events = []
with open(f"{OUTDIR}/trace_stream.txt") as f:
    for line in f:
        if any(x in line for x in ['filemap_fault_begin', 'vma_start_write_begin', 
                                     'vma_start_write_wait_start', 'vma_start_write_wait_end']):
            parts = line.strip().split()
            if len(parts) < 5:
                continue
            ts = None
            for p in parts:
                try:
                    ts = float(p.rstrip(':'))
                    if ts > 1000:
                        break
                except:
                    continue
            if ts is None:
                continue
            
            pid_field = parts[0]
            pid = pid_field.split('-')[-1] if '-' in pid_field else "0"
            
            if 'filemap_fault_begin' in line:
                m = re.search(r'address=([0-9a-fA-F]+)', line)
                if m:
                    addr = int(m.group(1), 16)
                    events.append((ts, pid, 'fault', addr))
            elif 'vma_start_write_begin' in line:
                m1 = re.search(r'vm_start=([0-9a-fA-F]+)', line)
                m2 = re.search(r'vm_end=([0-9a-fA-F]+)', line)
                m3 = re.search(r'caller=([^ ]+)', line)
                if m1 and m2:
                    vm_start = int(m1.group(1), 16)
                    vm_end = int(m2.group(1), 16)
                    caller = m3.group(1) if m3 else "unknown"
                    events.append((ts, pid, 'write', vm_start, vm_end, caller))

contention_callers = Counter()
contention_details = []

fault_events = [(ts, pid, addr) for ts, pid, evtype, *rest in events if evtype == 'fault']
write_events = [(ts, pid, start, end, caller) for ts, pid, evtype, start, end, caller in events if evtype == 'write']

for fts, fpid, faddr in fault_events:
    for wts, wpid, wstart, wend, caller in write_events:
        if fpid == wpid and wstart <= faddr < wend and abs(wts - fts) < 0.001:
            contention_callers[caller] += 1
            if len(contention_details) < 10:
                contention_details.append({
                    'pid': fpid,
                    'caller': caller,
                    'delta_ms': abs(wts - fts) * 1000,
                    'fault_addr': hex(faddr),
                    'vma_range': f"[{hex(wstart)}, {hex(wend)}]"
                })
            break

print("=== Contention by Caller ===")
for caller, count in contention_callers.most_common():
    print(f"  {caller}: {count}")

print(f"\nTotal: {sum(contention_callers.values())}")

if contention_details:
    print("\n=== Samples ===")
    for d in contention_details[:5]:
        print(f"  PID={d['pid']} caller={d['caller']} delta={d['delta_ms']:.3f}ms")
        print(f"    fault={d['fault_addr']} vma={d['vma_range']}")
```

#### 分析等待时间

```python
import re
from collections import defaultdict

OUTDIR = "/tmp/mmap_lock_test_xxx"

pid_wait_times = defaultdict(list)
current_wait = {}

with open(f"{OUTDIR}/trace_stream.txt") as f:
    for line in f:
        if 'vma_start_write_wait_start' in line or 'vma_start_write_wait_end' in line:
            parts = line.strip().split()
            ts = None
            for p in parts:
                try:
                    ts = float(p.rstrip(':'))
                    if ts > 1000:
                        break
                except:
                    continue
            if ts is None:
                continue
            
            pid = parts[0].split('-')[-1] if '-' in parts[0] else "0"
            
            if 'wait_start' in line:
                current_wait[pid] = ts
            elif 'wait_end' in line and pid in current_wait:
                wait_ms = (ts - current_wait[pid]) * 1000
                pid_wait_times[pid].append(wait_ms)
                del current_wait[pid]

all_waits = []
for pid, waits in sorted(pid_wait_times.items(), key=lambda x: -len(x[1]))[:15]:
    avg = sum(waits) / len(waits)
    max_wait = max(waits)
    all_waits.extend(waits)
    print(f"PID {pid}: {len(waits)} waits, avg={avg:.3f}ms, max={max_wait:.3f}ms")

if all_waits:
    print(f"\nOverall: {len(all_waits)} waits, avg={sum(all_waits)/len(all_waits):.3f}ms, max={max(all_waits):.3f}ms")
    print(f">1ms: {sum(1 for w in all_waits if w > 1)}, >10ms: {sum(1 for w in all_waits if w > 10)}")
```

#### 检查 VMA 类型

```python
import re

with open("/tmp/mmap_lock_test_xxx/trace_stream.txt") as f:
    for i, line in enumerate(f):
        if 'vma_start_write_begin' in line:
            m = re.search(r'flags=([0-9a-fA-F]+)', line)
            if m:
                flags = int(m.group(1), 16)
                is_file = flags & 0x40
                is_anon = flags & 0x100000
                is_shared = flags & 0x08
                caller = re.search(r'caller=([^ ]+)', line)
                caller_name = caller.group(1) if caller else "unknown"
                print(f"flags=0x{m.group(1)} FILE={bool(is_file)} ANON={bool(is_anon)} SHARED={bool(is_shared)} caller={caller_name}")
        if i > 1000:
            break
```

## 竞争类型分类

### 事件代数

```text
F_begin(t, pid, mm, vma, dev, ino, pgoff)        -- filemap_fault 入口
F_wait_start(t, pid, mm, vma, reason)            -- 开始等待
F_wait_end(t, pid, mm, vma, reason)              -- 等待结束
F_retry(t, pid, mm, vma, ret, reason)            -- 返回 VM_FAULT_RETRY
F_end(t, pid, mm, vma, ret)                      -- 正常返回

V_begin(t, pid, mm, vma, vm_start, vm_end)       -- vma_start_write 入口
V_wait_start(t, pid, mm, vma, vm_start, vm_end)  -- 开始等 VMA refcnt
V_wait_end(t, pid, mm, vma, vm_start, vm_end)    -- VMA refcnt 就绪
V_done(t, pid, mm, vma, vm_start, vm_end)        -- vm_lock_seq 写入完成

M_wait_start(t, pid, mm, write)                  -- mmap_lock 等待开始
M_wait_end(t, pid, mm, write)                    -- mmap_lock 等待结束
M_hold_start(t, pid, mm, write)                  -- mmap_lock 持有开始
M_hold_end(t, pid, mm, write)                    -- mmap_lock 持有释放
```

### Type-A：filemap_fault I/O wait 阻塞 vma_start_write

语义：低优先级 fault 线程持有 VMA read lock（等 I/O），高优先级 writer 线程在 `vma_start_write()` 中等待同一 VMA 的 write lock。

形式化：

```python
overlap(I_a, I_b) = I_a.start < I_b.end and I_b.start < I_b.end
same_vma(e_a, e_b) = e_a.mm == e_b.mm and e_a.vma == e_b.vma

type_a = {
    (fault_i, vma_j) |
        overlap(I_fault_wait(fault_i), I_vma_wait(vma_j))
        and same_vma(fault_i, vma_j)
}
```

### Type-B：filemap_fault retry 阻塞在 mmap_lock

语义：retry 后的 fault 路径需要重新获取 mmap_lock，而此时同一 mm 上有 writer 持有 mmap_write_lock。

形式化：

```python
type_b = {
    (fault_i, mmap_j) |
        F_retry(fault_i) exists
        and overlap(
            interval(F_retry(fault_i), F_begin(fault_{i+1})),
            I_mmap_wait(mmap_j)
        )
        and same_mm(fault_i, mmap_j)
        and mmap_j.write == true
}
```

### Type-C：优先级反转

语义：Type-A 或 Type-B 的重叠中，writer 线程的优先级高于 fault 线程。

形式化（需要 sched:sched_switch）：

```python
priority(fault_i) = 从 sched_switch 中 fault_i.pid 的 prev_prio/next_prio
priority(vma_j)   = 从 sched_switch 中 vma_j.pid 的 prev_prio/next_prio

type_c = {
    (fault_i, vma_j) |
        (fault_i, vma_j) in type_a
        and priority(vma_j) < priority(fault_i)
}
```

## 判定决策树

```text
if type_a_count == 0 and type_c_count == 0:
    结论：Priority inversion 在当前 workload 中未被观察到
    行动：继续扩大 workload 覆盖

elif type_c_count > 0 and type_c_score > threshold:
    结论：优先级反转真实存在
    行动：停止 pure rip-out，改推 Option 1（retry 后仍走 per-VMA lock）

elif type_a_count > 0 but type_c_count == 0:
    结论：有竞争但无优先级反转（同优先级线程竞争）
    行动：量化竞争对性能的影响

elif type_b_count > 0 and type_b_retry_to_mmap_lock_ns >> t_folio_wait:
    结论：retry 路径的 mmap_lock 阻塞比原 I/O 等待更长
    行动：rip-out 的收益明确
```

## 输出指标汇总

每个 workload 输出：

```json
{
  "workload": "Settings cold start",
  "kernel": "baseline",
  "filemap_fault": {
    "total_count": 1265,
    "retry_count": 8,
    "retry_rate_pct": 0.63,
    "wait_durations_us": {"min": 18, "median": 291, "max": 1460}
  },
  "vma_writer": {
    "total_count": 342,
    "wait_durations_us": {"min": 0, "median": 2, "max": 120}
  },
  "mmap_lock": {
    "wait_durations_us": {"min": 0, "median": 1, "max": 4187},
    "hold_durations_us": {"min": 0, "median": 2, "max": 43857}
  },
  "contention": {
    "type_a_count": 0,
    "type_a_duration_sum_us": 0,
    "type_b_count": 0,
    "type_b_retry_to_mmap_lock_sum_us": 0,
    "type_c_count": 0,
    "type_c_score_us": 0
  }
}
```

## 关键发现标准

**竞争证据：**
- 同一个 PID
- filemap_fault 地址在 vma_start_write 的 [vm_start, vm_end] 范围内
- 时间差 < 1ms

**优先级反转证据：**
- 上述竞争 + caller 来自 mmap_write_lock 保护的路径（munmap/mprotect/mmap_region）
- 等待时间 > 1ms（甚至 >10ms）

## 已知竞争路径（同时持有 mmap_lock + vma_lock）

| Caller | mmap_lock | vma_lock | 场景 |
|--------|-----------|----------|------|
| `vms_gather_munmap_vmas` | 写锁 | 有 | 进程退出/munmap |
| `mmap_region` | 写锁 | 有 | 创建新 VMA |
| `vma_expand` | 写锁 | 有 | VMA 扩展 |
| `__split_vma` | 写锁 | 有 | VMA 分裂 |
| `mprotect_fixup` | 写锁 | 有 | mprotect |
| `vma_modify` | 写锁 | 有 | VMA 修改 |
| `do_brk_flags` | 写锁 | 有 | 堆扩展 |

## 符号表获取指南

没有符号表 = 只能看到十六进制地址，无法还原 `dlopen`、`JNI`、ART 调用链。

### App Native `.so`

问 **App 构建/CI/Release owner** 要：

```text
同一个版本、同一个 ABI、同一个 build-id 的 unstripped .so
或者 native debug symbols zip
```

通常要的是：

```text
arm64-v8a 的未 strip libxxx.so
R8/ProGuard mapping.txt
对应 APK/AAB 的 versionCode / git sha / build id
```

simpleperf 官方建议用 `app_profiler.py -lib <unstripped_dir>` 或 `binary_cache_builder.py -lib <NATIVE_LIB_DIR>` 生成 symbol cache。([Android Git Repositories](https://android.googlesource.com/platform/system/extras/%2B/master/simpleperf/doc/android_application_profiling.md))

### Java/Kotlin 符号

问 **Android app release/build team** 要：

```text
mapping.txt
```

如果 Java/Kotlin 被 R8/ProGuard 混淆，没有 mapping 只能看到混淆名。simpleperf 报告脚本支持传 ProGuard mapping 文件做还原。([Android Git Repositories](https://android.googlesource.com/platform/system/extras/%2B/master/simpleperf/doc/android_application_profiling.md))

Android P 及以上 simpleperf 支持 Java 代码 profiling，包括 interpreter/JIT/AOT 场景。([Android Git Repositories](https://android.googlesource.com/platform/system/extras/%2B/master/simpleperf/doc/jit_symbols.md))

### Android Framework / ART / bionic / system server

问 **平台构建 team / ROM team / OEM / BSP team** 要：

```text
exact build fingerprint 对应的 out/target/product/<device>/symbols/
```

比如：

```text
symbols/system/lib64/libart.so
symbols/system/lib64/libc.so
symbols/apex/...
```

simpleperf 对 Android 系统环境有特殊支持，例如 Android O 起能读系统库里的 `.gnu_debugdata`，但这不等于所有函数名都一定完整。([Android Git Repositories](https://android.googlesource.com/platform/system/extras/%2B/master/simpleperf/doc))

### Kernel 符号

问 **kernel/BSP/OEM team** 要：

```text
vmlinux
System.map
kernel modules with debug symbols
exact kernel build config / commit / build id
```

如果是商用机 user build，很多时候你拿不到完整 kernel symbols，只能看 kallsyms 暴露的部分名字，或者看到地址。

## 常见陷阱

1. **必须使用 trace_pipe**（`/sys/kernel/debug/tracing/trace_pipe`），不能用 trace buffer，否则会被 lmkd 事件覆盖
2. **禁用 sched_switch** 减少噪音
3. **所有进程运行在 Host**，不会被设备 lmkd 杀掉
4. **do_brk_flags 的竞争对象是匿名 VMA**，不是 filemap_fault（需要独立验证）
5. **VM_FILE 标志位**（0x40）可判断是否为文件 VMA
6. **trace 分片时优先使用 Python 脚本**，保证行完整性；`split -b` 会在行中间切断
7. **压缩建议**：trace 数据冗余度高，gzip level=1 即可节省 70%+ 空间，且 CPU 开销极低
8. **trace clock 与 perf clock 可能不对齐**：trace 默认使用 `local` clock，perf 使用 `CLOCK_MONOTONIC`。关联前需验证两者是否使用同一基准，或在 trace 采集前将 trace_clock 设为 `mono`：
   ```bash
   adb shell "su -c 'echo mono > /sys/kernel/debug/tracing/trace_clock'"
   ```
9. **simpleperf 周期采样（cycles）不适合事件因果分析**：`-a -e cycles -g` 采集的是 CPU 周期样本，不是 syscall/tracepoint 的精确事件日志。要精确对应每个 `mprotect`/`mmap`，应使用 tracepoint 触发（`-e syscalls:sys_enter_mprotect -c 1`）或 Perfetto
10. **不要对高频 tracepoint 无过滤地抓 callstack**：对每个 `sched_switch` 都采 callstack 会炸，应通过 `filter: "common_pid == 12345"` 缩小范围
11. **synthetic VMA 竞争测试不要只读固定 offset**：`base[off]` 第一次 fault 后 PTE 通常已经建立，后续循环不会继续进入 `lock_vma_under_rcu()`；`(void)base[off]` 在优化编译下还可能被删掉。要制造稳定 `vma_start_read()` 面积，reader 应按页扫描多个 offset，并用 `volatile`/atomic sink 保留 load。若内核路径里临时加过 `msleep()` / delay，先移除或禁用再做 ABI/build/runtime 验证。

## Workflow Contract: Synthetic VMA Contention Test

### Main Workflow
1. Map a single target file-backed VMA and dirty it once so the file size and mapping are valid.
2. Drop PTEs with `madvise(MADV_DONTNEED)` before starting readers.
3. Create reader and writer threads behind a start gate; release readers first so their page faults can enter `lock_vma_under_rcu()`.
4. After a bounded delay, release writer threads to repeatedly call `mprotect()` on the same VMA.
5. Validate trace by matching `mm`, `vm_start/vm_end`, inode/dev, and `had_reader=true` or non-zero wait duration.
6. Report the page count, reader/writer count, compiler flags, and any kernel-side artificial delay.

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Test design | Trace shows only initial `vma_start_read` events or no `had_reader=true` | Replace fixed-offset read loops with bounded page sweeps across the VMA | `vma_start_read` appears for many page offsets in the same `mm` / VMA | Increase `FAULT_PAGES_PER_READER` or `N_READERS` after preflight confirms there is no artificial delay in the mmap fault path | replace |
| Test scheduling | Writer events appear before target reader events, or readers mostly fall back to `mmap_read_lock` | Gate both sides, release readers first, then release writers after a configurable delay | Trace shows target `vma_start_read` before `vma_start_write_wait` for the same `mm` / VMA | Increase writer delay or reduce writer count for the first proof run | replace |
| Build | Optimized binary emits no useful reader faults | Accumulate loads into a `volatile` or atomic sink | Binary prints or otherwise consumes the sink | Compile with lower optimization only as a fallback | block |
| Preflight | Kernel code contains artificial `sleep` / `msleep()` / delay in the mmap fault or VMA-lock path | Remove it, or isolate it behind an explicit debug-only gate that is disabled for ABI/build validation | `rg -n "msleep|ssleep|usleep|mdelay|udelay" mm/mmap_lock.c arch/*/mm/fault.c include/linux/mmap_lock.h` has no ungated delay in the traced path | Do not run ABI/build validation until the delay is removed or explicitly justified | block |
| Trace capture | Trace output is dominated by the trace helper, shell utilities, or unrelated short-lived processes | Start the workload behind a gate, get its TGID, set per-event tracefs filters such as `tgid == $TGID`, then release the workload | `trace.status` records the TGID filter, and trace rows show only the target TGID / target VMA identity | Discard the unfiltered run as non-diagnostic, then rerun with TGID filtering before interpreting `had_reader` or fallback absence | replace |
| Trace validation | Writer events do not overlap the target reader VMA | Match by `mm`, `vm_start/vm_end`, inode/dev, not just process name | Same VMA identity appears in read and write events | Narrow the test to one VMA and one writer pattern | block |

### Output Contract
- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## 用户态调用栈采集（simpleperf）

内核 trace 只能看到 syscall 入口（如 `__arm64_sys_mmap`），看不到用户态调用者（如 `dlopen()`、`JNI`调用）。要关联用户态调用链，需并行采集 simpleperf。

### 启动 simpleperf 并行采集

```bash
bash scripts/capture_simpleperf.sh <SERIAL> <victim_package> <duration_s> <out_dir>
```

示例：
```bash
bash scripts/capture_simpleperf.sh 18281FDF6007HB com.tencent.news 3600 /tmp/simpleperf_out
```

参数：
- `--duration`：采样时长（秒），建议覆盖整个 probe 周期
- `-e raw_syscalls:sys_enter_mmap,mprotect,munmap`：只采集 mmap 相关 syscall
- `-p <PID>`：只采样 victim 进程，减少数据量
- `-g`：采集完整调用栈（内核 + 用户态）

### 解析 simpleperf 数据

```bash
bash scripts/parse_simpleperf.sh /tmp/simpleperf_out/simpleperf_com_tencent_news.data /tmp/simpleperf_out/
```

或手动解析：
```bash
simpleperf report -g --dsos simpleperf_com_tencent_news.data
```

### 关联 trace 和 simpleperf

通过 **pid + 时间戳** 近似关联：
- trace 中的 `mmap_lock_wait_start` 告诉你内核层面何时等待
- simpleperf 的调用栈告诉你这个 mmap 是从哪个 Java/Native 函数调下来的

示例关联：
```
trace:    [100.123s] mmap_lock_wait_start pid=12345 write=true
simpleperf: [100.120s-100.130s] libart.so art::JNI::CallVoidMethodA
                              => libapp.so Java_com_example_loadLib
                              => libc.so dlopen
                              => __arm64_sys_mmap
```

### 脚本列表

| 脚本 | 用途 |
|---|---|
| `scripts/capture_simpleperf.sh` | 在设备上启动 simpleperf 并行采集 |
| `scripts/parse_simpleperf.sh` | Pull 数据并生成调用栈报告 |
| `scripts/analyze_lifecycle.py` | 分析进程生命周期时间轴 |
| `scripts/analyze_contention_v2.py` | 分析内核竞争链 |

## References

- For realistic, pressure-only refault generation without synthetic victim instrumentation, see references/refault_pressure_only_sop.md. Use that SOP when the user requests refault generation that relies only on black-box app pressure and normal user navigation paths.
