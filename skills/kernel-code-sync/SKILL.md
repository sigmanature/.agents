---
name: kernel-code-sync
description: Use when syncing modified kernel code files from a full kernel tree to a lightweight shadow repository, including batch initialization, incremental sync, and auto-daemon workflows.
---

# kernel-code-sync

同步完整内核树中的修改代码文件到轻量级影子仓库，支持分批初始化、增量同步和自动守护模式。

## 使用场景

1. **初始化影子仓库** — 从 `common_my_dec` 提取代码文件（排除 `arch/` 和 `drivers/`），分批推送到远程
2. **增量同步** — 检测 `git status` 中的修改文件，只同步代码文件到影子仓库
3. **commit 级同步** — `--from REF` 抓取已提交的变更（用于 post-commit CI）
4. **自动守护** — 后台定时检测改动并自动推送

## 快速开始

### 1. 初始化影子仓库

```bash
cd ~/learn_os/pixel

# 查看分批计划（dry-run）
bash ~/.agents/skills/kernel-code-sync/init.sh --remote https://github.com/user/repo.git --dry-run

# 正式执行（分批推送，每批 < 50MB，排除 arch/drivers）
bash ~/.agents/skills/kernel-code-sync/init.sh --remote https://github.com/user/repo.git
```

### 2. 日常同步

```bash
# 查看有哪些文件会被同步
bash ~/.agents/skills/kernel-code-sync/sync.sh --status

# 同步并推送
bash ~/.agents/skills/kernel-code-sync/sync.sh --push

# 先拉取远程最新，再推送（避免冲突）
bash ~/.agents/skills/kernel-code-sync/sync.sh --pull --push

# 只同步不推送（本地提交）
bash ~/.agents/skills/kernel-code-sync/sync.sh

# 同步最近一次 commit 的变更（无需未提交改动）
bash ~/.agents/skills/kernel-code-sync/sync.sh --from HEAD~1 --push

# 同步整个分支相对 main 的变更
bash ~/.agents/skills/kernel-code-sync/sync.sh --from main --push --status
```

### 3. 自动守护模式

```bash
# 启动后台守护（每 5 分钟检查一次）
bash ~/.agents/skills/kernel-code-sync/auto.sh --daemon

# 自定义间隔（每 60 秒）
bash ~/.agents/skills/kernel-code-sync/auto.sh --daemon --interval 60

# 查看守护状态
bash ~/.agents/skills/kernel-code-sync/auto.sh --status

# 停止守护
bash ~/.agents/skills/kernel-code-sync/auto.sh --stop
```

## 脚本参数

### init.sh（初始化）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-s, --source` | `common_my_dec` | 源内核树目录 |
| `-d, --dest` | `common_kernel_code` | 影子仓库目录 |
| `-r, --remote` | - | 远程仓库 URL |
| `-b, --branch` | `main` | 分支名 |
| `--batch-size` | `50` | 每批大小（MB） |
| `--dry-run` | - | 只显示分批计划 |
| `--skip-push` | - | 只本地提交，不推送 |

**注意**：`init.sh` 默认排除 `arch/` 和 `drivers/` 目录。如需修改，编辑脚本中的 `find` 命令：

```bash
find "$source_dir" -type f -not -path "$source_dir/arch/*" -not -path "$source_dir/drivers/*" ...
```

### sync.sh（同步）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-s, --source` | `common_my_dec` | 源内核树目录 |
| `-d, --dest` | `common_kernel_code` | 影子仓库目录 |
| `-r, --remote` | - | 远程仓库 URL |
| `-b, --branch` | `main` | 分支名 |
| `-f, --from` | - | 同步 `git diff REF..HEAD` 的变更（用于 post-commit CI） |
| `--push` | - | 提交并推送（自动先 pull，避免冲突） |
| `--pull` | - | 先拉取远程最新（`--push` 时自动执行） |
| `--status` | - | 显示将被同步的文件 |
| `--dry-run` | - | 同 `--status` |

### auto.sh（自动守护）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-s, --source` | `common_my_dec` | 源内核树目录 |
| `-d, --dest` | `common_kernel_code` | 影子仓库目录 |
| `-r, --remote` | - | 远程仓库 URL |
| `-b, --branch` | `main` | 分支名 |
| `-i, --interval` | `300` | 检查间隔（秒） |
| `--daemon` | - | 后台守护模式 |
| `--stop` | - | 停止守护 |
| `--status` | - | 查看守护状态 |

## 工作原理

1. **文件检测**：默认 `git status --porcelain`（未提交改动），`--from REF` 模式用 `git diff REF..HEAD --name-only`（已提交变更）
2. **代码过滤**：只保留 `.c/.h/.S/.py/.sh/Makefile/Kconfig/BUILD.bazel/Android.bp` 等代码文件，排除构建产物和临时文件
3. **树结构保持**：`cp --parents` 保持原始目录结构
4. **分批提交**：大目录按 50MB 分批 `git commit` + `git push`，避免单次推送超限

## CI：Post-Commit 自动同步

在 `~/.repo/projects/common.git/hooks/post-commit` 中部署 hook，每次 `git commit` 后自动将刚提交的变更同步到影子仓库：

```bash
#!/usr/bin/env bash
# Auto-sync committed code changes to common_kernel_code shadow repo.
# Only fires from common_my_dec worktree.
set -u -o pipefail

worktree_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

case "$worktree_root" in
  */common_my_dec|*/common_dec) ;;
  *) exit 0 ;;
esac

sync_script="$HOME/.agents/skills/kernel-code-sync/sync.sh"
log_file="${TMPDIR:-/tmp}/sync_kernel_code_post_commit.log"

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] post-commit: syncing HEAD~1..."
  cd "$HOME/learn_os/pixel" || exit 1
  bash "$sync_script" --from HEAD~1 --push
} >> "$log_file" 2>&1 &
```

查看同步日志：`tail -f /tmp/sync_kernel_code_post_commit.log`

> **注意**：`--push` 会自动先执行 `--pull`。如果推送因冲突被拒，脚本会输出冲突信息并退出（不自动 merge/rebase），本地 commit 保留在影子仓库中等待手工处理。
>
> 冲突输出示例：
> ```
> ============================================
>   PUSH REJECTED — manual intervention required
> ============================================
>   local  HEAD  : a1b2c3d
>   remote main  : e4f5g6h
>   To resolve:
>     cd common_kernel_code
>     git pull --rebase origin main   # integrate remote changes
>     git push origin main             # retry after rebase
> ```

## 文件过滤规则

**保留**：
- 扩展名：`.c`, `.cc`, `.cpp`, `.h`, `.hpp`, `.S`, `.s`, `.rs`, `.py`, `.sh`, `.pl`, `.awk`, `.dts`, `.dtsi`, `.dtso`, `.asn1`, `.ld`, `.bzl`, `.mk`, `.bp`, `.go`, `.inc`, `.tbl`, `.uc`, `.y`, `.l`
- 文件名：`Makefile`, `Kbuild`, `BUILD`, `BUILD.bazel`, `WORKSPACE`, `MODULE.bazel`, `Android.bp`, `Android.mk`, `Kconfig*`

**排除**：
- 构建产物：`*.o`, `*.ko`, `*.mod`, `*.mod.c`, `*.order`, `*.symvers`, `*.cmd`
- 临时文件：`*.tmp`, `*~`, `.*.swp`, `.#*`
- 目录：`arch/`, `drivers/`（初始化时）

## 多机器协作

### 机器 A（开发机）
```bash
# 修改代码后同步
bash ~/.agents/skills/kernel-code-sync/sync.sh --push
```

### 机器 B（公司机器）
```bash
# 拉取最新代码
bash ~/.agents/skills/kernel-code-sync/sync.sh --pull

# 或者直接用 git
cd common_kernel_code
git pull origin main
```

## 故障排查

### 推送被拒（多机器冲突）

sync.sh 遇到 push 被拒时会打印冲突信息并退出，不会自动 rebase。本地 commit 已保留在影子仓库中：

```bash
cd common_kernel_code
git pull --rebase origin main   # 整合远程变更
# 检查冲突文件，手工解决
git push origin main             # 重试推送
```

> 不建议 `--force` push，除非你确定要丢弃远程的改动。

### 影子仓库已存在
```bash
# 删除重新初始化
rm -rf common_kernel_code
bash ~/.agents/skills/kernel-code-sync/init.sh --remote https://github.com/user/repo.git
```

### 守护进程无响应
```bash
# 手动停止并清理
bash ~/.agents/skills/kernel-code-sync/auto.sh --stop
rm -f /tmp/sync_kernel_code_auto_*.pid /tmp/sync_kernel_code_auto_*.log
```

## 目录结构

```
~/.agents/skills/kernel-code-sync/
├── openai.yaml       # Codex 技能描述
├── SKILL.md          # 本文档
├── init.sh           # 初始化脚本
├── sync.sh           # 同步脚本
└── auto.sh           # 自动守护脚本
```

## 依赖

- `bash` >= 4.0
- `git`
- `find` (GNU find)
- `awk` (gawk/mawk)
- `zip`（仅打包脚本需要）
