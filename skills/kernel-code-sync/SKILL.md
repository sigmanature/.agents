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

### sync_back.sh（反向同步）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-s, --source` | `common_kernel_code` | 源影子仓库目录 |
| `-d, --dest` | `common_my_dec` | 目标主内核树目录 |
| `-f, --from` | - | 用 `REF..HEAD` 作为三方比较的 base，覆盖持久化基线 |
| `--state-file` | 自动生成 | 持久化反向同步基线状态文件 |
| `--init-state` | - | 若状态不存在，则把当前影子仓库 `HEAD` 记为初始基线 |
| `--reset-state` | - | 强制把当前影子仓库 `HEAD` 记为新基线 |
| `--status` | - | 显示将回灌的路径和冲突 |
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

## Workflow Contract

### Main Workflow
1. 在源 worktree 中确认当前工作分支和目标远端分支一致；如果长期工作分支就是远端默认分支，先完成本地分支重命名和 upstream 对齐。
2. 安装统一 hook：`post-commit`、`post-merge`、`post-rewrite` 都调用 `sync.sh`。
3. `post-commit` 用 `--from HEAD~1 --push` 同步刚提交的增量；`post-merge` / `post-rewrite` 用 `--push` 同步当前工作树内容。
4. 反向同步 `common_kernel_code -> common_my_dec` 使用持久化基线状态：以上次成功回灌时的影子仓库 commit 为 base，对影子仓库真实增量路径做 `base/source/dest` 三方比较。
5. 只有当 `source` 和 `dest` 都相对同一 `base` 改了同一路径且结果不同，才判定为真实冲突并阻止回灌；否则允许安全自动 hook。
6. hook 和手动执行都必须先完成“全量冲突扫描”，确认本次影子仓库真实增量路径里没有真实冲突，再应用所有安全更新。
7. 通过终端输出和日志文件共同验收；日志文件必须可直接 `tail -f` 观察。

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Preflight | 源仓库当前分支名与长期使用分支不一致，例如本地还停在 `ffs-*` 临时分支但要长期对接远端 `main` | 先把本地分支重命名成目标分支，再设置 upstream 到对应 remote branch | `git branch -vv` 显示当前分支名正确且跟踪目标远端 | 若该分支已被其他 worktree 占用或重名冲突，先清理/改名冲突分支后重试 | block |
| Status Perf | `git status` 因 ahead/behind 计算很慢，且当前分支已经不再需要远端跟踪 | 对该分支执行 `git branch --unset-upstream`，或临时用 `git status -sb --no-ahead-behind` | `git status -sb` 不再显示 `[ahead/behind]`，执行时间回落 | 若后续仍需要远端对齐，再显式重新设置 upstream | continue |
| Hook Install | 使用 repo/shared gitdir worktree，误把 hook 写到 worktree 私有路径 | 用 `git rev-parse --git-common-dir` 定位共享 hooks 目录，再安装统一 hook | `readlink -f "$(git rev-parse --git-common-dir)/hooks"` 下出现 `post-commit` / `post-merge` / `post-rewrite` | 若目录不可写，修正权限或手工写入共享 hook 目录 | replace |
| Sync Trigger | `git pull` 产生 merge 或 `git pull --rebase` 产生 rewrite，但影子仓库没有更新 | `post-merge` / `post-rewrite` 都调用 `sync.sh --push`，不要只依赖 `post-commit` | 执行 pull 后终端立即打印 hook 日志，且日志文件追加新记录 | 若 pull/rebase 后无日志，检查 hook 是否安装在共享 hooks 目录且可执行 | branch |
| Reverse Base | 第一次启用反向同步，或者历史基线不可信 | 先 `--init-state` 或 `--reset-state`，把当前影子仓库 `HEAD` 记为反向同步 base | 状态文件存在，且记录的 `LAST_SOURCE_COMMIT` 等于预期影子仓库 commit | 若 state 缺失或错位，hook/手动执行会拒绝继续，直到显式初始化/重置 | block |
| Reverse Sync | 需要把 `common_kernel_code` 的改动自动或手动回灌到 `common_my_dec`，但目标仓库同路径可能也有修改 | 对影子仓库真实增量路径逐个比较 `base/source/dest` blob；只有 `source!=base` 且 `dest!=base` 且 `source!=dest` 才记为真实冲突 | `--status` 中只有真正冲突的路径被标成 `CONFLICT`；安全路径被标成 `COPY` / `DELETE` / `NOOP` | 若有 conflict，停止整批应用，先人工处理这些路径，再重试 | branch |
| Reverse Hook | 希望把影子仓库提交、pull merge、pull --rebase 自动回灌到主仓库 | 允许安装 `post-commit` / `post-merge` / `post-rewrite` reverse hook，但必须依赖持久化 base 状态和三方冲突扫描 | hook 触发后终端和日志都能看到完整的 planned/apply/conflict 结果；无冲突时 state 自动推进到新 `HEAD` | 若 hook 报冲突，保持主仓库不变并保留 state，不自动跳过或强推 | continue |
| Logging | 需要现场看见同步过程，同时保留故障证据 | hook 用 `tee -a` 同时输出到终端和固定日志文件 | 终端能看到 `start/end rc=`，`tail -f` 同步日志有完整记录 | 若终端看不到，确认 hook 没有后台化；若文件没写入，检查日志目录权限 | continue |

### Output Contract
- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## CI：Commit / Pull 自动同步

推荐直接安装统一 hook 脚本：

```bash
bash ~/.agents/skills/kernel-code-sync/install_hooks.sh
```

安装脚本会自动找到 `git rev-parse --git-common-dir` 对应的共享 hooks 目录，而不是误写到某个 worktree 私有 `.git/hooks`。默认同时安装：

- 正向 hooks：`common_my_dec -> common_kernel_code`
- 反向 hooks：`common_kernel_code -> common_my_dec`

两个方向都会覆盖：

- `post-commit`
- `post-merge`
- `post-rewrite`，覆盖 `git pull --rebase` / `git rebase`

默认日志文件：

```bash
tail -f /tmp/sync_kernel_code_hooks.log
```

hook 不后台化，终端会直接看到同步日志；同时所有输出也会追加到日志文件，便于回看。

反向同步现在允许安全自动 hook，因为 `sync_back.sh` 不再用“目标仓库同路径脏了就拦截”的粗规则，而是使用持久化 base 做三方判断。只有真实冲突才会阻断整批回灌。手动查看计划时：

```bash
bash ~/.agents/skills/kernel-code-sync/sync_back.sh --status
bash ~/.agents/skills/kernel-code-sync/sync_back.sh
```

首次启用或需要重置历史基线时：

```bash
bash ~/.agents/skills/kernel-code-sync/sync_back.sh --init-state
bash ~/.agents/skills/kernel-code-sync/sync_back.sh --reset-state
```

### Hook Templates

下面这两类 hook 值得保留为示例模板，后续移植到别的主机时，只需要改：

- 影子仓库路径
- 主仓库路径
- 日志文件路径
- `--source` / `--dest` 参数

示例 1：正向同步 `post-commit` / `post-merge` / `post-rewrite`

```bash
#!/usr/bin/env bash
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

hook_name="$(basename "$0")"
worktree_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ "$worktree_root" = "/ABS/PATH/TO/common_my_dec" ] || exit 0

sync_script="$HOME/.agents/skills/kernel-code-sync/sync.sh"
source_dir="/ABS/PATH/TO/common_my_dec"
dest_dir="/ABS/PATH/TO/common_kernel_code"
log_file="/tmp/sync_kernel_code_hooks.log"

mkdir -p "$(dirname "$log_file")" 2>/dev/null || true
{
  printf '[%s] %s: start\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$hook_name"
  case "$hook_name" in
    post-commit) bash "$sync_script" --source "$source_dir" --dest "$dest_dir" --from HEAD~1 --push ;;
    post-merge|post-rewrite) bash "$sync_script" --source "$source_dir" --dest "$dest_dir" --push ;;
  esac
  rc=$?
  printf '[%s] %s: end rc=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$hook_name" "$rc"
  exit "$rc"
} 2>&1 | tee -a "$log_file"
```

示例 2：反向同步 `post-commit` / `post-merge` / `post-rewrite`

```bash
#!/usr/bin/env bash
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

hook_name="$(basename "$0")"
worktree_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ "$worktree_root" = "/ABS/PATH/TO/common_kernel_code" ] || exit 0

sync_back_script="$HOME/.agents/skills/kernel-code-sync/sync_back.sh"
source_dir="/ABS/PATH/TO/common_kernel_code"
dest_dir="/ABS/PATH/TO/common_my_dec"
log_file="/tmp/sync_kernel_code_reverse_hooks.log"

mkdir -p "$(dirname "$log_file")" 2>/dev/null || true
{
  printf '[%s] %s: start\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$hook_name"
  bash "$sync_back_script" --source "$source_dir" --dest "$dest_dir"
  rc=$?
  printf '[%s] %s: end rc=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$hook_name" "$rc"
  exit "$rc"
} 2>&1 | tee -a "$log_file"
```

这些模板的核心不需要因主机变化而改动，真正需要调整的是仓库绝对路径和日志落点；如果目标主机的影子仓库命名不同，也只替换 `source_dir` / `dest_dir` 即可。

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

### 从影子仓库回灌到主仓库
```bash
# 先看三方比较后的真实冲突 / 安全更新
bash ~/.agents/skills/kernel-code-sync/sync_back.sh --status

# 按持久化 base 回灌影子仓库真实增量
bash ~/.agents/skills/kernel-code-sync/sync_back.sh

# 首次启用时初始化 base
bash ~/.agents/skills/kernel-code-sync/sync_back.sh --init-state
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
├── install_hooks.sh  # 安装 commit/pull/rebase hook
├── init.sh           # 初始化脚本
├── sync_back.sh      # 影子仓库回灌主仓库
├── sync.sh           # 同步脚本
└── auto.sh           # 自动守护脚本
```

## 依赖

- `bash` >= 4.0
- `git`
- `find` (GNU find)
- `awk` (gawk/mawk)
- `zip`（仅打包脚本需要）
