"""
FFmpeg 视频裁剪工具 — 带图形界面的单文件应用
功能：自动检测 ffmpeg → 选择视频 → 检测完整性 → 可视化裁剪 → 选编码参数 → 执行输出
选项：视频编码器（含跟随原视频）、编码档位、音频处理
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
import tempfile
from datetime import datetime
from typing import Optional, Callable

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ──────────────────────────────────────────────
#  可调参数（下拉框显示"人话"，括号内是通俗解释）
# ──────────────────────────────────────────────

# 视频编码器：列表第一项为默认选项；「跟随原视频」用上面的 CODEC_TO_ENCODER 映射
ENCODER_CHOICES = [
    ("CPU · H.264（最通用，兼容性最好）", "libx264"),
    ("CPU · H.265（体积更小，速度较慢）", "libx265"),
    ("N卡 · H.264（NVIDIA 显卡加速，速度快）", "h264_nvenc"),
    ("N卡 · H.265（NVIDIA 显卡加速，体积小）", "hevc_nvenc"),
    ("N卡 · AV1（NVIDIA 显卡加速，压缩率最高）", "av1_nvenc"),
    ("跟随原视频（原片是什么编码就用什么）", "follow"),
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


# ──────────────────────────────────────────────
#  常量
# ──────────────────────────────────────────────

# 视频编码 → FFmpeg 编码器映射
CODEC_TO_ENCODER = {
    'h264': 'libx264',
    'hevc': 'libx265',
    'vp9': 'libvpx-vp9',
    'vp8': 'libvpx',
    'mpeg4': 'mpeg4',
    'mpeg2video': 'mpeg2video',
    'av1': 'libaom-av1',
    'prores': 'prores_ks',
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
    '.mp4':  ('aac', '192k'),
    '.mov':  ('aac', '192k'),
    '.m4v':  ('aac', '192k'),
    '.mkv':  ('aac', '192k'),
    '.avi':  ('mp3', '192k'),
    '.webm': ('libopus', '192k'),
    '.ts':   ('aac', '192k'),
    '.flv':  ('aac', '192k'),
    '.wmv':  ('wmav2', '192k'),
}

# 需要 -movflags +faststart 的容器（MP4 系列）
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
    ff_dir = os.path.dirname(ffmpeg_path)
    name = 'ffprobe.exe' if sys.platform == 'win32' else 'ffprobe'
    candidate = os.path.join(ff_dir, name)
    if os.path.isfile(candidate):
        return candidate
    return None


def generate_output_path(input_path: str, suffix: str = "_cropped") -> str:
    """在同目录下自动生成不重名的输出路径（保留原文件格式）。"""
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


def resolve_video_encoder(encoder: str, out_ext: str,
                          source_codec: Optional[str] = None) -> tuple:
    """确定真正使用的编码器，并处理「跟随原视频」与容器不兼容的兜底。
    返回 (编码器名, 提示文字或 None)。"""
    note = None
    if encoder == 'follow':
        encoder = CODEC_TO_ENCODER.get(source_codec or '', 'libx264')
        note = f"跟随原视频：{source_codec or '未知'} -> {encoder}"
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
#  裁剪预览窗口
# ──────────────────────────────────────────────

class CropPreviewWindow:
    """视频裁剪预览窗口：显示视频首帧，叠加可拖拽裁剪矩形。"""

    HANDLE_SIZE = 10       # 四角手柄边长（像素）
    MIN_CROP_SIZE = 30     # 裁剪矩形最小尺寸（显示像素）
    MAX_DISPLAY_W = 820    # 预览画布最大宽度
    MAX_DISPLAY_H = 520    # 预览画布最大高度

    def __init__(self, parent, frame_path: str, video_w: int, video_h: int,
                 on_confirm: Callable[[int, int, int, int], None],
                 on_cancel: Optional[Callable] = None):
        if not HAS_PIL:
            raise ImportError("Pillow is required")

        self.parent = parent
        self.video_w = video_w
        self.video_h = video_h
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.result = None  # (crop_x, crop_y, crop_w, crop_h) 视频像素坐标

        # ── 创建 Toplevel ──
        self.top = tk.Toplevel(parent)
        self.top.title("视频裁剪预览")
        self.top.transient(parent)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # ── 加载首帧图片 ──
        self.original_img = Image.open(frame_path)

        # 计算显示尺寸（等比缩放至画布范围内，不放大超过原始）
        self.scale = min(self.MAX_DISPLAY_W / video_w, self.MAX_DISPLAY_H / video_h)
        if self.scale > 1.0:
            self.scale = 1.0
        self.display_w = int(video_w * self.scale)
        self.display_h = int(video_h * self.scale)

        display_img = self.original_img.resize(
            (self.display_w, self.display_h), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(display_img)

        # ── 初始裁剪矩形：画面中央 60% ──
        self.crop_w = self.display_w * 0.6
        self.crop_h = self.display_h * 0.6
        self.crop_x = (self.display_w - self.crop_w) / 2
        self.crop_y = (self.display_h - self.crop_h) / 2

        # ── 交互状态 ──
        self.aspect_locked = False
        self.aspect_ratio = self.crop_w / self.crop_h  # w/h
        self.drag_mode: Optional[str] = None  # None | 'MOVE' | 'TL' | 'TR' | 'BL' | 'BR'
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_start_crop = (0, 0, 0, 0)

        # ── 构建 UI ──
        self._build_ui()
        self._redraw()

        # 居中窗口
        self.top.update_idletasks()
        sw = self.top.winfo_screenwidth()
        sh = self.top.winfo_screenheight()
        x = (sw - self.top.winfo_width()) // 2
        y = (sh - self.top.winfo_height()) // 2
        self.top.geometry(f"+{x}+{y}")

    # ── UI 构建 ────────────────────────────

    def _build_ui(self):
        """构建预览窗口界面。"""
        # 信息栏
        info_frame = ttk.Frame(self.top)
        info_frame.pack(fill="x", padx=10, pady=(10, 4))

        ttk.Label(info_frame, text=f"原视频分辨率: {self.video_w} x {self.video_h}",
                  foreground="#007acc").pack(side="left")

        # 提示文字
        ttk.Label(info_frame,
                  text="拖拽矩形或四角手柄调整裁剪区域 · 滚轮缩放",
                  foreground="gray").pack(side="right")

        # 画布
        canvas_frame = ttk.Frame(self.top)
        canvas_frame.pack(padx=10, pady=4)

        self.canvas = tk.Canvas(
            canvas_frame, width=self.display_w, height=self.display_h,
            bg="#1e1e1e", highlightthickness=1, highlightbackground="#555555")
        self.canvas.pack()

        # 绑定事件
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<MouseWheel>", self._on_wheel)          # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self._apply_wheel(120))   # Linux 上滚
        self.canvas.bind("<Button-5>", lambda e: self._apply_wheel(-120))  # Linux 下滚

        # 控制栏
        ctrl_frame = ttk.Frame(self.top)
        ctrl_frame.pack(fill="x", padx=10, pady=10)

        self.lock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl_frame, text="锁定纵横比",
                        variable=self.lock_var,
                        command=self._on_lock_toggle).pack(side="left")

        self.btn_ok = ttk.Button(ctrl_frame, text="确定裁剪", command=self._on_ok)
        self.btn_ok.pack(side="right", padx=(5, 0))

        ttk.Button(ctrl_frame, text="取消", command=self._on_cancel).pack(side="right")

    # ── 画布重绘 ────────────────────────────

    def _redraw(self):
        """重绘画布：视频帧 + 暗色遮罩 + 裁剪框 + 手柄 + 尺寸提示。"""
        c = self.canvas
        c.delete("all")

        x, y, w, h = self.crop_x, self.crop_y, self.crop_w, self.crop_h

        # 1. 视频首帧
        c.create_image(0, 0, anchor="nw", image=self.photo)

        # 2. 暗色半透明遮罩（裁剪区域外）
        overlay_fill = "#000000"
        overlay_stipple = "gray50"
        # 上 / 下 / 左 / 右
        c.create_rectangle(0, 0, self.display_w, y,
                           fill=overlay_fill, stipple=overlay_stipple, outline="")
        c.create_rectangle(0, y + h, self.display_w, self.display_h,
                           fill=overlay_fill, stipple=overlay_stipple, outline="")
        c.create_rectangle(0, y, x, y + h,
                           fill=overlay_fill, stipple=overlay_stipple, outline="")
        c.create_rectangle(x + w, y, self.display_w, y + h,
                           fill=overlay_fill, stipple=overlay_stipple, outline="")

        # 3. 裁剪矩形边框（蓝色）
        c.create_rectangle(x, y, x + w, y + h, outline="#00aaff", width=2)

        # 4. 四角拖拽手柄
        hs = self.HANDLE_SIZE
        for cx, cy in [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]:
            c.create_rectangle(cx - hs / 2, cy - hs / 2, cx + hs / 2, cy + hs / 2,
                               fill="#00aaff", outline="#ffffff", width=1)

        # 5. 尺寸提示（显示视频实际像素尺寸，置于矩形上方）
        video_cw = int(w / self.scale)
        video_ch = int(h / self.scale)
        size_text = f"{video_cw} x {video_ch}"

        text_x = x + w / 2
        text_y = y - 8
        if text_y < 20:
            text_y = y + h + 18  # 空间不够时放矩形下方

        text_id = c.create_text(text_x, text_y, text=size_text,
                                fill="#ffffff", font=("Consolas", 11, "bold"),
                                anchor="s" if text_y < y + h else "n")
        bbox = c.bbox(text_id)
        if bbox:
            c.create_rectangle(bbox[0] - 6, bbox[1] - 3, bbox[2] + 6, bbox[3] + 3,
                               fill="#00aaff", outline="")
            c.tag_raise(text_id)

    # ── 命中检测 ────────────────────────────

    def _hit_test(self, mx: float, my: float) -> Optional[str]:
        """判断鼠标位置命中的目标：四角手柄 / 矩形内部 / 外部。"""
        tol = self.HANDLE_SIZE / 2 + 3
        x, y = self.crop_x, self.crop_y
        w, h = self.crop_w, self.crop_h

        corners = {
            'TL': (x, y),
            'TR': (x + w, y),
            'BL': (x, y + h),
            'BR': (x + w, y + h),
        }
        for name, (cx, cy) in corners.items():
            if abs(mx - cx) <= tol and abs(my - cy) <= tol:
                return name

        if x < mx < x + w and y < my < y + h:
            return 'MOVE'

        return None

    # ── 鼠标事件 ────────────────────────────

    def _on_mouse_down(self, event):
        self.drag_mode = self._hit_test(event.x, event.y)
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.drag_start_crop = (self.crop_x, self.crop_y, self.crop_w, self.crop_h)

    def _on_mouse_move(self, event):
        if not self.drag_mode:
            return

        mx, my = event.x, event.y

        if self.drag_mode == 'MOVE':
            dx = mx - self.drag_start_x
            dy = my - self.drag_start_y
            self.crop_x = self.drag_start_crop[0] + dx
            self.crop_y = self.drag_start_crop[1] + dy
            self._clamp_to_frame()
        elif self.drag_mode in ('TL', 'TR', 'BL', 'BR'):
            if self.aspect_locked:
                self._resize_locked(self.drag_mode, mx, my)
            else:
                self._resize_free(self.drag_mode, mx, my)

        self._redraw()

    def _on_mouse_up(self, _event):
        self.drag_mode = None
        self.canvas.config(cursor="arrow")

    def _on_canvas_motion(self, event):
        """悬停时切换鼠标光标。"""
        if self.drag_mode:
            return
        hit = self._hit_test(event.x, event.y)
        if hit == 'MOVE':
            self.canvas.config(cursor="fleur")
        elif hit in ('TL', 'BR'):
            self.canvas.config(cursor="size_nw_se")
        elif hit in ('TR', 'BL'):
            self.canvas.config(cursor="size_ne_sw")
        else:
            self.canvas.config(cursor="arrow")

    # ── 滚轮缩放 ────────────────────────────

    def _on_wheel(self, event):
        """Windows / macOS 滚轮事件。"""
        self._apply_wheel(event.delta)

    def _apply_wheel(self, delta: int):
        """以裁剪矩形中心为基准缩放。"""
        if delta == 0:
            return
        # Windows: delta 是 120 的倍数；macOS: 可能更小
        if abs(delta) >= 120:
            steps = delta // 120
        else:
            steps = 1 if delta > 0 else -1
        factor = 1.08 ** steps

        cx = self.crop_x + self.crop_w / 2
        cy = self.crop_y + self.crop_h / 2

        new_w = self.crop_w * factor
        new_h = self.crop_h * factor

        # 锁定纵横比时，两个维度使用相同缩放系数（已满足）
        # 自由模式下滚轮也等比缩放（作为"精细缩放"功能）

        if new_w < self.MIN_CROP_SIZE or new_h < self.MIN_CROP_SIZE:
            return

        # 限制不超出画面
        if new_w > self.display_w:
            new_w = self.display_w
        if new_h > self.display_h:
            new_h = self.display_h

        self.crop_x = cx - new_w / 2
        self.crop_y = cy - new_h / 2
        self.crop_w = new_w
        self.crop_h = new_h
        self._clamp_to_frame()
        self._redraw()

    # ── 裁剪矩形操作 ────────────────────────

    def _resize_free(self, handle: str, mx: float, my: float):
        """自由调整裁剪矩形大小（不锁定纵横比）。"""
        min_s = self.MIN_CROP_SIZE

        if handle == 'TL':
            br_x = self.crop_x + self.crop_w
            br_y = self.crop_y + self.crop_h
            nx = max(0, min(mx, br_x - min_s))
            ny = max(0, min(my, br_y - min_s))
            self.crop_x, self.crop_y = nx, ny
            self.crop_w, self.crop_h = br_x - nx, br_y - ny
        elif handle == 'TR':
            bl_x = self.crop_x
            bl_y = self.crop_y + self.crop_h
            nx = min(mx, self.display_w)
            nx = max(nx, bl_x + min_s)
            ny = max(0, min(my, bl_y - min_s))
            self.crop_x = bl_x
            self.crop_y = ny
            self.crop_w = nx - bl_x
            self.crop_h = bl_y - ny
        elif handle == 'BL':
            tr_x = self.crop_x + self.crop_w
            tr_y = self.crop_y
            nx = max(0, min(mx, tr_x - min_s))
            ny = min(my, self.display_h)
            ny = max(ny, tr_y + min_s)
            self.crop_x = nx
            self.crop_y = tr_y
            self.crop_w = tr_x - nx
            self.crop_h = ny - tr_y
        else:  # BR
            tl_x = self.crop_x
            tl_y = self.crop_y
            nx = min(mx, self.display_w)
            nx = max(nx, tl_x + min_s)
            ny = min(my, self.display_h)
            ny = max(ny, tl_y + min_s)
            self.crop_w = nx - tl_x
            self.crop_h = ny - tl_y

    def _resize_locked(self, handle: str, mx: float, my: float):
        """锁定纵横比调整裁剪矩形大小。"""
        ar = self.aspect_ratio  # w/h
        min_s = self.MIN_CROP_SIZE

        # 对角固定点
        if handle == 'TL':
            fx, fy = self.crop_x + self.crop_w, self.crop_y + self.crop_h
        elif handle == 'TR':
            fx, fy = self.crop_x, self.crop_y + self.crop_h
        elif handle == 'BL':
            fx, fy = self.crop_x + self.crop_w, self.crop_y
        else:  # BR
            fx, fy = self.crop_x, self.crop_y

        dx = abs(mx - fx)
        dy = abs(my - fy)

        # 方案 A：以宽度为准
        w_a = max(min_s, dx)
        h_a = w_a / ar
        # 方案 B：以高度为准
        h_b = max(min_s, dy)
        w_b = h_b * ar

        # 取面积较小的方案（更保守，保证不超出画面）
        if w_a * h_a <= w_b * h_b:
            new_w, new_h = w_a, h_a
        else:
            new_w, new_h = w_b, h_b

        # 限制不超出画面
        if new_w > self.display_w:
            new_w = self.display_w
            new_h = new_w / ar
        if new_h > self.display_h:
            new_h = self.display_h
            new_w = new_h * ar

        # 根据固定点定位
        if handle in ('TL', 'BL'):
            self.crop_x = fx - new_w
            self.crop_w = new_w
        else:
            self.crop_x = fx
            self.crop_w = new_w
        if handle in ('TL', 'TR'):
            self.crop_y = fy - new_h
            self.crop_h = new_h
        else:
            self.crop_y = fy
            self.crop_h = new_h

    def _clamp_to_frame(self):
        """确保裁剪矩形完全在画面范围内。"""
        if self.crop_w > self.display_w:
            self.crop_w = self.display_w
        if self.crop_h > self.display_h:
            self.crop_h = self.display_h
        self.crop_x = max(0, min(self.crop_x, self.display_w - self.crop_w))
        self.crop_y = max(0, min(self.crop_y, self.display_h - self.crop_h))

    # ── 按钮回调 ────────────────────────────

    def _on_lock_toggle(self):
        self.aspect_locked = self.lock_var.get()
        if self.aspect_locked:
            self.aspect_ratio = self.crop_w / self.crop_h

    def _on_ok(self):
        """确认裁剪参数，回调主窗口执行 FFmpeg 命令。"""
        # 显示坐标 → 视频像素坐标
        vx = int(self.crop_x / self.scale)
        vy = int(self.crop_y / self.scale)
        vw = int(self.crop_w / self.scale)
        vh = int(self.crop_h / self.scale)

        # 确保偶数（多数编码器要求）
        if vw % 2 != 0:
            vw -= 1
        if vh % 2 != 0:
            vh -= 1

        # 边界保护
        vw = max(2, vw)
        vh = max(2, vh)
        if vx + vw > self.video_w:
            vx = self.video_w - vw
        if vy + vh > self.video_h:
            vy = self.video_h - vh
        vx = max(0, vx)
        vy = max(0, vy)

        self.result = (vx, vy, vw, vh)
        self.top.destroy()
        if self.on_confirm:
            self.on_confirm(vx, vy, vw, vh)

    def _on_cancel(self):
        self.top.destroy()
        if self.on_cancel:
            self.on_cancel()


# ──────────────────────────────────────────────
#  主 GUI 类
# ──────────────────────────────────────────────

class FFmpegCropGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("FFmpeg 视频裁剪工具")
        self.window.geometry("1000x800")
        self.window.minsize(820, 640)

        # 状态
        self.ffmpeg_path: str = ""
        self.input_file: str = ""
        self.video_width: Optional[int] = None
        self.video_height: Optional[int] = None
        self.video_codec: Optional[str] = None
        self.audio_codec: Optional[str] = None
        self.available_encoders: set = set()        # ffmpeg 实际支持的编码器（自动检测）
        self.is_running: bool = False
        self.process: Optional[subprocess.Popen] = None
        self.returncode: Optional[int] = None
        self.on_complete_cb: Optional[Callable] = None
        self.output_queue = queue.Queue()
        self.temp_frame_path: Optional[str] = None

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

        # ----- Row 2: 原视频信息 -----
        ttk.Label(self.window, text="原视频信息:").grid(row=2, column=0, sticky="w", **pad)
        self.info_var = tk.StringVar(value="（选择视频后自动获取）")
        ttk.Label(self.window, textvariable=self.info_var, foreground="#007acc")\
            .grid(row=2, column=1, sticky="w", **pad)

        # ----- Row 3: 编码设置 -----
        enc_frame = ttk.LabelFrame(
            self.window, text="编码设置（括号里是通俗说明，不确定就保持默认）", padding=8)
        enc_frame.grid(row=3, column=0, columnspan=4, sticky="ew", padx=10, pady=5)

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

        # ----- Row 4: 按钮区 -----
        btn_frame = ttk.Frame(self.window)
        btn_frame.grid(row=4, column=0, columnspan=4, pady=(8, 4), padx=10)

        self.btn_check = ttk.Button(btn_frame, text="检测完整性", command=self._check_integrity_only)
        self.btn_check.pack(side="left", padx=5)

        self.btn_crop = ttk.Button(btn_frame, text="开始裁剪", command=self._start_crop)
        self.btn_crop.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(btn_frame, text="停止", command=self._stop_process, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.btn_clear = ttk.Button(btn_frame, text="清空终端", command=self._clear_terminal)
        self.btn_clear.pack(side="left", padx=5)

        # ----- Row 6: 终端输出 -----
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
        filetypes = [("ffmpeg 可执行文件", "ffmpeg.exe" if sys.platform == 'win32' else "ffmpeg"),
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
        usable = [c for c in ENCODER_CHOICES if c[1] in available or c[1] == 'follow']
        if not usable:
            usable = [ENCODER_CHOICES[0]]
        self.cmb_encoder.configure(values=[c[0] for c in usable])
        if choice_code(ENCODER_CHOICES, self.encoder_var.get()) not in [c[1] for c in usable]:
            self.encoder_var.set(usable[0][0])
        hidden = [code for _label, code in ENCODER_CHOICES
                  if code not in available and code != 'follow']
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
        """选择输入视频文件。"""
        filetypes = [("视频文件", "*.mp4 *.mkv *.mov *.avi *.ts *.webm *.flv *.wmv"),
                     ("所有文件", "*.*")]
        path = filedialog.askopenfilename(title="选择视频文件", filetypes=filetypes)
        if not path:
            return
        self.input_file = os.path.abspath(path)
        self.input_var.set(self.input_file)
        self.info_var.set("（正在获取视频信息…）")
        self.window.update_idletasks()
        self._fetch_video_info()

    def _fetch_video_info(self) -> bool:
        """获取视频分辨率、视频编码和音频编码。"""
        ffprobe_path = find_ffprobe(self.ffmpeg_path) if self.ffmpeg_path else None
        if not ffprobe_path:
            self.info_var.set("（未找到 ffprobe）")
            self.video_width = None
            self.video_height = None
            self.video_codec = None
            self.audio_codec = None
            return False

        # 视频流信息（使用 JSON 格式，避免 CSV 字段顺序不确定的问题）
        cmd_v = (
            f'"{ffprobe_path}" -v error -select_streams v:0 '
            f'-show_entries stream=width,height,codec_name -of json '
            f'"{self.input_file}"'
        )
        try:
            result = subprocess.run(cmd_v, shell=True, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace',
                                    timeout=15)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip())
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if not streams:
                raise ValueError("未找到视频流")
            v_stream = streams[0]
            self.video_width = int(v_stream["width"])
            self.video_height = int(v_stream["height"])
            self.video_codec = v_stream.get("codec_name", "unknown")
        except Exception as e:
            self.video_width = None
            self.video_height = None
            self.video_codec = None
            self.audio_codec = None
            self.info_var.set("（获取失败）")
            self._append_terminal(f"[{self._ts()}] [WARN] 无法获取视频信息：{e}")
            return False

        # 音频流信息（可选）
        cmd_a = (
            f'"{ffprobe_path}" -v error -select_streams a:0 '
            f'-show_entries stream=codec_name -of json '
            f'"{self.input_file}"'
        )
        try:
            result = subprocess.run(cmd_a, shell=True, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace',
                                    timeout=15)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = data.get("streams", [])
                if streams:
                    self.audio_codec = streams[0].get("codec_name")
                else:
                    self.audio_codec = None
            else:
                self.audio_codec = None
        except Exception:
            self.audio_codec = None

        info_text = f"{self.video_width} x {self.video_height}  |  视频: {self.video_codec}"
        if self.audio_codec:
            info_text += f"  |  音频: {self.audio_codec}"
        self.info_var.set(info_text)
        self._append_terminal(
            f"[{self._ts()}] [SIZE] 原视频：{self.video_width}x{self.video_height}"
            f"  编码：{self.video_codec}"
            f"{'  音频：' + self.audio_codec if self.audio_codec else ''}")
        return True

    # ── 终端操作 ────────────────────────────

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _append_terminal(self, text: str):
        """向终端输出区域追加一行。"""
        self.terminal.configure(state="normal")
        self.terminal.insert("end", text + "\n")
        self.terminal.see("end")
        self.terminal.configure(state="disabled")

    def _update_last_terminal_line(self, text: str):
        """替换终端最后一行（用于 FFmpeg 进度刷新）。"""
        content = self.terminal.get("1.0", "end-1c")
        if not content.strip():
            self._append_terminal(text)
            return
        self.terminal.configure(state="normal")
        last_line_start = self.terminal.index("end-1c linestart")
        self.terminal.delete(last_line_start, "end")
        self.terminal.insert(last_line_start, text + "\n")
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
        self.btn_crop.configure(state=state)
        self.btn_ff_browse.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        # 编码设置：执行中一并禁用；档位在选了 N 卡时本来就固定
        self.cmb_encoder.configure(state="disabled" if running else "readonly")
        self.cmb_audio.configure(state="disabled" if running else "readonly")
        self.cmb_preset.configure(state="disabled" if running else self._preset_state())

    # ── 子进程管理 ──────────────────────────

    def _run_command(self, cmd_str: str, on_complete: Optional[Callable] = None):
        """
        后台执行命令，实时将 stdout/stderr 输出到终端。
        支持 FFmpeg 的 \\r 进度刷新（替换最后一行而非新增）。
        on_complete(returncode) 在命令结束后在主线程回调。
        """
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
                stream = self.process.stdout
                buf = ""
                while True:
                    ch = stream.read(1)
                    if not ch:
                        break
                    buf += ch
                    if ch == '\r':
                        text = buf[:-1]
                        if text.strip():
                            self.output_queue.put(('update', text.rstrip()))
                        buf = ""
                    elif ch == '\n':
                        text = buf[:-1]
                        if text.strip():
                            self.output_queue.put(('new', text.rstrip()))
                        buf = ""
                if buf.strip():
                    self.output_queue.put(('new', buf.rstrip()))
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
                item = self.output_queue.get_nowait()
                if item is None:
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
                elif isinstance(item, tuple):
                    action, text = item
                    if action == 'update':
                        self._update_last_terminal_line(text)
                    else:
                        self._append_terminal(text)
        except queue.Empty:
            pass
        self.window.after(150, self._poll_queue)

    def _stop_process(self):
        """终止正在运行的子进程。"""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._append_terminal(f"[{self._ts()}] [STOP] 用户手动停止")

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
        用作裁剪流程的前置步骤。
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

    # ── 裁剪流程 ────────────────────────────

    def _start_crop(self):
        """主入口：校验 → 获取视频信息 → 完整性检测 → 打开裁剪预览。"""
        if not self._pre_check():
            return

        if not HAS_PIL:
            messagebox.showerror(
                "缺少依赖",
                "裁剪功能需要 Pillow 库。\n请运行：pip install Pillow")
            return

        # 获取视频信息
        if self.video_width is None or self.video_height is None:
            if not self._fetch_video_info():
                messagebox.showwarning("缺少视频信息",
                                       "无法获取视频分辨率，请重新选择视频文件。")
                return

        # 完整性检测通过后打开预览
        self._check_integrity(on_pass=self._open_crop_preview)

    def _open_crop_preview(self):
        """提取视频首帧并打开裁剪预览窗口。"""
        self.temp_frame_path = os.path.join(
            tempfile.gettempdir(), f"ffmpeg_crop_preview_{os.getpid()}.jpg")

        self._append_terminal(f"[{self._ts()}] [FRAME] 提取视频首帧…")
        self.window.update()  # 强制刷新 UI

        # 加 -v error：否则 ffmpeg 会输出整段横幅，其中包含带中文的输入路径；
        # 同时统一按 UTF-8 解码子进程输出（Windows 默认用 GBK 解会直接抛异常）
        cmd = (f'"{self.ffmpeg_path}" -v error -i "{self.input_file}" '
               f'-vframes 1 -q:v 2 -y "{self.temp_frame_path}"')
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace',
                                    timeout=30)
            if result.returncode != 0 or not os.path.isfile(self.temp_frame_path):
                raise RuntimeError(result.stderr.strip() or "提取首帧失败")
        except Exception as e:
            self._append_terminal(f"[{self._ts()}] [WARN] 提取首帧失败：{e}")
            messagebox.showerror("提取首帧失败", f"无法提取视频首帧：\n{e}")
            self._cleanup_temp_frame()
            return

        self._append_terminal(f"[{self._ts()}] [OK] 首帧提取成功，打开裁剪预览窗口…")

        try:
            CropPreviewWindow(
                parent=self.window,
                frame_path=self.temp_frame_path,
                video_w=self.video_width,
                video_h=self.video_height,
                on_confirm=self._on_crop_confirmed,
                on_cancel=self._cleanup_temp_frame,
            )
        except Exception as e:
            self._append_terminal(f"[{self._ts()}] [WARN] 打开预览窗口失败：{e}")
            messagebox.showerror("预览窗口错误", f"无法打开裁剪预览窗口：\n{e}")
            self._cleanup_temp_frame()

    def _on_crop_confirmed(self, crop_x: int, crop_y: int, crop_w: int, crop_h: int):
        """裁剪参数已确认，构建并执行 FFmpeg 裁剪命令。"""
        self._cleanup_temp_frame()

        # 输出路径
        output_path = generate_output_path(self.input_file, "_cropped")
        out_ext = os.path.splitext(output_path)[1].lower()

        # 构建 FFmpeg 命令：裁剪 + 编码设置
        parts = [
            f'"{self.ffmpeg_path}"',
            '-i', f'"{self.input_file}"',
            '-vf', f'"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"',
        ]

        # 视频编码
        encoder, enc_note = resolve_video_encoder(
            choice_code(ENCODER_CHOICES, self.encoder_var.get()), out_ext, self.video_codec)
        preset = choice_code(PRESET_CHOICES, self.preset_var.get())
        parts.extend(build_video_args(encoder, preset))

        # 音频处理
        audio_args, audio_note = build_audio_args(
            choice_code(AUDIO_CHOICES, self.audio_var.get()), out_ext, self.audio_codec)
        parts.extend(audio_args)

        # faststart 仅对 MP4 系列容器有效
        if out_ext in FASTSTART_CONTAINERS:
            parts.extend(['-movflags', '+faststart'])

        parts.append(f'"{output_path}"')

        cmd = ' '.join(parts)

        center_x = crop_x + crop_w // 2
        center_y = crop_y + crop_h // 2
        self._append_terminal(f"[{self._ts()}] [CROP] 开始裁剪 → {os.path.basename(output_path)}")
        self._append_terminal(f"[{self._ts()}]    裁剪区域: {crop_w}x{crop_h} @ ({crop_x}, {crop_y})")
        self._append_terminal(f"[{self._ts()}]    中心坐标: ({center_x}, {center_y})")
        self._append_terminal(f"[{self._ts()}]    原视频编码: {self.video_codec or '未知'}")
        self._append_terminal(f"[{self._ts()}]    [ENC] 视频编码: {encoder}"
                              + (f"（{enc_note}）" if enc_note else f"（档位 {preset}）"))
        self._append_terminal(f"[{self._ts()}]    {audio_note}")

        self._run_command(cmd, on_complete=lambda rc: self._on_crop_done(rc, output_path))

    def _on_crop_done(self, returncode: int, output_path: str):
        if returncode == 0:
            self._append_terminal(f"[{self._ts()}] [DONE] 裁剪完成！输出：{output_path}")
            messagebox.showinfo("裁剪完成", f"已保存到：\n{output_path}")
        else:
            self._append_terminal(f"[{self._ts()}] [ERR] 裁剪失败（返回码 {returncode}），请查看上方终端输出排查")
            messagebox.showerror("裁剪失败", f"ffmpeg 返回错误码 {returncode}，请检查终端输出中的报错信息。")

    # ── 清理 ────────────────────────────────

    def _cleanup_temp_frame(self):
        """清理临时首帧文件。"""
        if self.temp_frame_path and os.path.isfile(self.temp_frame_path):
            try:
                os.remove(self.temp_frame_path)
            except Exception:
                pass
        self.temp_frame_path = None

    # ── 窗口关闭 ───────────────────────────

    def _on_close(self):
        self._cleanup_temp_frame()
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.window.destroy()

    def run(self):
        self.window.mainloop()


# ──────────────────────────────────────────────
#  入口
# ──────────────────────────────────────────────

if __name__ == "__main__":
    FFmpegCropGUI().run()
