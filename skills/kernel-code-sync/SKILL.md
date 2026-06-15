# kernel-code-sync

同步完整内核树中的修改代码文件到轻量级影子仓库，支持分批初始化、增量同步和自动守护模式。

## 使用场景

1. **初始化影子仓库** — 从 `common_my_dec` 提取代码文件（排除 `arch/` 和 `drivers/`），分批推送到远程
2. **增量同步** — 检测 `git status` 中的修改文件，只同步代码文件到影子仓库
3. **自动守护** — 后台定时检测改动并自动推送

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
| `--push` | - | 提交并推送 |
| `--pull` | - | 先拉取远程最新 |
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

1. **文件检测**：`git status --porcelain` 获取 modified/untracked 文件
2. **代码过滤**：只保留 `.c/.h/.S/.py/.sh/Makefile/Kconfig/BUILD.bazel/Android.bp` 等代码文件，排除构建产物和临时文件
3. **树结构保持**：`cp --parents` 保持原始目录结构
4. **分批提交**：大目录按 50MB 分批 `git commit` + `git push`，避免单次推送超限

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

### 推送被拒
```bash
# 强制推送（覆盖远程历史）
cd common_kernel_code
git push origin main --force
```

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
