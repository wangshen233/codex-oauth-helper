# Codex OAuth Helper

Standalone Python scripts for obtaining an OpenAI Codex `refresh_token` and exporting a CPA-compatible credential JSON file.

## Requirements

- Python 3.9 or newer
- Tkinter (normally included with the Windows Python installer)
- An OpenAI account that can use Codex OAuth

The scripts use only Python's standard library. No `pip install` is required.

## GUI

Run the desktop interface:

```powershell
python codex_oauth_gui.py
```

The GUI supports:

- Browser OAuth with PKCE
- Codex device-code login
- Refreshing an existing refresh token
- Extracting a token from an existing CPA auth JSON file
- HTTP/HTTPS proxy configuration
- Copying the refresh token or complete JSON
- Saving the complete JSON to a file

For a proxy, enter a URL such as:

```text
http://127.0.0.1:7890
```

The browser flow listens on `localhost:1455`. Change the callback port only if that port is already in use.

## Command Line

Browser login (the default) prints only the refresh token to stdout:

```powershell
python codex_oauth.py --proxy http://127.0.0.1:7890
```

Device-code login:

```powershell
python codex_oauth.py --device --proxy http://127.0.0.1:7890
```

Refresh an existing token:

```powershell
python codex_oauth.py --refresh-token "YOUR_REFRESH_TOKEN"
```

Extract a token from a CPA JSON file:

```powershell
python codex_oauth.py --auth-file path\to\codex.json
```

Use `--json` to print the complete credential object and `--output path\to\codex.json` to save it.

## CPA JSON

The exported object uses CPA's Codex credential fields:

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

You can place the saved file in CPA's `auths` directory. The `refresh_token` is the long-lived credential; keep it private and do not commit it to a repository.

## Security

This tool handles login credentials and tokens. Use a trusted proxy, do not share screenshots or logs containing tokens, and protect saved JSON files with appropriate file permissions.
