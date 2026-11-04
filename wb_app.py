#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb-migrate 桌面程序 — 工作台迁移

双击即用的原生窗口程序。界面仍然是 ui.html，但由系统自带的 Edge WebView2
内核渲染在应用程序自己的窗口里：没有地址栏、没有浏览器、没有端口要记。

迁移逻辑一行都没重写：wb_gui.py 负责参数组装与任务调度，wb_migrate.py 负责
真正的读写。这里只做「起本地服务 -> 开窗口 -> 关窗即退出」。
"""

import argparse
import ctypes
import io
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
_MEI = getattr(sys, "_MEIPASS", None)
if _MEI and _MEI not in sys.path:
    sys.path.insert(0, _MEI)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

APP_NAME = "工作台迁移"
APP_VERSION = "1.0.0"
WIN_TITLE = APP_NAME

# 打包成无控制台程序后 sys.stdout 是 None，任何 print 都会炸。
# 迁移过程中 wb_gui 会临时替换 stdout，但兜底必须有。
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

def _msgbox(text, title=APP_NAME, style=0x10):
    """不依赖 tkinter 的原生消息框。"""
    try:
        ctypes.windll.user32.MessageBoxW(0, str(text), str(title), style)
    except Exception:
        sys.stderr.write(str(text) + "\n")


def _fatal(text):
    _msgbox(text, APP_NAME, 0x10)
    sys.exit(1)


try:
    import wb_gui as gui
except Exception as _e:  # 打包后文件缺失时的兜底提示
    _fatal("程序文件不完整，缺少 wb_gui.py / wb_migrate.py。\n\n%s" % _e)


def _log(text):
    """窗口模式下没有控制台，把启动过程写进用户目录的日志里，便于排查。"""
    try:
        d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                         "wb-migrate")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "app.log")
        if os.path.isfile(p) and os.path.getsize(p) > 256 * 1024:
            os.replace(p, p + ".1")
        with open(p, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def _clipboard(text):
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        if not u.OpenClipboard(None):
            return False
        try:
            u.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            h = k.GlobalAlloc(0x0042, len(data))
            p = k.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k.GlobalUnlock(h)
            u.SetClipboardData(13, h)   # CF_UNICODETEXT
            return True
        finally:
            u.CloseClipboard()
    except Exception:
        return False


def _single_instance():
    """防止连点两次出来两个窗口、同时写数据库。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "Global\\wb-migrate-single-instance")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _asset(name):
    """在打包环境和源码目录里都能找到 ui.html 之类的资源。"""
    for base in (_MEI, HERE, os.path.dirname(os.path.abspath(__file__))):
        if not base:
            continue
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return p
    return os.path.join(HERE, name)


def _embedded_pack():
    """自己这个 exe 尾部是不是带着迁移包（单文件包双击运行的情况）。"""
    if not getattr(sys, "frozen", False):
        return ""
    exe = os.path.abspath(sys.executable)
    try:
        return exe if gui.core.has_embedded_package(exe) else ""
    except Exception:
        return ""


def serve(token, port=0):
    """起本地服务，返回 (httpd, port)。只监听回环地址。"""
    ui_file = _asset("ui.html")
    if not os.path.isfile(ui_file):
        raise RuntimeError("缺少 ui.html，程序文件不完整。")
    gui.UI_FILE = ui_file
    gui.Handler.token = token

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        port = s.getsockname()[1]
    finally:
        s.close()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), gui.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2},
                     daemon=True).start()
    return httpd, port


def wait_ready(port, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _screen_size():
    try:
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1440, 900


def run_window(url, probe=None, extra_js=None, wait=9.0):
    """在原生窗口里打开界面；返回 True 表示正常退出。

    probe 给自动测试用：拿到窗口后执行几段 JS 把渲染结果写出来，然后自动关窗。
    extra_js 会在检查完 DOM 后注入执行，用来模拟一次真实点击。
    """
    import webview

    # 1366x768 这类小屏上，固定 900 高会顶出屏幕，按屏幕比例收缩
    sw, sh = _screen_size()
    w = min(1220, max(940, int(sw * 0.84)))
    h = min(900, max(640, int(sh * 0.86)))

    win = webview.create_window(
        WIN_TITLE,
        url,
        width=w,
        height=h,
        min_size=(min(940, w), min(640, h)),
        resizable=True,
        text_select=False,
        confirm_close=False,
        background_color="#f4f7fc",
    )

    def watchdog():
        time.sleep(3.0)
        rep = {"ok": False, "checks": []}
        try:
            for name, js in (
                ("readyState", "document.readyState"),
                ("行数", "document.querySelectorAll('#sessionList .row').length"),
                ("选中数文本", "(document.getElementById('selCount')||{}).textContent"),
                ("主机标签", "(document.getElementById('hostChip')||{}).textContent"),
                ("标题", "document.title"),
                ("首行文字", "(document.querySelector('#sessionList .rtitle')||{}).textContent"),
                ("页签", "(document.querySelector('.seg.active')||{}).getAttribute('data-tab')"),
                ("导入路径", "(document.getElementById('importPath')||{}).value"),
                ("包卡片可见", "!(document.getElementById('pkgCard')||{}).hidden"),
                ("包内对话数", "(document.getElementById('pkgCount')||{}).textContent"),
            ):
                try:
                    rep["checks"].append({"name": name, "value": win.evaluate_js(js)})
                except Exception as e:
                    rep["checks"].append({"name": name, "error": str(e)})
            rep["ok"] = all("error" not in c for c in rep["checks"])

            if extra_js:
                rep["injected"] = win.evaluate_js(extra_js)
                time.sleep(wait)
                rep["after"] = {
                    "status": win.evaluate_js("(document.getElementById('statusChip')||{}).textContent"),
                    "console": win.evaluate_js("(document.getElementById('console')||{}).innerText"),
                    "finish": win.evaluate_js("(document.getElementById('finishBox')||{}).innerText"),
                }
        except Exception:
            rep["error"] = traceback.format_exc(limit=3)
        with open(probe, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        try:
            win.destroy()
        except Exception:
            pass

    if probe:
        threading.Thread(target=watchdog, daemon=True).start()

    # 用系统自带的 Edge 内核渲染，不额外下载任何运行时
    webview.start(gui="edgechromium", debug=False)
    return True


def selftest(out_path):
    """给自动测试用：把后端跑一遍并写结果，不开窗口。"""
    import urllib.request
    rep = {"ok": False, "steps": []}

    def step(name, fn):
        try:
            v = fn()
            rep["steps"].append({"name": name, "ok": True, "value": v})
            return v
        except Exception as e:
            rep["steps"].append({"name": name, "ok": False,
                                 "error": "%s: %s" % (type(e).__name__, e)})
            raise

    try:
        httpd, port = serve("selftest-token")
        rep["port"] = port
        step("服务监听", lambda: wait_ready(port))

        def get_state():
            req = urllib.request.Request("http://127.0.0.1:%d/api/state" % port,
                                         headers={"X-WB-Token": "selftest-token"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
            return {"ok": d.get("ok"), "sessions": len(d.get("sessions") or []),
                    "wb_home": d.get("wb_home"), "host": d.get("host"),
                    "app_running": d.get("app_running")}
        step("会话列表接口", get_state)

        def get_ui():
            with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=15) as r:
                body = r.read()
            return {"status": r.status, "bytes": len(body),
                    "has_markup": b"<!DOCTYPE html" in body[:200]}
        step("界面文件", get_ui)

        def no_token():
            req = urllib.request.Request("http://127.0.0.1:%d/api/state" % port)
            try:
                urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as e:
                return {"blocked": e.code == 403}
            return {"blocked": False}
        step("未授权请求被拒", no_token)

        httpd.shutdown()
        rep["ok"] = all(s["ok"] for s in rep["steps"])
    except Exception:
        rep["error"] = traceback.format_exc(limit=4)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    return 0 if rep["ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wb-migrate", add_help=False)
    ap.add_argument("--selftest", metavar="OUT", default=None)
    ap.add_argument("--check", metavar="OUT", default=None, help="开窗口读 DOM 后自关，用于验证")
    ap.add_argument("--js", default=None, help="配合 --check 注入一段脚本，模拟真实点击")
    ap.add_argument("--wait", type=float, default=9.0, help="注入脚本后等待多少秒再收结果")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--browser", action="store_true", help="改用默认浏览器打开")
    ap.add_argument("--version", action="store_true")
    a, _unknown = ap.parse_known_args(argv)

    if a.version:
        print("wb-migrate " + APP_VERSION)
        return 0
    if a.selftest:
        return selftest(a.selftest)

    # 单文件包：这个 exe 尾部带着一个迁移包，直接停在「接收进来」
    global WIN_TITLE
    pack = _embedded_pack()
    gui.PRESET_PACKAGE = pack
    if pack:
        WIN_TITLE = APP_NAME + " · 迁移包"

    if not _single_instance():
        if pack:
            _msgbox("迁移工具已经开着一个窗口了。\n\n"
                    "先把那个窗口关掉，再双击这个包，包才会读进来。", APP_NAME, 0x40)
        else:
            _msgbox("迁移工具已经开着了，看看任务栏。", APP_NAME, 0x40)
        return 0

    import secrets
    token = secrets.token_urlsafe(18)

    try:
        httpd, port = serve(token, a.port)
    except Exception as e:
        _log("启动失败：" + traceback.format_exc())
        _fatal("启动失败：%s" % e)
        return 1

    wait_ready(port)
    url = "http://127.0.0.1:%d/?t=%s" % (port, token)
    _log("启动 端口=%d 版本=%s 冻结=%s 包=%s"
         % (port, APP_VERSION, bool(getattr(sys, "frozen", False)), pack or "-"))

    if not a.browser:
        try:
            run_window(url, probe=a.check, extra_js=a.js, wait=a.wait)
            _log("窗口正常关闭")
            return 0
        except Exception:
            _log("窗口启动失败，回退到浏览器：" + traceback.format_exc())

    # 回退：WebView2 不可用或被策略禁用时，至少还能用浏览器干活
    _clipboard(url)
    webbrowser.open(url)
    _msgbox("这台机器上无法创建程序窗口，已改用浏览器打开。\n\n"
            "地址已经复制到剪贴板，粘进浏览器也行：\n%s\n\n用完关掉浏览器标签即可。" % url,
            APP_NAME, 0x40)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        _log("未捕获异常：" + traceback.format_exc())
        raise
