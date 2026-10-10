#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""历史 / 收藏功能冒烟测试（不联网、不实际播放）。"""
import os
import sys
import time
import tempfile

sys.argv = [sys.argv[0]]
os.environ.setdefault("LOCALAPPDATA", tempfile.gettempdir())

import bili_audio_player as m

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(extra)) if extra else ""))
    if not cond:
        ok = False


# ---- 1. 纯函数层：add / save / load -------------------------------------- #
tmpdir = tempfile.mkdtemp(prefix="bili_hist_")
m.data_dir = lambda: tmpdir          # 重定向到临时目录，避免污染真实 history.json

items = []
items = m.add_history(items, "https://www.bilibili.com/video/BV1xx411c7mD", "标题A", 3)
items = m.add_history(items, "https://www.bilibili.com/video/BV2yy411c7mD", "标题B", 1)
check("add_history 追加 2 条", len(items) == 2)
check("最新在前", items[0]["url"].endswith("BV2yy411c7mD"), items[0]["url"])

# 同链接覆盖 + 置顶
time.sleep(0.01)
items = m.add_history(items, "https://www.bilibili.com/video/BV1xx411c7mD", "标题A2", 5)
check("同 url 去重覆盖", len(items) == 2 and items[0]["url"].endswith("BV1xx411c7mD"))
check("覆盖后标题更新", items[0]["title"] == "标题A2", items[0]["title"])

m.save_history(items)
loaded = m.load_history()
check("落盘后可读回", len(loaded) == 2, len(loaded))
check("读回顺序一致", loaded[0]["url"] == items[0]["url"])

# 空 url 不应记录
n0 = len(items)
check("空链接被忽略", len(m.add_history(items, "   ", "x")) == n0)

# 上限裁剪
big = [{"url": "u%d" % i, "title": "t", "count": 1, "ts": float(i)} for i in range(m.HISTORY_MAX + 20)]
capped = m.add_history(big, "unew")
check("超出上限被裁剪", len(capped) == m.HISTORY_MAX, len(capped))

# 损坏文件容错
p = m.history_path()
with open(p, "w", encoding="utf-8") as f:
    f.write("{ this is not json")
check("损坏 json 容错为空列表", m.load_history() == [])

# 恢复成合法数据，供 UI 层使用
m.save_history(items)
check("恢复合法数据", len(m.load_history()) == 2)


# ---- 2. UI 层：App + HistoryDialog --------------------------------------- #
import tkinter as tk

# 避免弹窗阻塞测试
m.messagebox.askyesno = lambda *a, **k: True
m.messagebox.showinfo = lambda *a, **k: "ok"
m.messagebox.showerror = lambda *a, **k: "ok"
m.messagebox.showwarning = lambda *a, **k: "ok"

root = tk.Tk()
root.withdraw()
app = m.App(root)

check("启动时载入历史", len(app.history) == 2, len(app.history))
check("启动时填入最近链接", app.url_var.get().endswith("BV1xx411c7mD"), app.url_var.get())
check("历史条数提示正确", app.hist_hint.get() == "已保存 2 条记录", app.hist_hint.get())

# 手动收藏
app.playlist = [{"url": "https://www.bilibili.com/video/BV3zz411c7mD", "title": "手动收藏曲目"}]
app.cur_idx = 0
app.url_var.set("BV3zz411c7mD")
app.on_favorite()
check("收藏后新增记录", len(app.history) == 3, len(app.history))
check("收藏记录置顶且带标题",
      app.history[0]["title"] == "手动收藏曲目" and app.history[0]["count"] == 1,
      app.history[0])

# 打开历史窗口
app.on_open_history()
dlg = app.history_dlg
root.update()
check("历史窗口已创建", dlg is not None and bool(dlg.win.winfo_exists()))
check("窗口条目数正确", dlg.listbox.size() == 3, dlg.listbox.size())

# 搜索过滤
dlg.filter_var.set("BV2yy")
dlg.refresh()
root.update()
check("搜索过滤生效", dlg.listbox.size() == 1, dlg.listbox.size())
dlg.filter_var.set("")
dlg.refresh()
root.update()

# 选中第 2 条并「仅填入链接」
dlg.listbox.selection_clear(0, tk.END)
dlg.listbox.selection_set(1)
dlg.show_detail()
target = app.history[1]["url"]
dlg.load(play=False)
root.update()
check("仅填入链接：输入框已更新", app.url_var.get() == target, app.url_var.get())
check("仅填入链接：窗口已关闭", app.history_win is None)
check("仅填入链接：未启动播放", app.engine is None and not app._resolving)

# 删除一条
app.on_open_history()
dlg = app.history_dlg
root.update()
dlg.listbox.selection_clear(0, tk.END)
dlg.listbox.selection_set(0)
dlg.delete()
root.update()
check("删除后剩 2 条", len(app.history) == 2, len(app.history))

# 清空
app.on_open_history()
dlg = app.history_dlg
root.update()
dlg.items = [dict(x) for x in app.history]
dlg.clear_all()
root.update()
check("清空后为 0 条", len(app.history) == 0, len(app.history))
check("清空后文件为空", m.load_history() == [])
check("清空后提示更新", app.hist_hint.get() == "已保存 0 条记录", app.hist_hint.get())
dlg.close()

root.destroy()
print("\n=== 全部通过 ===" if ok else "\n=== 存在失败项 ===")
sys.exit(0 if ok else 1)
