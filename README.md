# B站音频播放器（Windows）

只听声音、不看视频：把 B 站视频链接粘进文本框，点「开始 / 暂停」即可用电脑扬声器播放音频。
纯音频流式解码，**无需安装 VLC 等任何播放器**。

## 功能
- 粘贴 B 站链接（BV 号 / av 号 / 完整网页链接均可，也支持不带 `https://` 的链接）
- 一个按钮控制「开始 / 暂停 / 继续」
- 停止、上一首 / 下一首、进度条拖动跳转、音量调节
- **多分P / 合集**：自动列出全部条目（含标题、时长），一首播完**自动播放下一首**，可勾选「列表循环」
- **音质选择**：自动枚举该视频的纯音频比特率（如 204k / 103k / 44k），下拉框可切换，**默认最高音质**
- 点击列表中的任意一首即可跳转播放；右侧滚动条查看长列表
- 解析与播放状态实时显示

## 工作原理
1. `yt-dlp` 解析出视频的**纯音频流直链**与所需请求头（含 Referer）；
   多分P 列表由 yt-dlp 的 playlist 模式获取，标题/时长用 B 站官方 API 一次性补全。
2. `ffmpeg` 把该直链实时解码为 PCM 裸流（绕开浏览器跨域 / Referer 限制）。
3. `PyAudio` 直接播放 PCM（Windows 自带音频后端，免驱动）。
4. 一首播完时引擎切换为 `finished`，主线程据此自动加载下一首（并预取下一首以缩短间隔）。

## 环境准备
需要 Python 3.8+。在本项目目录执行：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install yt-dlp numpy pyaudio
```

## ffmpeg（已内置，通常无需操作）
本仓库 `bin/ffmpeg.exe` 已附带便携版 ffmpeg，**正常情况开箱即用**。
仅当该文件缺失时，点「工具 → 下载 ffmpeg」即可自动下载到 `bin/`；
也可手动下载 https://www.gyan.dev/ffmpeg/builds/ 的 *essentials* 版本，把 `ffmpeg.exe` 放到 `bin/` 下
（或任一层子目录，程序会递归查找）。

## 运行
```bat
.venv\Scripts\activate
python bili_audio_player.py
```
或直接双击 `run.bat`。

## 打包为单个 exe（可选）
```bat
pip install pyinstaller
pyinstaller bili_audio_player.spec
```
生成的 `dist\bili_audio_player\bili_audio_player.exe` 可直接拷贝到别的 Windows 机器运行
（需把 `bin\ffmpeg.exe` 一起带上，或运行时用「工具 → 下载 ffmpeg」获取）。

## 常见问题
- **提示「未找到 ffmpeg」**：见上方「获取 ffmpeg」。
- **解析失败 / 403**：部分视频需要登录或地区限制，本工具仅支持公开可播放的音频流。
  链接过期后重新点「开始」会重新解析。
- **没声音**：检查系统音量 / 默认播放设备；进度条在走动说明已在播放。
- **暂停很久后继续没声音**：B 站音频直链有时效，重新点「开始」即可重新解析播放。
