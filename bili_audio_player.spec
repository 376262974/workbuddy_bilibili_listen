# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：生成单个 Windows 可执行文件
# 用法：pyinstaller bili_audio_player.spec
import os

block_cipher = None

# 把 bin/ 目录（含 ffmpeg.exe）一起打进包，运行时解压到临时目录
bin_dir = os.path.join(os.getcwd(), "bin")
added_bins = []
if os.path.isdir(bin_dir):
    for root, _dirs, files in os.walk(bin_dir):
        for f in files:
            if f.lower().endswith(".exe"):
                added_bins.append((os.path.join(root, f), os.path.relpath(root, os.getcwd())))

a = Analysis(
    ["bili_audio_player.py"],
    pathex=[],
    binaries=added_bins,
    datas=[],
    hiddenimports=["yt_dlp", "numpy", "pyaudio"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="bili_audio_player",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=None,
)
