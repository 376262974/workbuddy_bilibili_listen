import sys, time
import numpy as np
import bili_audio_player as app

# 用假的 ffmpeg（python 生成恒定 PCM）和假的 PyAudio（记录写入内容）来测试实时音量
GEN = (
    "import sys,struct;"
    "v=1000;"
    "blk=b''.join(struct.pack('<h',v) for _ in range(44100));"  # 0.5s @44.1k stereo
    "sys.stdout.buffer.write(blk*32)"                            # 16s 恒定音量
)

class FakeStream:
    def __init__(self): self.frames = []
    def write(self, data):
        self.frames.append(data)
        time.sleep(0.02)              # 放慢消费，留出中途调音量的时间
        return len(data)
    def stop_stream(self): pass
    def close(self): pass

class FakePA:
    def __init__(self, *a, **k): self._s = FakeStream()
    def get_format_from_width(self, *a, **k): return None
    def open(self, *a, **k): return self._s
    def terminate(self): pass

app.pyaudio.PyAudio = FakePA

TOTAL_BYTES = 44100 * 2 * 2 * 16  # 16s 的字节数

# 1) 初始 1.0，消费到 30% 时切到 0.0：前段应满音量(1000)，后段应静音(0)
eng = app.AudioEngine(app.find_ffmpeg(), volume=1.0)
eng._build_cmd = lambda sp: [sys.executable, "-c", GEN]
eng.load("http://dummy", {}, 16.0)
eng.start()
pa = eng._pa
switched = False
for _ in range(600):
    if not switched and sum(len(f) for f in pa._s.frames) > 0.30 * TOTAL_BYTES:
        eng.set_volume(0.0)
        switched = True
    if eng.get_state() == "finished":
        break
    time.sleep(0.05)

arr = np.frombuffer(b"".join(pa._s.frames), dtype=np.int16).astype(np.float64)
n = len(arr)
first = arr[: n // 7]
last = arr[-n // 7:]
print("first 15% mean abs (expect ~1000):", round(np.mean(np.abs(first)), 1))
print("last 15% mean abs (expect ~0):", round(np.mean(np.abs(last)), 1))
assert np.mean(np.abs(first)) > 900, "前段应是满音量"
assert np.mean(np.abs(last)) < 50, "后段应因静音接近 0"
print("LIVE_VOLUME_OK")

# 2) 初始 0.5：整体幅度应≈500
eng2 = app.AudioEngine(app.find_ffmpeg(), volume=0.5)
eng2._build_cmd = lambda sp: [sys.executable, "-c", GEN]
eng2.load("http://dummy", {}, 16.0)
pa2 = FakePA(); eng2._pa = pa2
eng2.start()
for _ in range(600):
    if eng2.get_state() == "finished":
        break
    time.sleep(0.05)
arr2 = np.frombuffer(b"".join(pa2._s.frames), dtype=np.int16).astype(np.float64)
print("vol=0.5 mean abs (expect ~500):", round(np.mean(np.abs(arr2)), 1))
assert 400 < np.mean(np.abs(arr2)) < 600, "0.5 音量幅度应约为 500"
print("STATIC_VOLUME_OK")
eng.close(); eng2.close()
print("ALL_VOLUME_TESTS_PASSED")
