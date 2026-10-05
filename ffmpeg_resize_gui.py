"""
FFmpeg 分辨率调整工具 — 带图形界面的单文件应用
功能：自动检测 ffmpeg → 选择视频 → 等比例/强制缩放 → 选编码参数 → 执行输出
选项：视频编码器、编码档位、音频处理
模式：单个文件 / 批量处理（整个文件夹，逐文件完整性检测 + 汇总报告）
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import subprocess
import threading
import queue
import os
import sys
import shutil
import json
import re
import time
from datetime import datetime
from typing import Optional, Callable

# ══════════════════════════════════════════════════════════════
#  自定义外观层：把界面控件换成 CustomTkinter（深色圆角），逻辑代码不动。
#  说明：CustomTkinter 启动时会把进程设为「每显示器 DPI 感知」并自行处理缩放，
#        因此不会再出现"打开文件对话框后窗口被系统改缩放而变小"的问题。
# ══════════════════════════════════════════════════════════════
import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class ui:
    """控件适配层：接口与原来 ttk 的用法保持一致，内部换成 CTk 控件。"""

    class Tk(ctk.CTk):
        pass

    class Frame(ctk.CTkFrame):
        def __init__(self, master=None, **kw):
            kw.pop("padding", None)
            super().__init__(master, **kw)

    class LabelFrame(ctk.CTkFrame):
        def __init__(self, master=None, text="", padding=8, **kw):
            kw.pop("padding", None)
            super().__init__(master, **kw, border_width=1, corner_radius=10)
            if text:
                # 标题在父容器里单独占一行（等子控件都加完后再去腾位置）
                self._title_text = text
                self.after_idle(self._place_title)

        def _place_title(self):
            """标题放到卡片正上方单独一行（父容器让出一行），不被裁剪也不压控件。"""
            return   # 分组标题暂缓：运行期插行在 CTk 上不可靠，改到源码布局里做
            if not text:
                return
            try:
                row = int(self.grid_info().get("row", 0))
                parent = self.master
            except Exception:
                return
            title = ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=13, weight="bold"),
                                 text_color=("gray20", "gray88"))
            moves = []
            for sib in parent.winfo_children():
                if sib is title or sib is self or sib.winfo_manager() != "grid":
                    continue
                try:
                    r = int(sib.grid_info().get("row", 0))
                except Exception:
                    continue
                if r >= row:
                    moves.append((r, sib))
            for r, sib in sorted(moves, key=lambda item: item[0], reverse=True):
                sib.grid_configure(row=r + 1)
            self.grid_configure(row=row + 1, pady=(6, 8))
            title.grid(row=row, column=0, columnspan=99, sticky="w", padx=12, pady=(10, 0))
            self._title_label = title

    class Toplevel(ctk.CTkToplevel):
        def __init__(self, master=None, **kw):
            kw.pop("bg", None)
            super().__init__(master, **kw)
            try:
                self.configure(fg_color=("gray95", "gray13"))
            except Exception:
                pass

    class Label(ctk.CTkLabel):
        def __init__(self, master=None, text="", foreground=None, **kw):
            if foreground:
                kw["text_color"] = foreground
            super().__init__(master, text=text, **kw)

        def configure(self, **kw):
            if "foreground" in kw:
                kw["text_color"] = kw.pop("foreground")
            super().configure(**kw)

    class Button(ctk.CTkButton):
        def __init__(self, master=None, text="", command=None, state="normal", **kw):
            kw.pop("padding", None)
            super().__init__(master, text=text, command=command, state=state, **kw)

    class Checkbutton(ctk.CTkCheckBox):
        def __init__(self, master=None, text="", variable=None, command=None, state="normal", **kw):
            super().__init__(master, text=text, variable=variable, command=command, state=state, **kw)

    class Radiobutton(ctk.CTkRadioButton):
        def __init__(self, master=None, text="", variable=None, value=None,
                     command=None, state="normal", **kw):
            super().__init__(master, text=text, variable=variable, value=value,
                             command=command, state=state, **kw)

    class Entry(ctk.CTkEntry):
        def __init__(self, master=None, textvariable=None, width=None, state="normal", **kw):
            kw.pop("padding", None)
            if width:
                kw["width"] = max(70, int(width) * 9)
            # 只读输入框用 disabled 表现（CTk 没有 readonly）
            self._readonly = (state == "readonly")
            super().__init__(master, textvariable=textvariable,
                             state="disabled" if self._readonly else state, **kw)

        def configure(self, **kw):
            if kw.get("state") == "readonly":
                self._readonly = True
                kw["state"] = "disabled"
            elif kw.get("state") == "normal":
                self._readonly = False
            super().configure(**kw)

    class Combobox(ctk.CTkOptionMenu):
        def __init__(self, master=None, textvariable=None, values=None,
                     state="readonly", width=None, **kw):
            kw.pop("padding", None)
            self._values = list(values or [])
            super().__init__(master, values=self._values, variable=textvariable,
                             width=max(140, int(width or 20) * 9), **kw)

        def cget(self, key):
            if key == "values":
                return list(self._values)
            return super().cget(key)

        def configure(self, **kw):
            if "values" in kw:
                self._values = list(kw.pop("values") or [])
                super().configure(values=self._values)
            if kw.get("state") == "readonly":
                kw["state"] = "normal"
            super().configure(**kw)

    class Progressbar(ctk.CTkProgressBar):
        def __init__(self, master=None, orient="horizontal", length=200,
                     mode="determinate", maximum=100, **kw):
            super().__init__(master, width=length, height=14, corner_radius=7)
            self._maximum = float(maximum or 100)
            self.set(0)

        def configure(self, **kw):
            if "maximum" in kw:
                self._maximum = float(kw.pop("maximum") or 100)
            if "value" in kw:
                value = float(kw.pop("value") or 0)
                self.set(max(0.0, min(value / self._maximum, 1.0)))
            if kw:
                super().configure(**kw)

    class ScrolledText(ctk.CTkTextbox):
        def __init__(self, master=None, font=None, bg=None, fg=None,
                     insertbackground=None, height=14, **kw):
            kw.pop("wrap", None)
            super().__init__(master, font=font, wrap="word", height=max(140, int(height) * 18))

        def bind(self, *args, **kwargs):      # 文本控件不支持这些绑定，忽略即可
            return None


StringVar = tk.StringVar
BooleanVar = tk.BooleanVar


class _MessageBox:
    """深色 CTk 弹窗，调用方式与 tkinter.messagebox 一致（showinfo/showwarning/showerror/askyesno）。"""

    @staticmethod
    def _build(title, message, buttons):
        win = ctk.CTkToplevel()
        win.title(title)
        win.geometry('600x360')
        win.attributes('-topmost', True)
        ctk.CTkLabel(win, text=title, font=ctk.CTkFont(size=16, weight='bold'),
                     anchor='w').pack(anchor='w', padx=18, pady=(16, 6))
        box = ctk.CTkTextbox(win, wrap='word', font=ctk.CTkFont(size=13))
        box.pack(fill='both', expand=True, padx=18, pady=(0, 8))
        box.insert('1.0', message)
        box.configure(state='disabled')
        bar = ctk.CTkFrame(win, fg_color='transparent')
        bar.pack(fill='x', padx=18, pady=(0, 16))
        for text, value in buttons:
            ctk.CTkButton(bar, text=text, width=120,
                          command=lambda v=value: _MessageBox._close(win, v)).pack(side='right', padx=6)
        win._result = buttons[-1][1]        # 直接关窗口时的默认返回值
        win.protocol('WM_DELETE_WINDOW', lambda: _MessageBox._close(win, None))
        win.grab_set()
        return win

    @staticmethod
    def _close(win, value):
        win._result = value
        win.destroy()

    @staticmethod
    def showinfo(title, message, **kw):
        _MessageBox._build(title, message, [('确定', True)])

    @staticmethod
    def showwarning(title, message, **kw):
        _MessageBox._build(title, message, [('知道了', True)])

    @staticmethod
    def showerror(title, message, **kw):
        _MessageBox._build(title, message, [('关闭', True)])

    @staticmethod
    def askyesno(title, message, **kw):
        win = _MessageBox._build(title, message, [('否', False), ('是', True)])
        win.wait_window()
        return bool(win._result)


class _SimpleDialog:
    """深色输入框弹窗，替代 simpledialog.askstring（用于批量确认码）。"""

    @staticmethod
    def askstring(title, prompt, parent=None, **kw):
        result = {'value': None}
        win = ctk.CTkToplevel(parent)
        win.title(title)
        win.geometry('620x460')
        win.attributes('-topmost', True)
        ctk.CTkLabel(win, text=title, font=ctk.CTkFont(size=16, weight='bold'),
                     anchor='w').pack(anchor='w', padx=18, pady=(16, 6))
        box = ctk.CTkTextbox(win, wrap='word', font=ctk.CTkFont(size=13))
        box.pack(fill='both', expand=True, padx=18, pady=(0, 8))
        box.insert('1.0', prompt)
        box.configure(state='disabled')
        entry = ctk.CTkEntry(win, width=260, font=ctk.CTkFont(size=14))
        entry.pack(anchor='w', padx=18, pady=(0, 10))
        bar = ctk.CTkFrame(win, fg_color='transparent')
        bar.pack(fill='x', padx=18, pady=(0, 16))

        def ok():
            result['value'] = entry.get()
            win.destroy()

        ctk.CTkButton(bar, text='取消', width=120, fg_color='gray35',
                      command=win.destroy).pack(side='right', padx=6)
        ctk.CTkButton(bar, text='确认', width=120, command=ok).pack(side='right')
        entry.bind('<Return>', lambda _e: ok())
        win.protocol('WM_DELETE_WINDOW', win.destroy)
        entry.focus_set()
        win.grab_set()
        win.wait_window()
        return result['value']


messagebox = _MessageBox
simpledialog = _SimpleDialog




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


# 批量扫描时认哪些扩展名（与单个文件对话框一致）
VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.ts', '.webm', '.flv', '.wmv')

# 工具自己的输出后缀：批量扫描时跳过，避免处理过的结果又被处理一遍
OWN_OUTPUT_MARKS = ('_resized', '_trimmed', '_cropped')

# 批量确认码前缀：需要用户手输 BATCH+文件数量（如 BATCH10）才开跑
BATCH_CONFIRM_PREFIX = 'BATCH'

# 终端最多保留的行数（防止几万行把界面拖死）
TERMINAL_MAX_LINES = 5000


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


def generate_output_path(input_path: str, suffix: str, out_dir: str = "") -> str:
    """生成输出路径：默认与原文件同目录，可指定输出文件夹；重名时自动加序号（不覆盖）。"""
    ext = os.path.splitext(input_path)[1]
    if out_dir:
        base = os.path.join(out_dir, os.path.splitext(os.path.basename(input_path))[0])
    else:
        base = os.path.splitext(input_path)[0]
    out = f"{base}{suffix}{ext}"
    if not os.path.exists(out):
        return out
    counter = 2
    while True:
        out = f"{base}{suffix}_{counter}{ext}"
        if not os.path.exists(out):
            return out
        counter += 1


def natural_key(name: str) -> list:
    """自然排序键：让 shot2.mp4 排在 shot10.mp4 前面。"""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', name)]


def list_media_files(folder: str, exts: tuple, recursive: bool = False,
                     skip_marks: tuple = ()) -> tuple:
    """扫描文件夹。
    返回 (可处理文件列表, 因是本体输出而跳过的文件, 因扩展名不符而跳过的文件)。
    """
    candidates = []
    if recursive:
        for root, _dirs, names in os.walk(folder):
            for n in names:
                candidates.append(os.path.join(root, n))
    else:
        for n in os.listdir(folder):
            p = os.path.join(folder, n)
            if os.path.isfile(p):
                candidates.append(p)
    result, skipped_own, skipped_other = [], [], []
    for p in candidates:
        if os.path.splitext(p)[1].lower() not in exts:
            skipped_other.append(p)
            continue
        stem = os.path.splitext(os.path.basename(p))[0]
        if any(mark in stem for mark in skip_marks):
            skipped_own.append(p)
            continue
        result.append(p)
    result.sort(key=lambda p: natural_key(os.path.basename(p)))
    skipped_own.sort(key=lambda p: natural_key(os.path.basename(p)))
    skipped_other.sort(key=lambda p: natural_key(os.path.basename(p)))
    return result, skipped_own, skipped_other


def human_duration(seconds: float) -> str:
    """把秒数写成人看的时长：9.8 秒 / 3 分 05 秒 / 1 小时 02 分。"""
    if seconds < 0:
        seconds = 0
    total = int(round(seconds))
    if total < 60:
        return f"{seconds:.1f} 秒" if seconds < 10 else f"{total} 秒"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes:02d} 分"


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
        self.window = ui.Tk()
        self.window.title("FFmpeg 分辨率调整工具")
        self.window.geometry("1020x700")
        self.window.minsize(860, 600)

        # 状态
        self.ffmpeg_path: str = ""
        self.input_file: str = ""
        self.video_width: Optional[int] = None
        self.video_height: Optional[int] = None
        self.audio_codec: Optional[str] = None      # 源视频的音频编码（判断能否直接复制）
        self.available_encoders: set = set()        # ffmpeg 实际支持的编码器（自动检测）
        # 批量处理状态
        self.input_dir: str = ""                    # 批量：输入文件夹
        self.output_dir: str = ""                   # 输出文件夹（空 = 原文件所在文件夹）
        self.batch_active: bool = False             # 是否正在批量处理
        self.batch_files: list = []                 # 扫描到的待处理文件
        self.batch_jobs: list = []                  # 待处理文件队列
        self.run_target = None                      # 本次运行的目标尺寸（开跑前锁定）
        self.batch_results: list = []               # 已完成的结果
        self.batch_index: int = 0                   # 正在处理第几个（从 0 开始）
        self.batch_start: float = 0.0               # 批量开始时间
        self.current_job: Optional[dict] = None     # 当前任务
        self.current_output: str = ""               # 当前任务的输出路径
        self.job_fraction: float = 0.0              # 当前文件进度 0~1
        self.job_duration: Optional[float] = None   # 当前文件时长（算进度用）
        self.job_raw_tail: list = []                # 当前文件的 ffmpeg 输出尾部（失败时打印）
        self.stop_batch_requested: bool = False     # 用户点了停止
        # 窗口尺寸保护：个别 Windows 对话框关掉后会把窗口挤小，这里记一份正常尺寸
        self.good_geometry: Optional[str] = None
        self.min_ok_size = (860, 700)
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
        ui.Label(self.window, text="FFmpeg 路径:").grid(row=0, column=0, sticky="w", **pad)
        self.ffmpeg_var = tk.StringVar(value="（未检测）")
        ui.Entry(self.window, textvariable=self.ffmpeg_var, state="readonly", width=60)\
            .grid(row=0, column=1, sticky="ew", **pad)
        ui.Button(self.window, text="自动检测", command=self._auto_detect_ffmpeg)\
            .grid(row=0, column=2, **pad)
        self.btn_ff_browse = ui.Button(self.window, text="手动选择…", command=self._manual_select_ffmpeg)
        self.btn_ff_browse.grid(row=0, column=3, **pad)

        # ----- Row 1: 输入方式 -----
        ui.Label(self.window, text="输入方式:").grid(row=1, column=0, sticky="w", **pad)
        mode_in = ui.Frame(self.window)
        mode_in.grid(row=1, column=1, columnspan=3, sticky="w")
        self.input_mode = tk.StringVar(value="single")
        ui.Radiobutton(mode_in, text="单个文件", variable=self.input_mode, value="single",
                        command=self._on_input_mode_change).pack(side="left", padx=(0, 16))
        ui.Radiobutton(mode_in, text="批量处理（整个文件夹）", variable=self.input_mode,
                        value="batch", command=self._on_input_mode_change).pack(side="left")

        # ----- Row 2: 输入文件 / 输入文件夹 -----
        self.input_label = ui.Label(self.window, text="输入视频:")
        self.input_label.grid(row=2, column=0, sticky="w", **pad)
        self.input_var = tk.StringVar(value="（未选择）")
        ui.Entry(self.window, textvariable=self.input_var, state="readonly", width=60)\
            .grid(row=2, column=1, sticky="ew", **pad)
        self.btn_input_browse = ui.Button(self.window, text="浏览…", command=self._select_input_file)
        self.btn_input_browse.grid(row=2, column=2, columnspan=2, sticky="w", **pad)

        # ----- Row 3: 原分辨率 / 批量状态 -----
        self.info_label = ui.Label(self.window, text="原分辨率:")
        self.info_label.grid(row=3, column=0, sticky="w", **pad)
        self.resolution_var = tk.StringVar(value="（选择视频后自动获取）")
        ui.Label(self.window, textvariable=self.resolution_var, foreground="#007acc")\
            .grid(row=3, column=1, columnspan=3, sticky="w", **pad)

        # ----- Row 4: 输出文件夹 -----
        ui.Label(self.window, text="输出文件夹（留空=原目录）:")\
            .grid(row=4, column=0, sticky="w", **pad)
        self.output_var = tk.StringVar(value="")
        ui.Entry(self.window, textvariable=self.output_var, state="readonly", width=60)\
            .grid(row=4, column=1, sticky="ew", **pad)
        out_btns = ui.Frame(self.window)
        out_btns.grid(row=4, column=2, columnspan=2, sticky="w", **pad)
        self.btn_output_browse = ui.Button(out_btns, text="浏览…", command=self._select_output_dir)
        self.btn_output_browse.pack(side="left")
        ui.Button(out_btns, text="清空", command=self._clear_output_dir).pack(side="left", padx=(6, 0))

        # ----- Row 5: 分辨率设置 ──────────────
        settings_frame = ui.LabelFrame(self.window, text="分辨率设置", padding=10)
        settings_frame.grid(row=5, column=0, columnspan=4, sticky="ew", padx=10, pady=5)

        # 模式选择
        self.resize_mode = tk.StringVar(value="width")
        mode_frame = ui.Frame(settings_frame)
        mode_frame.pack(fill="x", pady=(0, 8))

        ui.Radiobutton(mode_frame, text="等比例缩放：指定宽度", variable=self.resize_mode,
                        value="width", command=self._on_mode_change)\
            .grid(row=0, column=0, sticky="w", padx=(0, 20))
        ui.Radiobutton(mode_frame, text="等比例缩放：指定高度", variable=self.resize_mode,
                        value="height", command=self._on_mode_change)\
            .grid(row=0, column=1, sticky="w", padx=(0, 20))
        ui.Radiobutton(mode_frame, text="强制指定宽度 × 高度", variable=self.resize_mode,
                        value="force", command=self._on_mode_change)\
            .grid(row=0, column=2, sticky="w")

        # 输入面板容器（三个面板，按模式切换显示）
        panel_container = ui.Frame(settings_frame)
        panel_container.pack(fill="x", pady=(4, 0))

        # 面板 A — 等比例：指定宽度
        self.panel_width = ui.Frame(panel_container)
        ui.Label(self.panel_width, text="宽度:").pack(side="left")
        self.width_var = tk.StringVar()
        self.entry_width = ui.Entry(self.panel_width, textvariable=self.width_var, width=10)
        self.entry_width.pack(side="left", padx=(4, 6))
        ui.Label(self.panel_width, text="像素  →  高度按原比例自动计算", foreground="gray")\
            .pack(side="left")

        # 面板 B — 等比例：指定高度
        self.panel_height = ui.Frame(panel_container)
        ui.Label(self.panel_height, text="高度:").pack(side="left")
        self.height_var = tk.StringVar()
        self.entry_height = ui.Entry(self.panel_height, textvariable=self.height_var, width=10)
        self.entry_height.pack(side="left", padx=(4, 6))
        ui.Label(self.panel_height, text="像素  →  宽度按原比例自动计算", foreground="gray")\
            .pack(side="left")

        # 面板 C — 强制指定
        self.panel_force = ui.Frame(panel_container)
        ui.Label(self.panel_force, text="宽度:").pack(side="left")
        self.force_w_var = tk.StringVar()
        ui.Entry(self.panel_force, textvariable=self.force_w_var, width=10)\
            .pack(side="left", padx=(4, 10))
        ui.Label(self.panel_force, text="高度:").pack(side="left")
        self.force_h_var = tk.StringVar()
        ui.Entry(self.panel_force, textvariable=self.force_h_var, width=10)\
            .pack(side="left", padx=(4, 6))
        ui.Label(self.panel_force, text="像素  （不保持原比例，画面可能变形）", foreground="gray")\
            .pack(side="left")

        # 默认显示「指定宽度」面板
        self._on_mode_change()

        # ----- Row 6: 编码设置 -----
        enc_frame = ui.LabelFrame(
            self.window, text="编码设置（括号里是通俗说明，不确定就保持默认）", padding=8)
        enc_frame.grid(row=6, column=0, columnspan=4, sticky="ew", padx=10, pady=5)

        # 视频编码器
        ui.Label(enc_frame, text="视频编码器（用谁来压缩）:")\
            .grid(row=0, column=0, sticky="w", pady=2)
        self.encoder_var = tk.StringVar(value=ENCODER_CHOICES[0][0])
        self.cmb_encoder = ui.Combobox(enc_frame, textvariable=self.encoder_var,
                                        values=[c[0] for c in ENCODER_CHOICES],
                                        state="readonly", width=42)
        self.cmb_encoder.grid(row=0, column=1, sticky="w", padx=(6, 0), pady=2)
        self.cmb_encoder.bind("<<ComboboxSelected>>", lambda _e: self._on_encoder_change())

        # 编码档位
        ui.Label(enc_frame, text="编码档位（花多少时间换压缩率）:")\
            .grid(row=1, column=0, sticky="w", pady=2)
        self.preset_var = tk.StringVar(value=PRESET_CHOICES[1][0])
        self.cmb_preset = ui.Combobox(enc_frame, textvariable=self.preset_var,
                                       values=[c[0] for c in PRESET_CHOICES],
                                       state="readonly", width=42)
        self.cmb_preset.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=2)

        # 音频处理
        ui.Label(enc_frame, text="音频处理（声音怎么办）:")\
            .grid(row=2, column=0, sticky="w", pady=2)
        self.audio_var = tk.StringVar(value=AUDIO_CHOICES[0][0])
        self.cmb_audio = ui.Combobox(enc_frame, textvariable=self.audio_var,
                                      values=[c[0] for c in AUDIO_CHOICES],
                                      state="readonly", width=42)
        self.cmb_audio.grid(row=2, column=1, sticky="w", padx=(6, 0), pady=2)

        # ----- Row 7: 批量设置 -----
        batch_frame = ui.LabelFrame(self.window, text="批量设置（仅批量模式生效）", padding=6)
        batch_frame.grid(row=7, column=0, columnspan=4, sticky="ew", padx=10, pady=5)

        self.recursive_scan = tk.BooleanVar(value=False)
        self.chk_recursive = ui.Checkbutton(
            batch_frame, text="包含子文件夹（把子目录里的视频也一起处理）",
            variable=self.recursive_scan, command=self._on_recursive_change)
        self.chk_recursive.pack(side="left", padx=(0, 20))

        self.show_raw_output = tk.BooleanVar(value=False)
        self.chk_raw = ui.Checkbutton(
            batch_frame, text="显示 FFmpeg 原始输出（排错用，会刷屏）",
            variable=self.show_raw_output)
        self.chk_raw.pack(side="left")

        # ----- Row 8: 按钮区 + 进度条 -----
        btn_frame = ui.Frame(self.window)
        btn_frame.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(8, 4), padx=10)

        self.progress_var = tk.StringVar(value="就绪")
        ui.Label(btn_frame, textvariable=self.progress_var, foreground="#007acc", width=26,
                  anchor="e").pack(side="right")
        self.progress = ui.Progressbar(btn_frame, orient="horizontal", length=240,
                                        mode="determinate", maximum=100)
        self.progress.pack(side="right", padx=(10, 6))

        self.btn_check = ui.Button(btn_frame, text="检测完整性", command=self._check_integrity_only)
        self.btn_check.pack(side="left", padx=5)

        self.btn_resize = ui.Button(btn_frame, text="开始调整", command=self._start_resize)
        self.btn_resize.pack(side="left", padx=5)

        self.btn_stop = ui.Button(btn_frame, text="停止", command=self._stop_process, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.btn_clear = ui.Button(btn_frame, text="清空终端", command=self._clear_terminal)
        self.btn_clear.pack(side="left", padx=5)

        # ----- Row 9 / 10: 终端输出 -----
        ui.Label(self.window, text="终端输出（可选中复制）:").grid(
            row=9, column=0, columnspan=4, sticky="w", **pad)

        self.terminal = ui.ScrolledText(
            self.window,
            font=font_mono,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            state="disabled",
            wrap="word",
            height=14,
        )
        self.terminal.grid(row=10, column=0, columnspan=4, sticky="nsew", padx=10, pady=(0, 10))
        self.terminal.bind("<Control-c>", lambda e: self.terminal.event_generate("<<Copy>>"))
        self.terminal.bind("<Control-C>", lambda e: self.terminal.event_generate("<<Copy>>"))

        # 行列拉伸权重
        self.window.grid_columnconfigure(1, weight=1)
        self.window.grid_rowconfigure(10, weight=1)

        # 初始化输入方式相关的文案与可编辑状态（默认单个文件）
        self._on_input_mode_change()

        # 记录正常窗口尺寸，并监听尺寸变化（用于对话框把窗口挤小后的恢复）
        self.window.bind("<Configure>", self._on_window_configure)

        # DPI/界面缩放监视：打开原生对话框后缩放可能被系统切换，窗口会突然变小，
        # 这里定时检查，发现变化就按比例把窗口恢复回去（字体由 Tk 自己按 DPI 缩放）。
        self.base_dpi: float = 0.0
        self.base_size = None
        self.window.after(800, self._watch_dpi)
        self.in_dialog = False      # 文件对话框是否打开（期间的窗口变化不算用户调整）

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

    def _on_input_mode_change(self):
        """切换「单个文件 / 批量处理」：只改文案与可编辑状态，不动已选的输入。"""
        batch = self.input_mode.get() == "batch"
        self.input_label.configure(text="输入文件夹:" if batch else "输入视频:")
        self.info_label.configure(text="批量状态:" if batch else "原分辨率:")
        self.btn_input_browse.configure(text="浏览文件夹…" if batch else "浏览…")
        self.chk_recursive.configure(state="normal" if batch else "disabled")
        self.chk_raw.configure(state="normal" if batch else "disabled")
        if batch:
            if self.input_dir:
                self._scan_input_dir()
            else:
                self.input_var.set("（未选择文件夹）")
                self.resolution_var.set("（选择文件夹后自动统计文件数量）")
        else:
            self.input_var.set(self.input_file or "（未选择）")
            if self.input_file and self.video_width:
                self.resolution_var.set(f"{self.video_width} × {self.video_height}")
            else:
                self.resolution_var.set("（选择视频后自动获取）")

    def _on_recursive_change(self):
        """勾选/取消「包含子文件夹」时重新统计数量。"""
        if self.input_mode.get() == "batch" and self.input_dir:
            self._scan_input_dir()

    # ── 文件/文件夹对话框（带"正在打开"标记）──

    def _ask_open_file(self, **kwargs):
        """选文件；期间标记 in_dialog，避免系统重排被当成用户调整尺寸。"""
        self.in_dialog = True
        try:
            return filedialog.askopenfilename(**kwargs)
        finally:
            self.window.after_idle(self._clear_dialog_flag)

    def _ask_dir(self, **kwargs):
        """选文件夹；同样带 in_dialog 标记。"""
        self.in_dialog = True
        try:
            return filedialog.askdirectory(**kwargs)
        finally:
            self.window.after_idle(self._clear_dialog_flag)

    def _clear_dialog_flag(self):
        """窗口变化事件会延迟送达，等空闲后再清标记，避免把系统重排记成用户调整。"""
        self.in_dialog = False

    # ── 界面缩放（DPI）监视 ─────────────────

    def _current_dpi(self) -> float:
        """当前界面 DPI（96 = 100%，144 = 150%）。"""
        try:
            return float(self.window.winfo_fpixels('1i'))
        except Exception:
            return 96.0

    def _watch_dpi(self):
        """定时检查界面缩放是否被系统改掉；改了就把窗口按比例还原。"""
        try:
            dpi = self._current_dpi()
            if not self.base_dpi:
                self.base_dpi = dpi
                self.base_size = (self.window.winfo_width(), self.window.winfo_height())
            elif abs(dpi - self.base_dpi) > 1.0:
                ratio = dpi / self.base_dpi
                width, height = self.base_size or (self.window.winfo_width(),
                                                   self.window.winfo_height())
                new_w = max(int(width * ratio), self.min_ok_size[0])
                new_h = max(int(height * ratio), self.min_ok_size[1])
                self.window.geometry(f"{new_w}x{new_h}")
                self.window.after(60, lambda: self.window.geometry(f"{new_w}x{new_h}"))
                self._append_terminal(
                    f"[{self._ts()}] [INFO] 检测到界面缩放变化"
                    f"（{self.base_dpi:.0f} → {dpi:.0f} DPI），窗口已按比例恢复为 {new_w}×{new_h}")
                self.base_dpi = dpi
                self.base_size = (new_w, new_h)
        except Exception:
            pass
        self.window.after(1000, self._watch_dpi)

    # ── 窗口尺寸保护 ────────────────────────

    def _on_window_configure(self, event):
        """记录正常状态下的窗口尺寸（供对话框把窗口挤小后恢复用）。"""
        if event.widget is not self.window:
            return
        try:
            if self.window.state() != "normal":
                return
        except Exception:
            pass
        if self.in_dialog:
            return            # 对话框期间的变化（含系统重排）不算用户调整
        if event.width >= self.min_ok_size[0] and event.height >= self.min_ok_size[1]:
            self.good_geometry = f"{event.width}x{event.height}"

    def _restore_window_size(self):
        """打开过文件对话框之后调用：窗口被挤小就恢复，标题栏跑到屏幕外就拉回来。"""
        try:
            if self.good_geometry:
                want_w, want_h = self.good_geometry.split('x')
                cur_w, cur_h = self.window.winfo_width(), self.window.winfo_height()
                if cur_w < int(want_w) - 20 or cur_h < int(want_h) - 20:
                    self.window.wm_geometry(self.good_geometry)
                    self._append_terminal(
                        f"[{self._ts()}] [INFO] 窗口被缩小（{cur_w}×{cur_h}），"
                        f"已恢复为 {self.good_geometry}")
            if self.window.winfo_y() < 0:
                self.window.geometry(f"+{max(self.window.winfo_x(), 0)}+0")
            if self.window.winfo_x() < 0:
                self.window.geometry(f"+0+{max(self.window.winfo_y(), 0)}")
        except Exception as e:
            try:
                self._append_terminal(f"[{self._ts()}] [WARN] 恢复窗口尺寸失败：{e!r}")
            except Exception:
                pass

    # ── 文件夹选择与扫描（批量）────────────

    def _select_input_dir(self):
        """选择批量处理的文件夹，并立刻统计里面有多少个视频。"""
        folder = self._ask_dir(title="选择要批量处理的文件夹")
        self.window.after(80, self._restore_window_size)
        if not folder:
            return
        self.input_dir = os.path.abspath(folder)
        self.input_var.set(self.input_dir)
        self.input_file = ""
        self._scan_input_dir()
        self.window.after(80, self._restore_window_size)

    def _select_output_dir(self):
        """选择输出文件夹（不选则输出到每个原文件所在的文件夹）。"""
        folder = self._ask_dir(title="选择输出文件夹")
        self.window.after(80, self._restore_window_size)
        if not folder:
            return
        self.output_dir = os.path.abspath(folder)
        self.output_var.set(self.output_dir)
        self.window.after(80, self._restore_window_size)

    def _clear_output_dir(self):
        """清空输出文件夹 → 回到「原文件所在文件夹」。"""
        self.output_dir = ""
        self.output_var.set("")

    def _scan_input_dir(self) -> list:
        """扫描输入文件夹，返回可处理的视频列表（自然排序，跳过本工具的输出文件）。"""
        if not self.input_dir or not os.path.isdir(self.input_dir):
            return []
        found, skipped_own, skipped_other = list_media_files(
            self.input_dir, VIDEO_EXTS, recursive=self.recursive_scan.get(),
            skip_marks=OWN_OUTPUT_MARKS)
        self.batch_files = found
        count = len(self.batch_files)
        scope = "含子文件夹" if self.recursive_scan.get() else "只处理根目录"
        self.resolution_var.set(f"已找到 {count} 个视频文件（{scope}）")
        self._append_terminal(
            f"[{self._ts()}] [DIR] 扫描 {self.input_dir}（{scope}）→ {count} 个视频文件")
        for path in self.batch_files[:20]:
            self._append_terminal(f"[{self._ts()}]       {os.path.basename(path)}")
        if count > 20:
            self._append_terminal(f"[{self._ts()}]       …（还有 {count - 20} 个）")
        # 说明跳过了什么，避免用户以为文件"丢了"
        if skipped_own:
            names = '、'.join(os.path.basename(p) for p in skipped_own[:5])
            more = f"…（共 {len(skipped_own)} 个）" if len(skipped_own) > 5 else ""
            self._append_terminal(
                f"[{self._ts()}] [DIR] 跳过 {len(skipped_own)} 个本工具生成过的输出文件：{names}{more}")
            self._append_terminal(
                f"[{self._ts()}]       原因：文件名里带本工具的输出后缀"
                f"（如 _resized_w1280、_resized_720、_resized_1280x720）")
            self._append_terminal(
                f"[{self._ts()}]       提示：如果想继续用本工具处理这些文件，"
                f"请先把它们重命名，去掉文件名里的本工具后缀（例如 _resized_h1080）。")
        if skipped_other:
            names = '、'.join(os.path.basename(p) for p in skipped_other[:5])
            more = f"…（共 {len(skipped_other)} 个）" if len(skipped_other) > 5 else ""
            self._append_terminal(
                f"[{self._ts()}] [DIR] 跳过 {len(skipped_other)} 个非视频文件：{names}{more}")
        return self.batch_files

    def _ask_batch_confirm(self, count: int) -> bool:
        """二次确认：必须手输 BATCH+文件数量（如 BATCH10）才会开始处理。"""
        code = f"{BATCH_CONFIRM_PREFIX}{count}"
        where = self.output_dir or "每个原文件所在的文件夹"
        msg = (f"将要处理这个文件夹里的全部视频：\n{self.input_dir}\n\n"
               f"共 {count} 个文件\n输出到：{where}\n\n"
               f"说明：\n"
               f"  · 每个文件都会先做完整性检测，损坏的会被跳过并记入报告\n"
               f"  · 输出文件名自动加后缀，重名自动加序号（不会覆盖原文件）\n"
               f"  · 单个文件失败不会中断，会继续处理下一个\n\n"
               f"确认无误请输入：{code}")
        answer = simpledialog.askstring("确认批量处理", msg, parent=self.window)
        if answer is None:
            self._append_terminal(f"[{self._ts()}] [INFO] 已取消批量处理")
            return False
        if answer.strip().upper() != code:
            self._append_terminal(f"[{self._ts()}] [WARN] 确认码不正确（应输入 {code}），已取消")
            messagebox.showwarning("确认码不正确", f"需要输入 {code} 才会开始，本次未执行。")
            return False
        return True

    # ── 进度条 ──────────────────────────────

    def _refresh_progress(self):
        """刷新进度条：单个文件显示百分比；批量显示「第几个 + 当前文件百分比」。"""
        if self.batch_active:
            total = max(len(self.batch_jobs), 1)
            done = self.batch_index
            self.progress.configure(maximum=total, value=done + self.job_fraction)
            self.progress_var.set(
                f"第 {min(done + 1, total)}/{total} 个 · 当前 {int(self.job_fraction * 100)}%")
        else:
            self.progress.configure(maximum=100, value=self.job_fraction * 100)
            self.progress_var.set(f"处理中 {int(self.job_fraction * 100)}%")

    def _update_progress_from_output(self, text: str):
        """从 ffmpeg 输出里解析 time= 更新进度（需要先知道文件时长）。"""
        duration = self.job_duration
        if not duration or duration <= 0:
            return
        last = None
        for last in re.finditer(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', text):
            pass
        if last is None:
            return
        seconds = int(last.group(1)) * 3600 + int(last.group(2)) * 60 + float(last.group(3))
        self.job_fraction = max(0.0, min(seconds / duration, 1.0))
        self._refresh_progress()

    # ── 批量流程 ────────────────────────────

    def _start_batch(self):
        """批量主入口：扫描 → 参数校验 → 确认码 → 逐个处理。"""
        files = self._scan_input_dir()
        if not files:
            messagebox.showwarning(
                "没有可处理的文件",
                "这个文件夹里没有找到可处理的视频文件。\n\n"
                "· 已自动跳过本工具生成的结果文件\n"
                "· 子文件夹里的文件需要先勾选「包含子文件夹」")
            return
        target = self._read_target()          # 分辨率设置先校验一次，批量共用
        if target is None:
            return
        if self.output_dir and not os.path.isdir(self.output_dir):
            messagebox.showwarning("输出文件夹无效", "指定的输出文件夹不存在，请重新选择。")
            return
        if not self._ask_batch_confirm(len(files)):
            return

        self.run_target = target
        self.batch_jobs = [{'input': p, 'index': i + 1, 'status': 'pending'}
                           for i, p in enumerate(files)]
        self.batch_results = []
        self.batch_index = 0
        self.batch_active = True
        self.stop_batch_requested = False
        self.batch_start = time.time()
        self.job_fraction = 0.0
        self.progress.configure(maximum=len(self.batch_jobs), value=0)
        self.progress_var.set(f"第 1/{len(self.batch_jobs)} 个 · 当前 0%")
        self._append_terminal("=" * 62)
        self._append_terminal(f"[{self._ts()}] [BATCH] 开始批量处理：共 {len(self.batch_jobs)} 个文件")
        self._append_terminal(
            f"[{self._ts()}] [BATCH] 输出到：{self.output_dir or '每个原文件所在的文件夹'}")
        self._run_next_job()

    def _run_next_job(self):
        """处理队列里的下一个文件；队列空了就出汇总报告。"""
        if self.batch_index >= len(self.batch_jobs):
            self._finish_batch()
            return
        job = self.batch_jobs[self.batch_index]
        self.current_job = job
        job['start'] = time.time()
        self.input_file = job['input']        # 复用单文件那套逻辑
        self.job_fraction = 0.0
        self.job_raw_tail = []
        self.job_duration = None
        self._append_terminal("─" * 62)
        self._append_terminal(
            f"[{self._ts()}] [{job['index']}/{len(self.batch_jobs)}] {os.path.basename(self.input_file)}")
        self._fetch_video_resolution()        # 顺便拿到分辨率 / 音频编码 / 时长
        if self.video_width is None:
            self._fail_job("读不到视频信息（可能不是视频文件或文件已损坏）")
            return
        self._refresh_progress()
        self._check_integrity(on_pass=self._do_resize, quiet=True)

    def _fail_job(self, reason: str):
        """批量模式：把当前文件记为失败，然后继续处理下一个。"""
        job = self.current_job or {}
        job['status'] = 'fail'
        job['reason'] = reason
        job['elapsed'] = time.time() - job.get('start', time.time())
        self._append_terminal(
            f"[{self._ts()}] [ERR] {os.path.basename(job.get('input', ''))}：{reason}")
        self.batch_results.append(job)
        self.batch_index += 1
        self.job_fraction = 0.0
        self._refresh_progress()
        if self.stop_batch_requested:
            self._cancel_rest()
        else:
            self._run_next_job()

    def _on_job_done(self, returncode: int):
        """单个文件处理结束：记录结果 → 处理下一个。"""
        job = self.current_job or {}
        job['rc'] = returncode
        job['output'] = self.current_output
        job['elapsed'] = time.time() - job.get('start', time.time())
        out_ok, out_reason = self._verify_output(self.current_output)
        if returncode == 0 and out_ok:
            job['status'] = 'ok'
            self._append_terminal(
                f"[{self._ts()}] [OK] {os.path.basename(job.get('input', ''))} → "
                f"{os.path.basename(self.current_output)}（{job['elapsed']:.1f} 秒）")
        else:
            job['status'] = 'fail'
            job['reason'] = (out_reason if (returncode == 0 and not out_ok)
                             else self._job_fail_reason(returncode))
            if self._remove_bad_output(self.current_output):
                job['reason'] += "（已删除失败产生的废文件）"
            self._append_terminal(
                f"[{self._ts()}] [ERR] {os.path.basename(job.get('input', ''))}：{job['reason']}")
            for line in self.job_raw_tail[-8:]:
                self._append_terminal(f"[{self._ts()}]       {line}")
        self.batch_results.append(job)
        self.batch_index += 1
        self.job_fraction = 0.0
        self.progress.configure(value=self.batch_index)
        self._refresh_progress()
        if self.stop_batch_requested:
            self._cancel_rest()
        else:
            self._run_next_job()

    def _job_fail_reason(self, returncode: int) -> str:
        """失败原因：从 ffmpeg 输出尾部挑最有信息量的一行。"""
        keys = ('error', 'invalid', 'not divisible', 'failed', 'unable', 'no such',
                'unknown encoder', 'conversion', 'cannot', 'unrecognized', 'permission')
        for line in reversed(self.job_raw_tail):
            text = line.strip()
            if text and any(k in text.lower() for k in keys):
                return text[:200]
        for line in reversed(self.job_raw_tail):
            text = line.strip()
            if text and not text.startswith('frame=') and 'fps=' not in text:
                return text[:160]
        return f"ffmpeg 返回码 {returncode}"

    def _cancel_rest(self):
        """用户点了停止：把当前与剩余任务标记为已取消，然后出报告。"""
        job = self.current_job
        if job and job.get('status') == 'pending':
            job['status'] = 'cancel'
            job['reason'] = '用户停止'
            self.batch_results.append(job)
        for pending in self.batch_jobs[self.batch_index + 1:]:
            pending['status'] = 'cancel'
            pending['reason'] = '用户停止'
        self.batch_index = len(self.batch_jobs)
        self._finish_batch()

    def _finish_batch(self):
        """批量结束：终端打印统计 + 弹一次汇总报告窗口。"""
        self.batch_active = False
        self.current_job = None
        self._set_buttons_state(False)
        ok = [j for j in self.batch_results if j.get('status') == 'ok']
        fail = [j for j in self.batch_results if j.get('status') == 'fail']
        cancel = [j for j in self.batch_results if j.get('status') == 'cancel']
        cancel += [j for j in self.batch_jobs if j.get('status') == 'pending']
        spent = time.time() - self.batch_start
        self.progress.configure(maximum=max(len(self.batch_jobs), 1), value=len(self.batch_results))
        summary = f"完成：成功 {len(ok)} · 失败 {len(fail)}"
        if cancel:
            summary += f" · 取消 {len(cancel)}"
        self.progress_var.set(summary)

        lines = ["批量处理完成",
                 f"输入文件夹：{self.input_dir}",
                 f"输出位置：{self.output_dir or '每个原文件所在的文件夹'}",
                 f"共 {len(self.batch_jobs)} 个 · 成功 {len(ok)} · 失败 {len(fail)}"
                 + (f" · 取消 {len(cancel)}" if cancel else "")
                 + f" · 用时 {human_duration(spent)}"]
        if fail:
            lines.append("")
            lines.append("失败清单：")
            lines += [f"  - {os.path.basename(j.get('input', ''))}：{j.get('reason', '未知原因')}"
                      for j in fail]
        if cancel:
            lines.append("")
            lines.append("已取消：")
            lines += [f"  - {os.path.basename(j.get('input', ''))}" for j in cancel]
        report = "\n".join(lines)
        self._append_terminal("=" * 62)
        for line in lines:
            self._append_terminal(f"[{self._ts()}] {line}" if line else "")
        self._show_batch_report(report)

    def _show_batch_report(self, report: str):
        """汇总报告窗口（只弹一次）：可打开输出文件夹 / 复制报告。"""
        win = ui.Toplevel(self.window)
        win.title("批量处理完成")
        win.transient(self.window)
        box = ui.ScrolledText(win, width=88, height=26,
                           font=('Consolas', 10) if sys.platform == 'win32' else ('Menlo', 12))
        box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        box.insert("1.0", report)
        box.configure(state="disabled")
        bar = ui.Frame(win)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        ui.Button(bar, text="打开输出文件夹", command=self._open_output_dir).pack(side="left")
        ui.Button(bar, text="复制报告",
                   command=lambda: self._copy_to_clipboard(report)).pack(side="left", padx=6)
        ui.Button(bar, text="关闭", command=win.destroy).pack(side="right")
        win.update_idletasks()
        win.geometry(f"+{self.window.winfo_rootx() + 60}+{self.window.winfo_rooty() + 40}")

    def _open_output_dir(self):
        """打开输出文件夹（没指定就打开输入文件夹）。"""
        target = self.output_dir or self.input_dir
        if not target or not os.path.isdir(target):
            messagebox.showwarning("无法打开", "输出文件夹不存在。")
            return
        try:
            if sys.platform == 'win32':
                os.startfile(target)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', target])
            else:
                subprocess.Popen(['xdg-open', target])
        except Exception as e:
            messagebox.showwarning("无法打开", f"打开文件夹失败：{e}")

    def _copy_to_clipboard(self, text: str):
        """把报告放进剪贴板，方便反馈问题。"""
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self._append_terminal(f"[{self._ts()}] [INFO] 报告已复制到剪贴板")

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
        path = self._ask_open_file(title="选择 ffmpeg 可执行文件", filetypes=filetypes)
        self.window.after(80, self._restore_window_size)
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
        self.window.after(80, self._restore_window_size)

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
        """选择输入视频文件；批量模式下改为选择文件夹。"""
        if self.input_mode.get() == "batch":
            self._select_input_dir()
            return
        filetypes = [("视频文件", "*.mp4 *.mkv *.mov *.avi *.ts *.webm *.flv *.wmv"),
                     ("所有文件", "*.*")]
        path = self._ask_open_file(title="选择视频文件", filetypes=filetypes)
        self.window.after(80, self._restore_window_size)
        if not path:
            return
        self.input_file = os.path.abspath(path)
        self.input_var.set(self.input_file)
        self._fetch_video_resolution()
        self.window.after(80, self._restore_window_size)

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

        # 用 JSON 输出按压名取值，避免 CSV 字段顺序不确定的问题；
        # 顺便取时长（进度条要按 time/时长 算百分比）
        cmd = (
            f'"{ffprobe_path}" -v error -select_streams v:0 '
            f'-show_entries stream=width,height:format=duration -of json '
            f'"{self.input_file}"'
        )
        try:
            # 统一按 UTF-8 解码子进程输出：Windows 默认按 GBK 解，遇到非 GBK 字节会抛异常
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=15)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip())
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if not streams:
                raise ValueError("未找到视频流")
            self.video_width = int(streams[0]["width"])
            self.video_height = int(streams[0]["height"])
            duration = data.get("format", {}).get("duration")
            self.job_duration = float(duration) if duration else None
            self.resolution_var.set(f"{self.video_width} × {self.video_height}")
            self._log(f"[{self._ts()}] [SIZE] 原分辨率：{self.video_width}×{self.video_height}",
                      quiet=True)
        except Exception as e:
            self.video_width = None
            self.video_height = None
            self.resolution_var.set("（获取失败）")
            self._log(f"[{self._ts()}] [WARN] 无法获取视频分辨率：{e}", quiet=True)
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
            self._log(f"[{self._ts()}] [AUDIO] 音频编码：{self.audio_codec or '无音轨'}", quiet=True)
        except Exception:
            self.audio_codec = None

    # ── 终端操作 ────────────────────────────

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _append_terminal(self, text: str):
        self.terminal.configure(state="normal")
        self.terminal.insert("end", text + "\n")
        # 行数上限：超出就丢掉最旧的行（几万行会把文本框拖死）
        try:
            total = int(self.terminal.index('end-1c').split('.')[0])
            if total > TERMINAL_MAX_LINES:
                self.terminal.delete('1.0', f'{total - TERMINAL_MAX_LINES}.0')
        except Exception:
            pass
        self.terminal.see("end")
        self.terminal.configure(state="disabled")

    def _log(self, text: str, quiet: bool = False):
        """写终端。quiet=True 的行在「批量精简模式」下省略（勾了原始输出就照常显示）。"""
        if quiet and self.batch_active and not self.show_raw_output.get():
            return
        self._append_terminal(text)

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
        self.btn_input_browse.configure(state=state)
        self.btn_output_browse.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.entry_width.configure(state="disabled" if running else "normal")
        self.entry_height.configure(state="disabled" if running else "normal")
        self.chk_recursive.configure(
            state="disabled" if running else
            ("normal" if self.input_mode.get() == "batch" else "disabled"))
        self.chk_raw.configure(
            state=state if self.input_mode.get() == "batch" else "disabled")
        # 编码设置：执行中一并禁用；档位在选了 N 卡时本来就固定
        self.cmb_encoder.configure(state="disabled" if running else "readonly")
        self.cmb_audio.configure(state="disabled" if running else "readonly")
        self.cmb_preset.configure(state="disabled" if running else self._preset_state())

    # ── 子进程管理 ──────────────────────────

    def _run_command(self, cmd_str: str, on_complete: Optional[Callable] = None,
                     quiet_logs: bool = False):
        """后台执行命令，实时将 stdout/stderr 输出到终端。"""
        self.is_running = True
        self.on_complete_cb = on_complete
        self._set_buttons_state(True)

        self._log(f"[{self._ts()}] [RUN] {cmd_str}", quiet=quiet_logs)
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
                    rc = self.returncode if self.returncode is not None else -1
                    # 批量精简模式下不打印每条命令的分隔线和返回码（避免刷屏）
                    if (not self.batch_active) or self.show_raw_output.get():
                        self._append_terminal("─" * 60)
                        if rc == 0:
                            self._append_terminal(f"[{self._ts()}] [OK] 命令执行成功（返回码 0）\n")
                        else:
                            self._append_terminal(f"[{self._ts()}] [ERR] 命令异常退出（返回码 {rc}）\n")
                    self.is_running = False
                    self.process = None
                    # 批量处理中按钮保持禁用，等下一个任务开始
                    self._set_buttons_state(self.batch_active)
                    if self.on_complete_cb:
                        cb = self.on_complete_cb
                        self.on_complete_cb = None
                        cb(rc)
                else:
                    # 始终留一份输出尾部：失败时用来告诉用户"到底报的什么错"
                    self.job_raw_tail.append(line)
                    if len(self.job_raw_tail) > 30:
                        del self.job_raw_tail[0]
                    if (not self.batch_active) or self.show_raw_output.get():
                        self._append_terminal(line)
                    self._update_progress_from_output(line)
        except queue.Empty:
            pass
        self.window.after(150, self._poll_queue)

    def _stop_process(self):
        """终止正在运行的子进程；批量模式下等于停止整批。"""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._append_terminal(f"[{self._ts()}] [STOP] 用户手动停止")
            if self.batch_active:
                self.stop_batch_requested = True
                self._append_terminal(f"[{self._ts()}] [INFO] 批量模式：当前文件中止，剩余文件将标记为已取消")

    # ── 前置校验 ──────────────────────────

    def _pre_check(self) -> bool:
        """执行命令前的通用校验（批量模式校验文件夹，单个模式校验文件）。"""
        if not self.ffmpeg_path:
            messagebox.showwarning("未配置 FFmpeg", "请先检测或手动选择 ffmpeg 可执行文件。")
            return False
        if self.input_mode.get() == "batch":
            if not self.input_dir or not os.path.isdir(self.input_dir):
                messagebox.showwarning("未选择文件夹", "请先选择要批量处理的文件夹。")
                return False
        elif not self.input_file or not os.path.isfile(self.input_file):
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

    def _check_integrity(self, on_pass: Callable, quiet: bool = False):
        """
        检测输入视频完整性；通过后调用 on_pass()，失败则弹窗。
        用作核心操作的前置步骤（视频不完整时提前拦下，避免白跑一遍）。
        """
        if self.is_running:
            return
        cmd = f'"{self.ffmpeg_path}" -v error -xerror -i "{self.input_file}" -f null -'
        self._log(f"[{self._ts()}] [CHECK] 开始完整性检测…", quiet=quiet)
        self._run_command(cmd, on_complete=lambda rc: self._on_check_result(rc, on_pass, quiet),
                          quiet_logs=quiet)

    def _on_check_result(self, returncode: int, on_pass: Callable, quiet: bool = False):
        if returncode == 0:
            self._log(f"[{self._ts()}] [OK] 视频完整性检测通过", quiet=quiet)
            on_pass()
        else:
            self._log(f"[{self._ts()}] [ERR] 视频文件不完整或已损坏", quiet=quiet)
            if self.batch_active:          # 批量：跳过这个文件，继续下一个
                self._fail_job("完整性检测失败（文件不完整或已损坏）")
                return
            messagebox.showerror("视频损坏", "该视频文件不完整或已损坏，请选择其他文件。")

    # ── 分辨率调整流程 ──────────────────────

    def _start_resize(self):
        """主入口：单个文件 = 校验+完整性检测；批量 = 扫描+确认码后逐个处理。"""
        if not self._pre_check():
            return

        if self.input_mode.get() == "batch":
            self._start_batch()
            return

        # 单个文件：先校验参数（填错了立刻让用户重填，不浪费时间跑检测）
        target = self._read_target()
        if target is None:
            return
        self.run_target = target
        self.job_fraction = 0.0
        self._refresh_progress()
        # 再做完整性检测，通过后进入实际调整
        self._check_integrity(on_pass=self._do_resize)

    def _read_target(self):
        """读取并校验分辨率设置；返回 (mode, out_w, out_h, label)，非法时返回 None。"""
        mode = self.resize_mode.get()

        # 取值 & 校验（宽高必须是偶数，否则编码器会直接报错）
        if mode == "width":
            ok, out_w, err = self._parse_dimension("宽度", self.width_var.get())
            if not ok:
                self._warn_input(err)
                return None
            return mode, out_w, None, f"w{out_w}"
        if mode == "height":
            ok, out_h, err = self._parse_dimension("高度", self.height_var.get())
            if not ok:
                self._warn_input(err)
                return None
            return mode, None, out_h, f"h{out_h}"
        # force：强制指定宽 × 高
        ok, out_w, err = self._parse_dimension("宽度", self.force_w_var.get())
        if not ok:
            self._warn_input(err)
            return None
        ok, out_h, err = self._parse_dimension("高度", self.force_h_var.get())
        if not ok:
            self._warn_input(err)
            return None
        return mode, out_w, out_h, f"{out_w}x{out_h}"

    def _parse_dimension(self, name: str, raw: str):
        """校验单个宽/高输入，返回 (是否合格, 数值, 错误说明)。"""
        text = (raw or "").strip()
        if not text:
            return False, 0, f"{name}不能为空，请填写数字，例如 1280。"
        if not text.isdigit():
            return False, 0, f"{name}必须是整数，你填的是「{text}」。\n请填写纯数字，例如 1280。"
        value = int(text)
        if value < 2:
            return False, 0, f"{name}至少是 2 像素，你填的是 {value}。"
        if value > 8192:
            return False, 0, f"{name}最多 8192 像素（再大编码器基本都会失败），你填的是 {value}。"
        if value % 2 != 0:
            return False, 0, (f"{name}必须是偶数：视频编码器要求宽和高都能被 2 整除，\n"
                              f"填奇数会导致输出失败（就是刚才那个报错）。\n\n"
                              f"你填的是 {value}，请改成 {value - 1} 或 {value + 1}。")
        return True, value, ""

    def _warn_input(self, err: str):
        """输入不合格：提示用户重填，本次不执行。"""
        self._append_terminal(f"[{self._ts()}] [WARN] 输入检查未通过，未执行：{err.splitlines()[0]}")
        messagebox.showwarning("输入有误，先不执行", err + "\n\n请修改后重新点击「开始调整」。")

    def _verify_output(self, path: str) -> tuple:
        """检查输出文件是否有效：存在、非空、含视频流。返回 (是否有效, 失败原因)。"""
        if not path or not os.path.isfile(path):
            return False, "没有生成输出文件"
        try:
            if os.path.getsize(path) == 0:
                return False, "输出文件是 0 字节（编码没有真正完成）"
        except OSError:
            return False, "读不到输出文件"
        if not self._output_has_video(path):
            return False, "输出文件里没有视频流（结果不完整）"
        return True, ""

    def _output_has_video(self, path: str) -> bool:
        """用 ffprobe 检查文件里到底有没有视频流。"""
        ffprobe_path = find_ffprobe(self.ffmpeg_path) if self.ffmpeg_path else None
        if not ffprobe_path:
            return True                      # 探测不可用时不删，避免误删
        cmd = (f'"{ffprobe_path}" -v error -select_streams v:0 '
               f'-show_entries stream=codec_name -of json "{path}"')
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=20)
            return bool(json.loads(result.stdout or "{}").get("streams"))
        except Exception:
            return True

    def _remove_bad_output(self, path: str) -> bool:
        """删除失败产生的废文件（0 字节 / 没有视频流）。返回是否删除了。"""
        if not path or not os.path.isfile(path):
            return False
        try:
            ok, _reason = self._verify_output(path)
            if not ok:
                os.remove(path)
                return True
        except Exception:
            return False
        return False

    def _build_resize_command(self, target):
        """按当前设置构建缩放命令，返回 (命令字符串, 输出路径)。"""
        mode, out_w, out_h, label = target

        # 构建 scale 参数
        if mode == "width":
            scale_arg = f"scale={out_w}:-2"
        elif mode == "height":
            scale_arg = f"scale=-2:{out_h}"
        else:
            scale_arg = f"scale={out_w}:{out_h}"

        output_path = generate_output_path(self.input_file, f"_resized_{label}", self.output_dir)
        out_ext = os.path.splitext(output_path)[1].lower()

        # 构建命令：缩放 + 编码设置
        parts = [f'"{self.ffmpeg_path}"', '-i', f'"{self.input_file}"', '-vf', scale_arg]

        encoder, enc_note = resolve_video_encoder(
            choice_code(ENCODER_CHOICES, self.encoder_var.get()), out_ext)
        preset = choice_code(PRESET_CHOICES, self.preset_var.get())
        parts.extend(build_video_args(encoder, preset))
        self._log(f"[{self._ts()}] [ENC] 视频编码：{encoder}"
                  + (f"（{enc_note}）" if enc_note else f"（档位 {preset}）"), quiet=True)

        audio_args, audio_note = build_audio_args(
            choice_code(AUDIO_CHOICES, self.audio_var.get()), out_ext, self.audio_codec)
        parts.extend(audio_args)
        self._log(f"[{self._ts()}] {audio_note}", quiet=True)

        if out_ext in FASTSTART_CONTAINERS:
            parts.extend(['-movflags', '+faststart'])
        parts.append(f'"{output_path}"')
        return ' '.join(parts), output_path

    def _do_resize(self):
        """实际执行分辨率调整（单个：检测已通过；批量：当前文件检测已通过）。"""
        if self.video_width is None or self.video_height is None:
            if self.batch_active:
                self._fail_job("读不到分辨率")
                return
            messagebox.showwarning("缺少原分辨率", "无法获取原始分辨率，请重新选择视频文件。")
            return

        # 批量模式下参数在开跑前已校验并锁定，这里直接用
        target = self.run_target or self._read_target()
        if target is None:
            return

        cmd, output_path = self._build_resize_command(target)
        self.current_output = output_path
        self._log(f"[{self._ts()}] [SIZE] 开始调整分辨率 → {os.path.basename(output_path)}",
                  quiet=True)

        if self.batch_active:
            self._run_command(cmd, on_complete=self._on_job_done, quiet_logs=True)
        else:
            self._run_command(cmd, on_complete=lambda rc: self._on_resize_done(rc, output_path))

    def _on_resize_done(self, returncode: int, output_path: str):
        if returncode == 0:
            self.job_fraction = 1.0
            self._refresh_progress()
            self._append_terminal(f"[{self._ts()}] [DONE] 调整完成！输出：{output_path}")
            messagebox.showinfo("调整完成", f"已保存到：\n{output_path}")
            return
        # 失败：把具体原因说清楚，并删掉失败产生的废文件
        reason = self._job_fail_reason(returncode)
        removed = self._remove_bad_output(output_path)
        self._append_terminal(f"[{self._ts()}] [ERR] 调整失败：{reason}")
        if removed:
            self._append_terminal(
                f"[{self._ts()}] [CLN] 已删除失败产生的废文件：{os.path.basename(output_path)}")
        msg = (f"ffmpeg 返回错误码 {returncode}\n\n"
               f"原因（ffmpeg 输出末尾）：\n{reason}")
        if removed:
            msg += f"\n\n已自动删除失败产生的废文件：\n{os.path.basename(output_path)}"
        else:
            msg += "\n\n（没有产生可删除的输出文件）"
        messagebox.showerror("调整失败", msg)

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
