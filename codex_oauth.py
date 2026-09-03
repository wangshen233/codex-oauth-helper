#!/usr/bin/env python3
"""Standalone Codex OAuth helper.

This mirrors CPA's Codex flow without importing CPA or third-party packages.
It can run the localhost callback flow, the device-code flow, refresh an
existing token, or extract a refresh token from a CPA auth JSON file.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from queue import Empty, Queue
from typing import Any, Callable, Dict, Optional, Tuple


AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
DEVICE_USER_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
DEVICE_VERIFICATION_URL = "https://auth.openai.com/codex/device"
DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
AuthURLCallback = Callable[[str], None]
DeviceCodeCallback = Callable[[str, str], None]
BrowserOpenCallback = Callable[[str], None]


class OAuthError(RuntimeError):
    """Raised when the provider or callback returns an authentication error."""


class OAuthHTTPError(OAuthError):
    """Raised for an HTTP error while calling a Codex endpoint."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_opener(proxy: Optional[str]) -> urllib.request.OpenerDirector:
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


def post_json(opener: urllib.request.OpenerDirector, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise OAuthHTTPError(f"Codex endpoint returned HTTP {exc.code}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise OAuthError(f"Codex endpoint request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthError("Codex endpoint returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise OAuthError("Codex endpoint returned a non-object response")
    return parsed


def post_form(opener: urllib.request.OpenerDirector, url: str, fields: Dict[str, str]) -> Dict[str, Any]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise OAuthHTTPError(
            f"Codex token endpoint returned HTTP {exc.code}", exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise OAuthError(f"Codex token request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthError("Codex token endpoint returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise OAuthError("Codex token endpoint returned a non-object response")
    return parsed


def generate_pkce() -> Tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(96)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def decode_jwt_claims(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def credentials_from_token_response(
    response: Dict[str, Any], fallback_refresh_token: str = ""
) -> Dict[str, Any]:
    access_token = str(response.get("access_token") or "").strip()
    refresh_token = str(response.get("refresh_token") or fallback_refresh_token).strip()
    id_token = str(response.get("id_token") or "").strip()
    if not access_token:
        raise OAuthError("Codex token response has no access_token")
    if not refresh_token:
        raise OAuthError("Codex token response has no refresh_token")

    claims = decode_jwt_claims(id_token)
    auth_claims = claims.get("https://api.openai.com/auth")
    if not isinstance(auth_claims, dict):
        auth_claims = {}
    expires_in = response.get("expires_in")
    try:
        expires_seconds = int(expires_in)
    except (TypeError, ValueError):
        expires_seconds = 0
    expires_at = ""
    if expires_seconds > 0:
        expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_seconds)).isoformat().replace("+00:00", "Z")

    return {
        "type": "codex",
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": str(auth_claims.get("chatgpt_account_id") or "").strip(),
        "email": str(claims.get("email") or "").strip(),
        "expired": expires_at,
        "last_refresh": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    callback_queue: Queue

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/auth/callback":
            query = urllib.parse.parse_qs(parsed.query)
            result = {
                "code": query.get("code", [""])[0],
                "state": query.get("state", [""])[0],
                "error": query.get("error", [""])[0],
                "error_description": query.get("error_description", [""])[0],
            }
            self.callback_queue.put(result)
            self.send_response(302)
            self.send_header("Location", "/success")
            self.end_headers()
            return
        if parsed.path == "/success":
            body = b"Codex authentication received. You can close this window."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: Any) -> None:
        # Callback URLs contain authorization material; never log them.
        return


def wait_for_callback(
    state: str,
    port: int,
    timeout: int,
    no_browser: bool,
    on_auth_url: Optional[AuthURLCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    open_browser: Optional[BrowserOpenCallback] = None,
) -> Dict[str, str]:
    callback_queue: Queue = Queue(maxsize=1)

    class BoundCallbackHandler(CallbackHandler):
        pass

    BoundCallbackHandler.callback_queue = callback_queue
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), BoundCallbackHandler)
    except OSError as exc:
        raise OAuthError(f"cannot listen on localhost:{port}: {exc}") from exc

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        verifier, challenge = generate_pkce()
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI.replace(":1455/", f":{port}/"),
            "scope": "openid email profile offline_access",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "login",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        }
        auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
        if on_auth_url:
            try:
                on_auth_url(auth_url)
            except Exception as exc:
                raise OAuthError("cannot publish Codex authorization URL") from exc
        else:
            print(f"Open this URL to sign in:\n{auth_url}", file=sys.stderr, flush=True)
        if not no_browser:
            try:
                if open_browser:
                    open_browser(auth_url)
                else:
                    import webbrowser

                    webbrowser.open(auth_url)
            except Exception as exc:
                raise OAuthError("cannot open browser for Codex authorization") from exc

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                raise OAuthError("authentication cancelled")
            try:
                result = callback_queue.get(timeout=min(0.5, deadline - time.monotonic()))
            except Empty:
                continue
            if result["error"]:
                detail = result["error_description"] or result["error"]
                raise OAuthError(f"Codex authorization failed: {detail}")
            if result["state"] != state:
                raise OAuthError("Codex callback state mismatch")
            if not result["code"]:
                raise OAuthError("Codex callback did not contain an authorization code")
            return {"code": result["code"], "verifier": verifier, "redirect_uri": params["redirect_uri"]}
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    raise OAuthError(f"timed out waiting for Codex callback after {timeout} seconds")


def browser_login(
    opener: urllib.request.OpenerDirector,
    port: int,
    no_browser: bool,
    on_auth_url: Optional[AuthURLCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    open_browser: Optional[BrowserOpenCallback] = None,
) -> Dict[str, Any]:
    state = secrets.token_hex(16)
    callback = wait_for_callback(
        state,
        port,
        300,
        no_browser,
        on_auth_url=on_auth_url,
        cancel_event=cancel_event,
        open_browser=open_browser,
    )
    response = post_form(
        opener,
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": callback["code"],
            "redirect_uri": callback["redirect_uri"],
            "code_verifier": callback["verifier"],
        },
    )
    return credentials_from_token_response(response)


def device_login(
    opener: urllib.request.OpenerDirector,
    no_browser: bool,
    on_device_code: Optional[DeviceCodeCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    user_code_response = post_json(opener, DEVICE_USER_CODE_URL, {"client_id": CLIENT_ID})
    device_auth_id = str(user_code_response.get("device_auth_id") or "").strip()
    user_code = str(user_code_response.get("user_code") or user_code_response.get("usercode") or "").strip()
    if not device_auth_id or not user_code:
        raise OAuthError("Codex device endpoint returned incomplete credentials")

    try:
        interval = int(user_code_response.get("interval") or 5)
    except (TypeError, ValueError):
        interval = 5
    interval = max(interval, 1)
    if on_device_code:
        on_device_code(DEVICE_VERIFICATION_URL, user_code)
    else:
        print(
            f"Open {DEVICE_VERIFICATION_URL} and enter code: {user_code}",
            file=sys.stderr,
            flush=True,
        )
    if not no_browser:
        import webbrowser

        webbrowser.open(DEVICE_VERIFICATION_URL)

    deadline = time.monotonic() + 15 * 60
    while time.monotonic() < deadline:
        if cancel_event and cancel_event.is_set():
            raise OAuthError("authentication cancelled")
        try:
            response = post_json(
                opener,
                DEVICE_TOKEN_URL,
                {"device_auth_id": device_auth_id, "user_code": user_code},
            )
            break
        except OAuthHTTPError as exc:
            if exc.status_code not in (403, 404):
                raise
            if cancel_event:
                if cancel_event.wait(interval):
                    raise OAuthError("authentication cancelled")
            else:
                time.sleep(interval)
    else:
        raise OAuthError("timed out waiting for Codex device authorization")

    auth_code = str(response.get("authorization_code") or "").strip()
    verifier = str(response.get("code_verifier") or "").strip()
    challenge = str(response.get("code_challenge") or "").strip()
    if not auth_code or not verifier or not challenge:
        raise OAuthError("Codex device token response is incomplete")
    token_response = post_form(
        opener,
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": auth_code,
            "redirect_uri": DEVICE_REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    return credentials_from_token_response(token_response)


def refresh_token(opener: urllib.request.OpenerDirector, token: str) -> Dict[str, Any]:
    token = token.strip()
    if not token:
        raise OAuthError("refresh token is required")
    response = post_form(
        opener,
        TOKEN_URL,
        {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": token,
            "scope": "openid profile email",
        },
    )
    return credentials_from_token_response(response, fallback_refresh_token=token)


def read_auth_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise OAuthError(f"cannot read auth file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OAuthError("auth file is not valid JSON") from exc
    if not isinstance(data, dict):
        raise OAuthError("auth file must contain a JSON object")
    token = str(data.get("refresh_token") or "").strip()
    if not token:
        raise OAuthError("auth file has no refresh_token")
    return data


def write_json(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    temp_path = f"{path}.tmp-{os.getpid()}"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise OAuthError(f"cannot write credentials: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Log in to Codex and obtain a refresh token")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--device", action="store_true", help="use Codex device-code login")
    mode.add_argument("--refresh-token", metavar="TOKEN", help="refresh an existing token")
    mode.add_argument("--auth-file", metavar="PATH", help="read refresh_token from a CPA auth JSON file")
    parser.add_argument("--port", type=int, default=1455, help="localhost callback port (default: 1455)")
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    parser.add_argument("--proxy", help="HTTP(S) proxy URL")
    parser.add_argument("--output", metavar="PATH", help="write the full credential JSON to this path")
    parser.add_argument("--json", action="store_true", help="print full credential JSON instead of only refresh_token")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.port <= 65535:
        raise OAuthError("callback port must be between 1 and 65535")
    opener = build_opener(args.proxy)

    if args.auth_file:
        credentials = read_auth_file(args.auth_file)
    elif args.refresh_token:
        credentials = refresh_token(opener, args.refresh_token)
    elif args.device:
        credentials = device_login(opener, args.no_browser)
    else:
        credentials = browser_login(opener, args.port, args.no_browser)

    if args.output:
        write_json(args.output, credentials)
        print(f"Credentials written to {args.output}", file=sys.stderr)
    if args.json:
        print(json.dumps(credentials, ensure_ascii=True, indent=2))
    else:
        print(credentials["refresh_token"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OAuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
