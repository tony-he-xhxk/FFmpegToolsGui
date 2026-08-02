"""
FFmpeg 视频剪辑工具 — 带图形界面的单文件应用
功能：自动检测 ffmpeg → 选择视频 → 检测完整性 → 设置起止时间 → 剪辑输出
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import subprocess
import threading
import queue
import os
import sys
import shutil
import re
from datetime import datetime
from typing import Optional, Callable

# ──────────────────────────────────────────────
#  辅助函数
# ──────────────────────────────────────────────

def validate_time(time_str: str) -> bool:
    """校验时间格式：空（缺省）、HH:MM:SS[.ms]、MM:SS[.ms]、或纯秒数。"""
    s = time_str.strip()
    if not s:
        return True
    return bool(
        re.match(r'^\d+:\d{2}:\d{2}(\.\d+)?$', s)    # HH:MM:SS[.ms]
        or re.match(r'^\d+:\d{2}(\.\d+)?$', s)         # MM:SS[.ms]
        or re.match(r'^\d+(\.\d+)?$', s)                # 纯秒数
    )


def find_ffmpeg() -> Optional[str]:
    """在系统 PATH 中查找 ffmpeg；返回绝对路径或 None。"""
    path = shutil.which('ffmpeg')
    if path:
        return os.path.abspath(path)
    return None


def generate_output_path(input_path: str) -> str:
    """在同目录下自动生成不重名的输出路径：原文件名_trimmed.扩展名"""
    base, ext = os.path.splitext(input_path)
    out = f"{base}_trimmed{ext}"
    if not os.path.exists(out):
        return out
    counter = 2
    while True:
        out = f"{base}_trimmed_{counter}{ext}"
        if not os.path.exists(out):
            return out
        counter += 1


# ──────────────────────────────────────────────
#  主 GUI 类
# ──────────────────────────────────────────────

class FFmpegClipGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("FFmpeg 视频剪辑工具")
        self.window.geometry("960x680")
        self.window.minsize(740, 500)

        # 状态
        self.ffmpeg_path: str = ""
        self.input_file: str = ""
        self.is_running: bool = False
        self.process: Optional[subprocess.Popen] = None
        self.returncode: Optional[int] = None
        self.on_complete_cb: Optional[Callable] = None
        self.output_queue = queue.Queue()

        # 布局
        self._build_ui()

        # 启动后自动检测 ffmpeg
        self.window.after(200, self._auto_detect_ffmpeg)

        # 开始轮询子进程输出
        self._poll_queue()

        # 关闭窗口时清理
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI 构建 ────────────────────────────

    def _build_ui(self):
        """构建全部界面组件。"""
        pad = {"padx": 10, "pady": 5}
        font_mono = ('Consolas', 10) if sys.platform == 'win32' else ('Menlo', 12)

        # ----- Row 0: FFmpeg 路径 -----
        ttk.Label(self.window, text="FFmpeg 路径:").grid(row=0, column=0, sticky="w", **pad)
        self.ffmpeg_var = tk.StringVar(value="（未检测）")
        ttk.Entry(self.window, textvariable=self.ffmpeg_var, state="readonly", width=60)\
            .grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(self.window, text="自动检测", command=self._auto_detect_ffmpeg)\
            .grid(row=0, column=2, **pad)
        self.btn_ff_browse = ttk.Button(self.window, text="手动选择…", command=self._manual_select_ffmpeg)
        self.btn_ff_browse.grid(row=0, column=3, **pad)

        # ----- Row 1: 输入视频 -----
        ttk.Label(self.window, text="输入视频:").grid(row=1, column=0, sticky="w", **pad)
        self.input_var = tk.StringVar(value="（未选择）")
        ttk.Entry(self.window, textvariable=self.input_var, state="readonly", width=60)\
            .grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(self.window, text="浏览…", command=self._select_input_file)\
            .grid(row=1, column=2, columnspan=2, sticky="w", **pad)

        # ----- Row 2: 时间输入 -----
        time_frame = ttk.Frame(self.window)
        time_frame.grid(row=2, column=0, columnspan=4, sticky="w", padx=10, pady=5)

        ttk.Label(time_frame, text="开头时间:").grid(row=0, column=0, sticky="w")
        self.start_var = tk.StringVar()
        self.entry_start = ttk.Entry(time_frame, textvariable=self.start_var, width=20)
        self.entry_start.grid(row=0, column=1, sticky="w", padx=(0, 12))

        ttk.Label(time_frame, text="结尾时间:").grid(row=0, column=2, sticky="w")
        self.end_var = tk.StringVar()
        self.entry_end = ttk.Entry(time_frame, textvariable=self.end_var, width=20)
        self.entry_end.grid(row=0, column=3, sticky="w")

        ttk.Label(
            self.window,
            text="开头时间留空，默认为从开头截起；结尾时间留空，默认为截到结尾。",
            foreground="gray"
        ).grid(row=3, column=1, columnspan=3, sticky="w", **pad)

        # ----- Row 3: 按钮区 -----
        btn_frame = ttk.Frame(self.window)
        btn_frame.grid(row=4, column=0, columnspan=4, pady=(8, 4), padx=10)

        self.btn_check = ttk.Button(btn_frame, text="🔍 检测完整性", command=self._check_integrity_only)
        self.btn_check.pack(side="left", padx=5)

        self.btn_clip = ttk.Button(btn_frame, text="✂️ 开始剪辑", command=self._start_clip)
        self.btn_clip.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(btn_frame, text="⏹ 停止", command=self._stop_process, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.btn_clear = ttk.Button(btn_frame, text="🗑 清空终端", command=self._clear_terminal)
        self.btn_clear.pack(side="left", padx=5)

        # ----- Row 4: 终端输出 -----
        ttk.Label(self.window, text="终端输出（可选中复制）:").grid(
            row=5, column=0, columnspan=4, sticky="w", **pad)

        self.terminal = ScrolledText(
            self.window,
            font=font_mono,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            state="disabled",
            wrap="word",
            height=22,
        )
        self.terminal.grid(row=6, column=0, columnspan=4, sticky="nsew", padx=10, pady=(0, 10))
        # 绑定 Ctrl+C 复制
        self.terminal.bind("<Control-c>", lambda e: self.terminal.event_generate("<<Copy>>"))
        self.terminal.bind("<Control-C>", lambda e: self.terminal.event_generate("<<Copy>>"))

        # 行列拉伸权重
        self.window.grid_columnconfigure(1, weight=1)
        self.window.grid_rowconfigure(6, weight=1)

    # ── FFmpeg 检测 ────────────────────────

    def _auto_detect_ffmpeg(self):
        """自动查找 ffmpeg，找不到则提示手动选择。"""
        path = find_ffmpeg()
        if path:
            self.ffmpeg_path = path
            self.ffmpeg_var.set(path)
            self._append_terminal(f"[{self._ts()}] ✅ 自动检测到 FFmpeg：{path}")
        else:
            self.ffmpeg_var.set("（未找到，请手动选择）")
            self._append_terminal(f"[{self._ts()}] ❌ 未在系统 PATH 中找到 FFmpeg")
            # 弹窗引导手动选择
            answer = messagebox.askyesno(
                "未找到 FFmpeg",
                "未能自动检测到 ffmpeg。\n\n是否现在手动选择 ffmpeg.exe 所在位置？"
            )
            if answer:
                self._manual_select_ffmpeg()

    def _manual_select_ffmpeg(self):
        """弹出文件对话框让用户手动选择 ffmpeg 可执行文件。"""
        filetypes = [("ffmpeg 可执行文件", "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"),
                     ("所有文件", "*.*")]
        path = filedialog.askopenfilename(title="选择 ffmpeg 可执行文件", filetypes=filetypes)
        if not path:
            return
        path = os.path.abspath(path)
        # 简单验证
        if not os.path.isfile(path):
            messagebox.showerror("错误", f"所选路径无效：\n{path}")
            return
        self.ffmpeg_path = path
        self.ffmpeg_var.set(path)
        self._append_terminal(f"[{self._ts()}] 📁 手动指定 FFmpeg：{path}")

    # ── 文件选择 ───────────────────────────

    def _select_input_file(self):
        """选择输入视频文件。"""
        filetypes = [("视频文件", "*.mp4 *.mkv *.mov *.avi *.ts *.webm *.flv *.wmv"),
                     ("所有文件", "*.*")]
        path = filedialog.askopenfilename(title="选择要剪辑的视频文件", filetypes=filetypes)
        if not path:
            return
        self.input_file = os.path.abspath(path)
        self.input_var.set(self.input_file)

    # ── 终端操作 ────────────────────────────

    def _ts(self) -> str:
        """简短时间戳。"""
        return datetime.now().strftime("%H:%M:%S")

    def _append_terminal(self, text: str):
        """向终端输出区域追加一行。"""
        self.terminal.configure(state="normal")
        self.terminal.insert("end", text + "\n")
        self.terminal.see("end")
        self.terminal.configure(state="disabled")

    def _clear_terminal(self):
        self.terminal.configure(state="normal")
        self.terminal.delete("1.0", "end")
        self.terminal.configure(state="disabled")

    # ── 按钮状态管理 ────────────────────────

    def _set_buttons_state(self, running: bool):
        """执行中禁用关键按钮，防止重复触发。"""
        state = "disabled" if running else "normal"
        self.btn_check.configure(state=state)
        self.btn_clip.configure(state=state)
        self.btn_ff_browse.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.entry_start.configure(state="disabled" if running else "normal")
        self.entry_end.configure(state="disabled" if running else "normal")

    # ── 子进程管理 ──────────────────────────

    def _run_command(self, cmd_str: str, on_complete: Optional[Callable] = None):
        """
        后台执行命令，实时将 stdout/stderr 输出到终端。
        on_complete(returncode) 在命令结束后在主线程回调。
        """
        self.is_running = True
        self.on_complete_cb = on_complete
        self._set_buttons_state(True)

        self._append_terminal(f"[{self._ts()}] ▶ {cmd_str}")
        self._append_terminal("─" * 60)

        self.process = subprocess.Popen(
            cmd_str,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def _read_output():
            try:
                for line in self.process.stdout:
                    self.output_queue.put(line.rstrip())
            except Exception:
                pass
            self.process.wait()
            self.returncode = self.process.returncode
            self.output_queue.put(None)   # 信号：完成

        threading.Thread(target=_read_output, daemon=True).start()

    def _poll_queue(self):
        """定时轮询输出队列，刷新终端。"""
        try:
            while True:
                line = self.output_queue.get_nowait()
                if line is None:
                    self._append_terminal("─" * 60)
                    rc = self.returncode if self.returncode is not None else -1
                    if rc == 0:
                        self._append_terminal(f"[{self._ts()}] ✅ 命令执行成功（返回码 0）\n")
                    else:
                        self._append_terminal(f"[{self._ts()}] ❌ 命令异常退出（返回码 {rc}）\n")
                    self.is_running = False
                    self.process = None
                    self._set_buttons_state(False)
                    # 触发回调
                    if self.on_complete_cb:
                        cb = self.on_complete_cb
                        self.on_complete_cb = None
                        cb(rc)
                else:
                    self._append_terminal(line)
        except queue.Empty:
            pass
        self.window.after(150, self._poll_queue)

    def _stop_process(self):
        """终止正在运行的子进程。"""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._append_terminal(f"[{self._ts()}] ⏹ 用户手动停止")

    # ── 完整性检测 ──────────────────────────

    def _check_integrity_only(self):
        """仅检测完整性（按钮触发）。"""
        if not self._pre_check():
            return
        cmd = f'"{self.ffmpeg_path}" -v error -xerror -i "{self.input_file}" -f null -'
        self._run_command(cmd)

    def _check_integrity(self, on_pass: callable):
        """
        检测输入视频完整性；通过后调用 on_pass()，失败则弹窗。
        用作剪辑流程的前置步骤。
        """
        if self.is_running:
            return
        cmd = f'"{self.ffmpeg_path}" -v error -xerror -i "{self.input_file}" -f null -'
        self._append_terminal(f"[{self._ts()}] 🔍 开始完整性检测…")
        self._run_command(cmd, on_complete=lambda rc: self._on_check_result(rc, on_pass))

    def _on_check_result(self, returncode: int, on_pass: callable):
        if returncode == 0:
            self._append_terminal(f"[{self._ts()}] ✅ 视频完整性检测通过")
            on_pass()
        else:
            self._append_terminal(f"[{self._ts()}] ❌ 视频文件不完整或已损坏")
            messagebox.showerror("视频损坏", "该视频文件不完整或已损坏，请选择其他文件。")

    # ── 前置校验 ──────────────────────────

    def _pre_check(self) -> bool:
        """执行命令前的通用校验。返回 True 表示通过。"""
        if not self.ffmpeg_path:
            messagebox.showwarning("未配置 FFmpeg", "请先检测或手动选择 ffmpeg 可执行文件。")
            return False
        if not self.input_file or not os.path.isfile(self.input_file):
            messagebox.showwarning("未选择文件", "请先选择要处理的视频文件。")
            return False
        if self.is_running:
            messagebox.showinfo("任务进行中", "当前有命令正在执行，请等待完成或点击「停止」。")
            return False
        return True

    # ── 剪辑流程 ────────────────────────────

    def _start_clip(self):
        """主入口：校验 → 完整性检测 → 剪辑。"""
        if not self._pre_check():
            return

        start = self.start_var.get().strip()
        end = self.end_var.get().strip()

        # 起止时间不能同时为空
        if not start and not end:
            messagebox.showwarning("参数不足", "开头和结尾时间不能同时为空，请至少填写一项。")
            return

        # 格式校验
        if not validate_time(start):
            messagebox.showwarning("格式错误", f"开头时间格式不正确：\n「{start}」\n支持 HH:MM:SS、MM:SS、纯秒数")
            return
        if not validate_time(end):
            messagebox.showwarning("格式错误", f"结尾时间格式不正确：\n「{end}」\n支持 HH:MM:SS、MM:SS、纯秒数")
            return

        # 先做完整性检测，通过后进入剪辑
        self._check_integrity(on_pass=self._do_clip)

    def _do_clip(self):
        """执行剪辑（完整性检测已通过）。"""
        start = self.start_var.get().strip()
        end = self.end_var.get().strip()
        output = generate_output_path(self.input_file)

        # 构建命令：-ss/-to 全部放在 -i 后面（重新编码模式）
        parts = [f'"{self.ffmpeg_path}"', '-i', f'"{self.input_file}"']
        if start:
            parts.extend(['-ss', start])
        if end:
            parts.extend(['-to', end])
        parts.extend([
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
            '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart',
            f'"{output}"'
        ])

        cmd = ' '.join(parts)

        self._append_terminal(f"[{self._ts()}] ✂️ 开始剪辑 → {os.path.basename(output)}")
        self._run_command(cmd, on_complete=lambda rc: self._on_clip_done(rc, output))

    def _on_clip_done(self, returncode: int, output_path: str):
        if returncode == 0:
            self._append_terminal(f"[{self._ts()}] 🎉 剪辑完成！输出：{output_path}")
            messagebox.showinfo("剪辑完成", f"已保存到：\n{output_path}")
        else:
            self._append_terminal(f"[{self._ts()}] ❌ 剪辑失败（返回码 {returncode}），请查看上方终端输出排查")
            messagebox.showerror("剪辑失败", f"ffmpeg 返回错误码 {returncode}，请检查终端输出中的报错信息。")

    # ── 窗口关闭 ───────────────────────────

    def _on_close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.window.destroy()

    def run(self):
        self.window.mainloop()


# ──────────────────────────────────────────────
#  入口
# ──────────────────────────────────────────────

if __name__ == "__main__":
    FFmpegClipGUI().run()
