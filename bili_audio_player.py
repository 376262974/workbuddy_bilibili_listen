#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
B站音频播放器 (Windows)
==========================

功能：
  - 在文本框粘贴 B 站视频链接（支持 BV 号、av 号、完整 url）
  - 点击「开始 / 暂停」按钮，只播放音频、不显示视频
  - 支持暂停 / 继续 / 停止 / 进度拖动 / 音量调节
  - 多分P / 合集：自动列出全部条目，一首播完自动播放下一首（可列表循环）

实现思路（无需安装 VLC / 播放器）：
  1. yt-dlp 解析出视频的「纯音频流」直链与所需请求头
  2. ffmpeg 把该直链实时解码为 PCM 裸流（绕开浏览器跨域 / Referer 限制）
  3. PyAudio 直接播放 PCM（Windows 上自带音频后端，无需额外驱动）

依赖：Python 3.8+，以及本目录 .venv 中安装的 yt-dlp / numpy / pyaudio
ffmpeg：优先使用 bin/ 下的 ffmpeg.exe；缺失时可点「工具 → 下载 ffmpeg」。
"""

import os
import sys
import json
import queue
import tempfile
import subprocess
import threading
import time
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, simpledialog
    _HAVE_TK = True
except Exception:
    tk = None
    _HAVE_TK = False
try:
    import numpy as np
    import pyaudio
except ImportError:
    sys.stderr.write(
        "错误：缺少第三方依赖（numpy / pyaudio）。\n"
        "本程序需要用项目自带的虚拟环境运行，而不是直接用 `python`：\n"
        "  方式一：双击 run.bat 启动（自动选 dist 里的 exe，否则用 .venv）\n"
        "  方式二：.\venv\\Scripts\\python.exe bili_audio_player.py\n"
        "（当前 python 指向：%s，未安装这些库）\n" % sys.executable
    )
    sys.exit(1)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
SAMPLE_RATE = 44100          # 解码采样率
CHANNELS = 2                # 声道数
SAMPLE_WIDTH = 2            # 采样宽度（字节），即 int16
BYTE_RATE = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH   # 每秒字节数
CHUNK_FRAMES = 4096         # 每次写入的帧数
CHUNK_BYTES = CHUNK_FRAMES * CHANNELS * SAMPLE_WIDTH

APP_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_MAX = 200


def _write_error_log(msg):
    """把错误信息追加写到程序目录下的 error.log，便于排查闪退。"""
    try:
        import datetime
        path = os.path.join(APP_DIR, "error.log")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write("[{0}]\n{1}\n{2}\n".format(ts, msg, "-" * 60))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 历史 / 收藏持久化
# --------------------------------------------------------------------------- #
def data_dir():
    """可写数据目录。

    PyInstaller 单文件模式下 __file__ 指向临时解包目录（退出即删），
    所以打包后要用 exe 所在目录；若该目录不可写（如 Program Files）则回退 LOCALAPPDATA。
    """
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else APP_DIR
    try:
        os.makedirs(base, exist_ok=True)
        probe = os.path.join(base, ".writetest.tmp")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return base
    except Exception:
        alt = os.path.join(
            os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
            "bili_audio_player",
        )
        try:
            os.makedirs(alt, exist_ok=True)
        except Exception:
            pass
        return alt


def history_path():
    return os.path.join(data_dir(), "history.json")


def load_history():
    """读取历史记录，最新在前；任何异常都退化为空列表。"""
    try:
        with open(history_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("items") if isinstance(data, dict) else data
        items = []
        for it in raw or []:
            if isinstance(it, dict) and it.get("url"):
                items.append({
                    "url": str(it["url"]),
                    "title": str(it.get("title") or ""),
                    "count": int(it.get("count") or 0),
                    "ts": float(it.get("ts") or 0),
                })
        items.sort(key=lambda x: x["ts"], reverse=True)
        return items
    except Exception:
        return []


def save_history(items):
    try:
        with open(history_path(), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "items": list(items)[:HISTORY_MAX]},
                      f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _write_error_log("保存历史失败：%s" % e)
        return False


def add_history(items, url, title="", count=0):
    """记录一条链接：同 url 覆盖并置顶，超出上限丢弃最旧的。返回新列表。"""
    url = (url or "").strip()
    if not url:
        return items
    items = [x for x in items if x.get("url") != url]
    items.insert(0, {
        "url": url,
        "title": title or "",
        "count": int(count or 0),
        "ts": time.time(),
    })
    del items[HISTORY_MAX:]
    return items


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def find_ffmpeg():
    """查找 ffmpeg 可执行文件，返回路径或 None。"""
    # 1. 环境变量
    env = os.environ.get("FFMPEG_PATH")
    if env and os.path.isfile(env):
        return env
    # 2. 本目录 bin/ 下递归查找（含 PyInstaller 打包后解出的目录）
    for root, _dirs, files in os.walk(os.path.join(APP_DIR, "bin")):
        if "ffmpeg.exe" in files:
            return os.path.join(root, "ffmpeg.exe")
    # 3. 系统 PATH
    try:
        from shutil import which
        p = which("ffmpeg")
        if p:
            return p
    except Exception:
        pass
    return None


# 注意：不要再用 subprocess 调 `sys.executable -m yt_dlp`。
# PyInstaller 单文件 exe 下 sys.executable == exe 本身，会把本程序再启动一次
# （表现为「点开始又打开一个窗口 + 未获取到任何条目」）。
# 现在统一使用 yt-dlp 的 Python API（见下方 resolve_* 函数）。


def fmt_time(sec):
    if sec is None or sec < 0:
        return "00:00"
    sec = int(sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def download_ffmpeg(progress_cb=None):
    """下载并解压便携版 ffmpeg 到 bin/，返回最终 exe 路径。"""
    import zipfile
    import urllib.request

    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    dest_dir = os.path.join(APP_DIR, "bin")
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, "ffmpeg.zip")

    if progress_cb:
        progress_cb("正在下载 ffmpeg（约 80MB，请稍候）...")

    def _report(block_num, block_size, total_size):
        if progress_cb and total_size:
            pct = min(100, int(block_num * block_size * 100 / total_size))
            progress_cb(f"下载 ffmpeg 中：{pct}%")

    urllib.request.urlretrieve(url, zip_path, _report)
    if progress_cb:
        progress_cb("正在解压 ffmpeg...")

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(dest_dir)
    os.remove(zip_path)

    exe = find_ffmpeg()
    if not exe:
        raise RuntimeError("解压后未找到 ffmpeg.exe")
    return exe


# --------------------------------------------------------------------------- #
# 音频引擎
# --------------------------------------------------------------------------- #
class AudioEngine:
    """基于 ffmpeg + PyAudio 的纯音频流式播放引擎。"""

    def __init__(self, ffmpeg_path, on_state=None, volume=1.0):
        self.ffmpeg = ffmpeg_path
        self.on_state = on_state          # callback(state: str)
        self._volume = volume
        self._pa = pyaudio.PyAudio()
        self._stream = None
        self._proc = None
        self._reader = None

        self._state = "idle"              # idle | loading | playing | paused | finished | error
        self._start_pos = 0.0             # 当前 ffmpeg 段起始秒数
        self._played_since_start = 0      # 当前段已播放字节数
        self._pause_pos = 0.0             # 暂停时的位置
        self._intentional_stop = False
        self._abort = False
        self._error = None

        self.audio_url = None
        self.headers = {}
        self.duration = 0.0

    # ---- 对外状态 -------------------------------------------------------- #
    def get_state(self):
        return self._state

    def position(self):
        return self._start_pos + self._played_since_start / BYTE_RATE

    def set_volume(self, v):
        self._volume = max(0.0, min(1.0, float(v)))

    # ---- 加载解析结果 ---------------------------------------------------- #
    def load(self, audio_url, headers, duration):
        self.audio_url = audio_url
        self.headers = headers or {}
        self.duration = duration or 0.0
        self._pause_pos = 0.0
        self._start_pos = 0.0
        self._played_since_start = 0

    # ---- 控制 ------------------------------------------------------------ #
    def start(self):
        """从空闲 / 停止态开始播放。"""
        if self._state in ("playing", "loading"):
            return
        self._play_from(0.0)

    def pause(self):
        if self._state != "playing":
            return
        self._pause_pos = self.position()
        self._teardown(keep_position=True)
        self._set_state("paused")

    def resume(self):
        if self._state != "paused":
            return
        self._play_from(self._pause_pos)

    def stop(self):
        self._teardown(keep_position=False)
        self._pause_pos = 0.0
        self._set_state("idle")

    def seek(self, sec):
        if sec is None:
            return
        sec = max(0.0, float(sec))
        if self._state == "playing":
            self._teardown(keep_position=True)
            self._play_from(sec)
        elif self._state == "paused":
            self._pause_pos = sec
        elif self._state in ("idle", "finished"):
            self._pause_pos = sec

    # ---- 内部 ------------------------------------------------------------ #
    def _set_state(self, s):
        self._state = s
        if self.on_state:
            try:
                self.on_state(s)
            except Exception:
                pass

    def _build_cmd(self, start_pos):
        hdr = dict(self.headers)
        # B 站音频流通常需要 Referer，缺失时补上
        if "Referer" not in hdr and "referer" not in {k.lower() for k in hdr}:
            hdr["Referer"] = "https://www.bilibili.com/"
        if "User-Agent" not in hdr:
            hdr["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        header_str = "".join(f"{k}: {v}\r\n" for k, v in hdr.items())

        cmd = [
            self.ffmpeg,
            "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_pos:.3f}",
            "-headers", header_str,
            "-i", self.audio_url,
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ac", str(CHANNELS),
            "-ar", str(SAMPLE_RATE),
            "-",
        ]
        return cmd

    def _play_from(self, start_pos):
        if not self.audio_url:
            self._set_state("error")
            self._error = "未加载音频地址"
            return
        self._intentional_stop = False
        self._abort = False
        self._start_pos = start_pos
        self._played_since_start = 0

        # 打开 PyAudio 输出流
        try:
            self._stream = self._pa.open(
                format=self._pa.get_format_from_width(SAMPLE_WIDTH),
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                frames_per_buffer=CHUNK_FRAMES,
            )
        except Exception as e:
            self._error = f"打开音频设备失败：{e}"
            self._set_state("error")
            return

        # 启动 ffmpeg（Windows 下必须隐藏控制台窗口，否则会弹出黑窗）
        try:
            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=CHUNK_BYTES * 4,
            )
            if os.name == "nt":
                # CREATE_NO_WINDOW：不要为新进程创建控制台窗口
                popen_kwargs["creationflags"] = 0x08000000
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0  # SW_HIDE
                popen_kwargs["startupinfo"] = si
            self._proc = subprocess.Popen(self._build_cmd(start_pos), **popen_kwargs)
        except Exception as e:
            self._error = f"启动 ffmpeg 失败：{e}"
            self._set_state("error")
            return

        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        self._set_state("playing")

    def _read_full_chunk(self, n):
        """从 ffmpeg stdout 稳定读取 n 字节，返回已读到的（可能少于 n，仅表示流结束）。"""
        chunks = []
        remaining = n
        while remaining > 0:
            b = self._proc.stdout.read(remaining)
            if not b:
                break
            chunks.append(b)
            remaining -= len(b)
        return b"".join(chunks)

    def _reader_loop(self):
        try:
            while not self._abort:
                data = self._read_full_chunk(CHUNK_BYTES)
                if not data:
                    break
                vol = self._volume  # 每次循环读取，支持播放中实时调音量
                if vol != 1.0:
                    arr = np.frombuffer(data, dtype=np.int16).astype(np.int32)
                    arr = np.clip((arr * vol), -32768, 32767).astype(np.int16)
                    data = arr.tobytes()
                self._stream.write(data)
                self._played_since_start += len(data)
        except Exception as e:
            self._error = str(e)
        finally:
            # 自然结束 -> finished；人为停止 -> 不切换
            if not self._intentional_stop and not self._abort:
                try:
                    if self._stream:
                        self._stream.stop_stream()
                        self._stream.close()
                except Exception:
                    pass
                rc = self._proc.poll() if self._proc else None
                # ffmpeg 异常退出且几乎没有产出 -> 视为出错（如链接过期 / 需登录）
                if rc not in (0, None) and self._played_since_start < BYTE_RATE:
                    self._error = f"ffmpeg 异常退出（退出码 {rc}，链接可能已过期或需要登录）"
                    self._set_state("error")
                else:
                    self._set_state("finished")

    def _teardown(self, keep_position):
        self._intentional_stop = True
        self._abort = True
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=3)
        self._reader = None
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if not keep_position:
            self._start_pos = 0.0
            self._played_since_start = 0

    def close(self):
        self._teardown(keep_position=False)
        try:
            self._pa.terminate()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# yt-dlp 解析
# --------------------------------------------------------------------------- #
def normalize_url(raw):
    """把用户输入的链接整理成 yt-dlp 可识别的完整 URL。
    支持：完整链接 / 缺 http(s):// 的链接 / 纯 BV 号 / 纯 av 号。
    """
    import re
    u = (raw or "").strip().strip('"').strip("'")
    if not u:
        return ""
    low = u.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return u
    # 纯 BV 号 / av 号
    m = re.fullmatch(r"(?i)(BV[0-9A-Za-z]{10}|av\d+)", u)
    if m:
        return "https://www.bilibili.com/video/" + u
    # 其余情况：补上 https://
    return "https://" + u.lstrip("/")


def _bvid_of(url):
    import re
    m = re.search(r"(BV[0-9A-Za-z]{10})", url or "")
    return m.group(1) if m else None


def _pnum_of(url):
    import re
    m = re.search(r"[?&]p=(\d+)", url or "")
    return int(m.group(1)) if m else None


def fetch_pages_meta(bvid):
    """通过 B 站官方 API 一次性拿到某 BV 下所有分P的「标题/时长」。best-effort。"""
    import urllib.request
    if not bvid:
        return {}
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    req = urllib.request.Request(api, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        "Referer": "https://www.bilibili.com/",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    if d.get("code") != 0:
        return {}
    pages = (d.get("data") or {}).get("pages") or []
    return {p.get("page"): (p.get("part"), p.get("duration")) for p in pages}


def is_login_required_error(text):
    """粗略判断 yt-dlp 的报错是否因「需要登录 / 大会员 / 会员专享」导致。"""
    if not text:
        return False
    t = text.lower()
    keys = ["大会员", "会员专享", "会员", "仅会员", "登录后", "请登录", "需要登录",
            "login", "members only", "member only", "vip", "sign in", "logged in",
            "for members", "请先登录", "该视频为会员"]
    return any(k in t for k in keys)


def _ydl_base_opts(cookie_args=None):
    """把命令行风格的 cookie_args 转成 yt-dlp Python API 的 options。

    重要：这里必须用 yt-dlp 的 Python API，而不是 subprocess 调 `sys.executable -m yt_dlp`。
    因为 PyInstaller 打包成单文件 exe 后，sys.executable 就是 exe 本身，
    再 `-m yt_dlp` 会重新启动本程序（表现为「又打开一个窗口 + 未获取到任何条目」）。
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 60,
        "no_color": True,
    }
    if cookie_args:
        i = 0
        while i < len(cookie_args):
            a = cookie_args[i]
            if a == "--cookies" and i + 1 < len(cookie_args):
                opts["cookiefile"] = cookie_args[i + 1]
                i += 2
                continue
            if a == "--cookies-from-browser" and i + 1 < len(cookie_args):
                opts["cookiesfrombrowser"] = (cookie_args[i + 1], None, None, None)
                i += 2
                continue
            i += 1
    return opts


def resolve_playlist(url, timeout=150, cookie_args=None):
    """解析链接，返回条目列表 [{"url","title","duration"}, ...]。
    单个视频返回 1 个条目；多分P / 合集返回多个（用于顺序播放、自动下一首）。
    使用 yt-dlp Python API（打包成 exe 后同样可靠）。
    """
    import yt_dlp
    opts = _ydl_base_opts(cookie_args)
    opts["extract_flat"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise RuntimeError(str(e) or "yt-dlp 解析失败")

    raw = []
    if isinstance(info, dict):
        ents = info.get("entries")
        raw = list(ents) if ents else [info]

    entries = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        u = d.get("url") or d.get("webpage_url") or d.get("id")
        if not u:
            continue
        u = str(u)
        if not u.startswith("http"):
            u = "https://www.bilibili.com/video/" + u
        entries.append({"url": u, "title": d.get("title"), "duration": d.get("duration")})

    if not entries:
        raise RuntimeError("未获取到任何播放条目")

    # 同一 BV 的多分P：用官方 API 一次性补全标题与时长
    try:
        bvids = {_bvid_of(e["url"]) for e in entries}
        if len(bvids) == 1:
            meta = fetch_pages_meta(bvids.pop())
            if meta:
                for e in entries:
                    pn = _pnum_of(e["url"])
                    if pn and pn in meta:
                        part, dur = meta[pn]
                        if part:
                            e["title"] = part
                        if dur and not e.get("duration"):
                            e["duration"] = dur
    except Exception:
        pass

    for i, e in enumerate(entries):
        if not e.get("title"):
            e["title"] = f"P{i+1}"
    return entries


def list_audio_formats(url, timeout=90, cookie_args=None):
    """返回该视频全部「纯音频」格式的列表（按比特率从高到低）。
    每项 {id, abr, ext, acodec}。用于音质选择下拉框。
    使用 yt-dlp Python API。
    """
    import yt_dlp
    opts = _ydl_base_opts(cookie_args)
    opts["noplaylist"] = True
    opts["format"] = "bestaudio"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return []
    if not isinstance(info, dict):
        return []
    formats = info.get("formats") or []
    seen = {}
    for f in formats:
        if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none"):
            fid = f.get("format_id")
            if fid and fid not in seen:
                seen[fid] = {
                    "id": fid,
                    "abr": float(f.get("abr") or 0),
                    "ext": f.get("ext") or "",
                    "acodec": f.get("acodec") or "",
                }
    lst = list(seen.values())
    lst.sort(key=lambda x: x["abr"], reverse=True)
    return lst


def resolve_audio(url, timeout=90, audio_format=None, cookie_args=None):
    """返回 (audio_url, headers, title, duration)。失败时抛异常。
    audio_format 为 None 时取最高音质（bestaudio），否则用指定 format id。
    使用 yt-dlp Python API。
    """
    import yt_dlp
    opts = _ydl_base_opts(cookie_args)
    opts["noplaylist"] = True
    opts["format"] = audio_format if audio_format else "bestaudio"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise RuntimeError(str(e) or "yt-dlp 解析失败")

    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp 解析失败")

    # 选定格式：bestaudio 解析后通常只有一个音频条目
    rf = info.get("requested_formats") or [info]
    audio_fmt = None
    for f in rf:
        if f.get("vcodec") == "none" or f.get("acodec") not in (None, "none"):
            audio_fmt = f
            break
    if audio_fmt is None:
        audio_fmt = rf[0]
    audio_url = audio_fmt.get("url")
    if not audio_url:
        raise RuntimeError("未找到音频直链")
    headers = audio_fmt.get("http_headers") or info.get("http_headers") or {}
    title = info.get("title", "")
    duration = info.get("duration") or 0.0
    return audio_url, headers, title, duration


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("B站音频播放器")
        self.root.geometry("660x640")
        self.root.minsize(560, 480)

        self.ffmpeg = find_ffmpeg()
        self.engine = None
        # yt-dlp 通过 Python API 调用（见 resolve_* 函数）
        self._resolving = False
        self._seek_dragging = False
        self._title = ""

        # 播放列表（分P / 合集）
        self.playlist = []        # [{"url","title","duration"}]
        self.cur_idx = None       # 当前播放序号
        self._loading = False     # 正在解析某一首
        self._loaded_url = None   # 已解析播放列表对应的链接（用于「开始」时判断是否需重新解析）
        self._cache = {}          # idx -> (audio_url, headers, title, duration)
        self._prefetching = set()
        self._fail_streak = 0
        self._suppress_list = False
        # 音质选择：None 表示最高（bestaudio），否则为具体 format id
        self.audio_format = None
        self.audio_qualities = []     # [{"id","abr","ext","acodec"}]
        self._quality_labels = []
        # 历史 / 收藏
        self.history = load_history()
        self.history_win = None
        self.history_dlg = None
        self.auto_hist_var = tk.BooleanVar(value=True)
        # 线程安全的 UI 消息队列：工作线程只往这里投递，主线程统一执行
        self.ui_queue = queue.Queue()

        # 登录态（用于大会员/受限内容）：None | ("browser", name) | ("file", path)
        self.cookie_mode = None
        self.login_var = tk.BooleanVar(value=False)
        self.cookie_status_var = tk.StringVar(value="未启用登录（公开视频无需登录）")

        self._build_ui()
        self._refresh_ffmpeg_status()
        self._restore_last_link()
        self._poll()

    # ---- 线程安全的 UI 调度 ---------------------------------------------- #
    def post(self, fn, *args):
        """从任意线程投递一个要在主线程执行的函数。"""
        self.ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        try:
            while True:
                fn, args = self.ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    pass
        except queue.Empty:
            pass

    # ---- UI 构建 --------------------------------------------------------- #
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self.root)
        frm.pack(fill="x", **pad)

        ttk.Label(frm, text="B站链接：").pack(side="left")
        self.url_var = tk.StringVar()
        self.entry = ttk.Entry(frm, textvariable=self.url_var, width=48)
        self.entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.entry.bind("<Return>", lambda e: self.on_toggle())
        ttk.Button(frm, text="⭐ 收藏", width=8, command=self.on_favorite).pack(side="left", padx=(6, 0))
        ttk.Button(frm, text="📂 历史", width=8, command=self.on_open_history).pack(side="left", padx=(6, 0))

        ctrl = ttk.Frame(self.root)
        ctrl.pack(fill="x", **pad)
        self.btn_toggle = ttk.Button(ctrl, text="▶ 开始", width=12, command=self.on_toggle)
        self.btn_toggle.pack(side="left")
        self.btn_load = ttk.Button(ctrl, text="📥 加载", width=10, command=self.on_load)
        self.btn_load.pack(side="left", padx=(6, 0))
        self.btn_stop = ttk.Button(ctrl, text="⏹ 停止", width=10, command=self.on_stop)
        self.btn_stop.pack(side="left", padx=(6, 0))
        self.btn_prev = ttk.Button(ctrl, text="⏮ 上一首", width=10, command=self.on_prev)
        self.btn_prev.pack(side="left", padx=(6, 0))
        self.btn_next = ttk.Button(ctrl, text="⏭ 下一首", width=10, command=self.on_next)
        self.btn_next.pack(side="left", padx=(6, 0))
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl, text="列表循环", variable=self.loop_var).pack(side="left", padx=(10, 0))

        # 历史记录开关
        hist = ttk.Frame(self.root)
        hist.pack(fill="x", padx=10, pady=(0, 2))
        ttk.Checkbutton(hist, text="自动记录播放历史", variable=self.auto_hist_var).pack(side="left")
        self.hist_hint = tk.StringVar(value="")
        ttk.Label(hist, textvariable=self.hist_hint, foreground="#555555").pack(side="left", padx=(10, 0))

        # 进度条
        prog = ttk.Frame(self.root)
        prog.pack(fill="x", **pad)
        self.cur_var = tk.StringVar(value="00:00")
        self.dur_var = tk.StringVar(value="00:00")
        ttk.Label(prog, textvariable=self.cur_var, width=6).pack(side="left")
        self.scale = ttk.Scale(
            prog, from_=0, to=100, orient="horizontal",
            command=self.on_scale,
        )
        self.scale.set(0)
        self.scale.bind("<ButtonPress-1>", self._seek_press)
        self.scale.bind("<ButtonRelease-1>", self._seek_release)
        self.scale.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(prog, textvariable=self.dur_var, width=6).pack(side="left")

        # 音量
        vol = ttk.Frame(self.root)
        vol.pack(fill="x", **pad)
        ttk.Label(vol, text="音量：").pack(side="left")
        self.vol_var = tk.DoubleVar(value=1.0)
        self.vol_pct = ttk.Label(vol, text="100%")
        self.vol_pct.pack(side="left")
        self.vol = ttk.Scale(
            vol, from_=0, to=1.0, orient="horizontal",
            variable=self.vol_var, command=self.on_vol, length=200,
        )
        self.vol.set(1.0)
        self.vol.pack(side="left", padx=6)

        # 音质选择
        qua = ttk.Frame(self.root)
        qua.pack(fill="x", **pad)
        ttk.Label(qua, text="音质：").pack(side="left")
        self.quality_var = tk.StringVar(value="最高（自动）")
        self.quality_combo = ttk.Combobox(
            qua, textvariable=self.quality_var, state="readonly", width=20
        )
        self.quality_combo.bind("<<ComboboxSelected>>", self.on_quality)
        self.quality_combo.pack(side="left", padx=6)
        ttk.Label(qua, text="（仅音频比特率，与视频分辨率无关）").pack(side="left")

        # 登录（受限 / 大会员内容）
        login_frm = ttk.Frame(self.root)
        login_frm.pack(fill="x", **pad)
        self.login_chk = ttk.Checkbutton(
            login_frm, text="启用 B站登录（大会员/受限）", variable=self.login_var,
            command=self._on_login_toggle,
        )
        self.login_chk.pack(side="left")
        ttk.Button(login_frm, text="从浏览器导入", command=self._import_browser_cookies).pack(side="left", padx=(6, 0))
        ttk.Button(login_frm, text="选择 cookies.txt", command=self._import_cookie_file).pack(side="left", padx=(6, 0))
        self.cookie_status = ttk.Label(login_frm, textvariable=self.cookie_status_var, foreground="#555555")
        self.cookie_status.pack(side="left", padx=(10, 0))

        # 播放列表
        self.list_header = tk.StringVar(value="播放列表（0）")
        ttk.Label(self.root, textvariable=self.list_header, anchor="w").pack(fill="x", padx=10, pady=(6, 0))
        lbox = ttk.Frame(self.root)
        lbox.pack(fill="both", expand=True, padx=10, pady=(2, 6))
        self.listbox = tk.Listbox(lbox, activestyle="dotbox", exportselection=False,
                                  font=("Microsoft YaHei UI", 9))
        sb = ttk.Scrollbar(lbox, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        # 状态
        self.status_var = tk.StringVar(value="就绪")
        self.status = ttk.Label(
            self.root, textvariable=self.status_var,
            foreground="#555555", anchor="w",
        )
        self.status.pack(fill="x", **pad)

        # 菜单
        menubar = tk.Menu(self.root)
        toolmenu = tk.Menu(menubar, tearoff=0)
        toolmenu.add_command(label="下载 ffmpeg（如缺失）", command=self.on_download_ffmpeg)
        toolmenu.add_command(label="打开 ffmpeg 官网", command=lambda: self._open("https://www.gyan.dev/ffmpeg/builds/"))
        toolmenu.add_separator()
        toolmenu.add_command(label="历史 / 收藏链接  (Ctrl+H)", command=self.on_open_history)
        menubar.add_cascade(label="工具", menu=toolmenu)
        self.root.config(menu=menubar)
        self.root.bind_all("<Control-h>", lambda e: self.on_open_history())
        self.root.bind_all("<Control-H>", lambda e: self.on_open_history())

    def _refresh_ffmpeg_status(self):
        if self.ffmpeg:
            self.status_var.set(f"就绪（ffmpeg: {os.path.basename(self.ffmpeg)}）")
        else:
            self.status_var.set("⚠ 未找到 ffmpeg，请点「工具 → 下载 ffmpeg」")

    # ---- 历史 / 收藏 ---------------------------------------------------- #
    def _restore_last_link(self):
        """启动时把最近一条记录填入输入框（不自动播放）。"""
        n = len(self.history)
        self.hist_hint.set(f"已保存 {n} 条记录" if n else "暂无记录")
        if self.history:
            self.url_var.set(self.history[0]["url"])
            self.status_var.set(f"已载入上次链接（共 {n} 条记录，点『📂 历史』可切换）")

    def _record_history(self, url, title="", count=0):
        try:
            url = normalize_url(url)
            if not url:
                return
            self.history = add_history(self.history, url, title, count)
            save_history(self.history)
            self.hist_hint.set(f"已保存 {len(self.history)} 条记录")
        except Exception:
            pass

    def set_history(self, items):
        """供历史窗口调用：替换全量记录并落盘。"""
        self.history = list(items)
        save_history(self.history)
        self.hist_hint.set(f"已保存 {len(self.history)} 条记录")

    def on_favorite(self):
        url = normalize_url(self.url_var.get())
        if not url:
            messagebox.showinfo("提示", "请先粘贴或载入 B 站链接，再点收藏。")
            return
        title = ""
        if self.playlist and self.cur_idx is not None and self.cur_idx < len(self.playlist):
            title = self.playlist[self.cur_idx].get("title") or ""
        if not title and self.playlist:
            title = self.playlist[0].get("title") or ""
        self.url_var.set(url)
        self._record_history(url, title, len(self.playlist))
        self.status_var.set("已收藏：" + (title or url)[:60])

    def on_open_history(self):
        if self.history_win is not None:
            try:
                if self.history_win.winfo_exists():
                    self.history_win.lift()
                    self.history_win.focus_force()
                    return
            except Exception:
                pass
        self.history_dlg = HistoryDialog(self.root, self)
        self.history_win = self.history_dlg.win

    def load_from_history(self, url, play=False):
        """从历史记录载入链接；play=True 时立即开始播放。"""
        if not url:
            return
        self.url_var.set(url)
        if not play:
            self.status_var.set("已填入链接，点『▶ 开始』播放：" + url[:60])
            return
        if self.engine:
            self.engine.stop()
        self.playlist = []
        self.cur_idx = None
        self._cache.clear()
        self._prefetching.clear()
        self._start_new()

    # ---- 登录态（大会员 / 受限内容）------------------------------------ #
    def _cookie_args(self):
        """返回传给 yt-dlp 的登录态参数；未启用则返回空列表。"""
        if not self.login_var.get():
            return []
        if isinstance(self.cookie_mode, tuple):
            if self.cookie_mode[0] == "browser":
                return ["--cookies-from-browser", self.cookie_mode[1]]
            if self.cookie_mode[0] == "file":
                return ["--cookies", self.cookie_mode[1]]
        return []

    def _refresh_cookie_status(self):
        if isinstance(self.cookie_mode, tuple) and self.cookie_mode[0] == "browser":
            self.cookie_status_var.set(f"已用浏览器登录：{self.cookie_mode[1]}（需在该浏览器登录 B站）")
        elif isinstance(self.cookie_mode, tuple) and self.cookie_mode[0] == "file":
            self.cookie_status_var.set(f"已用 Cookie 文件：{os.path.basename(self.cookie_mode[1])}")
        else:
            self.cookie_status_var.set("请点『从浏览器导入』或『选择 cookies.txt』")

    def _on_login_toggle(self):
        if self.login_var.get():
            self._refresh_cookie_status()
        else:
            self.cookie_status_var.set("未启用登录（公开视频无需登录）")

    def _import_browser_cookies(self):
        choice = simpledialog.askstring(
            "选择浏览器",
            "输入已登录 B站 的浏览器名称（chrome / edge / firefox / brave）：",
            initialvalue="chrome",
        )
        if not choice:
            return
        choice = choice.strip().lower()
        if choice not in ("chrome", "edge", "firefox", "brave"):
            messagebox.showwarning("提示", "浏览器名称需为 chrome / edge / firefox / brave 之一。")
            return
        self.cookie_mode = ("browser", choice)
        self.login_var.set(True)
        self._refresh_cookie_status()
        self.status_var.set(f"已设置从浏览器 {choice} 读取 B站登录态（请确保已在该浏览器登录 B站）")

    def _import_cookie_file(self):
        path = filedialog.askopenfilename(
            title="选择 cookies.txt",
            filetypes=[("Cookie 文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.cookie_mode = ("file", path)
        self.login_var.set(True)
        self._refresh_cookie_status()
        self.status_var.set(f"已选择 Cookie 文件：{os.path.basename(path)}")

    def _open(self, url):
        import webbrowser
        webbrowser.open(url)

    # ---- 事件 ------------------------------------------------------------ #
    def on_load(self):
        """仅解析并加载用户填写的链接（分P列表 / 音质），不自动播放。"""
        if not self.ffmpeg:
            messagebox.showwarning("缺少 ffmpeg", "请先通过「工具 → 下载 ffmpeg」获取解码器。")
            return
        if self._resolving or self._loading:
            return
        self._start_new(autoplay=False)

    def on_toggle(self):
        if not self.ffmpeg:
            messagebox.showwarning("缺少 ffmpeg", "请先通过「工具 → 下载 ffmpeg」获取解码器。")
            return
        st = self.engine.get_state() if self.engine else "idle"
        if st == "playing":
            self.engine.pause()
        elif st == "paused":
            self.engine.resume()
        elif st == "loading" or self._loading or self._resolving:
            return
        else:  # idle / finished / error / None
            url = normalize_url(self.url_var.get())
            # 已加载过该链接的播放列表：直接播放当前/第一首，无需重新解析
            if url and url == self._loaded_url and self.playlist:
                self._play_index(self.cur_idx if self.cur_idx is not None else 0)
            elif self.playlist and self.cur_idx is not None:
                self._play_index(self.cur_idx)     # 重播当前这首
            else:
                self._start_new(autoplay=True)

    def _start_new(self, autoplay=True):
        url = normalize_url(self.url_var.get())
        if not url:
            messagebox.showinfo("提示", "请先粘贴 B 站视频链接。")
            return
        self.url_var.set(url)
        if self._resolving:
            return
        self._resolving = True
        self._loaded_url = url
        self.btn_toggle.config(state="disabled")
        self.status_var.set("正在解析链接与分P列表...")
        threading.Thread(target=self._resolve_playlist_thread, args=(url, autoplay), daemon=True).start()

    def _resolve_playlist_thread(self, url, autoplay):
        try:
            entries = resolve_playlist(url, cookie_args=self._cookie_args())
            self.post(self._after_playlist, entries, autoplay)
        except Exception as e:
            self.post(self._resolve_failed, str(e))

    def _after_playlist(self, entries, autoplay):
        self._resolving = False
        self.btn_toggle.config(state="normal")
        self.playlist = entries
        self._cache.clear()
        self._prefetching.clear()
        self._fail_streak = 0

        # 枚举该视频的纯音频格式，填充音质下拉框（默认最高）
        self._populate_quality(entries[0]["url"] if entries else None)

        # 自动记录到历史（标题优先取解析出的第一条）
        if self.auto_hist_var.get():
            first_title = (entries[0].get("title") or "") if entries else ""
            self._record_history(self.url_var.get(), first_title, len(entries))

        self._populate_list()
        if autoplay:
            self.status_var.set(f"共 {len(entries)} 个条目")
            self._play_index(0)
        else:
            self.status_var.set(f"已加载 {len(entries)} 个条目，点『▶ 开始』播放")

    def _populate_quality(self, url):
        quals = []
        if url:
            try:
                quals = list_audio_formats(url, cookie_args=self._cookie_args())
            except Exception:
                quals = []
        self.audio_qualities = quals
        if quals:
            labels = [f"{q['abr']:.0f}k ({q['ext']})" for q in quals]
            self._quality_labels = labels
            self.quality_combo.config(values=labels, state="readonly")
            self.quality_var.set(labels[0])          # 默认最高音质
            self.audio_format = quals[0]["id"]
        else:
            self._quality_labels = ["最高（自动）"]
            self.quality_combo.config(values=["最高（自动）"], state="readonly")
            self.quality_var.set("最高（自动）")
            self.audio_format = None

    def on_quality(self, event=None):
        sel = self.quality_var.get()
        if not self.audio_qualities:
            self.audio_format = None
            return
        try:
            idx = self._quality_labels.index(sel)
        except ValueError:
            return
        self.audio_format = self.audio_qualities[idx]["id"]
        self._cache.clear()        # 旧音质的缓存作废
        self._prefetching.clear()
        # 若正在播放，则立即用新音质重载当前曲目
        if self.playlist and self.cur_idx is not None and self.engine \
                and self.engine.get_state() not in ("idle",):
            self._play_index(self.cur_idx)

    def _resolve_failed(self, err):
        self._resolving = False
        self._loading = False
        self.btn_toggle.config(state="normal")
        if is_login_required_error(err) and not self.login_var.get():
            self.status_var.set("解析失败：该视频可能需要登录（大会员/受限）。请启用『B站登录』并导入 Cookie 后重试。")
            messagebox.showwarning(
                "需要登录",
                "该视频/音频可能需要 B站登录（大会员或受限内容）。\n\n"
                "请在下方『启用 B站登录』处：\n"
                "1) 勾选该复选框\n"
                "2) 点『从浏览器导入』（需在该浏览器已登录 B站）或『选择 cookies.txt』\n"
                "然后重新粘贴链接并点击开始。",
            )
        else:
            self.status_var.set("解析失败：" + err[:120])
            messagebox.showerror("解析失败", err)

    # ---- 播放列表操作 ---------------------------------------------------- #
    def _populate_list(self):
        self._suppress_list = True
        self.listbox.delete(0, tk.END)
        for i in range(len(self.playlist)):
            self.listbox.insert(tk.END, self._item_text(i))
        self._suppress_list = False
        self._update_list_header()

    def _item_text(self, i):
        e = self.playlist[i]
        title = e.get("title") or f"P{i+1}"
        dur = e.get("duration")
        suffix = f"   {fmt_time(dur)}" if dur else ""
        return f"{i+1}. {title}{suffix}"

    def _refresh_list_item(self, i):
        if not (0 <= i < len(self.playlist)):
            return
        self._suppress_list = True
        try:
            self.listbox.delete(i)
            self.listbox.insert(i, self._item_text(i))
        finally:
            self._suppress_list = False

    def _update_list_header(self):
        n = len(self.playlist)
        cur = (self.cur_idx + 1) if self.cur_idx is not None else 0
        self.list_header.set(f"播放列表（{cur}/{n}）")

    def _select_list(self, i):
        self._suppress_list = True
        try:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(i)
            self.listbox.activate(i)
            self.listbox.see(i)
        finally:
            self._suppress_list = False

    def _on_list_select(self, event):
        if self._suppress_list:
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        i = sel[0]
        if i != self.cur_idx:
            self._play_index(i)

    def on_prev(self):
        if not self.playlist or self.cur_idx is None:
            return
        if self.cur_idx - 1 >= 0:
            self._play_index(self.cur_idx - 1)

    def on_next(self):
        if not self.playlist:
            return
        nxt = (self.cur_idx if self.cur_idx is not None else -1) + 1
        if nxt < len(self.playlist):
            self._play_index(nxt)

    # ---- 播放指定条目 ---------------------------------------------------- #
    def _play_index(self, idx):
        if not self.playlist or not (0 <= idx < len(self.playlist)):
            return
        if self._loading:
            return
        self._loading = True
        self.cur_idx = idx
        self._select_list(idx)
        self._update_list_header()
        title = self.playlist[idx].get("title") or f"P{idx+1}"
        self.status_var.set(f"正在加载 第 {idx+1}/{len(self.playlist)} 首：{title}")
        self.btn_toggle.config(state="disabled")
        threading.Thread(target=self._load_thread, args=(idx,), daemon=True).start()

    def _get_audio(self, idx):
        """阻塞式解析第 idx 条的音频直链（带缓存）。"""
        if idx in self._cache:
            return self._cache[idx]
        entry = self.playlist[idx]
        au, h, t, d = resolve_audio(entry["url"], audio_format=self.audio_format, cookie_args=self._cookie_args())
        if t:
            entry["title"] = t
        if d:
            entry["duration"] = d
        result = (au, h, t or entry.get("title") or f"P{idx+1}", d or entry.get("duration") or 0.0)
        self._cache[idx] = result
        return result

    def _load_thread(self, idx):
        try:
            au, h, t, d = self._get_audio(idx)
            self.post(self._after_loaded, idx, au, h, t, d)
        except Exception as e:
            self.post(self._load_failed, idx, str(e))

    def _after_loaded(self, idx, au, h, t, d):
        self._loading = False
        self.btn_toggle.config(state="normal")
        if self.cur_idx != idx:
            return  # 期间已切歌
        self.cur_var.set("00:00")
        self.dur_var.set(fmt_time(d))
        self.scale.config(to=max(1.0, d or 1.0))
        self.scale.set(0)
        self._refresh_list_item(idx)
        self._update_list_header()
        self.status_var.set(f"正在播放 第 {idx+1}/{len(self.playlist)} 首：{(t or '')[:38]}")
        self.root.title(f"B站音频播放器 - {(t or '')[:40]}")
        if self.engine is None:
            self.engine = AudioEngine(self.ffmpeg, on_state=self.on_engine_state, volume=self.vol_var.get())
        elif self.engine.get_state() != "idle":
            self.engine.stop()   # 切歌前先停下旧播放
        self.engine.load(au, h, d or 0.0)
        self.engine.start()
        self._fail_streak = 0
        self._prefetch(idx + 1)

    def _load_failed(self, idx, err):
        self._loading = False
        self.btn_toggle.config(state="normal")
        self._fail_streak += 1
        if is_login_required_error(err) and not self.login_var.get():
            self.status_var.set(f"第 {idx+1} 首需要登录：请启用『B站登录』并导入 Cookie 后重试。")
        else:
            self.status_var.set(f"第 {idx+1} 首加载失败：{err[:100]}")
        if self._fail_streak <= 5 and idx + 1 < len(self.playlist):
            self.root.after(800, lambda: self._play_index(idx + 1))
        else:
            self.status_var.set("连续多首加载失败，已停止。请检查链接或网络。")

    def _prefetch(self, idx):
        if idx is None or idx < 0 or idx >= len(self.playlist):
            return
        if idx in self._cache or idx in self._prefetching:
            return
        self._prefetching.add(idx)

        def run():
            try:
                self._get_audio(idx)
            except Exception:
                pass
            finally:
                self._prefetching.discard(idx)

        threading.Thread(target=run, daemon=True).start()

    def on_stop(self):
        if self.engine:
            self.engine.stop()
        self.scale.set(0)
        self.cur_var.set("00:00")

    def on_scale(self, val):
        # 拖动过程中只更新时间显示，真正跳转在松开时执行
        if self._seek_dragging:
            self.cur_var.set(fmt_time(float(val)))

    def _seek_press(self, event):
        self._seek_dragging = True

    def _seek_release(self, event):
        self._seek_dragging = False
        if self.engine:
            val = float(self.scale.get())
            self.engine.seek(val)
            self.cur_var.set(fmt_time(val))

    def on_vol(self, val):
        v = float(val)
        pct = int(v * 100)
        if hasattr(self, "vol_pct"):
            self.vol_pct.config(text=f"{pct}%")
        if self.engine:
            self.engine.set_volume(v)

    def on_engine_state(self, state):
        # 引擎回调发生在播放线程，通过队列切回主线程更新 UI
        self.post(self._update_button_by_state, state)

    def _update_button_by_state(self, state):
        if state == "playing":
            self.btn_toggle.config(text="⏸ 暂停", state="normal")
        elif state == "paused":
            self.btn_toggle.config(text="▶ 继续", state="normal")
        elif state == "finished":
            self.btn_toggle.config(text="▶ 开始", state="normal")
            # 一首播完 -> 自动播放下一首
            self.root.after(150, self._auto_advance)
        elif state == "idle":
            self.btn_toggle.config(text="▶ 开始", state="normal")
        elif state == "error":
            self.btn_toggle.config(text="▶ 开始", state="normal")
            err = (self.engine._error if self.engine else "") or "未知错误"
            self.status_var.set("播放出错：" + str(err)[:160])
            # 播放失败也尝试跳到下一首
            self.root.after(1000, self._auto_advance)

    def _auto_advance(self):
        if not self.playlist or self.cur_idx is None:
            return
        if self._loading:
            return
        nxt = self.cur_idx + 1
        if nxt < len(self.playlist):
            self._play_index(nxt)
        elif self.loop_var.get():
            self._play_index(0)
        else:
            self.status_var.set(f"全部播放完毕（共 {len(self.playlist)} 首）")
            self.scale.set(0)
            self.cur_var.set("00:00")

    # ---- 进度轮询 -------------------------------------------------------- #
    def _poll(self):
        # 主线程：先处理来自工作线程的 UI 消息，再刷新进度
        self._drain_ui_queue()
        try:
            if self.engine and self.engine.get_state() == "playing":
                pos = self.engine.position()
                if not self._seek_dragging:
                    dur = self.engine.duration or 0
                    self.scale.set(min(pos, dur or pos))
                    self.cur_var.set(fmt_time(pos))
        except Exception:
            pass
        self.root.after(150, self._poll)

    # ---- ffmpeg 下载 ----------------------------------------------------- #
    def on_download_ffmpeg(self):
        if self.ffmpeg:
            messagebox.showinfo("提示", "已检测到 ffmpeg，无需下载。")
            return
        if messagebox.askyesno("下载 ffmpeg", "将下载约 80MB 便携版 ffmpeg 到 bin/ 目录，是否继续？"):
            threading.Thread(target=self._download_thread, daemon=True).start()

    def _download_thread(self):
        try:
            self.post(self.status_var.set, "准备下载 ffmpeg...")
            exe = download_ffmpeg(progress_cb=lambda m: self.post(self.status_var.set, m))
            self.ffmpeg = exe
            self.post(self._refresh_ffmpeg_status)
            self.post(lambda: messagebox.showinfo("完成", f"ffmpeg 已就绪：\n{exe}"))
        except Exception as e:
            self.post(lambda: messagebox.showerror("下载失败", str(e)))
            self.post(self.status_var.set, "ffmpeg 下载失败，请手动下载并放到 bin/。")


# --------------------------------------------------------------------------- #
# 历史 / 收藏窗口
# --------------------------------------------------------------------------- #
class HistoryDialog:
    """历史 / 收藏链接窗口：搜索、加载、删除、清空。

    不直接继承 tk.Toplevel，是为了在没有 tkinter 的环境下模块也能被 import。
    """

    def __init__(self, master, app):
        self.app = app
        self.items = list(app.history)
        self.filtered = list(self.items)

        win = self.win = tk.Toplevel(master)
        win.title("历史 / 收藏")
        win.geometry("760x460")
        win.minsize(560, 360)
        win.transient(master)

        top = ttk.Frame(win)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="搜索：").pack(side="left")
        self.filter_var = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.filter_var, width=32)
        e.pack(side="left", padx=(4, 8))
        e.bind("<KeyRelease>", lambda ev: self.refresh())
        ttk.Label(top, text="（匹配标题或链接，双击直接播放）").pack(side="left")

        box = ttk.Frame(win)
        box.pack(fill="both", expand=True, padx=10)
        self.listbox = tk.Listbox(box, font=("Microsoft YaHei UI", 9), activestyle="dotbox")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda ev: self.load(play=True))
        self.listbox.bind("<<ListboxSelect>>", lambda ev: self.show_detail())
        self.listbox.bind("<Delete>", lambda ev: self.delete())
        self.listbox.bind("<Return>", lambda ev: self.load(play=True))

        self.detail_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=self.detail_var, anchor="w",
                  foreground="#555555", wraplength=720).pack(fill="x", padx=10, pady=(6, 0))

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=8)
        ttk.Button(bar, text="▶ 加载并播放", command=lambda: self.load(play=True)).pack(side="left")
        ttk.Button(bar, text="仅填入链接", command=lambda: self.load(play=False)).pack(side="left", padx=6)
        ttk.Button(bar, text="删除", command=self.delete).pack(side="left", padx=6)
        ttk.Button(bar, text="清空全部", command=self.clear_all).pack(side="left", padx=6)
        ttk.Button(bar, text="关闭", command=self.close).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        if self.filtered:
            self.listbox.selection_set(0)
            self.show_detail()
        try:
            e.focus_set()
        except Exception:
            pass

    # ---- 数据 ----------------------------------------------------------- #
    def refresh(self):
        kw = self.filter_var.get().strip().lower()
        if kw:
            self.filtered = [x for x in self.items
                             if kw in x["url"].lower() or kw in (x.get("title") or "").lower()]
        else:
            self.filtered = list(self.items)
        self.listbox.delete(0, tk.END)
        for it in self.filtered:
            ts = time.strftime("%m-%d %H:%M", time.localtime(it["ts"])) if it.get("ts") else "  --  "
            title = (it.get("title") or "").strip() or it["url"]
            cnt = "  [{}P]".format(it["count"]) if it.get("count") else ""
            self.listbox.insert(tk.END, "{}  {}{}".format(ts, title, cnt))
        self.win.title("历史 / 收藏（{}）".format(len(self.filtered)))

    def _sel_item(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        i = sel[0]
        if 0 <= i < len(self.filtered):
            return self.filtered[i]
        return None

    def show_detail(self):
        it = self._sel_item()
        self.detail_var.set(it["url"] if it else "")

    # ---- 操作 ----------------------------------------------------------- #
    def load(self, play=False):
        it = self._sel_item()
        if not it:
            messagebox.showinfo("提示", "请先选择一条记录。", parent=self.win)
            return
        url = it["url"]
        self.close()
        self.app.load_from_history(url, play=play)

    def delete(self):
        it = self._sel_item()
        if not it:
            return
        self.items = [x for x in self.items if x["url"] != it["url"]]
        self.app.set_history(self.items)
        self.refresh()

    def clear_all(self):
        if not self.items:
            return
        if not messagebox.askyesno("确认", "确定清空全部历史 / 收藏记录？", parent=self.win):
            return
        self.items = []
        self.app.set_history([])
        self.refresh()

    def close(self):
        try:
            self.app.history_win = None
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    if not _HAVE_TK:
        err = (
            "错误：当前 Python 未包含 tkinter 模块。\n"
            "请使用标准 CPython（官网安装包默认包含 tkinter）来运行本程序。\n"
        )
        sys.stderr.write(err)
        _write_error_log(err)
        sys.exit(1)

    import traceback as _tb

    # 任何未捕获的异常都记录到 error.log
    def _excepthook(exc_type, exc_val, exc_tb):
        msg = "未捕获异常:\n" + "".join(_tb.format_exception(exc_type, exc_val, exc_tb))
        _write_error_log(msg)
    sys.excepthook = _excepthook

    try:
        root = tk.Tk()
    except Exception as e:
        msg = "无法创建窗口 (Tk 初始化失败):\n" + repr(e)
        _write_error_log(msg)
        sys.stderr.write(msg + "\n")
        sys.exit(1)

    try:
        # Windows 下让标题栏更清爽
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # 捕获 tkinter 回调里的异常（避免静默闪退，改为弹窗 + 写日志）
    def _report_cb(exc, val, tb):
        msg = "界面回调异常:\n" + "".join(_tb.format_exception(exc, val, tb))
        _write_error_log(msg)
        try:
            messagebox.showerror("程序出错", msg[:3000])
        except Exception:
            pass
    root.report_callback_exception = _report_cb

    try:
        app = App(root)
    except Exception as e:
        msg = "启动失败:\n" + "".join(_tb.format_exception(type(e), e, e.__traceback__))
        _write_error_log(msg)
        try:
            messagebox.showerror("启动失败", msg[:3000])
        except Exception:
            pass
        root.destroy()
        sys.exit(1)

    root.protocol("WM_DELETE_WINDOW", lambda: (app.engine.close() if app.engine else None, root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
