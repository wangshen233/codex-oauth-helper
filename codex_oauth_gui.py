#!/usr/bin/env python3
"""Tkinter desktop UI for the standalone Codex OAuth helper."""

from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Optional

try:
    from codex_oauth import (
        OAuthError,
        browser_login,
        build_opener,
        device_login,
        read_auth_file,
        refresh_token,
        write_json,
    )
except ImportError:  # Running as a module from the repository root.
    from scripts.codex_oauth import (  # type: ignore[no-redef]
        OAuthError,
        browser_login,
        build_opener,
        device_login,
        read_auth_file,
        refresh_token,
        write_json,
    )


BROWSER_SYSTEM = "系统默认"
BROWSER_CHROME = "Google Chrome"
BROWSER_EDGE = "Microsoft Edge"
BROWSER_CUSTOM = "自定义"
BROWSER_CHOICES = (BROWSER_SYSTEM, BROWSER_CHROME, BROWSER_EDGE, BROWSER_CUSTOM)


def batch_output_path(path: str, index: int, total: int) -> str:
    """Return a unique per-run path without changing single-run behavior."""
    if not path or total <= 1:
        return path
    stem, extension = os.path.splitext(path)
    return f"{stem}-{index:03d}{extension}"


def find_browser_executable(browser: str, custom_path: str = "") -> str:
    """Find the selected browser without adding a separate browser profile."""
    if browser == BROWSER_CUSTOM:
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(custom_path.strip())))
        if not custom_path.strip() or not os.path.isfile(path):
            raise OAuthError("请选择存在的自定义浏览器可执行文件。")
        return path

    if browser == BROWSER_CHROME:
        command, folders, executable = (
            "chrome",
            ("Google", "Chrome", "Application"),
            "chrome.exe",
        )
    elif browser == BROWSER_EDGE:
        command, folders, executable = (
            "msedge",
            ("Microsoft", "Edge", "Application"),
            "msedge.exe",
        )
    else:
        raise OAuthError("请选择要启动的浏览器。")

    found = shutil.which(command)
    if found:
        return found
    for base in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if not base:
            continue
        candidate = os.path.join(base, *folders, executable)
        if os.path.isfile(candidate):
            return candidate
    raise OAuthError(f"未找到 {browser}，请改选自定义浏览器可执行文件。")


def parse_browser_arguments(arguments: str) -> list[str]:
    """Parse user-supplied browser arguments without invoking a shell."""
    if not arguments.strip():
        return []
    if os.name != "nt":
        try:
            return shlex.split(arguments)
        except ValueError as exc:
            raise OAuthError("浏览器启动参数格式无效。") from exc

    import ctypes
    from ctypes import wintypes

    argument_count = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_int),
    ]
    command_line_to_argv.restype = ctypes.POINTER(wintypes.LPWSTR)
    # CommandLineToArgvW treats the first token as an executable, so add one
    # and return only the user-provided arguments.
    argv = command_line_to_argv(
        f'"codex-oauth-browser.exe" {arguments}', ctypes.byref(argument_count)
    )
    if not argv:
        raise OAuthError("浏览器启动参数格式无效。")
    try:
        return [argv[index] for index in range(1, argument_count.value)]
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(argv, ctypes.c_void_p))


def validate_browser_configuration(browser: str, custom_path: str, arguments: str) -> None:
    parse_browser_arguments(arguments)
    if browser != BROWSER_SYSTEM:
        find_browser_executable(browser, custom_path)


def open_authorization_url(
    url: str, browser: str, custom_path: str, arguments: str
) -> None:
    """Open an OAuth URL in the requested existing browser installation."""
    validate_browser_configuration(browser, custom_path, arguments)
    if browser == BROWSER_SYSTEM:
        try:
            opened = webbrowser.open(url)
        except Exception as exc:
            raise OAuthError("无法启动系统默认浏览器。") from exc
        if not opened:
            raise OAuthError("系统默认浏览器未能打开授权地址。")
        return

    executable = find_browser_executable(browser, custom_path)
    try:
        subprocess.Popen([executable, *parse_browser_arguments(arguments), url])
    except OSError as exc:
        raise OAuthError(f"无法启动 {browser}。") from exc


class CodexOAuthApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex OAuth 登录工具")
        self.root.geometry("920x860")
        self.root.minsize(900, 800)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.closed = False
        self.worker: Optional[threading.Thread] = None
        self.credentials: Optional[Dict[str, Any]] = None
        self.last_url = ""
        self.pending_batch_acknowledgements: Dict[int, queue.Queue] = {}

        self.mode = tk.StringVar(value="browser")
        self.proxy = tk.StringVar()
        self.port = tk.StringVar(value="1455")
        self.auth_file = tk.StringVar()
        self.refresh_value = tk.StringVar()
        self.output_file = tk.StringVar()
        self.auto_open = tk.BooleanVar(value=True)
        self.batch_count = tk.StringVar(value="1")
        self.browser_choice = tk.StringVar(value=BROWSER_SYSTEM)
        self.browser_path = tk.StringVar()
        self.browser_arguments = tk.StringVar()
        self.status = tk.StringVar(value="就绪")
        self.token_value = tk.StringVar()
        self.show_token = tk.BooleanVar(value=True)
        self.url_value = tk.StringVar()
        self.device_code = tk.StringVar()
        self.json_result = ""

        self.build_ui()
        self.browser_choice.trace_add("write", self.update_browser_choice)
        self.root.after(100, self.process_events)

    def build_ui(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root, padding=(20, 16, 20, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Codex OAuth 登录", font=("Segoe UI", 17, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="获取 refresh token，支持浏览器授权、设备码、刷新和 CPA 文件导入",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        body = ttk.Frame(root, padding=(20, 8, 20, 12))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(5, weight=1)

        mode_box = ttk.LabelFrame(body, text="操作", padding=12)
        mode_box.grid(row=0, column=0, sticky="ew")
        for index, (value, label) in enumerate(
            (("browser", "浏览器 OAuth"), ("device", "设备码登录"), ("refresh", "刷新 token"), ("file", "CPA 文件提取"))
        ):
            ttk.Radiobutton(
                mode_box, text=label, value=value, variable=self.mode, command=self.update_mode
            ).grid(row=0, column=index, padx=(0 if index == 0 else 16, 0), sticky="w")

        network_box = ttk.LabelFrame(body, text="网络设置", padding=12)
        network_box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        network_box.columnconfigure(1, weight=1)
        ttk.Label(network_box, text="代理 URL:").grid(row=0, column=0, sticky="w")
        ttk.Entry(network_box, textvariable=self.proxy).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(network_box, text="例如 http://127.0.0.1:7890").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Label(network_box, text="回调端口:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(network_box, textvariable=self.port, width=10).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Checkbutton(network_box, text="自动打开浏览器", variable=self.auto_open).grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(8, 0))

        browser_box = ttk.LabelFrame(body, text="浏览器 OAuth 设置", padding=12)
        browser_box.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        browser_box.columnconfigure(3, weight=1)
        self.browser_box = browser_box
        ttk.Label(browser_box, text="重复授权次数:").grid(row=0, column=0, sticky="w")
        self.batch_count_spinbox = ttk.Spinbox(
            browser_box, from_=1, to=20, textvariable=self.batch_count, width=5
        )
        self.batch_count_spinbox.grid(row=0, column=1, sticky="w", padx=(8, 16))
        ttk.Label(browser_box, text="浏览器:").grid(row=0, column=2, sticky="w")
        self.browser_choice_box = ttk.Combobox(
            browser_box,
            textvariable=self.browser_choice,
            values=BROWSER_CHOICES,
            state="readonly",
            width=16,
        )
        self.browser_choice_box.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self.browser_args_label = ttk.Label(browser_box, text="启动参数（可选）:")
        self.browser_args_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.browser_args_entry = ttk.Entry(browser_box, textvariable=self.browser_arguments)
        self.browser_args_entry.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0))
        self.browser_path_label = ttk.Label(browser_box, text="自定义浏览器:")
        self.browser_path_entry = ttk.Entry(browser_box, textvariable=self.browser_path)
        self.browser_path_button = ttk.Button(
            browser_box, text="选择程序", command=self.choose_browser_executable
        )

        input_box = ttk.LabelFrame(body, text="输入（仅当前操作需要时显示）", padding=12)
        input_box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.input_box = input_box
        input_box.columnconfigure(1, weight=1)
        self.refresh_label = ttk.Label(input_box, text="已有 refresh token（仅刷新模式）:")
        self.refresh_label.grid(row=0, column=0, sticky="w")
        self.refresh_entry = ttk.Entry(input_box, textvariable=self.refresh_value, show="*")
        self.refresh_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.auth_label = ttk.Label(input_box, text="CPA auth JSON（仅文件提取模式）:")
        self.auth_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.auth_entry = ttk.Entry(input_box, textvariable=self.auth_file)
        self.auth_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        self.auth_button = ttk.Button(input_box, text="选择文件", command=self.choose_auth_file)
        self.auth_button.grid(row=1, column=2, padx=(8, 0), pady=(8, 0))

        output_box = ttk.LabelFrame(body, text="输出", padding=12)
        output_box.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        output_box.columnconfigure(1, weight=1)
        ttk.Label(output_box, text="保存 JSON:").grid(row=0, column=0, sticky="w")
        ttk.Entry(output_box, textvariable=self.output_file).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(output_box, text="选择路径", command=self.choose_output_file).grid(row=0, column=2, padx=(8, 0))

        result_box = ttk.LabelFrame(body, text="授权信息", padding=12)
        result_box.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        result_box.columnconfigure(1, weight=1)
        result_box.rowconfigure(4, weight=1)
        ttk.Label(result_box, text="refresh_token 输出:").grid(row=0, column=0, sticky="w")
        self.token_entry = ttk.Entry(result_box, textvariable=self.token_value, state="readonly", show="")
        self.token_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.show_token_button = ttk.Checkbutton(
            result_box, text="显示 token", variable=self.show_token, command=self.toggle_token
        )
        self.show_token_button.grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Button(result_box, text="复制 token", command=self.copy_token).grid(row=0, column=3, padx=(8, 0))
        ttk.Label(result_box, text="授权地址:").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self.url_entry = ttk.Entry(result_box, textvariable=self.url_value, state="readonly")
        self.url_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        self.copy_url_button = ttk.Button(result_box, text="复制", command=self.copy_url, state="disabled")
        self.copy_url_button.grid(row=1, column=2, padx=(8, 0), pady=(8, 0))
        self.open_url_button = ttk.Button(result_box, text="打开", command=self.open_url, state="disabled")
        self.open_url_button.grid(row=1, column=3, padx=(8, 0), pady=(8, 0))
        ttk.Label(result_box, text="设备码:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(result_box, textvariable=self.device_code, state="readonly").grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Label(result_box, text="CPA JSON:").grid(row=3, column=0, sticky="nw", pady=(12, 0))
        json_frame = ttk.Frame(result_box)
        json_frame.grid(row=3, column=1, columnspan=2, sticky="nsew", padx=(8, 0), pady=(12, 0))
        json_frame.columnconfigure(0, weight=1)
        json_frame.rowconfigure(0, weight=1)
        self.json_text = tk.Text(
            json_frame, height=8, wrap="none", state="disabled", relief="flat", background="#f5f5f5"
        )
        self.json_text.grid(row=0, column=0, sticky="nsew")
        json_scroll = ttk.Scrollbar(json_frame, orient="vertical", command=self.json_text.yview)
        json_scroll.grid(row=0, column=1, sticky="ns")
        json_scroll_x = ttk.Scrollbar(json_frame, orient="horizontal", command=self.json_text.xview)
        json_scroll_x.grid(row=1, column=0, sticky="ew")
        self.json_text.configure(
            xscrollcommand=json_scroll_x.set,
            yscrollcommand=json_scroll.set,
        )
        ttk.Button(result_box, text="复制 JSON", command=self.copy_json).grid(row=3, column=3, sticky="n", padx=(8, 0), pady=(12, 0))
        log_frame = ttk.Frame(result_box)
        log_frame.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(
            log_frame, height=3, wrap="word", state="disabled", relief="flat", background="#f5f5f5"
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)

        footer = ttk.Frame(root, padding=(20, 0, 20, 16))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=150)
        self.progress.grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.status).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Button(footer, text="保存当前 JSON", command=self.save_current_json).grid(row=0, column=2, padx=(8, 0))
        self.start_button = ttk.Button(footer, text="开始", command=self.start)
        self.start_button.grid(row=0, column=3, padx=(8, 0))
        self.cancel_button = ttk.Button(footer, text="取消", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=4, padx=(8, 0))

        self.update_mode()

    def update_mode(self) -> None:
        mode = self.mode.get()
        is_browser = mode == "browser"
        is_refresh = mode == "refresh"
        is_file = mode == "file"
        if is_browser:
            self.browser_box.grid()
            self.update_browser_choice()
        else:
            self.browser_box.grid_remove()
        if is_refresh or is_file:
            self.input_box.grid()
        else:
            self.input_box.grid_remove()
        if is_refresh:
            self.refresh_label.grid()
            self.refresh_entry.configure(state="normal")
        else:
            self.refresh_label.grid_remove()
            self.refresh_entry.configure(state="disabled")
        if is_file:
            self.auth_label.grid()
            self.auth_entry.grid()
            self.auth_button.grid()
            self.auth_entry.configure(state="normal")
        else:
            self.auth_label.grid_remove()
            self.auth_entry.grid_remove()
            self.auth_button.grid_remove()
            self.auth_entry.configure(state="disabled")

    def update_browser_choice(self, *_args: str) -> None:
        if self.browser_choice.get() == BROWSER_CUSTOM:
            self.browser_path_label.grid(row=2, column=0, sticky="w", pady=(8, 0))
            self.browser_path_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))
            self.browser_path_button.grid(row=2, column=3, sticky="w", padx=(8, 0), pady=(8, 0))
        else:
            self.browser_path_label.grid_remove()
            self.browser_path_entry.grid_remove()
            self.browser_path_button.grid_remove()

    def choose_auth_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("JSON files", "*.json"), ("All files", "*.*")))
        if path:
            self.auth_file.set(path)

    def choose_browser_executable(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=(("Executable files", "*.exe"), ("All files", "*.*"))
        )
        if path:
            self.browser_path.set(path)

    def choose_output_file(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=(("JSON files", "*.json"), ("All files", "*.*"))
        )
        if path:
            self.output_file.set(path)

    def append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def enqueue_url(self, url: str, index: int = 0, total: int = 0) -> None:
        self.events.put(("url", (url, index, total)))

    def enqueue_device(self, url: str, code: str) -> None:
        self.events.put(("device", (url, code)))

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        mode = self.mode.get()
        batch_count = 1
        if mode == "refresh" and not self.refresh_value.get().strip():
            messagebox.showwarning("缺少输入", "请填写 refresh token")
            return
        if mode == "file" and not self.auth_file.get().strip():
            messagebox.showwarning("缺少输入", "请选择 CPA auth JSON 文件")
            return
        if mode == "browser":
            try:
                batch_count = int(self.batch_count.get().strip())
                if not 1 <= batch_count <= 20:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("次数错误", "重复授权次数必须是 1 到 20")
                return
        try:
            port = int(self.port.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning("端口错误", "回调端口必须是 1 到 65535")
            return

        inputs = {
            "proxy": self.proxy.get().strip() or None,
            "auto_open": self.auto_open.get(),
            "batch_count": batch_count,
            "browser": self.browser_choice.get(),
            "browser_path": self.browser_path.get().strip(),
            "browser_arguments": self.browser_arguments.get().strip(),
            "refresh": self.refresh_value.get(),
            "auth_file": self.auth_file.get(),
            "output": self.output_file.get().strip(),
        }
        if mode == "browser" and inputs["auto_open"]:
            try:
                validate_browser_configuration(
                    inputs["browser"], inputs["browser_path"], inputs["browser_arguments"]
                )
            except OAuthError as exc:
                messagebox.showwarning("浏览器设置", str(exc))
                return
        if mode == "browser" and batch_count > 1 and inputs["output"]:
            output_paths = [
                batch_output_path(inputs["output"], index, batch_count)
                for index in range(1, batch_count + 1)
            ]
            existing = next((path for path in output_paths if os.path.exists(path)), "")
            if existing:
                messagebox.showwarning(
                    "输出文件已存在",
                    f"为避免覆盖现有 CPA JSON，请更换保存路径。\n{existing}",
                )
                return
        self.credentials = None
        self.token_value.set("")
        self.url_value.set("")
        self.device_code.set("")
        self.set_json_result("")
        self.last_url = ""
        self.cancel_event.clear()
        self.set_running(True)
        if mode == "browser" and batch_count > 1:
            self.append_log(f"准备顺序执行 {batch_count} 次浏览器授权。")
        else:
            self.append_log("正在请求 Codex 授权...")
        self.worker = threading.Thread(target=self.run_worker, args=(mode, port, inputs), daemon=True)
        self.worker.start()

    def run_worker(self, mode: str, port: int, inputs: Dict[str, Any]) -> None:
        try:
            opener = build_opener(inputs["proxy"])
            if mode == "browser":
                total = int(inputs["batch_count"])

                def open_selected_browser(url: str) -> None:
                    open_authorization_url(
                        url,
                        inputs["browser"],
                        inputs["browser_path"],
                        inputs["browser_arguments"],
                    )

                for index in range(1, total + 1):
                    if self.cancel_event.is_set():
                        raise OAuthError("authentication cancelled")
                    self.events.put(("batch_started", (index, total)))
                    credentials = browser_login(
                        opener,
                        port,
                        not inputs["auto_open"],
                        on_auth_url=lambda url, current=index: self.enqueue_url(url, current, total),
                        cancel_event=self.cancel_event,
                        open_browser=open_selected_browser,
                    )
                    if self.cancel_event.is_set():
                        raise OAuthError("authentication cancelled")
                    acknowledgement: queue.Queue = queue.Queue(maxsize=1)
                    output = batch_output_path(inputs["output"], index, total)
                    self.events.put(
                        ("batch_success", (index, total, credentials, output, acknowledgement))
                    )
                    while True:
                        if self.cancel_event.is_set():
                            raise OAuthError("authentication cancelled")
                        try:
                            accepted = acknowledgement.get(timeout=0.1)
                            break
                        except queue.Empty:
                            continue
                    if not accepted:
                        return
                self.events.put(("batch_complete", total))
                return
            elif mode == "device":
                credentials = device_login(
                    opener, not inputs["auto_open"],
                    on_device_code=self.enqueue_device, cancel_event=self.cancel_event,
                )
            elif mode == "refresh":
                credentials = refresh_token(opener, inputs["refresh"])
            else:
                credentials = read_auth_file(inputs["auth_file"])
            if self.cancel_event.is_set():
                raise OAuthError("authentication cancelled")
            self.events.put(("success", (credentials, inputs["output"])))
        except OAuthError as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:  # Keep unexpected worker failures visible in the UI.
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def display_credentials(self, credentials: Dict[str, Any], output: str) -> bool:
        """Persist and reveal one result; callers decide whether a batch continues."""
        if self.cancel_event.is_set():
            self.append_log("认证结果已取消，未显示或保存凭据。")
            self.status.set("已取消")
            return False
        token = str(credentials.get("refresh_token") or "").strip()
        if not token:
            self.append_log("错误：认证响应中没有 refresh token，未保存凭据。")
            self.status.set("失败")
            messagebox.showerror("Codex OAuth", "认证响应中没有 refresh token")
            return False
        if output:
            try:
                write_json(output, credentials)
            except OAuthError as exc:
                self.append_log(f"保存 CPA JSON 失败：{exc}")
                self.status.set("失败")
                messagebox.showerror("保存失败", str(exc))
                return False
        if self.cancel_event.is_set():
            self.append_log("认证结果已取消，未显示或保存凭据。")
            self.status.set("已取消")
            return False
        self.credentials = credentials
        self.token_value.set(token)
        self.set_json_result(json.dumps(credentials, ensure_ascii=False, indent=2))
        self.append_log("认证成功，refresh token 已显示在顶部。")
        if output:
            self.append_log(f"完整凭据已保存到：{output}")
        return True

    @staticmethod
    def acknowledge_batch_result(acknowledgement: queue.Queue, accepted: bool) -> None:
        try:
            acknowledgement.put_nowait(accepted)
        except queue.Full:
            return

    def release_pending_batch_acknowledgements(self) -> None:
        pending = list(self.pending_batch_acknowledgements.values())
        self.pending_batch_acknowledgements.clear()
        for acknowledgement in pending:
            self.acknowledge_batch_result(acknowledgement, False)

    def defer_batch_acknowledgement(
        self, acknowledgement: queue.Queue, accepted: bool
    ) -> None:
        key = id(acknowledgement)
        if self.closed:
            self.acknowledge_batch_result(acknowledgement, False)
            return
        self.pending_batch_acknowledgements[key] = acknowledgement

        def confirm() -> None:
            pending = self.pending_batch_acknowledgements.pop(key, None)
            if pending is None:
                return
            confirmed = accepted and not self.closed and not self.cancel_event.is_set()
            self.acknowledge_batch_result(pending, confirmed)
            if not confirmed and not self.closed:
                if self.cancel_event.is_set():
                    self.status.set("已取消")
                self.set_running(False)

        try:
            self.root.after_idle(confirm)
        except tk.TclError:
            self.pending_batch_acknowledgements.pop(key, None)
            self.acknowledge_batch_result(acknowledgement, False)

    def process_events(self) -> None:
        if self.closed:
            return
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "url":
                    url, index, total = payload
                    self.last_url = url
                    self.url_value.set(url)
                    self.copy_url_button.configure(state="normal")
                    self.open_url_button.configure(state="normal")
                    if index and total:
                        self.status.set(f"第 {index}/{total} 次授权中")
                        self.append_log(f"第 {index}/{total} 次授权地址已生成，请在浏览器中完成登录。")
                    else:
                        self.append_log("授权地址已生成，请在浏览器中完成登录。")
                elif event == "batch_started":
                    index, total = payload
                    self.status.set(f"第 {index}/{total} 次授权中")
                    self.append_log(f"正在进行第 {index}/{total} 次浏览器授权。")
                elif event == "device":
                    url, code = payload
                    self.last_url = url
                    self.url_value.set(url)
                    self.device_code.set(code)
                    self.copy_url_button.configure(state="normal")
                    self.open_url_button.configure(state="normal")
                    self.append_log(f"设备码已生成：{code}")
                elif event == "batch_success":
                    index, total, credentials, output, acknowledgement = payload
                    accepted = False
                    try:
                        accepted = self.display_credentials(credentials, output)
                    except Exception as exc:  # Never leave the worker waiting for a UI acknowledgement.
                        self.append_log(f"处理第 {index}/{total} 次结果失败：{type(exc).__name__}: {exc}")
                        self.status.set("失败")
                    self.defer_batch_acknowledgement(acknowledgement, accepted)
                    if accepted:
                        self.status.set(f"已完成 {index}/{total}")
                    else:
                        self.set_running(False)
                elif event == "batch_complete":
                    total = payload
                    if self.cancel_event.is_set():
                        self.status.set("已取消")
                    else:
                        self.status.set(f"已完成 {total}/{total} 次授权")
                        self.append_log(f"已顺序完成 {total} 次浏览器授权。")
                    self.set_running(False)
                elif event == "success":
                    credentials, output = payload
                    if self.display_credentials(credentials, output):
                        self.status.set("完成，refresh token 已显示")
                    self.set_running(False)
                elif event == "error":
                    if self.cancel_event.is_set():
                        self.append_log("认证已取消，未继续后续授权。")
                        self.status.set("已取消")
                    else:
                        self.append_log(f"错误：{payload}")
                        self.status.set("失败")
                        messagebox.showerror("Codex OAuth", payload)
                    self.set_running(False)
        except queue.Empty:
            pass
        if not self.closed:
            self.root.after(100, self.process_events)

    def set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")
        if running:
            self.progress.start(12)
            self.status.set("处理中...")
        else:
            self.progress.stop()

    def cancel(self) -> None:
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self.status.set("正在取消...")
            self.append_log("已请求取消，正在结束当前请求。")

    def toggle_token(self) -> None:
        self.token_entry.configure(show="" if self.show_token.get() else "*")

    def copy_url(self) -> None:
        if self.last_url:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.last_url)
            self.status.set("授权地址已复制")

    def open_url(self) -> None:
        if self.last_url:
            try:
                open_authorization_url(
                    self.last_url,
                    self.browser_choice.get(),
                    self.browser_path.get(),
                    self.browser_arguments.get(),
                )
            except OAuthError as exc:
                messagebox.showerror("浏览器设置", str(exc))

    def copy_token(self) -> None:
        token = self.token_value.get()
        if token:
            self.root.clipboard_clear()
            self.root.clipboard_append(token)
            self.status.set("refresh token 已复制")

    def set_json_result(self, value: str) -> None:
        self.json_result = value
        self.json_text.configure(state="normal")
        self.json_text.delete("1.0", "end")
        self.json_text.insert("1.0", value)
        self.json_text.configure(state="disabled")

    def copy_json(self) -> None:
        if self.json_result:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.json_result)
            self.status.set("CPA JSON 已复制")

    def save_current_json(self) -> None:
        if not self.credentials:
            messagebox.showwarning("没有结果", "请先完成一次登录或 token 操作")
            return
        path = self.output_file.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
        if not path:
            return
        try:
            write_json(path, self.credentials)
        except OAuthError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.output_file.set(path)
        self.append_log(f"完整 CPA JSON 已保存到：{path}")
        self.status.set("CPA JSON 已保存")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cancel_event.set()
        self.release_pending_batch_acknowledgements()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    CodexOAuthApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
