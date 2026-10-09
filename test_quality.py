import sys, subprocess, json, tkinter as tk
import numpy as np
import bili_audio_player as app

URL = "https://www.bilibili.com/video/BV1GJ411x7h7"

# 1) list_audio_formats：返回按比特率降序的纯音频格式
quals = app.list_audio_formats(URL, app.find_ytdlp())
print("qualities:", [(q["id"], q["abr"]) for q in quals])
assert len(quals) >= 2, "应至少列出两种音频音质"
# 降序
abrs = [q["abr"] for q in quals]
assert abrs == sorted(abrs, reverse=True), "应按比特率降序"
print("QUALITY_LIST_OK (highest =", quals[0]["id"], f'{quals[0]["abr"]:.0f}k)')

# 2) 指定具体 format id 能解析并解出真实音频
def decode_ok(fid):
    au, h, t, d = app.resolve_audio(URL, app.find_ytdlp(), audio_format=fid)
    hdr = dict(h); hdr.setdefault("Referer", "https://www.bilibili.com/")
    hstr = "".join(f"{k}: {v}\r\n" for k, v in hdr.items())
    out = subprocess.run([app.find_ffmpeg(), "-hide_banner", "-loglevel", "error",
                          "-ss", "0", "-headers", hstr, "-i", au,
                          "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "2",
                          "-ar", "44100", "-t", "4", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    pcm = out.stdout
    rms = np.sqrt(np.mean(np.frombuffer(pcm, dtype=np.int16).astype(np.float64) ** 2))
    return len(pcm) > 44100 * 2 * 2 * 3 and rms > 1

low_id = quals[-1]["id"]   # 最低音质（如 30216 / 44k）
print(f"decode with {low_id}:", decode_ok(low_id))
assert decode_ok(low_id)
print("QUALITY_SELECT_OK")

# 3) App 下拉框默认选中最高音质
root = tk.Tk()
a = app.App(root)
a._populate_quality(URL)
print("default audio_format:", a.audio_format, "| labels:", a._quality_labels, "| var:", a.quality_var.get())
assert a.audio_format == quals[0]["id"], "默认应为最高音质"
assert a.quality_var.get() == a._quality_labels[0]
root.destroy()
print("APP_DEFAULT_HIGHEST_OK")
print("ALL_QUALITY_TESTS_PASSED")
