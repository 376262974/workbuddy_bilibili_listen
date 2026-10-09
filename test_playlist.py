import tkinter as tk
import time
import bili_audio_player as app

# 1) 多分P 列表
entries = app.resolve_playlist("https://www.bilibili.com/video/BV1BDk2YCEHF/", app.find_ytdlp())
print("multi entries:", len(entries))
print("first3:", [e["title"] for e in entries[:3]])
print("durations:", [e["duration"] for e in entries[:3]])
assert len(entries) == 150, len(entries)
assert entries[0]["title"] == "偏爱"

# 2) 单视频
single = app.resolve_playlist("https://www.bilibili.com/video/BV1GJ411x7h7", app.find_ytdlp())
print("single entries:", len(single), "|", single[0]["title"])
assert len(single) == 1
print("PLAYLIST_RESOLVE_OK")

# 3) 队列顺序播放 + 自动下一首（真实解析，音量置 0 保持静音）
root = tk.Tk()
a = app.App(root)
a.vol_var.set(0.0)
a.on_vol(0.0)
a.playlist = entries
a._cache.clear()
a._populate_list()
a._play_index(0)

def wait_until(cond, secs=60):
    for _ in range(int(secs * 4)):
        root.update()
        if cond():
            return True
        time.sleep(0.25)
    return False

ok = wait_until(lambda: a.engine is not None and a.engine.get_state() == "playing")
print("play idx0:", a.cur_idx, "state:", a.engine.get_state() if a.engine else None)
assert ok and a.cur_idx == 0
for _ in range(8):
    root.update(); time.sleep(0.25)
print("position:", round(a.engine.position(), 2))
assert a.engine.position() > 1.0

# 模拟第 1 首播完 -> 自动跳第 2 首
a._loading = False
a._auto_advance()
ok2 = wait_until(lambda: a.cur_idx == 1 and a.engine.get_state() == "playing")
print("after advance idx:", a.cur_idx, "state:", a.engine.get_state())
assert ok2
print("prev/next test:")
a.on_next()
wait_until(lambda: a.cur_idx == 2 and a.engine.get_state() == "playing")
print("after next idx:", a.cur_idx)
assert a.cur_idx == 2
a.engine.close(); root.destroy()
print("QUEUE_OK")
print("ALL_PLAYLIST_TESTS_PASSED")
