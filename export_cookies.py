#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从本机 Chrome 导出 bilibili.com 的 Cookie 为 Netscape 格式 cookies.txt。

用法（用带 browser_cookie3 的解释器跑）：
    <python> export_cookies.py [chrome|edge|firefox] [输出路径]
默认：chrome，输出到本目录下的 cookies.txt

已知限制：Chrome / Edge v127+ 使用 app-bound 加密，第三方库无法解密，
          会报 "Unable to get key for cookie decryption"。
          此时请改用浏览器扩展「Get cookies.txt LOCALLY」手动导出，
          或改用 Firefox（无加密，通常可直接读取）。
"""
import os
import sys
import time

BROWSER = sys.argv[1].lower() if len(sys.argv) > 1 else "chrome"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

DOMAIN_KEYS = ("bilibili",)


def main():
    try:
        import browser_cookie3
    except ImportError:
        sys.stderr.write("缺少依赖：请先 pip install browser_cookie3\n")
        return 2

    try:
        loader = getattr(browser_cookie3, BROWSER)
    except AttributeError:
        sys.stderr.write("不支持的浏览器：%s（可选 chrome / edge / firefox）\n" % BROWSER)
        return 2
    try:
        cj = loader()
    except Exception as e:
        sys.stderr.write("读取 %s Cookie 失败：%r\n" % (BROWSER, e))
        sys.stderr.write("提示：Chrome / Edge v127+ 的 app-bound 加密无法被第三方库解密，"
                         "请改用扩展手动导出，或改用 Firefox。\n")
        return 3

    items = []
    for c in cj:
        dom = (c.domain or "").lower()
        if any(k in dom for k in DOMAIN_KEYS):
            items.append(c)
    if not items:
        sys.stderr.write("未在任何 Cookie 中找到 bilibili 相关条目，"
                         "请确认 Chrome 已登录 bilibili.com。\n")
        return 4

    lines = [
        "# Netscape HTTP Cookie File",
        "# Exported from Chrome by export_cookies.py",
        "# %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
    ]
    for c in items:
        dom = c.domain or ""
        inc = "TRUE" if dom.startswith(".") else "FALSE"
        path = c.path or "/"
        secure = "TRUE" if c.secure else "FALSE"
        exp = int(c.expires) if c.expires else 0
        name = c.name or ""
        val = (c.value or "").replace("\n", "").replace("\r", "")
        lines.append("\t".join([dom, inc, path, secure, str(exp), name, val]))

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print("已导出 %d 条 Cookie -> %s" % (len(items), OUT))
    for c in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3"):
        hit = [x for x in items if x.name == c]
        if hit:
            print("  %-12s %s" % (c, (hit[0].value or "")[:12] + "..."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
