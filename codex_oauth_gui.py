#!/usr/bin/env python3
"""Tkinter desktop UI for the standalone Codex OAuth helper."""

from __future__ import annotations

import queue
import json
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


class CodexOAuthApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex OAuth 登录工具")
        self.root.geometry("760x700")
        self.root.minsize(680, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.credentials: Optional[Dict[str, Any]] = None
        self.last_url = ""

        self.mode = tk.StringVar(value="browser")
        self.proxy = tk.StringVar()
        self.port = tk.StringVar(value="1455")
        self.auth_file = tk.StringVar()
        self.refresh_value = tk.StringVar()
        self.output_file = tk.StringVar()
        self.auto_open = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="就绪")
        self.token_value = tk.StringVar()
        self.show_token = tk.BooleanVar(value=True)
        self.url_value = tk.StringVar()
        self.device_code = tk.StringVar()
        self.json_result = ""

        self.build_ui()
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
        body.rowconfigure(4, weight=1)

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

        input_box = ttk.LabelFrame(body, text="输入（仅当前操作需要时显示）", padding=12)
        input_box.grid(row=2, column=0, sticky="ew", pady=(10, 0))
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
        output_box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        output_box.columnconfigure(1, weight=1)
        ttk.Label(output_box, text="保存 JSON:").grid(row=0, column=0, sticky="w")
        ttk.Entry(output_box, textvariable=self.output_file).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(output_box, text="选择路径", command=self.choose_output_file).grid(row=0, column=2, padx=(8, 0))

        result_box = ttk.LabelFrame(body, text="授权信息", padding=12)
        result_box.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
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
        self.json_text = tk.Text(result_box, height=8, wrap="none", state="disabled", relief="flat", background="#f5f5f5")
        self.json_text.grid(row=3, column=1, columnspan=2, sticky="nsew", padx=(8, 0), pady=(12, 0))
        ttk.Button(result_box, text="复制 JSON", command=self.copy_json).grid(row=3, column=3, sticky="n", padx=(8, 0), pady=(12, 0))
        self.log = tk.Text(result_box, height=3, wrap="word", state="disabled", relief="flat", background="#f5f5f5")
        self.log.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=(12, 0))

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
        is_refresh = mode == "refresh"
        is_file = mode == "file"
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

    def choose_auth_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("JSON files", "*.json"), ("All files", "*.*")))
        if path:
            self.auth_file.set(path)

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

    def enqueue_url(self, url: str) -> None:
        self.events.put(("url", url))

    def enqueue_device(self, url: str, code: str) -> None:
        self.events.put(("device", (url, code)))

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        mode = self.mode.get()
        if mode == "refresh" and not self.refresh_value.get().strip():
            messagebox.showwarning("缺少输入", "请填写 refresh token")
            return
        if mode == "file" and not self.auth_file.get().strip():
            messagebox.showwarning("缺少输入", "请选择 CPA auth JSON 文件")
            return
        try:
            port = int(self.port.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning("端口错误", "回调端口必须是 1 到 65535")
            return

        self.credentials = None
        self.token_value.set("")
        self.url_value.set("")
        self.device_code.set("")
        self.set_json_result("")
        self.last_url = ""
        self.cancel_event.clear()
        self.set_running(True)
        self.append_log("正在请求 Codex 授权...")
        inputs = {
            "proxy": self.proxy.get().strip() or None,
            "auto_open": self.auto_open.get(),
            "refresh": self.refresh_value.get(),
            "auth_file": self.auth_file.get(),
            "output": self.output_file.get().strip(),
        }
        self.worker = threading.Thread(target=self.run_worker, args=(mode, port, inputs), daemon=True)
        self.worker.start()

    def run_worker(self, mode: str, port: int, inputs: Dict[str, Any]) -> None:
        try:
            opener = build_opener(inputs["proxy"])
            if mode == "browser":
                credentials = browser_login(
                    opener, port, not inputs["auto_open"],
                    on_auth_url=self.enqueue_url, cancel_event=self.cancel_event,
                )
            elif mode == "device":
                credentials = device_login(
                    opener, not inputs["auto_open"],
                    on_device_code=self.enqueue_device, cancel_event=self.cancel_event,
                )
            elif mode == "refresh":
                credentials = refresh_token(opener, inputs["refresh"])
            else:
                credentials = read_auth_file(inputs["auth_file"])
            output = inputs["output"]
            if output:
                write_json(output, credentials)
            self.events.put(("success", (credentials, output)))
        except OAuthError as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:  # Keep unexpected worker failures visible in the UI.
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "url":
                    self.last_url = payload
                    self.url_value.set(payload)
                    self.copy_url_button.configure(state="normal")
                    self.open_url_button.configure(state="normal")
                    self.append_log("授权地址已生成，请在浏览器中完成登录。")
                elif event == "device":
                    url, code = payload
                    self.last_url = url
                    self.url_value.set(url)
                    self.device_code.set(code)
                    self.copy_url_button.configure(state="normal")
                    self.open_url_button.configure(state="normal")
                    self.append_log(f"设备码已生成：{code}")
                elif event == "success":
                    credentials, output = payload
                    token = str(credentials.get("refresh_token") or "").strip()
                    if not token:
                        self.append_log("错误：认证响应中没有 refresh token。")
                        self.status.set("失败")
                        self.set_running(False)
                        messagebox.showerror("Codex OAuth", "认证响应中没有 refresh token")
                        continue
                    self.credentials = credentials
                    self.token_value.set(token)
                    self.set_json_result(json.dumps(credentials, ensure_ascii=False, indent=2))
                    self.append_log("认证成功，refresh token 已显示在顶部。")
                    if output:
                        self.append_log(f"完整凭据已保存到：{output}")
                    self.status.set("完成，refresh token 已显示")
                    self.set_running(False)
                elif event == "error":
                    self.append_log(f"错误：{payload}")
                    self.status.set("失败")
                    self.set_running(False)
                    if payload != "authentication cancelled":
                        messagebox.showerror("Codex OAuth", payload)
        except queue.Empty:
            pass
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
            webbrowser.open(self.last_url)

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
        self.cancel_event.set()
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
