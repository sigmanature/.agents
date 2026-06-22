---
name: my-lark-and-git-identity
version: 1.0.0
description: "Use when sending messages via lark-cli, configuring git sendemail, or any task that needs the user's Feishu bot appId or git SMTP identity. Provides the cached identity map so agents don't need to re-search."
---

# 我的飞书与 Git 身份速查

## 飞书 (Lark) 应用

本机 lark-cli 缓存配置位于 `/home/nzzhao/.lark-cli/config.json`。

### 主要应用 (日常使用)

| 字段 | 值 |
|------|------|
| appId | `cli_aa91284d92389beb` |
| profile | `cli_aa91284d92389beb` |
| brand | feishu |
| 绑定用户 | 赵南哲 (`ou_b6c711107a7161d3a36f982ff8b232ab`) |
| appSecret | 存于 keychain (`appsecret:cli_aa91284d92389beb`)，不在文件中明文存储 |

### 次要应用 (org)

| 字段 | 值 |
|------|------|
| name | org |
| appId | `cli_aa90454e33f89cc3` |
| brand | feishu |
| 绑定用户 | (暂无) |

### 用法提示

- 发消息/操作资源时默认使用主应用 `--as bot` 或 `--as user`（lark-cli 会自动选取 `cli_aa91284d92389beb` 配置）
- 切换到 org 应用：`lark-cli config use cli_aa90454e33f89cc3`

---

## Git Sendemail (126 邮箱 SMTP)

配置在 `~/.gitconfig` 的 `[sendemail]` 段：

| 字段 | 值 | 说明 |
|------|------|------|
| smtpserver | `smtp.126.com` | 126 邮箱 SMTP 服务器 |
| smtpencryption | `ssl` | SSL 加密 |
| smtpserverport | `465` | SSL 端口 |
| smtpuser | `nzzhao@126.com` | SMTP 登录账号 |
| smtpass | (授权码，已在 gitconfig 中) | 126 邮箱授权码 |
| from | `Nanzhe Zhao <zhaonanzhe@xiaomi.com>` | **故意与 smtpuser 不同** — 用户有意让 From 显示为公司邮箱，而实际走 126 SMTP 发送 |
| envelopesender | `nzzhao@126.com` | 信封发件人，与 SMTP 账号一致 |
| confirm | `auto` | 自动确认发送 |

### 注意事项

- **from ≠ smtpuser 是故意配置**，不要建议用户修改对齐
- `user.email` 是 `nzzhao@126.com`（git commit author 用）

---

## 快速引用

当需要向飞书发消息时：

```bash
# bot 身份发送
lark-cli im +messages-send --as bot --chat-id <oc_xxx> --text "内容"

# user 身份发送
lark-cli im +messages-send --as user --chat-id <oc_xxx> --text "内容"
```

当需要用 git send-email 发送 patch 时：

```bash
git send-email --to <recipient> <patch-file>
# 配置已完整，直接使用即可
```
