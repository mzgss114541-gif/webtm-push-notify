# WebTiebaManager 贴吧命中推送插件 (push_notify)

**本插件是 [WebTiebaManager](https://github.com/TiebaMeow/WebTiebaManager)（贴吧吧务管理工具）的插件**，放置于其 `plugins/` 目录。

[WebTiebaManager](https://github.com/TiebaMeow/WebTiebaManager) (WTM) 的插件：当爬虫抓取到命中规则的帖子（敏感词、广告、违规内容等）时，**立即**推送到你的微信或邮箱。

- ⚡ 命中即推，零延迟（运行时挂钩，不轮询数据库）
- 📱 双通道：微信（Server酱）+ 邮件（SMTP）
- 👤 多账户独立配置：每个 WTM 账户可配自己的 SendKey / SMTP（邮箱供应商可不同）
- 🛠 网页管理页：`/push-notify` 图形化配置，自动联动 WTM 账户列表
- 🔒 纯插件，不改 WTM 核心源码，升级不冲突
- 🔐 配置存独立文件，代码不含任何真实凭据

## 功能

贴吧帖子命中规则后，推送内容包含：

```
贴吧: 某贴吧
规则: 词库
标题: 帖子标题
内容: 帖子内容（前200字）
时间: 2026-08-16 16:44
链接: http://tieba.baidu.com/p/123456789
```

## 安装

1. 将 `push_notify.py` 放入 WTM 的 `plugins/` 目录：

```bash
cp push_notify.py /path/to/WebTiebaManager/plugins/
chown <wtm-user>:<wtm-user> /path/to/WebTiebaManager/plugins/push_notify.py
```

2. 重启 WTM：

```bash
systemctl restart webtieba   # 或你的 WTM 启动方式
```

3. 浏览器打开管理页（WTM 地址 + 路径）：

```
http://<wtm-host>:36799/push-notify
```

## 配置

管理页 `/push-notify` 上，每个账户一张卡片，可独立配置：

| 字段 | 说明 |
|---|---|
| **SendKey** | Server酱 的 SendKey（[sct.ftqq.com](https://sct.ftqq.com) 免费获取），填了则走微信推送 |
| **SMTP 服务器 / 端口** | 如 `smtp.qq.com:465`、`smtp.163.com:465`，填了则走邮件推送 |
| **发件邮箱 / 授权码** | SMTP 授权码（邮箱设置里开启 SMTP 服务后生成，非登录密码） |
| **收件邮箱** | 该账户命中后通知发到哪个邮箱 |

- 每个账户的微信/邮件可分别启用或留空
- 账户列表自动联动 WTM（`users/` 目录），新增/删除账户自动反映
- 保存立即生效，无需重启

## 配置存储位置

```
<WTM>/WebTMData/plugin_data/push_notify/config.json
```

> ⚠️ 配置文件中包含 **SendKey / SMTP 授权码** 等敏感信息，请确保：
> - 文件权限收紧（如 `chmod 600`）
> - 不要提交到任何版本仓库
> - `.gitignore` 已排除 `config.json`

## 安全性

- **API 需登录认证**：所有管理 API（读取/修改配置、测试推送）都依赖 WTM 的登录认证（`current_user_depends`），未登录返回 `401`
- **代码无凭据**：插件代码不含任何真实密钥，配置全部在外部文件
- **隐私**：推送消息不含敏感凭据；管理页为纯静态壳，敏感数据仅通过认证 API 获取

## 工作原理

插件在加载时运行时包装（monkey-patch）`Processer.process` 方法：WTM 爬虫处理帖子时，命中规则的那一瞬间触发推送——**不轮询数据库，零延迟**。

## 许可证

[MIT](LICENSE)

## 关于本项目

本项目是 **vibe coding**（AI 辅助开发）的产物：由 [mzgss114541-gif] 主导设计、需求与测试，编码过程中使用 **DeepSeek Harness**（DSH，AI agent 框架）作为编程助手协作完成。

代码经过实际生产环境验证（贴吧吧务日常监控），但作为 AI 辅助生成的项目，建议使用前 review 代码、按需调整。

**Contributors**

| | |
|---|---|
| <img src="https://github.com/mzgss114541-gif.png" width="70" height="70" style="border-radius:50%"> | <img src="https://github.com/deepseek-ai.png" width="70" height="70" style="border-radius:50%"> |
| **mzgss114541-gif**<br>设计 · 需求 · 测试 · 部署 | **DeepSeek Harness**<br>AI agent（辅助实现） |
