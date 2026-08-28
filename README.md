# Codex OAuth 工具

这是一个独立的 Python 工具，用于获取 OpenAI Codex 的 `refresh_token`，并导出 CPA 可直接使用的完整认证 JSON。

## 环境要求

- Python 3.9 或更高版本
- Tkinter（Windows 官方 Python 安装包通常已包含）
- 可以使用 Codex OAuth 的 OpenAI 账号

脚本只使用 Python 标准库，不需要执行 `pip install`。

## 可视化界面

启动桌面界面：

```powershell
python codex_oauth_gui.py
```

也可以使用 `pythonw` 启动并隐藏控制台窗口：

```powershell
pythonw codex_oauth_gui.py
```

界面支持：

- 浏览器 OAuth + PKCE 登录
- Codex 设备码登录
- 使用已有 refresh token 刷新凭据
- 从 CPA auth JSON 文件提取凭据
- HTTP/HTTPS 代理
- 复制授权地址、refresh token 和完整 JSON
- 保存完整 CPA JSON 文件

代理填写示例：

```text
http://127.0.0.1:7890
```

浏览器 OAuth 默认监听 `localhost:1455`。如果端口被占用，可以在界面中修改回调端口。

## 命令行用法

### 浏览器登录

默认浏览器登录，标准输出只包含 refresh token：

```powershell
python codex_oauth.py --proxy http://127.0.0.1:7890
```

不自动打开浏览器：

```powershell
python codex_oauth.py --no-browser
```

### 设备码登录

```powershell
python codex_oauth.py --device --proxy http://127.0.0.1:7890
```

### 刷新已有 token

```powershell
python codex_oauth.py --refresh-token "你的_refresh_token"
```

### 从 CPA 文件提取

```powershell
python codex_oauth.py --auth-file path\to\codex.json
```

使用 `--json` 输出完整 JSON，使用 `--output path\to\codex.json` 保存完整凭据。

## CPA JSON 格式

导出的登录结果包含 CPA Codex 所需字段：

```json
{
  "type": "codex",
  "id_token": "...",
  "access_token": "...",
  "refresh_token": "...",
  "account_id": "...",
  "email": "...",
  "expired": "2026-01-01T00:00:00Z",
  "last_refresh": "2026-01-01T00:00:00Z"
}
```

将保存的 JSON 文件放入 CPA 的 `auths` 目录即可。具体目录取决于 CPA 的配置。

## 安全提示

`refresh_token` 是长期凭据。请勿把真实 token 提交到 Git、发送给他人或发布到截图/日志中；保存的 JSON 文件也应妥善保护。
