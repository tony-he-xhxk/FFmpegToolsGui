"""
FFmpeg 分辨率调整工具 — 带图形界面的单文件应用
功能：自动检测 ffmpeg → 选择视频 → 等比例/强制缩放 → 选编码参数 → 执行输出
选项：视频编码器、编码档位、音频处理
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
import json
import re
from datetime import datetime
from typing import Optional, Callable


# ──────────────────────────────────────────────
#  可调参数（下拉框显示"人话"，括号内是通俗解释）
# ──────────────────────────────────────────────

# 视频编码器：列表第一项为默认选项
ENCODER_CHOICES = [
    ("CPU · H.264（最通用，兼容性最好）", "libx264"),
    ("CPU · H.265（体积更小，速度较慢）", "libx265"),
    ("N卡 · H.264（NVIDIA 显卡加速，速度快）", "h264_nvenc"),
    ("N卡 · H.265（NVIDIA 显卡加速，体积小）", "hevc_nvenc"),
    ("N卡 · AV1（NVIDIA 显卡加速，压缩率最高）", "av1_nvenc"),
]

# 编码档位：只对 CPU 编码器生效（N 卡固定用 p4 平衡档，不用选）
PRESET_CHOICES = [
    ("快速（速度优先，体积略大）", "veryfast"),
    ("平衡（默认）", "medium"),
    ("高压缩（体积优先，更慢）", "slow"),
]

# 音频处理
AUDIO_CHOICES = [
    ("复制（原样保留，无损，最省时间）", "copy"),
    ("重编码 AAC（体积小，通用）", "aac"),
    ("静音（不要声音）", "none"),
]
AUDIO_AAC_BITRATE = "192k"          # 音频重编码的固定码率（不开放给用户调）

# 画质默认值（内部固定）：x264 用 CRF 23，x265 用 CRF 28，N 卡用 -cq
CRF_BY_ENCODER = {'libx264': '23', 'libx265': '28'}
CQ_BY_ENCODER = {'h264_nvenc': '23', 'hevc_nvenc': '28', 'av1_nvenc': '30'}

# 各容器能装的视频编码器（不在表里的容器不做限制）
CONTAINER_VIDEO_COMPAT = {
    '.mp4':  {'libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'av1_nvenc'},
    '.mov':  {'libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'av1_nvenc'},
    '.m4v':  {'libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'av1_nvenc'},
    '.mkv':  {'libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'av1_nvenc',
              'libvpx-vp9', 'libvpx', 'libaom-av1'},
    '.webm': {'libvpx-vp9', 'libvpx', 'libaom-av1', 'av1_nvenc'},
    '.avi':  {'libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'mpeg4'},
    '.ts':   {'libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'mpeg2video'},
    '.flv':  {'libx264', 'h264_nvenc'},
    '.wmv':  {'wmv2', 'mpeg4'},
}

# 编码器与输出容器不兼容时的兜底编码器
CONTAINER_VIDEO_FALLBACK = {
    '.mp4': 'libx264', '.mov': 'libx264', '.m4v': 'libx264', '.mkv': 'libx264',
    '.webm': 'libvpx-vp9', '.avi': 'libx264', '.ts': 'libx264',
    '.flv': 'libx264', '.wmv': 'wmv2',
}

# 各容器兼容的音频编码（可直接流复制，无需重编码）
CONTAINER_AUDIO_COMPAT = {
    '.mp4':  {'aac', 'mp3', 'ac3', 'eac3', 'alac'},
    '.mov':  {'aac', 'mp3', 'ac3', 'eac3', 'alac'},
    '.m4v':  {'aac', 'mp3', 'ac3', 'eac3', 'alac'},
    '.mkv':  {'aac', 'mp3', 'ac3', 'eac3', 'alac', 'flac',
              'opus', 'vorbis', 'pcm_s16le', 'pcm_s24le'},
    '.avi':  {'mp3', 'ac3', 'aac', 'pcm_s16le'},
    '.webm': {'opus', 'vorbis'},
    '.ts':   {'aac', 'mp3', 'ac3', 'eac3'},
    '.flv':  {'aac', 'mp3'},
    '.wmv':  {'wmav2'},
}

# 音频编码不兼容容器时的回退编码器 (encoder, bitrate)
CONTAINER_AUDIO_FALLBACK = {
    '.mp4':  ('aac', AUDIO_AAC_BITRATE),
    '.mov':  ('aac', AUDIO_AAC_BITRATE),
    '.m4v':  ('aac', AUDIO_AAC_BITRATE),
    '.mkv':  ('aac', AUDIO_AAC_BITRATE),
    '.avi':  ('mp3', AUDIO_AAC_BITRATE),
    '.webm': ('libopus', AUDIO_AAC_BITRATE),
    '.ts':   ('aac', AUDIO_AAC_BITRATE),
    '.flv':  ('aac', AUDIO_AAC_BITRATE),
    '.wmv':  ('wmav2', AUDIO_AAC_BITRATE),
}

# 需要 -movflags +faststart 的容器（MP4 系列，便于边下边播）
FASTSTART_CONTAINERS = {'.mp4', '.mov', '.m4v'}


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


def choice_code(choices: list, display_text: str) -> str:
    """把下拉框里显示的文本换回程序内部用的代码。"""
    for label, code in choices:
        if label == display_text:
            return code
    return choices[0][1]


def list_available_encoders(ffmpeg_path: str) -> set:
    """问一次 ffmpeg 支持哪些编码器，用于自动隐藏不可用的选项。"""
    if not ffmpeg_path:
        return set()
    try:
        result = subprocess.run(
            f'"{ffmpeg_path}" -hide_banner -encoders',
            shell=True, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=15)
        names = set()
        for line in (result.stdout or '').splitlines():
            m = re.match(r'^\s*[VASD][\w.]*\s+(\S+)', line)
            if m:
                names.add(m.group(1))
        return names
    except Exception:
        return set()


def resolve_video_encoder(encoder: str, out_ext: str) -> tuple:
    """确定真正使用的编码器，并处理与输出容器不兼容时的兜底。
    返回 (编码器名, 提示文字或 None)。"""
    note = None
    compat = CONTAINER_VIDEO_COMPAT.get(out_ext)
    if compat is not None and encoder not in compat:
        fallback = CONTAINER_VIDEO_FALLBACK.get(out_ext, 'libx264')
        note = f"编码器 {encoder} 与 {out_ext} 容器不兼容，自动改用 {fallback}"
        encoder = fallback
    return encoder, note


def build_video_args(encoder: str, preset: str) -> list:
    """生成视频编码参数（画质值内部固定：x264=CRF23 / x265=CRF28 / N卡=-cq）。"""
    args = ['-c:v', encoder]
    if encoder in CRF_BY_ENCODER:
        args += ['-crf', CRF_BY_ENCODER[encoder], '-preset', preset]
    elif encoder in CQ_BY_ENCODER:
        args += ['-cq', CQ_BY_ENCODER[encoder]]      # N 卡用固定档位（默认 p4），不开放给用户
    return args


def build_audio_args(mode: str, out_ext: str, source_audio_codec: Optional[str]) -> tuple:
    """生成音频参数，返回 (参数列表, 提示文字或 None)。"""
    if mode == 'none':
        return ['-an'], "音频：静音（不要声音）"
    if mode == 'aac':
        return (['-c:a', 'aac', '-b:a', AUDIO_AAC_BITRATE],
                f"音频：重编码为 AAC {AUDIO_AAC_BITRATE}")
    # 复制（默认）
    if not source_audio_codec:
        return ['-an'], "音频：原视频没有音轨，已跳过"
    compat = CONTAINER_AUDIO_COMPAT.get(out_ext, set())
    if source_audio_codec in compat:
        return ['-c:a', 'copy'], f"音频：复制 {source_audio_codec}（无损）"
    fb_encoder, fb_bitrate = CONTAINER_AUDIO_FALLBACK.get(out_ext, ('aac', AUDIO_AAC_BITRATE))
    return (['-c:a', fb_encoder, '-b:a', fb_bitrate],
            f"音频：{source_audio_codec} 与 {out_ext} 不兼容，自动转为 {fb_encoder} {fb_bitrate}")


# ──────────────────────────────────────────────
#  主 GUI 类
# ──────────────────────────────────────────────

class FFmpegResizeGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("FFmpeg 分辨率调整工具")
        self.window.geometry("1000x820")
        self.window.minsize(820, 660)

        # 状态
        self.ffmpeg_path: str = ""
        self.input_file: str = ""
        self.video_width: Optional[int] = None
        self.video_height: Optional[int] = None
        self.audio_codec: Optional[str] = None      # 源视频的音频编码（判断能否直接复制）
        self.available_encoders: set = set()        # ffmpeg 实际支持的编码器（自动检测）
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

        # ----- Row 4: 编码设置 -----
        enc_frame = ttk.LabelFrame(
            self.window, text="编码设置（括号里是通俗说明，不确定就保持默认）", padding=8)
        enc_frame.grid(row=4, column=0, columnspan=4, sticky="ew", padx=10, pady=5)

        # 视频编码器
        ttk.Label(enc_frame, text="视频编码器（用谁来压缩）:")\
            .grid(row=0, column=0, sticky="w", pady=2)
        self.encoder_var = tk.StringVar(value=ENCODER_CHOICES[0][0])
        self.cmb_encoder = ttk.Combobox(enc_frame, textvariable=self.encoder_var,
                                        values=[c[0] for c in ENCODER_CHOICES],
                                        state="readonly", width=42)
        self.cmb_encoder.grid(row=0, column=1, sticky="w", padx=(6, 0), pady=2)
        self.cmb_encoder.bind("<<ComboboxSelected>>", lambda _e: self._on_encoder_change())

        # 编码档位
        ttk.Label(enc_frame, text="编码档位（花多少时间换压缩率）:")\
            .grid(row=1, column=0, sticky="w", pady=2)
        self.preset_var = tk.StringVar(value=PRESET_CHOICES[1][0])
        self.cmb_preset = ttk.Combobox(enc_frame, textvariable=self.preset_var,
                                       values=[c[0] for c in PRESET_CHOICES],
                                       state="readonly", width=42)
        self.cmb_preset.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=2)

        # 音频处理
        ttk.Label(enc_frame, text="音频处理（声音怎么办）:")\
            .grid(row=2, column=0, sticky="w", pady=2)
        self.audio_var = tk.StringVar(value=AUDIO_CHOICES[0][0])
        self.cmb_audio = ttk.Combobox(enc_frame, textvariable=self.audio_var,
                                      values=[c[0] for c in AUDIO_CHOICES],
                                      state="readonly", width=42)
        self.cmb_audio.grid(row=2, column=1, sticky="w", padx=(6, 0), pady=2)

        # ----- Row 5: 按钮区 -----
        btn_frame = ttk.Frame(self.window)
        btn_frame.grid(row=5, column=0, columnspan=4, pady=(8, 4), padx=10)

        self.btn_check = ttk.Button(btn_frame, text="检测完整性", command=self._check_integrity_only)
        self.btn_check.pack(side="left", padx=5)

        self.btn_resize = ttk.Button(btn_frame, text="开始调整", command=self._start_resize)
        self.btn_resize.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(btn_frame, text="停止", command=self._stop_process, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.btn_clear = ttk.Button(btn_frame, text="清空终端", command=self._clear_terminal)
        self.btn_clear.pack(side="left", padx=5)

        # ----- Row 6 / 7: 终端输出 -----
        ttk.Label(self.window, text="终端输出（可选中复制）:").grid(
            row=6, column=0, columnspan=4, sticky="w", **pad)

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
        self.terminal.grid(row=7, column=0, columnspan=4, sticky="nsew", padx=10, pady=(0, 10))
        self.terminal.bind("<Control-c>", lambda e: self.terminal.event_generate("<<Copy>>"))
        self.terminal.bind("<Control-C>", lambda e: self.terminal.event_generate("<<Copy>>"))

        # 行列拉伸权重
        self.window.grid_columnconfigure(1, weight=1)
        self.window.grid_rowconfigure(7, weight=1)

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
            self._append_terminal(f"[{self._ts()}] [OK] 自动检测到 FFmpeg：{path}")
            self._refresh_encoders()
        else:
            self.ffmpeg_var.set("（未找到，请手动选择）")
            self._append_terminal(f"[{self._ts()}] [ERR] 未在系统 PATH 中找到 FFmpeg")
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
        self._append_terminal(f"[{self._ts()}] [FILE] 手动指定 FFmpeg：{path}")
        self._refresh_encoders()

    def _refresh_encoders(self):
        """自动检测 ffmpeg 支持哪些编码器，把用不了的选项从下拉框里去掉。"""
        if not self.ffmpeg_path:
            return
        available = list_available_encoders(self.ffmpeg_path)
        if not available:
            self._append_terminal(f"[{self._ts()}] [WARN] 未取到编码器列表，编码器选项保持完整")
            return
        self.available_encoders = available
        usable = [c for c in ENCODER_CHOICES if c[1] in available]
        if not usable:
            usable = [ENCODER_CHOICES[0]]
        self.cmb_encoder.configure(values=[c[0] for c in usable])
        if choice_code(ENCODER_CHOICES, self.encoder_var.get()) not in [c[1] for c in usable]:
            self.encoder_var.set(usable[0][0])
        hidden = [code for _label, code in ENCODER_CHOICES if code not in available]
        self._append_terminal(f"[{self._ts()}] [ENC] 可用编码器：{' / '.join(c[0] for c in usable)}")
        if hidden:
            self._append_terminal(f"[{self._ts()}]    当前 FFmpeg 不支持、已隐藏：{', '.join(hidden)}")
        self.cmb_preset.configure(state=self._preset_state())

    def _preset_state(self) -> str:
        """档位下拉框状态：N 卡编码器用固定档位，直接禁用该项。"""
        if choice_code(ENCODER_CHOICES, self.encoder_var.get()) in CQ_BY_ENCODER:
            return "disabled"
        return "readonly"

    def _on_encoder_change(self):
        """切换编码器时联动档位下拉框（N 卡不需要选档位）。"""
        self.cmb_preset.configure(state=self._preset_state())

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
        """同步获取视频原始分辨率和音频编码（ffprobe 很快）。"""
        self.audio_codec = None
        if not self.ffmpeg_path or not self.input_file:
            return

        ffprobe_path = find_ffprobe(self.ffmpeg_path)
        if not ffprobe_path:
            self.resolution_var.set("（未找到 ffprobe）")
            self.video_width = None
            self.video_height = None
            return

        # 用 JSON 输出按压名取值，避免 CSV 字段顺序不确定的问题
        cmd = (
            f'"{ffprobe_path}" -v error -select_streams v:0 '
            f'-show_entries stream=width,height -of json '
            f'"{self.input_file}"'
        )
        try:
            # 统一按 UTF-8 解码子进程输出：Windows 默认按 GBK 解，遇到非 GBK 字节会抛异常
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=15)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip())
            streams = json.loads(result.stdout).get("streams", [])
            if not streams:
                raise ValueError("未找到视频流")
            self.video_width = int(streams[0]["width"])
            self.video_height = int(streams[0]["height"])
            self.resolution_var.set(f"{self.video_width} × {self.video_height}")
            self._append_terminal(f"[{self._ts()}] [SIZE] 原分辨率：{self.video_width}×{self.video_height}")
        except Exception as e:
            self.video_width = None
            self.video_height = None
            self.resolution_var.set("（获取失败）")
            self._append_terminal(f"[{self._ts()}] [WARN] 无法获取视频分辨率：{e}")
            return

        # 音频编码（用于判断音频能否直接复制）
        cmd_a = (
            f'"{ffprobe_path}" -v error -select_streams a:0 '
            f'-show_entries stream=codec_name -of json '
            f'"{self.input_file}"'
        )
        try:
            result = subprocess.run(cmd_a, shell=True, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=15)
            if result.returncode == 0:
                streams = json.loads(result.stdout).get("streams", [])
                self.audio_codec = streams[0].get("codec_name") if streams else None
            self._append_terminal(f"[{self._ts()}] [AUDIO] 音频编码：{self.audio_codec or '无音轨'}")
        except Exception:
            self.audio_codec = None

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
        self.btn_check.configure(state=state)
        self.btn_resize.configure(state=state)
        self.btn_ff_browse.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.entry_width.configure(state="disabled" if running else "normal")
        self.entry_height.configure(state="disabled" if running else "normal")
        # 编码设置：执行中一并禁用；档位在选了 N 卡时本来就固定
        self.cmb_encoder.configure(state="disabled" if running else "readonly")
        self.cmb_audio.configure(state="disabled" if running else "readonly")
        self.cmb_preset.configure(state="disabled" if running else self._preset_state())

    # ── 子进程管理 ──────────────────────────

    def _run_command(self, cmd_str: str, on_complete: Optional[Callable] = None):
        """后台执行命令，实时将 stdout/stderr 输出到终端。"""
        self.is_running = True
        self.on_complete_cb = on_complete
        self._set_buttons_state(True)

        self._append_terminal(f"[{self._ts()}] [RUN] {cmd_str}")
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
                        self._append_terminal(f"[{self._ts()}] [OK] 命令执行成功（返回码 0）\n")
                    else:
                        self._append_terminal(f"[{self._ts()}] [ERR] 命令异常退出（返回码 {rc}）\n")
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
            self._append_terminal(f"[{self._ts()}] [STOP] 用户手动停止")

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

    # ── 完整性检测 ──────────────────────────

    def _check_integrity_only(self):
        """仅检测完整性（按钮触发）。"""
        if not self._pre_check():
            return
        cmd = f'"{self.ffmpeg_path}" -v error -xerror -i "{self.input_file}" -f null -'
        self._run_command(cmd)

    def _check_integrity(self, on_pass: Callable):
        """
        检测输入视频完整性；通过后调用 on_pass()，失败则弹窗。
        用作核心操作的前置步骤（视频不完整时提前拦下，避免白跑一遍）。
        """
        if self.is_running:
            return
        cmd = f'"{self.ffmpeg_path}" -v error -xerror -i "{self.input_file}" -f null -'
        self._append_terminal(f"[{self._ts()}] [CHECK] 开始完整性检测…")
        self._run_command(cmd, on_complete=lambda rc: self._on_check_result(rc, on_pass))

    def _on_check_result(self, returncode: int, on_pass: Callable):
        if returncode == 0:
            self._append_terminal(f"[{self._ts()}] [OK] 视频完整性检测通过")
            on_pass()
        else:
            self._append_terminal(f"[{self._ts()}] [ERR] 视频文件不完整或已损坏")
            messagebox.showerror("视频损坏", "该视频文件不完整或已损坏，请选择其他文件。")

    # ── 分辨率调整流程 ──────────────────────

    def _start_resize(self):
        """主入口：校验 → 完整性检测 → 调整分辨率。"""
        if not self._pre_check():
            return

        # 先做完整性检测，通过后进入实际调整
        self._check_integrity(on_pass=self._do_resize)

    def _do_resize(self):
        """实际执行分辨率调整（完整性检测已通过）。"""
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

        # 输出路径
        suffix = f"_resized_{label}"
        output_path = generate_output_path(self.input_file, suffix)
        out_ext = os.path.splitext(output_path)[1].lower()

        # 构建命令：缩放 + 编码设置
        parts = [f'"{self.ffmpeg_path}"', '-i', f'"{self.input_file}"', '-vf', scale_arg]

        encoder, enc_note = resolve_video_encoder(
            choice_code(ENCODER_CHOICES, self.encoder_var.get()), out_ext)
        preset = choice_code(PRESET_CHOICES, self.preset_var.get())
        parts.extend(build_video_args(encoder, preset))
        self._append_terminal(f"[{self._ts()}] [ENC] 视频编码：{encoder}"
                              + (f"（{enc_note}）" if enc_note else f"（档位 {preset}）"))

        audio_args, audio_note = build_audio_args(
            choice_code(AUDIO_CHOICES, self.audio_var.get()), out_ext, self.audio_codec)
        parts.extend(audio_args)
        self._append_terminal(f"[{self._ts()}] {audio_note}")

        if out_ext in FASTSTART_CONTAINERS:
            parts.extend(['-movflags', '+faststart'])
        parts.append(f'"{output_path}"')

        cmd = ' '.join(parts)

        self._append_terminal(f"[{self._ts()}] [SIZE] 开始调整分辨率 → {os.path.basename(output_path)}")
        self._run_command(cmd, on_complete=lambda rc: self._on_resize_done(rc, output_path))

    def _on_resize_done(self, returncode: int, output_path: str):
        if returncode == 0:
            self._append_terminal(f"[{self._ts()}] [DONE] 调整完成！输出：{output_path}")
            messagebox.showinfo("调整完成", f"已保存到：\n{output_path}")
        else:
            self._append_terminal(f"[{self._ts()}] [ERR] 调整失败（返回码 {returncode}），请查看上方终端输出排查")
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
