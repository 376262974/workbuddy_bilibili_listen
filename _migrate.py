import os
import shutil

# 1) 把迁移记录写入 E: 的 memory（这份会被保留）
mem = r"E:\workbuddy_bilibili_listen\.workbuddy\memory\2026-10-09.md"
note = r"""

### 项目迁移：C:\Users\Administrator\WorkBuddy\bilibili_listen -> E:\workbuddy_bilibili_listen
- 用户要求迁移并清空原目录。已用 shutil.copytree 完整拷贝（排除 __pycache__、PyInstaller 的 build/ 缓存、
  以及误建的 Windows 保留名垃圾文件 nul）。
- 校验：4256 个文件、216,905,079 字节，零缺失、零大小不一致；E: 上 exe 可启动、.venv 仍可用
  （venv 的 pyvenv.cfg 指向 C 盘 base Python，仍在，故迁移后 venv 依旧工作）。
- 随后已清空原目录内容（保留空目录本身）。
- 主交付物仍是自包含的 dist\bili_audio_player.exe，不依赖 Python 环境。
"""
with open(mem, "a", encoding="utf-8") as f:
    f.write(note)
print("memory note appended to E: copy")

# 2) 清空原目录
src = r"C:\Users\Administrator\WorkBuddy\bilibili_listen"
errors = []
for name in list(os.listdir(src)):
    p = os.path.join(src, name)
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            try:
                os.remove(p)
            except Exception:
                os.remove("\\\\?\\" + p)  # 扩展路径，处理 nul 等保留名
    except Exception as e:
        errors.append((name, repr(e)[:150]))

print("remaining in original dir:", os.listdir(src))
print("errors:", errors)
print("total files left in original:", sum(len(fs) for _ in (0,) for _ in [0] for fs in [[]]) if False else sum(len(fs) for _, _, fs in os.walk(src)))
