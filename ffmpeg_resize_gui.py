"""
FFmpeg 分辨率调整工具 — 带图形界面的单文件应用
功能：自动检测 ffmpeg → 选择视频 → 等比例/强制缩放 → 执行输出
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
from datetime import datetime
from typing import Optional, Callable


# ──────────────────────────────────────────────
#  辅助函数
# ──────────────────────────────────────────────

def find_ffmpeg() -> Optional[str]:
    """在系统 PATH 中查找 ffmpeg；返回绝对路径或 None。"""
    path = shutil.which('ffmpeg')
    if path:
        return os.path.abspath(path)
    return None


def find_ffprobe(ffmpeg_path: str) -> Optional[str]:
    """查找 ffprobe：优先从 PATH 找，其次从 ffmpeg 同目录推断。"""
    path = shutil.which('ffprobe')
    if path:
        return os.path.abspath(path)
    # 从 ffmpeg 路径推断
    ff_dir = os.path.dirname(ffmpeg_path)
    name = 'ffprobe.exe' if sys.platform == 'win32' else 'ffprobe'
    candidate = os.path.join(ff_dir, name)
    if os.path.isfile(candidate):
        return candidate
    return None


def generate_output_path(input_path: str, suffix: str) -> str:
    """在同目录下自动生成不重名的输出路径。"""
    base, ext = os.path.splitext(input_path)
    out = f"{base}{suffix}{ext}"
    if not os.path.exists(out):
        return out
    counter = 2
    while True:
        out = f"{base}{suffix}_{counter}{ext}"
        if not os.path.exists(out):
            return out
        counter += 1


# ──────────────────────────────────────────────
#  主 GUI 类
# ──────────────────────────────────────────────

class FFmpegResizeGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("FFmpeg 分辨率调整工具")
        self.window.geometry("960x720")
        self.window.minsize(740, 560)

        # 状态
        self.ffmpeg_path: str = ""
        self.input_file: str = ""
        self.video_width: Optional[int] = None
        self.video_height: Optional[int] = None
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

        # 原分辨率显示
        ttk.Label(self.window, text="原分辨率:").grid(row=2, column=0, sticky="w", **pad)
        self.resolution_var = tk.StringVar(value="（选择视频后自动获取）")
        ttk.Label(self.window, textvariable=self.resolution_var, foreground="#007acc")\
            .grid(row=2, column=1, sticky="w", **pad)

        # ----- Row 3: 分辨率设置 ──────────────
        settings_frame = ttk.LabelFrame(self.window, text="分辨率设置", padding=10)
        settings_frame.grid(row=3, column=0, columnspan=4, sticky="ew", padx=10, pady=5)

        # 模式选择
        self.resize_mode = tk.StringVar(value="width")
        mode_frame = ttk.Frame(settings_frame)
        mode_frame.pack(fill="x", pady=(0, 8))

        ttk.Radiobutton(mode_frame, text="等比例缩放：指定宽度", variable=self.resize_mode,
                        value="width", command=self._on_mode_change)\
            .grid(row=0, column=0, sticky="w", padx=(0, 20))
        ttk.Radiobutton(mode_frame, text="等比例缩放：指定高度", variable=self.resize_mode,
                        value="height", command=self._on_mode_change)\
            .grid(row=0, column=1, sticky="w", padx=(0, 20))
        ttk.Radiobutton(mode_frame, text="强制指定宽度 × 高度", variable=self.resize_mode,
                        value="force", command=self._on_mode_change)\
            .grid(row=0, column=2, sticky="w")

        # 输入面板容器（三个面板，按模式切换显示）
        panel_container = ttk.Frame(settings_frame)
        panel_container.pack(fill="x", pady=(4, 0))

        # 面板 A — 等比例：指定宽度
        self.panel_width = ttk.Frame(panel_container)
        ttk.Label(self.panel_width, text="宽度:").pack(side="left")
        self.width_var = tk.StringVar()
        self.entry_width = ttk.Entry(self.panel_width, textvariable=self.width_var, width=10)
        self.entry_width.pack(side="left", padx=(4, 6))
        ttk.Label(self.panel_width, text="像素  →  高度按原比例自动计算", foreground="gray")\
            .pack(side="left")

        # 面板 B — 等比例：指定高度
        self.panel_height = ttk.Frame(panel_container)
        ttk.Label(self.panel_height, text="高度:").pack(side="left")
        self.height_var = tk.StringVar()
        self.entry_height = ttk.Entry(self.panel_height, textvariable=self.height_var, width=10)
        self.entry_height.pack(side="left", padx=(4, 6))
        ttk.Label(self.panel_height, text="像素  →  宽度按原比例自动计算", foreground="gray")\
            .pack(side="left")

        # 面板 C — 强制指定
        self.panel_force = ttk.Frame(panel_container)
        ttk.Label(self.panel_force, text="宽度:").pack(side="left")
        self.force_w_var = tk.StringVar()
        ttk.Entry(self.panel_force, textvariable=self.force_w_var, width=10)\
            .pack(side="left", padx=(4, 10))
        ttk.Label(self.panel_force, text="高度:").pack(side="left")
        self.force_h_var = tk.StringVar()
        ttk.Entry(self.panel_force, textvariable=self.force_h_var, width=10)\
            .pack(side="left", padx=(4, 6))
        ttk.Label(self.panel_force, text="像素  （不保持原比例，画面可能变形）", foreground="gray")\
            .pack(side="left")

        # 默认显示「指定宽度」面板
        self._on_mode_change()

        # ----- Row 4: 按钮区 -----
        btn_frame = ttk.Frame(self.window)
        btn_frame.grid(row=4, column=0, columnspan=4, pady=(8, 4), padx=10)

        self.btn_resize = ttk.Button(btn_frame, text="📐 开始调整", command=self._start_resize)
        self.btn_resize.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(btn_frame, text="⏹ 停止", command=self._stop_process, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.btn_clear = ttk.Button(btn_frame, text="🗑 清空终端", command=self._clear_terminal)
        self.btn_clear.pack(side="left", padx=5)

        # ----- Row 5 / 6: 终端输出 -----
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
        self.terminal.bind("<Control-c>", lambda e: self.terminal.event_generate("<<Copy>>"))
        self.terminal.bind("<Control-C>", lambda e: self.terminal.event_generate("<<Copy>>"))

        # 行列拉伸权重
        self.window.grid_columnconfigure(1, weight=1)
        self.window.grid_rowconfigure(6, weight=1)

    def _on_mode_change(self):
        """切换分辨率设置面板。"""
        for panel in (self.panel_width, self.panel_height, self.panel_force):
            panel.pack_forget()
        mode = self.resize_mode.get()
        if mode == "width":
            self.panel_width.pack(fill="x")
        elif mode == "height":
            self.panel_height.pack(fill="x")
        else:
            self.panel_force.pack(fill="x")

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
        if not os.path.isfile(path):
            messagebox.showerror("错误", f"所选路径无效：\n{path}")
            return
        self.ffmpeg_path = path
        self.ffmpeg_var.set(path)
        self._append_terminal(f"[{self._ts()}] 📁 手动指定 FFmpeg：{path}")

    # ── 文件选择 ───────────────────────────

    def _select_input_file(self):
        """选择输入视频文件，并获取原始分辨率。"""
        filetypes = [("视频文件", "*.mp4 *.mkv *.mov *.avi *.ts *.webm *.flv *.wmv"),
                     ("所有文件", "*.*")]
        path = filedialog.askopenfilename(title="选择视频文件", filetypes=filetypes)
        if not path:
            return
        self.input_file = os.path.abspath(path)
        self.input_var.set(self.input_file)
        self._fetch_video_resolution()

    def _fetch_video_resolution(self):
        """同步获取视频原始分辨率（ffprobe 很快）。"""
        if not self.ffmpeg_path or not self.input_file:
            return

        ffprobe_path = find_ffprobe(self.ffmpeg_path)
        if not ffprobe_path:
            self.resolution_var.set("（未找到 ffprobe）")
            self.video_width = None
            self.video_height = None
            return

        cmd = (
            f'"{ffprobe_path}" -v error -select_streams v:0 '
            f'-show_entries stream=width,height -of csv=p=0 '
            f'"{self.input_file}"'
        )
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip())
            parts = result.stdout.strip().split(',')
            if len(parts) != 2:
                raise ValueError(f"意外输出: {result.stdout}")
            self.video_width = int(parts[0])
            self.video_height = int(parts[1])
            self.resolution_var.set(f"{self.video_width} × {self.video_height}")
            self._append_terminal(f"[{self._ts()}] 📏 原分辨率：{self.video_width}×{self.video_height}")
        except Exception as e:
            self.video_width = None
            self.video_height = None
            self.resolution_var.set("（获取失败）")
            self._append_terminal(f"[{self._ts()}] ⚠ 无法获取视频分辨率：{e}")

    # ── 终端操作 ────────────────────────────

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _append_terminal(self, text: str):
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
        state = "disabled" if running else "normal"
        self.btn_resize.configure(state=state)
        self.btn_ff_browse.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.entry_width.configure(state="disabled" if running else "normal")
        self.entry_height.configure(state="disabled" if running else "normal")

    # ── 子进程管理 ──────────────────────────

    def _run_command(self, cmd_str: str, on_complete: Optional[Callable] = None):
        """后台执行命令，实时将 stdout/stderr 输出到终端。"""
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
            self.output_queue.put(None)

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

    # ── 前置校验 ──────────────────────────

    def _pre_check(self) -> bool:
        """执行命令前的通用校验。"""
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

    # ── 分辨率调整流程 ──────────────────────

    def _start_resize(self):
        """主入口：校验 → 构建命令 → 执行。"""
        if not self._pre_check():
            return

        if self.video_width is None or self.video_height is None:
            messagebox.showwarning("缺少原分辨率", "无法获取原始分辨率，请重新选择视频文件。")
            return

        mode = self.resize_mode.get()

        # 取值 & 校验
        if mode == "width":
            raw = self.width_var.get().strip()
            if not raw.isdigit() or int(raw) <= 0:
                messagebox.showwarning("输入错误", "宽度必须是一个正整数。")
                return
            out_w = int(raw)
            out_h = None
            label = f"w{out_w}"
        elif mode == "height":
            raw = self.height_var.get().strip()
            if not raw.isdigit() or int(raw) <= 0:
                messagebox.showwarning("输入错误", "高度必须是一个正整数。")
                return
            out_w = None
            out_h = int(raw)
            label = f"h{out_h}"
        else:  # force
            raw_w = self.force_w_var.get().strip()
            raw_h = self.force_h_var.get().strip()
            if not raw_w.isdigit() or int(raw_w) <= 0:
                messagebox.showwarning("输入错误", "宽度必须是一个正整数。")
                return
            if not raw_h.isdigit() or int(raw_h) <= 0:
                messagebox.showwarning("输入错误", "高度必须是一个正整数。")
                return
            out_w = int(raw_w)
            out_h = int(raw_h)
            label = f"{out_w}x{out_h}"

        # 构建 scale 参数
        if mode == "width":
            scale_arg = f"scale={out_w}:-2"
        elif mode == "height":
            scale_arg = f"scale=-2:{out_h}"
        else:
            scale_arg = f"scale={out_w}:{out_h}"

        # 判断是否需要 -crf 23（任一输出边 < 对应输入边即为缩小）
        is_shrinking = False
        if mode == "width":
            is_shrinking = out_w < self.video_width
        elif mode == "height":
            is_shrinking = out_h < self.video_height
        else:
            is_shrinking = (out_w < self.video_width) or (out_h < self.video_height)

        # 输出路径
        suffix = f"_resized_{label}"
        output_path = generate_output_path(self.input_file, suffix)

        # 构建命令
        parts = [f'"{self.ffmpeg_path}"', '-i', f'"{self.input_file}"', '-vf', scale_arg]
        if is_shrinking:
            parts.append('-crf')
            parts.append('23')
            self._append_terminal(f"[{self._ts()}] ℹ 检测到缩小分辨率，自动追加 -crf 23")
        parts.append(f'"{output_path}"')

        cmd = ' '.join(parts)

        self._append_terminal(f"[{self._ts()}] 📐 开始调整分辨率 → {os.path.basename(output_path)}")
        self._run_command(cmd, on_complete=lambda rc: self._on_resize_done(rc, output_path))

    def _on_resize_done(self, returncode: int, output_path: str):
        if returncode == 0:
            self._append_terminal(f"[{self._ts()}] 🎉 调整完成！输出：{output_path}")
            messagebox.showinfo("调整完成", f"已保存到：\n{output_path}")
        else:
            self._append_terminal(f"[{self._ts()}] ❌ 调整失败（返回码 {returncode}），请查看上方终端输出排查")
            messagebox.showerror("调整失败", f"ffmpeg 返回错误码 {returncode}，请检查终端输出中的报错信息。")

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
    FFmpegResizeGUI().run()
