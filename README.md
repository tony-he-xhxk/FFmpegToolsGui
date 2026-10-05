# FFmpegToolsGui（FFmpeg 视频工具箱）

> 一套基于 Python + CustomTkinter 的本地视频 / 图片处理桌面工具集，封装 FFmpeg 四类常用操作，附带统一启动器。

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-green?logo=ffmpeg)](https://ffmpeg.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey?logo=windows)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-yellow)](https://opensource.org/licenses/MIT)

---

## 项目文件

```
FFmpegToolsGui/
├── ffmpeg_crop_gui.py         # 工具①：视频画面裁剪（可视化拖拽 / 裁黑边）
├── ffmpeg_resize_gui.py       # 工具②：分辨率调整（缩放 / 改变宽高）
├── ffmpeg_clip_gui.py         # 工具③：时间裁剪（截取视频片段）
├── ffmpeg_image_resize_gui.py # 工具④：图片分辨率调整（调宽 / 调高 / 自定义）
├── ffmpeg_launcher.bat        # 统一启动器（双击运行，交互菜单）
```

| 工具  | 脚本                     | 功能            | 输出命名                    |
| --- | ---------------------- | ------------- | ----------------------- |
| ①   | `ffmpeg_crop_gui.py`   | 可视拖拽裁剪画面区域    | `原文件名_cropped.扩展名`      |
| ②   | `ffmpeg_resize_gui.py` | 等比例 / 强制缩放分辨率 | `原文件名_resized_{参数}.扩展名` |
| ③   | `ffmpeg_clip_gui.py`   | 按起止时间截取片段     | `原文件名_trimmed.扩展名`      |
| ④   | `ffmpeg_image_resize_gui.py` | 图片分辨率调整（调宽 / 调高 / 自定义） | `原文件名_resized_{参数}.扩展名` |

---

## Windows 兼容性

| 项目 | 兼容性 |
|------|--------|
| 操作系统 | [OK] Windows 10 / 11 全面支持 |
| Python | [OK] Python 3.10+（**须从 [python.org](https://www.python.org/) 下载完整安装版**，含 tkinter —— CustomTkinter 基于它） |
| CustomTkinter | [OK] 界面库，见下方「预装依赖」；`pip install customtkinter` |
| FFmpeg | [OK] 支持 PATH 自动检测 + 手动选择 `ffmpeg.exe` 两种模式 |
| 文件路径 | [OK] 支持含空格 / 中文的路径（各脚本均用 `os.path` + 双引号包裹） |
| 编码 | [OK] `.bat` 以 GBK 编码保存，中文菜单在 `cmd.exe` 正常显示 |

> [注意] **已知限制**：通过 Chocolatey / Scoop 等包管理器安装的 Python **可能不包含 tkinter**（CustomTkinter 依赖它），会导致界面无法启动。请确保 `python -c "import tkinter, customtkinter"` 无报错。

---

## 预装依赖

### 必需（所有工具）

| 依赖 | 安装方式 | 说明 |
|------|----------|------|
| **FFmpeg** | [下载地址](https://ffmpeg.org/download.html) 或 `winget install ffmpeg` | 请将 `bin` 目录加入系统 PATH，或启动后手动选择 `ffmpeg.exe` |
| **customtkinter** | `pip install customtkinter` | 界面库（深色圆角风格）；同时解决 Windows 高 DPI 下"打开文件对话框后窗口突然变小"的问题 |

### 按需（仅特定工具）

| 工具 | 额外依赖 | 安装命令 |
|------|----------|----------|
| ① 视频画面裁剪 | **Pillow** | `pip install Pillow` |
| ② 分辨率调整 | *无* | — |
| ③ 时间裁剪 | *无* | — |
| ④ 图片分辨率调整 | *无* | — |

### 一键安装

```powershell
# 1. 安装界面库 CustomTkinter（所有工具都需要）
pip install customtkinter

# 2. 安装 Pillow（仅视频画面裁剪工具需要：预览首帧）
pip install Pillow

# 3. 安装 FFmpeg（Windows 包管理器方式）
winget install "FFmpeg (Essentials Build)"

# 4. 验证：Python 依赖 + FFmpeg
python -c "import tkinter, customtkinter; from PIL import Image; print('依赖 OK')"
ffmpeg -version
```

---

## 使用方法

### 方式一：统一启动器（推荐 — 双击即可）

1. 双击 `ffmpeg_launcher.bat`
2. 在交互菜单中选择操作：
   ```
   [1] 视频画面裁剪   [2] 调整分辨率   [3] 时间裁剪   [4] 图片分辨率   [0] 退出
   ```
3. 工具执行完毕后按回车返回菜单，可继续选择其他操作

### 方式二：单独运行

```bash
# 视频画面裁剪（含可视化拖拽框）
python ffmpeg_crop_gui.py

# 分辨率调整（支持等比例/强制缩放）
python ffmpeg_resize_gui.py

# 时间裁剪（按起止时间截取片段）
python ffmpeg_clip_gui.py

# 图片分辨率调整（调宽 / 调高 / 自定义宽高，输出格式与输入一致）
python ffmpeg_image_resize_gui.py
```

### 通用操作流程

每个工具的操作流程一致：

```
① 自动检测 FFmpeg（或手动浏览 ffmpeg.exe）
       ↓
② 点击「浏览…」选择输入视频（或图片）文件
       ↓
③ 根据工具类型设置参数（拖拽裁剪框 / 输入分辨率 / 填写起止时间 / 填写图片尺寸）
       ↓
④ （可选）点击「检测完整性」验证视频文件
       ↓
⑤ 点击「确定裁剪」/「开始调整」/「开始剪辑」
       ↓
⑥ 底部终端区实时显示 FFmpeg 进度，完成后弹窗提示
       ↓
⑦ 输出文件保存在输入视频同目录下
```

---

## 界面预览

四个工具共享统一的界面风格：

- **顶部**：FFmpeg 路径显示（自动检测/手动选择）
- **中部**：工具专属参数区 + 操作按钮（检测完整/执行/停止/清空终端）
- **底部**：暗色终端风格日志窗口，实时输出 FFmpeg 执行进度
- **安全**：执行中自动禁用按钮防重复触发，窗口关闭时终止子进程

---

## 技术栈

| 组件 | 用途 |
|------|------|
| Python 3.10+ | 运行环境和 GUI 构建 |
| CustomTkinter（基于 tkinter） | 图形界面控件（深色圆角风格，内置高 DPI 适配） |
| Pillow (PIL) | 裁剪预览：视频首帧加载 → Canvas 渲染 |
| FFmpeg / FFprobe | 视频解码/编码/信息获取 |
| subprocess + threading + queue | 异步进程管理 + 实时终端输出 |

---

## 常见问题

<details>
<summary><b>Q: 启动提示找不到 Python，或 `import customtkinter` 报错？</b></summary>
<br>
两种原因：① Python 缺少 tkinter（CustomTkinter 基于它）——请从 <a href="https://www.python.org/">python.org</a> 重新下载完整安装版（不是嵌入式包或包管理器版本）；② 没装界面库——运行 <code>pip install customtkinter</code>。
</details>

<details>
<summary><b>Q: 裁剪工具提示 "Pillow is required"？</b></summary>
<br>
运行 <code>pip install Pillow</code> 安装即可。其余工具不需要 Pillow。
</details>

<details>
<summary><b>Q: 没安装 FFmpeg 能用吗？</b></summary>
<br>
需要。请从 <a href="https://ffmpeg.org/download.html">ffmpeg.org</a> 下载 Windows 版本，解压后将 <code>bin</code> 目录加入 PATH，或者启动工具后点「手动选择」浏览到 <code>ffmpeg.exe</code>。
</details>

<details>
<summary><b>Q: 图片分辨率调整支持哪些格式？</b></summary>
<br>
支持 PNG / JPG / JPEG / BMP / WebP / TIFF / TGA / GIF / ICO / PPM 等常见格式，<b>输出格式与输入保持一致</b>（沿用原扩展名，如 <code>photo.png → photo_resized_w800.png</code>）。等比例模式只填一边：调宽只填宽度、调高只填高度，另一边由 FFmpeg 按原比例自动计算；自定义模式填宽、高两个值，不保持原比例。
</details>

<details>
<summary><b>Q: 控制台中文乱码？</b></summary>
<br>
启动器 <code>.bat</code> 已用 GBK 编码保存，正常情况下不会乱码。如果仍有问题，请确认系统区域设置为"中国"（设置 → 时间和语言 → 语言和区域 → 管理语言设置 → 更改系统区域设置）。
</details>

<details>
<summary><b>Q: 输出文件在哪里？</b></summary>
<br>
在输入文件的**同一目录下**，文件名自动追加后缀（如 <code>_cropped.mp4</code>、<code>_trimmed.mp4</code>、<code>_resized_1920x1080.png</code>），已存在则自动加序号避免覆盖。
</details>

---

## License

MIT License —— 可自由使用、修改和分发。
