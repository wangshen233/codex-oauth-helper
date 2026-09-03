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
- 对同一已登录账号顺序重复执行 1 到 20 次浏览器 OAuth 授权
- Codex 设备码登录
- 使用已有 refresh token 刷新凭据
- 从 CPA auth JSON 文件提取凭据
- HTTP/HTTPS 代理
- 系统默认浏览器、Google Chrome、Microsoft Edge 或自定义浏览器可执行文件
- 可选浏览器启动参数，以及手动打开授权地址
- 复制授权地址、refresh token 和完整 JSON
- 保存完整 CPA JSON 文件

代理填写示例：

```text
http://127.0.0.1:7890
```

浏览器 OAuth 默认监听 `localhost:1455`。如果端口被占用，可以在界面中修改回调端口。

### 批量重新授权与浏览器选择

在“浏览器 OAuth”模式中，可将“重复授权次数”设为 1 到 20。每一轮都会重新生成独立的 OAuth `state`、PKCE 和本地回调，只有当前轮授权成功并由界面保存/显示结果后，才会打开下一轮授权页。

“浏览器”可以选择系统默认、Google Chrome、Microsoft Edge 或“自定义”。Chrome 和 Edge 会自动从系统命令和常见 Windows 安装目录查找；自定义模式可选择对应的 `.exe`。启动参数是可选项，例如浏览器本身需要的参数。工具不会自动添加浏览器 profile 参数，因此会使用所选浏览器现有的登录状态。

勾选“自动打开浏览器”时，每轮会用选定浏览器打开授权地址；取消勾选后只显示授权地址，可通过右侧“打开”按钮按当前浏览器选项启动。代理设置仍用于工具向 Codex OAuth 端点发送的 HTTP 请求，浏览器自身的网络设置由所选浏览器决定。

批量且指定“保存 JSON”路径时，`codex.json` 会依次保存为 `codex-001.json`、`codex-002.json` 等，启动前若其中任意文件已存在则会拒绝执行，以免覆盖原有凭据。未指定保存路径时，界面仍会显示每轮结果中的最后一份凭据。

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

本工具只负责发起标准 OAuth 授权、接收本地回调和保存结果，不会自动填写账号密码、处理验证码/CAPTCHA，或规避 OpenAI 的服务端确认。请在已登录的浏览器窗口中自行完成每一轮需要的交互。
