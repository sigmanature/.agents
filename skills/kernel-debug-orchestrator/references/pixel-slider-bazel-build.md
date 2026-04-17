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

如果你在 `pixel/common/` 目录下手动跑过 kbuild（例如 `make O=pixel/common/output/...`），`O=` 目录里可能会自动生成：

- `source -> <kernel srctree>`

这会导致 Bazel 在加载 `//common` 的 `glob()` 时出现循环 symlink 错误：

- `Symlink issue while evaluating globs: Infinite symlink expansion: .../pixel/common/output/.../source -> .../pixel/common`

快速修复：

```bash
rm -rf ~/learn_os/pixel/common/output/compile_pixel_common
```

预防：

- 不要把 `O=` build 输出目录放在 `pixel/common/` 下面；改用 `pixel/out/` 或 repo 顶层 `output/`（或 `/tmp/...`）。
