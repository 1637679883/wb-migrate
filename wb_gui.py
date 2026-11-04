#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb-gui — wb-migrate 的图形界面

启动一个只监听 127.0.0.1 的本地网页服务，用浏览器当界面。
所有迁移动作仍然走 wb_migrate.py 里已经验证过的函数，本文件只负责
「拿到参数 -> 调用 -> 把输出流回页面」，不重复实现任何迁移逻辑。

只依赖 Python 标准库。
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import wb_migrate as core
except ImportError:
    sys.stderr.write("找不到 wb_migrate.py，请把 wb_gui.py 和它放在同一个目录里。\n")
    sys.exit(1)

GUI_VERSION = "1.0.0"

UI_FILE = os.path.join(HERE, "ui.html")

# 任务表：一次只允许跑一个迁移任务（避免两个界面同时写数据库）
JOBS = {}
JOBS_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()

STATE_CACHE = {"at": 0, "key": None, "data": None}
STATE_TTL = 25

# 如果程序本体里带着迁移包（单文件 exe 包），启动时由 wb_app 填进来：
# 界面一打开就停在「接收进来」，并且把包预先读好。
PRESET_PACKAGE = ""


# ---------------------------------------------------------------- 任务与日志

class JobWriter(object):
    """把 core.out() 的打印内容收集成行，供前端轮询。

    刻意不继承 io.TextIOBase：它的 encoding 是只读属性，赋值会直接抛异常，
    而 print() 只需要 write/flush/isatty 三个方法。
    """

    encoding = "utf-8"
    errors = "replace"

    # 命令行里「拷到另一台电脑后运行 wb-migrate import ...」这类提示，在图形
    # 界面里是误导：用户根本不会开命令行。这里换成人话，并丢掉命令本身。
    CLI_NEXT = "下一步：把这个文件拷贝到另一台电脑，然后运行"
    GUI_NEXT = "下一步：把这个文件拷到另一台电脑，在那边打开本程序，用「接收进来」导入。"

    def __init__(self, job):
        self.job = job
        self.buf = ""
        self.skip_next = False

    def _clean(self, line):
        if self.skip_next:
            self.skip_next = False
            if line.startswith("  wb-migrate "):
                return None
        if line.startswith(self.CLI_NEXT):
            self.skip_next = True
            return self.GUI_NEXT
        if line.startswith("  wb-migrate "):
            return None
        return line

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = self._clean(line)
            if line is not None:
                self.job["lines"].append(line)
        if len(self.job["lines"]) > 4000:
            del self.job["lines"][:1500]
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def start_job(kind, title, fn):
    job = {
        "id": secrets.token_hex(8),
        "kind": kind,
        "title": title,
        "status": "running",
        "lines": [],
        "error": "",
        "result": None,
        "started": time.time(),
        "ended": None,
    }
    with JOBS_LOCK:
        JOBS[job["id"]] = job
        # 只保留最近 20 个任务记录
        if len(JOBS) > 20:
            for k in sorted(JOBS, key=lambda x: JOBS[x]["started"])[:-20]:
                JOBS.pop(k, None)
    threading.Thread(target=_run_job, args=(job, fn), daemon=True).start()
    return job["id"]


def _run_job(job, fn):
    if not RUN_LOCK.acquire(blocking=False):
        job["status"] = "error"
        job["error"] = "已经有任务在运行，请等它结束再操作。"
        job["ended"] = time.time()
        return

    old_out = sys.stdout
    writer = None
    try:
        writer = JobWriter(job)
        sys.stdout = writer
        try:
            fn(job)
            job["status"] = "done"
        except SystemExit as e:
            job["status"] = "error" if e.code not in (0, None) else "done"
            if job["status"] == "error":
                job["error"] = _last_error(job)
        except Exception:
            job["status"] = "error"
            job["error"] = traceback.format_exc(limit=3).strip().splitlines()[-1]
            job["lines"].append("[异常] " + job["error"])
        finally:
            writer.flush()
            if writer.buf.strip():
                job["lines"].append(writer.buf.rstrip())
                writer.buf = ""
    except Exception:
        # 连日志收集器都没建起来，也要保证任务不卡在「运行中」
        job["status"] = "error"
        job["error"] = traceback.format_exc(limit=3).strip().splitlines()[-1]
    finally:
        sys.stdout = old_out
        job["ended"] = time.time()
        RUN_LOCK.release()


def _last_error(job):
    for line in reversed(job["lines"]):
        if line.startswith("[错误]"):
            return line.replace("[错误]", "").strip()
    return "操作失败，详见下方日志。"


def job_view(job, since=0):
    lines = job["lines"]
    return {
        "id": job["id"],
        "title": job["title"],
        "status": job["status"],
        "error": job["error"],
        "result": job["result"],
        "lines": lines[since:],
        "next": len(lines),
        "elapsed": round((job["ended"] or time.time()) - job["started"], 1),
    }


# ---------------------------------------------------------------- 数据组装

def human_size(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def ts_text(ms):
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000.0).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return ""


def default_export_path():
    ext = ".zip"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    for name in ("Downloads", "下载"):
        d = os.path.join(os.path.expanduser("~"), name)
        if os.path.isdir(d):
            return os.path.join(d, "workbuddy-迁移-%s%s" % (stamp, ext))
    return os.path.join(os.path.expanduser("~"), "workbuddy-迁移-%s%s" % (stamp, ext))


def build_state(include_deleted=False):
    key = bool(include_deleted)
    now = time.time()
    if (STATE_CACHE["data"] is not None and STATE_CACHE["key"] == key
            and now - STATE_CACHE["at"] < STATE_TTL):
        return STATE_CACHE["data"]

    try:
        items = core.list_sessions(include_deleted=include_deleted)
    except SystemExit:
        raise
    sessions = []
    for s in items:
        sessions.append({
            "id": s["id"],
            "title": s["title"],
            "cwd": s["cwd"],
            "updated": s["last_activity_at"],
            "updated_text": ts_text(s["last_activity_at"]),
            "lines": s["lines"],
            "bytes": s["transcript_bytes"],
            "size_text": human_size(s["transcript_bytes"]),
            "ws_exists": s["workspace_exists"],
            "ws_size": s["workspace_bytes"],
            "ws_size_text": human_size(s["workspace_bytes"]),
            "aux": sorted(s["aux"].keys()) if s["aux"] else [],
            "has_transcript": bool(s["transcript"]),
            "deleted": s["deleted"],
        })

    data = {
        "wb_home": core.wb_home(),
        "home": os.path.expanduser("~"),
        "app_running": core.is_app_running(),
        "host": core.machine_name(),
        "python": sys.version.split()[0],
        "sessions": sessions,
        "default_export": default_export_path(),
        "preset_package": PRESET_PACKAGE or "",
        "default_root": os.path.join(os.path.expanduser("~"), "WorkBuddy"),
        "gui_version": GUI_VERSION,
        "tool_version": core.VERSION,
    }
    STATE_CACHE.update({"at": now, "key": key, "data": data})
    return data


def inspect_package(path):
    """读包内的 manifest，返回结构化摘要（不改动任何文件）。"""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ValueError("文件不存在：%s" % path)
    import zipfile
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        if path.lower().endswith(".exe"):
            raise ValueError("这个 exe 里没有迁移包。如果是工具本体，请打开它再去「接收进来」选包。")
        raise ValueError("这个文件不是压缩包，选 .zip 或带包的 .exe 都可以。")
    with zf:
        try:
            raw = zf.read(core.MANIFEST_NAME)
        except KeyError:
            raise ValueError("这不是 wb-migrate 的迁移包（缺少 manifest.json）。")
        try:
            m = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ValueError("包内的 manifest.json 解析失败，文件可能已损坏。")
        if m.get("format") != core.FORMAT_NAME:
            raise ValueError("包格式不匹配：%s" % m.get("format"))
        total = sum(i.file_size for i in zf.infolist())

    con = core.connect(True)
    try:
        existing = {r["id"] for r in con.execute("SELECT id FROM sessions")}
    finally:
        con.close()

    src = m.get("source") or {}
    sessions = []
    for s in m.get("sessions") or []:
        w = s.get("workspace") or {}
        sessions.append({
            "id": s.get("id"),
            "title": s.get("title") or "(无标题)",
            "cwd": s.get("cwd") or "",
            "messages": s.get("message_count") or 0,
            "ws_included": bool(w.get("included")),
            "ws_files": w.get("files") or 0,
            "ws_bytes_text": human_size(w.get("bytes")),
            "aux": [a.get("kind") for a in (s.get("aux") or [])],
            "conflict": s.get("id") in existing,
        })

    return {
        "package": path,
        "size_text": human_size(os.path.getsize(path)),
        "unpacked_text": human_size(total),
        "created_at": m.get("created_at") or "",
        "tool_version": m.get("tool_version") or "",
        "source_host": src.get("host") or "未知",
        "source_os": src.get("os") or "",
        "source_home": src.get("home") or "",
        "sessions": sessions,
        "conflicts": sum(1 for s in sessions if s["conflict"]),
        "same_home": bool(src.get("home")) and core.norm_path(src.get("home")) == core.norm_path(os.path.expanduser("~")),
    }


# ---------------------------------------------------------------- 系统对话框

PICK_TK = '''# -*- coding: utf-8 -*-
import sys
mode, out_path = sys.argv[1], sys.argv[2]
initial = sys.argv[3] if len(sys.argv) > 3 else ""
res = ""
try:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    if mode == "open":
        res = filedialog.askopenfilename(
            title="选择迁移包",
            filetypes=[("迁移包", "*.zip"), ("所有文件", "*.*")])
    elif mode == "save":
        d, f = initial.rsplit("\\\\", 1) if "\\\\" in initial else ("", initial)
        res = filedialog.asksaveasfilename(
            title="迁移包保存到",
            defaultextension=".zip",
            initialdir=d or None,
            initialfile=f or None,
            filetypes=[("压缩包", "*.zip")])
    else:
        res = filedialog.askdirectory(title="选择目录", initialdir=initial or None)
    root.destroy()
except Exception:
    res = ""
with open(out_path, "w", encoding="utf-8") as f:
    f.write(res or "")
'''

# Windows 自带 Python 常常没有 tkinter，用系统原生的 PowerShell 对话框兜底。
PICK_PS = '''param([string]$Mode = "open",[string]$Out = "",[string]$Initial = "")
$ErrorActionPreference = "Stop"

if ($Mode -eq "selftest") {
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $t = [System.Windows.Forms.FolderBrowserDialog].FullName
  [System.IO.File]::WriteAllText($Out, "SELFTEST-OK " + $t, [System.Text.Encoding]::UTF8)
  exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Width = 1
$owner.Height = 1
$owner.StartPosition = "CenterScreen"
$owner.Show()

$result = ""
try {
  if ($Mode -eq "open") {
    $d = New-Object System.Windows.Forms.OpenFileDialog
    $d.Title = "选择迁移包"
    $d.Filter = "迁移包 (*.zip)|*.zip|所有文件 (*.*)|*.*"
    if ($Initial -and (Test-Path (Split-Path $Initial -Parent))) {
      $d.InitialDirectory = (Split-Path $Initial -Parent)
    }
    if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $result = $d.FileName }
  } elseif ($Mode -eq "save") {
    $d = New-Object System.Windows.Forms.SaveFileDialog
    $d.Title = "迁移包保存到"
    $d.Filter = "压缩包 (*.zip)|*.zip"
    $d.FilterIndex = 1
    $d.DefaultExt = "zip"
    $d.AddExtension = $true
    if ($Initial) {
      $p = Split-Path $Initial -Parent
      if ($p -and (Test-Path $p)) { $d.InitialDirectory = $p }
      $d.FileName = (Split-Path $Initial -Leaf)
    }
    if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $result = $d.FileName }
  } else {
    $d = New-Object System.Windows.Forms.FolderBrowserDialog
    $d.Description = "选择目录"
    $d.ShowNewFolderButton = $true
    if ($Initial -and (Test-Path $Initial)) { $d.SelectedPath = $Initial }
    if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $result = $d.SelectedPath }
  }
} finally {
  $owner.Close()
}

[System.IO.File]::WriteAllText($Out, $result, [System.Text.Encoding]::UTF8)
'''


def _has_tkinter():
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def _run_dialog_helper(args, out_file):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.run(args, timeout=900, creationflags=flags,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.isfile(out_file):
        with open(out_file, "r", encoding="utf-8-sig") as f:
            return f.read().strip()
    return ""


def pick_path(kind, initial=""):
    """弹系统文件对话框。tkinter 不可用时（Windows 上很常见）改用 PowerShell。"""
    tmp_dir = tempfile.mkdtemp(prefix="wbgui-")
    out = os.path.join(tmp_dir, "out.txt")
    try:
        if _has_tkinter():
            script = os.path.join(tmp_dir, "pick.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write(PICK_TK)
            got = _run_dialog_helper([sys.executable, script, kind, out, initial or ""], out)
            if got:
                return got

        if os.name == "nt":
            script = os.path.join(tmp_dir, "pick.ps1")
            with open(script, "w", encoding="utf-8-sig") as f:
                f.write(PICK_PS)
            for exe in ("powershell", "pwsh"):
                try:
                    cmd = [exe, "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
                           "-File", script, "-Mode", kind, "-Out", out]
                    # PowerShell 把空字符串当成「参数缺值」，没有初值就干脆不传
                    if initial:
                        cmd += ["-Initial", initial]
                    got = _run_dialog_helper(cmd, out)
                    if got:
                        return got
                except (OSError, subprocess.SubprocessError):
                    continue
        return ""
    except Exception:
        return ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def reveal_path(path):
    path = os.path.abspath(path)
    try:
        if os.name == "nt":
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", path])
            else:
                os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path) if os.path.isfile(path) else path])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 业务动作

def do_export(job, ids, include_workspace, out_path):
    job["result"] = {"out": out_path}
    args = argparse.Namespace(
        session=",".join(ids),
        out=out_path,
        no_workspace=not include_workspace,
        max_file_mb=core.MAX_FILE_MB_DEFAULT,
        include_cloud=False,
        include_deleted=False,
    )
    final = core.cmd_export(args) or out_path
    final = os.path.abspath(final)
    job["result"] = {"out": final,
                     "size_text": human_size(os.path.getsize(final))}


def do_import(job, package, root, overwrite, force, new_id, no_workspace, allow_running):
    args = argparse.Namespace(
        package=package,
        yes=True,
        workspace_root=root,
        keep_cwd=False,
        force=force,
        new_id=new_id,
        no_workspace=no_workspace,
        overwrite_files=overwrite,
        map=None,
        allow_running=allow_running,
    )
    core.cmd_import(args)
    STATE_CACHE["at"] = 0          # 会话表变了，让列表缓存立刻失效
    job["result"] = {"root": root}


def do_doctor(job):
    core.cmd_doctor(argparse.Namespace())


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = "wb-gui/" + GUI_VERSION
    token = ""

    def log_message(self, *a):
        pass

    # ---- 工具
    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _guard(self):
        if self.headers.get("X-WB-Token") != self.token:
            self._json({"ok": False, "error": "令牌无效，请从启动时给出的地址重新打开。"}, 403)
            return False
        return True

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return {}

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            if not os.path.isfile(UI_FILE):
                self._send(500, "缺少 ui.html，请确认它和 wb_gui.py 在同一个目录。", "text/plain; charset=utf-8")
                return
            with open(UI_FILE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
            return
        if u.path == "/api/state":
            if not self._guard():
                return
            try:
                data = build_state(include_deleted=bool(q.get("deleted")))
                data["ok"] = True
                self._json(data)
            except SystemExit:
                self._json({"ok": False, "error": "读不到 WorkBuddy 数据目录。"}, 500)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return
        if u.path == "/api/job":
            if not self._guard():
                return
            jid = (q.get("id") or [""])[0]
            since = int((q.get("since") or ["0"])[0] or 0)
            job = JOBS.get(jid)
            if not job:
                self._json({"ok": False, "error": "任务不存在（可能已过期）。"}, 404)
                return
            v = job_view(job, since)
            v["ok"] = True
            self._json(v)
            return
        if u.path == "/api/doctor":
            if not self._guard():
                return
            jid = start_job("doctor", "环境自检", do_doctor)
            self._json({"ok": True, "job": jid})
            return
        self._json({"ok": False, "error": "未知路径"}, 404)

    # ---- POST
    def do_POST(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        data = self._body()

        if u.path == "/api/pick":
            kind = data.get("kind") or "open"
            initial = data.get("initial") or ""
            self._json({"ok": True, "path": pick_path(kind, initial)})
            return

        if u.path == "/api/reveal":
            self._json({"ok": reveal_path(data.get("path") or "")})
            return

        if u.path == "/api/inspect":
            try:
                info = inspect_package(data.get("package") or "")
                info["ok"] = True
                self._json(info)
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                self._json({"ok": False, "error": "读取失败：%s" % e}, 500)
            return

        if u.path == "/api/export":
            ids = data.get("ids") or []
            out = (data.get("out") or "").strip()
            if not ids:
                self._json({"ok": False, "error": "先选一个会话。"}, 400)
                return
            if not out:
                self._json({"ok": False, "error": "请填写保存位置。"}, 400)
                return
            if out.lower().endswith(".exe"):
                out = out[:-4] + ".zip"
            elif not out.lower().endswith(".zip"):
                out += ".zip"
            os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
            jid = start_job("export", "打包 %d 个会话" % len(ids),
                            lambda job: do_export(job, ids,
                                                  bool(data.get("include_workspace", True)),
                                                  out))
            self._json({"ok": True, "job": jid})
            return

        if u.path == "/api/import":
            pkg = (data.get("package") or "").strip()
            root = (data.get("root") or "").strip() or os.path.join(os.path.expanduser("~"), "WorkBuddy")
            if not pkg:
                self._json({"ok": False, "error": "先选择迁移包。"}, 400)
                return
            jid = start_job("import", "导入迁移包",
                            lambda job: do_import(
                                job, pkg, root,
                                bool(data.get("overwrite")),
                                bool(data.get("force")),
                                bool(data.get("new_id")),
                                bool(data.get("no_workspace")),
                                True))
            self._json({"ok": True, "job": jid})
            return

        if u.path == "/api/doctor":
            jid = start_job("doctor", "环境自检", do_doctor)
            self._json({"ok": True, "job": jid})
            return

        self._json({"ok": False, "error": "未知路径"}, 404)


def free_port(preferred=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", preferred))
        return s.getsockname()[1]
    finally:
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wb-gui", description="wb-migrate 图形界面")
    ap.add_argument("--port", type=int, default=0, help="指定端口（默认随机空闲端口）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--version", action="version", version="wb-gui " + GUI_VERSION)
    a = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    if not os.path.isfile(UI_FILE):
        sys.stderr.write("缺少 ui.html，请把它和 wb_gui.py 放在同一个目录。\n")
        return 1

    Handler.token = os.environ.get("WB_GUI_TOKEN") or secrets.token_urlsafe(18)
    port = free_port(a.port)
    url = "http://127.0.0.1:%d/?t=%s" % (port, Handler.token)

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        sys.stderr.write("端口 %d 无法监听：%s\n" % (port, e))
        return 1
    httpd.daemon_threads = True

    print("")
    print("  wb-migrate 界面已启动")
    print("  地址：%s" % url)
    print("")
    print("  浏览器没自动弹出来的话，把上面这行地址粘进浏览器。")
    print("  用完直接关掉这个窗口即可，不会在后台留东西。")
    print("")
    sys.stdout.flush()
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
