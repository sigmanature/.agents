# Diff 自检规范

## 什么时候用

改完内核 C 代码后，在编译前快速自检 diff 的括号配对、语法完整性。

## 手动检查清单

### 1. 括号配对
```bash
# 检查改动行附近的花括号是否成对
git diff | grep -E '^\+.*[\{\}]' | head -20
# 视觉检查：左花括号数 == 右花括号数？
```

### 2. 分号完整性
```bash
# 每条新增语句末尾要有分号
git diff | grep '^\+.*[^;\{]$' | grep -vE '^\+\s*#|^\+\s*//|\*|^\+\s*$|^\+.*\\$'
```

### 3. 宏括号匹配
- `TRACE_EVENT(name, ...)` 最后必须以 `);` 结束
- `TP_STRUCT__entry(...)` / `TP_fast_assign(...)` 自身有花括号
- `TP_printk(...)` 是宏参数，不需要额外分号

### 4. 条件语句花括号
```c
// ✗ if 单条语句没花括号
if (flags & MAP_FIXED)
    trace_xxx(...);
    return addr;         // ← 缩进误导，实际上无条件执行

// ✓
if (flags & MAP_FIXED) {
    trace_xxx(...);
    return addr;
}
```

### 5. include 路径
```bash
# 验证新增的 include 文件存在
git diff | grep '^\+#include' | sed 's/.*<\(.*\)>/\1/' | while read f; do
  [ -f "$f" ] || echo "MISSING: $f"
done
```

### 6. CREATE_TRACE_POINTS 唯一性
```bash
# 确认新 tracepoint 的 TRACE_SYSTEM 只有一个 CREATE_TRACE_POINTS 文件
grep -rn "CREATE_TRACE_POINTS.*<新subsystem>" --include='*.c'
# 如果输出 >1 行 → 重复定义 → 链接时 duplicate symbol
```

## 自动检查脚本

```bash
#!/bin/bash
# check_diff.sh — 快速自检 git diff
echo "=== 括号检查 ==="
plus_braces=$(git diff | grep -c '^+.*{')
minus_braces=$(git diff | grep -c '^\+.*}')
echo "  +{ : $plus_braces"
echo "  +} : $minus_braces"
[ "$plus_braces" = "$minus_braces" ] || echo "  [WARN] 花括号不匹配!"

echo "=== 无分号行 ==="
git diff | grep '^\+' | grep -vE '^\+\s*(#|//|/\*|\*|$|\{|$)' | grep -v ';$' | grep -v '\\$'
echo "  (如有输出请检查)"

echo "=== 新增 tracepoint ==="
git diff | grep 'TRACE_EVENT(' | sed 's/.*TRACE_EVENT(//;s/,.*//'
echo "  确认该 tracepoint 只有一个 CREATE_TRACE_POINTS 文件"
```
