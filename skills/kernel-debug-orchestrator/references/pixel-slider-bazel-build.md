# Pixel / Slider: Bazel build sanity check

## Rule of thumb

- 想“检查能不能编译”：用 `tools/bazel build ...`（只编译，不执行 install/run）。
- 想“产物/安装脚本”：用 `tools/bazel run ... -- <args>`（注意 `--` 分隔）。

## Run from the correct directory

`build_slider.sh` / `private/google-modules/soc/gs/build_slider.sh` 都是 cwd-sensitive：

- 必须从 `~/learn_os/pixel` 目录运行（否则会找不到 `tools/bazel`）。

## Commands

### Compile-only (recommended)

```bash
cd ~/learn_os/pixel
tools/bazel build --config=raviole --config=fast //private/google-modules/soc/gs:slider_dist
```

也可以 build 更小的目标（仍会触发 kernel build）：

```bash
cd ~/learn_os/pixel
tools/bazel build --config=raviole --config=fast //private/google-modules/soc/gs:slider
```

### Using `build_slider.sh` properly

当前 repo 根目录 `build_slider.sh` 实际执行的是：

```bash
tools/bazel run --config=raviole --config=fast //private/google-modules/soc/gs:slider_dist "$@"
```

所以：

- 直接 `./build_slider.sh build //...:slider` **不是在调用 bazel build**，而是把 `build //...:slider` 当作 *slider_dist 的运行参数*，最终会报 `unrecognized arguments`。
- 如果想给 slider_dist 的 install 脚本传参数，需要：

```bash
cd ~/learn_os/pixel
tools/bazel run --config=raviole --config=fast //private/google-modules/soc/gs:slider_dist -- --help
```

## Lane wrapper gotcha (`debug` vs `my_dec`)

如果使用 repo 根目录的 lane wrapper：

```bash
cd ~/learn_os/pixel
./build_slider.sh --lane my_dec --dry-run
```

先看 wrapper 打印的路径，而不是凭经验找 `out/slider/dist`：

- `workspace=`：Bazel 实际 workspace。`my_dec` 通常是 `out/workspaces/slider_my_dec`。
- `common=`：内核源码目录。`my_dec` 应该是 `common_my_dec`。
- `output_root=`：Bazel output root。`my_dec` 通常是 `out/slider_my_dec`。
- `dist=`：最终镜像目录。`my_dec` 通常是 `out/workspaces/slider_my_dec/out/slider/dist`。

快速确认当前产物和源码输入：

```bash
cd ~/learn_os/pixel
readlink -f out/workspaces/slider_my_dec/common
stat -c '%y %s %n' out/workspaces/slider_my_dec/out/slider/dist/boot.img out/slider/dist/boot.img
rg -n 'source_fs/f2fs/data.o|common_my_dec/fs/f2fs/data.c' out/slider_my_dec/cache/*/common/fs/f2fs/.data.o.cmd
```

判断规则：

- 如果 `.data.o.cmd` 里的 `source_fs/f2fs/data.o` 指向 `common_my_dec/fs/f2fs/data.c`，说明 kbuild compile action 的源码输入已经是 development lane。
- 如果 `out/workspaces/slider_my_dec/out/slider/dist/boot.img` 更新时间比根目录 `out/slider/dist/boot.img` 新，刷机/比较时应使用前者。
- 根目录 `out/slider/dist` 可能是 debug/root workspace 的旧产物；不能用它判断 `--lane my_dec` 是否吃到了源码修改。

## Git worktree gotcha (when building against a specific branch)

如果你用 git worktree 同时开了多个工作目录，可能会遇到：

```text
fatal: '<branch>' is already used by worktree at '<path>'
```

这时为了“临时编译验证某个 branch 的内容”，推荐在 `pixel/common` 这个路径上：

```bash
git switch --detach <branch>
```

避免和另一个 worktree 抢同一个 branch。

## Bazel glob / symlink loop pitfall (common)

如果你在 `pixel/common/` 或 `pixel/common_my_dec/` 目录下手动跑过 kbuild（例如 `make O=pixel/common/output/...` 或 `make O=pixel/common_my_dec/output/...`），`O=` 目录里可能会自动生成：

- `source -> <kernel srctree>`

这会导致 Bazel 在加载 `//common` 的 `glob()` 时出现循环 symlink 错误：

- `Symlink issue while evaluating globs: Infinite symlink expansion: .../pixel/common/output/.../source -> .../pixel/common`

也可能造成更隐蔽的 lane 混淆：

- `pixel/common_my_dec/output/compile_pixel_common_my_dec/source -> /home/nzzhao/learn_os/pixel/common`
- 这不是 `my_dec` lane 的源码选择结果，而是旧 `O=` build tree 记录的 srctree symlink。
- 如果它留在 `common_my_dec/` 源码树下，Bazel 的 `glob(["**"])` 可能把这个输出目录/软链纳入输入视野，造成排查时误以为 `common_my_dec` 又指回了 `common`。

快速修复：

```bash
rm -rf ~/learn_os/pixel/common/output/compile_pixel_common
rm -rf ~/learn_os/pixel/common_my_dec/output/compile_pixel_common_my_dec
```

预防：

- 不要把 `O=` build 输出目录放在 `pixel/common/` 或 `pixel/common_my_dec/` 下面；改用 `pixel/out/` 或 repo 顶层 `output/`（或 `/tmp/...`）。
