# Pixel / Kleaf: make a new Kconfig option land in `boot.img`

目标：让最终打进 `boot.img` 的 kernel `.config` 显式包含你新增的 Kconfig（例：`CONFIG_F2FS_LARGE_FOLIO=y`）。

这份笔记适用于 Pixel / AOSP 的 Kleaf+Bazel 流程，尤其是 Slider/Raviole 这类产物链路里 `boot.img` 来自 `//common:*gki_artifacts` 的场景。

## 1) 先确认：你的 `boot.img` 来自哪个 kernel target

经验判断：Slider/Raviole 的 `*_dist` 往往直接拷贝 `//common:kernel_aarch64_gki_artifacts` 的 `boot.img`，而不是设备侧的 `kernel_build` 自己产出。

快速定位方法：

- 在 `pixel/private/google-modules/soc/gs/BUILD.bazel`（或对应 SoC 目录）里搜 `boot.img` / `gki_artifacts`：
  - 如果看到 `srcs = ["//common:kernel_aarch64_gki_artifacts"]` 之类，说明 `boot.img` 的 kernel 来自 `//common:kernel_aarch64`。

结论：如果 `boot.img` 来自 `//common:kernel_aarch64`，那你该改的是 `pixel/common/` 这套 common kernel 的 defconfig/fragment（不是 `slider_gki.fragment`）。

## 2) 推荐落点：新增一个 fragment + 挂到 `post_defconfig_fragments`

为什么用 `post_defconfig_fragments`：

- 它更像“强约束/强覆盖”：最终 `.config` 必须包含这些项；
- 更易回滚（只撤一个 fragment + BUILD wiring）；
- 比直接改 `gki_defconfig` 更不容易引起上游冲突。

### 2.1 新建 fragment

例（放在 `pixel/common` kernel 树内）：

- `pixel/common/arch/arm64/configs/f2fs_large_folio.fragment`
  - `CONFIG_F2FS_LARGE_FOLIO=y`

### 2.2 把 fragment 挂到 common kernel（影响 `boot.img`）

在 `pixel/common/BUILD.bazel` 的 `common_kernel(name="kernel_aarch64", ...)` 上加：

- `post_defconfig_fragments = ["arch/arm64/configs/f2fs_large_folio.fragment"],`

如果你也需要 16K page 变体（`kernel_aarch64_16k`），同样加一份。

## 3) 验证：看最终 dot_config（推荐两层验证）

### 3.1 先验证 Bazel 输出的 dot_config

```bash
cd ~/learn_os/pixel
tools/bazel build --config=raviole --config=fast //common:kernel_aarch64 --noshow_progress
rg -n '^CONFIG_F2FS_LARGE_FOLIO=' bazel-bin/common/kernel_aarch64/kernel_aarch64_dot_config
```

### 3.2 再验证 dist（对应你实际拿去刷/打包的目录）

```bash
cd ~/learn_os/pixel
tools/bazel run --config=raviole --config=fast //private/google-modules/soc/gs:slider_dist --noshow_progress
rg -n '^CONFIG_F2FS_LARGE_FOLIO=' out/slider/dist/kernel_aarch64_dot_config
```

## 4) 常见坑：Bazel 报 “Infinite symlink expansion” / symlink loop

症状（示例）：

- `tools/bazel query //common:kernel_aarch64` 报：
  - `Symlink issue while evaluating globs: Infinite symlink expansion: .../pixel/common/output/compile_pixel_common/source -> .../pixel/common`

原因：

- 你在 `pixel/common/` 下面跑过 Kbuild out-of-tree 编译（`make O=pixel/common/output/...`），Kbuild 会在 `O=` 目录里生成 `source -> <srctree>` 的 symlink。
- Bazel 在 `//common` 包里做 `glob()` 时会遍历到这个 symlink，导致循环展开。

快速修复：

- 删除该 `O=` 输出目录（或至少删掉里面的 `source` symlink）：
  - `rm -rf ~/learn_os/pixel/common/output/compile_pixel_common`

预防：

- 不要把 `O=` 放在 Bazel package tree 里（尤其不要放在 `pixel/common/` 下面）。
- 推荐放到 `pixel/out/` 或 repo 顶层 `output/`（或 `/tmp/...`）。

