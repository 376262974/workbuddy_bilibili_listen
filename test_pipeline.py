import sys, os, time, subprocess
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bili_audio_player as app

URL = "https://www.bilibili.com/video/BV1GJ411x7h7"
ytdlp = app.find_ytdlp()
ffmpeg = app.find_ffmpeg()
print("ffmpeg:", ffmpeg)

# 1) resolve
audio_url, headers, title, duration = app.resolve_audio(URL, ytdlp)
print("title:", title, "duration:", duration)
print("headers:", list(headers.keys()))

# 2) decode 6s to raw PCM and verify it's real audio
hdr = dict(headers)
if "Referer" not in hdr:
    hdr["Referer"] = "https://www.bilibili.com/"
hstr = "".join(f"{k}: {v}\r\n" for k, v in hdr.items())
cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0",
       "-headers", hstr, "-i", audio_url,
       "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "2", "-ar", "44100", "-t", "6", "-"]
out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
pcm = out.stdout
print("decoded bytes:", len(pcm), "expected ~", 44100*2*2*6)
arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
rms = np.sqrt(np.mean(arr**2))
print("RMS (should be >0 for real audio):", round(rms, 1))
assert len(pcm) > 44100*2*2*5, "too few bytes"
assert rms > 1, "audio appears silent"
print("DECODE_TEST_OK")

# 3) live engine test (silent: volume=0 to avoid noise), verify controls
eng = app.AudioEngine(ffmpeg, volume=0.0)
states = []
eng.load(audio_url, headers, duration)
eng.start()
assert eng.get_state() == "playing"
time.sleep(3.0)
pos1 = eng.position()
print("position after 3s:", round(pos1, 2))
assert pos1 > 2.0, "position did not advance"
eng.pause()
assert eng.get_state() == "paused"
pos_pause = eng.position()
time.sleep(1.0)
assert abs(eng.position() - pos_pause) < 0.2, "position moved while paused"
print("paused at:", round(pos_pause, 2))
eng.resume()
assert eng.get_state() == "playing"
time.sleep(1.5)
assert eng.position() > pos_pause, "did not resume"
print("resumed to:", round(eng.position(), 2))
eng.stop()
assert eng.get_state() == "idle"
print("ENGINE_CONTROL_TEST_OK")
eng.close()
print("ALL_TESTS_PASSED")
