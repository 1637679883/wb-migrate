#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb-migrate — WorkBuddy 对话跨机器迁移工具

同一账号、不同电脑之间迁移一整个会话：对话记录（transcript）、
会话附属数据（文件历史 / 变更记录 / 产物索引 / 任务清单 / 任务备份）
以及该会话对应的工作空间目录文件。

只依赖 Python 3.8+ 标准库。用法见 README.md 或运行 --help。

WorkBuddy 本地数据结构（本工具所依赖的事实）：
  <home>/workbuddy.db                        SQLite，sessions 表存会话元数据
  <home>/projects/<encoded-cwd>/<sid>.jsonl  对话正文，一行一条记录
  <home>/projects/<encoded-cwd>/<sid>.meta.json
  <home>/projects/<encoded-cwd>/<sid>.file-rollback.ndjson
  <home>/file-history/<sid>/                 文件快照（撤销用）
  <home>/changes-index/<sid>.json            变更索引
  <home>/changes-detail/<sid>/               变更明细
  <home>/artifact-index/<sid>.json           产物索引
  <home>/file-tree-manifests/<sid>.json      文件树清单
  <home>/tasks/<sid>/                        任务清单
  <home>/workspace/sessions/<sid>/           会话内文件修改备份
  <cwd>/                                     工作空间目录（含 .workbuddy/memory）
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
import hashlib
from datetime import datetime, timezone

VERSION = "1.0.0"
FORMAT_NAME = "workbuddy-migration"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"

# 打包时默认忽略的系统垃圾文件
JUNK_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}

# 会话级辅助数据的目录型 / 文件型资源
AUX_DIRS = [
    ("file-history", "file-history"),
    ("tasks", "tasks"),
    ("workspace-sessions", "workspace/sessions"),
    ("changes-detail", "changes-detail"),
]
AUX_FILES = [
    ("changes-index", "changes-index", ".json"),
    ("artifact-index", "artifact-index", ".json"),
    ("file-tree-manifests", "file-tree-manifests", ".json"),
]

MAX_FILE_MB_DEFAULT = 300


# ---------------------------------------------------------------- 基础工具

def out(msg=""):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


def machine_name():
    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME")
    if not name and hasattr(os, "uname"):
        try:
            name = os.uname().nodename
        except OSError:
            name = None
    return name or "unknown"


def short_title(t, width=38):
    t = (t or "").replace("\n", " ").strip()
    return t if len(t) <= width else t[:width] + "…"


def human_size(n):
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return "%d B" % n
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def ts_to_str(ms):
    if not ms:
        return "-"
    try:
        return datetime.fromtimestamp(ms / 1000.0).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return "-"


def wb_home():
    """WorkBuddy 用户级数据目录。可用 WORKBUDDY_HOME 覆盖（测试/多装场景）。"""
    env = os.environ.get("WORKBUDDY_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".workbuddy")


def db_file():
    return os.path.join(wb_home(), "workbuddy.db")


def connect(readonly=True):
    path = db_file()
    if not os.path.isfile(path):
        die("找不到 WorkBuddy 数据库：%s" % path)
    if readonly:
        uri = "file:%s?mode=ro" % path.replace("\\", "/").replace("?", "%3f").replace("#", "%23")
        con = sqlite3.connect(uri, uri=True, timeout=15)
    else:
        con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def die(msg, code=1):
    out("")
    out("[错误] " + msg)
    sys.exit(code)


def warn(msg):
    out("[注意] " + msg)


# ---------------------------------------------------------------- 路径编码

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def encode_cwd(path):
    """把工作空间绝对路径编码成 projects/ 下的目录名。

    C:\\Users\\Akina23\\WorkBuddy\\2026-09-08-21-49-06
      -> c-Users-Akina23-WorkBuddy-2026-09-08-21-49-06
    规则：盘符小写、去掉冒号、分隔符统一替换为 '-'
    （已在本机 10 个真实会话上验证 10/10 命中）
    """
    p = str(path).replace("\\", "/").strip()
    if _DRIVE_RE.match(p):
        p = p[0].lower() + p[2:]
    p = p.replace(":", "").strip("/")
    return p.replace("/", "-")


def projects_dir():
    return os.path.join(wb_home(), "projects")


def find_transcript(sid, cwd):
    """定位会话 JSONL：先按 cwd 编码猜，再全盘扫一遍（防编码规则变化）。"""
    if cwd:
        cand = os.path.join(projects_dir(), encode_cwd(cwd), sid + ".jsonl")
        if os.path.isfile(cand):
            return cand
    root = projects_dir()
    if os.path.isdir(root):
        for name in os.listdir(root):
            cand = os.path.join(root, name, sid + ".jsonl")
            if os.path.isfile(cand):
                return cand
    return None


def project_dir_for(cwd):
    """导入时决定 JSONL 该落到哪个项目目录。

    优先复用磁盘上已存在的同 cwd 目录（这样完全绕开编码规则的差异），
    否则用 encode_cwd 计算。
    """
    enc = encode_cwd(cwd)
    guess = os.path.join(projects_dir(), enc)
    if os.path.isdir(guess):
        return guess
    try:
        con = connect(True)
    except SystemExit:
        return guess
    try:
        for row in con.execute("SELECT cwd FROM sessions"):
            if row["cwd"] and norm_path(row["cwd"]) == norm_path(cwd):
                cand = os.path.join(projects_dir(), encode_cwd(row["cwd"]))
                if os.path.isdir(cand):
                    return cand
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return guess


def norm_path(p):
    return str(p).replace("\\", "/").rstrip("/").lower()


# ---------------------------------------------------------------- 会话读取

def local_user_id(con):
    try:
        row = con.execute(
            "SELECT user_id, COUNT(*) c FROM sessions WHERE user_id IS NOT NULL "
            "AND user_id <> '' GROUP BY user_id ORDER BY c DESC LIMIT 1"
        ).fetchone()
        if row:
            return row["user_id"]
    except sqlite3.Error:
        pass
    return None


def count_lines(path):
    n = 0
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                n += chunk.count(b"\n")
    except OSError:
        return 0
    return n


def aux_summary(sid):
    """统计会话附属数据，返回 {kind: path, ...}"""
    home = wb_home()
    found = {}
    for kind, rel in AUX_DIRS:
        p = os.path.join(home, rel, sid)
        if os.path.isdir(p):
            found[kind] = p
    for kind, rel, ext in AUX_FILES:
        p = os.path.join(home, rel, sid + ext)
        if os.path.isfile(p):
            found[kind] = p
    return found


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def list_sessions(include_cloud=False, include_deleted=False):
    con = connect(True)
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
        sql = "SELECT * FROM sessions"
        rows = [dict(r) for r in con.execute(sql)]
    finally:
        con.close()
    items = []
    for row in rows:
        if row.get("deleted_at") and not include_deleted:
            continue
        transport = row.get("transport") or "local"
        if transport != "local" and not include_cloud:
            continue
        sid = row["id"]
        cwd = row.get("cwd") or ""
        jl = find_transcript(sid, cwd) if transport == "local" else None
        items.append({
            "id": sid,
            "title": row.get("custom_title") or row.get("title") or "(无标题)",
            "cwd": cwd,
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "last_activity_at": row.get("last_activity_at") or row.get("updated_at"),
            "transport": transport,
            "status": row.get("status"),
            "deleted": bool(row.get("deleted_at")),
            "model": row.get("model"),
            "transcript": jl,
            "lines": count_lines(jl) if jl else 0,
            "transcript_bytes": os.path.getsize(jl) if jl else 0,
            "workspace_exists": bool(cwd) and os.path.isdir(cwd),
            "workspace_bytes": dir_size(cwd) if cwd and os.path.isdir(cwd) else 0,
            "aux": aux_summary(sid),
            "row": row,
        })
    items.sort(key=lambda x: x.get("last_activity_at") or x.get("updated_at") or 0, reverse=True)
    return items


def _process_snapshot():
    """返回 (进程名小写集合, pid 集合)。取不到就返回两个空集合。

    Windows 走 CreateToolhelp32Snapshot（纯 ctypes，比 tasklist 快且不受 PATH 影响），
    其他平台用 ps。
    """
    names, pids = set(), set()
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _PROCESSENTRY32(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_char * 260),
                ]

            k32 = ctypes.windll.kernel32
            snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
            if snap and snap != -1:
                try:
                    pe = _PROCESSENTRY32()
                    pe.dwSize = ctypes.sizeof(_PROCESSENTRY32)
                    ok = k32.Process32First(snap, ctypes.byref(pe))
                    while ok:
                        pids.add(int(pe.th32ProcessID))
                        names.add(pe.szExeFile.decode("mbcs", "replace").lower())
                        ok = k32.Process32Next(snap, ctypes.byref(pe))
                finally:
                    k32.CloseHandle(snap)
        except Exception:
            pass
    else:
        try:
            import subprocess
            raw = subprocess.check_output(["ps", "-A", "-o", "pid=,comm="],
                                          text=True, stderr=subprocess.DEVNULL)
            for line in raw.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    pids.add(int(parts[0]))
                    names.add(os.path.basename(parts[1]).lower())
        except Exception:
            pass
    return names, pids


# 进程名里出现这些片段就认为 WorkBuddy 在跑
APP_PROCESS_HINTS = ("workbuddy", "codebuddy")


def is_app_running():
    """判断 WorkBuddy 是否正在运行。

    以进程为准：WorkBuddy 只在会话启动时写一次 sessions/*.json 心跳，
    之后整个运行期间都不再刷新，所以拿心跳时间窗判断会把开着的应用误判成
    「已退出」（实测开着时心跳已经过去 9 分钟）。进程列表拿不到时，才退回
    心跳的进程号与时间窗。
    """
    names, _pids = _process_snapshot()
    if names:
        for n in names:
            for hint in APP_PROCESS_HINTS:
                if hint in n:
                    return True
        return False

    # 拿不到进程列表：退回心跳文件
    d = os.path.join(wb_home(), "sessions")
    if not os.path.isdir(d):
        return False
    now = time.time() * 1000
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                hb = json.load(f).get("lastHeartbeat") or 0
        except (OSError, ValueError):
            continue
        if now - hb < 120000:
            return True
    return False


# ---------------------------------------------------------------- 路径重写

def build_maps(pairs):
    """生成路径替换表，返回 [(字面量, 替换文本), ...]，按长度倒序。

    对每个 Windows 路径同时生成 3 种写法：原样、正斜杠、JSON 转义反斜杠，
    这样无论对话记录里写的是 C:\\Users\\x 还是 C:/Users/x、无论 --map
    传进来的是哪种斜杠，都能命中。
    """
    table = []
    seen = set()
    for old, new in pairs:
        if not old or not new:
            continue
        if norm_path(old) == norm_path(new):
            continue
        if _DRIVE_RE.match(old) or _DRIVE_RE.match(new):
            o = str(old).replace("/", "\\")
            n = str(new).replace("/", "\\")
            variants = [
                (o.replace("\\", "\\\\"), n.replace("\\", "\\\\")),
                (o, n),
                (o.replace("\\", "/"), n.replace("\\", "/")),
            ]
        else:
            variants = [(str(old), str(new))]
        for a, b in variants:
            if not a or a == b or a in seen:
                continue
            seen.add(a)
            table.append((a, b))
    table.sort(key=lambda x: len(x[0]), reverse=True)
    return table


def rewrite_text(text, table):
    """单遍替换：命中的片段不会被后续规则再次改写，避免新路径被连锁替换。"""
    if not table:
        return text
    flags = re.IGNORECASE if any(_DRIVE_RE.match(a) for a, _ in table) else 0
    pattern = re.compile("|".join(re.escape(a) for a, _ in table), flags)
    exact = dict(table)
    folded = {a.lower(): b for a, b in table}

    def _sub(m):
        s = m.group(0)
        if s in exact:
            return exact[s]
        return folded.get(s.lower(), s)

    return pattern.sub(_sub, text)


# ---------------------------------------------------------------- export

def walk_workspace(root, max_file_bytes, exclude=()):
    """遍历工作空间，返回 (files, skipped)。skipped 记录因体积跳过的大文件。

    exclude 是必须跳过的绝对路径集合——主要是正在写入的压缩包本身。
    导出目标如果被保存到工作空间目录里面，不排除它就会「压缩自己」：
    一边读一边写，包会无限膨胀直到把磁盘写满。
    """
    files, skipped = [], []
    root = os.path.abspath(root)
    skip = {os.path.normcase(os.path.abspath(p)) for p in exclude if p}
    for cur, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git",)]
        for name in names:
            if name in JUNK_FILES:
                continue
            full = os.path.join(cur, name)
            key = os.path.normcase(os.path.abspath(full))
            if key in skip:
                continue
            rel = os.path.relpath(full, root)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if max_file_bytes and size > max_file_bytes:
                skipped.append({"path": rel, "size": size})
                continue
            files.append((full, rel, size))
    return files, skipped


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def has_embedded_package(path):
    """这个 exe 里是否带着一个迁移包（双击它就该进入导入界面）。

    现在只出 zip 包，不再生成这种 exe；此函数保留用于识别早期版本产出的包。
    """
    try:
        if not os.path.isfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            return MANIFEST_NAME in zf.namelist()
    except Exception:
        return False


def cmd_export(args):
    items = list_sessions(include_cloud=args.include_cloud,
                          include_deleted=args.include_deleted)
    if not items:
        die("本机没有可导出的本地会话。")

    targets = select_sessions(items, args.session)
    if not targets:
        die("未选中任何会话。")

    if is_app_running():
        warn("检测到 WorkBuddy 可能正在运行。建议先退出应用再导出，"
             "否则正在写入的会话可能只打包到一半的内容。")

    ext = ".zip"

    out_path = args.out
    if not out_path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if len(targets) == 1:
            slug = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", targets[0]["title"])[:30].strip("_")
            out_path = "wb-%s-%s%s" % (stamp, slug or targets[0]["id"][:8], ext)
        else:
            out_path = "wb-all-%s%s" % (stamp, ext)
    else:
        base = out_path
        if base.lower().endswith((".zip", ".exe")):
            base = base[:-4]
        out_path = base + ext
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    max_bytes = args.max_file_mb * 1024 * 1024
    manifest = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "tool_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "host": machine_name(),
            "os": sys.platform,
            "python": sys.version.split()[0],
            "workbuddy_home": wb_home(),
            "home": os.path.expanduser("~"),
            "user_id": local_user_id(connect(True)),
        },
        "options": {
            "include_workspace": not args.no_workspace,
            "max_file_mb": args.max_file_mb,
        },
        "sessions": [],
    }

    out("正在打包 %d 个会话 -> %s" % (len(targets), out_path))
    tmp = out_path + ".tmp"
    total_files = total_bytes = 0
    exported = 0
    no_transcript = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for idx, s in enumerate(targets, 1):
                sid = s["id"]
                out("")
                out("[%d/%d] %s" % (idx, len(targets), short_title(s["title"])))
                if not s["transcript"]:
                    warn("找不到该会话的对话记录，跳过。")
                    no_transcript += 1
                    continue
                exported += 1

                entry = {
                    "id": sid,
                    "title": s["title"],
                    "cwd": s["cwd"],
                    "created_at": s["created_at"],
                    "updated_at": s["updated_at"],
                    "status": s["status"],
                    "model": s["model"],
                    "message_count": s["lines"],
                    "transcript_sha256": sha256_file(s["transcript"]),
                    "transcript_bytes": s["transcript_bytes"],
                    "db_row": s["row"],
                    "aux": [],
                    "workspace": None,
                }

                zf.write(s["transcript"], "transcript/%s.jsonl" % sid)
                for suffix in (".meta.json", ".file-rollback.ndjson"):
                    p = os.path.splitext(s["transcript"])[0] + suffix
                    if os.path.isfile(p):
                        zf.write(p, "transcript/%s%s" % (sid, suffix))
                total_files += 1
                total_bytes += s["transcript_bytes"]
                out("  对话记录：%d 条，%s" % (s["lines"], human_size(s["transcript_bytes"])))

                # 辅助数据
                for akind, path in s["aux"].items():
                    if os.path.isdir(path):
                        n = 0
                        b = 0
                        for cur, _d, names in os.walk(path):
                            for name in names:
                                full = os.path.join(cur, name)
                                rel = os.path.relpath(full, path)
                                zf.write(full, "aux/%s/%s/%s" % (akind, sid, rel.replace("\\", "/")))
                                n += 1
                                try:
                                    b += os.path.getsize(full)
                                except OSError:
                                    pass
                        entry["aux"].append({"kind": akind, "type": "dir", "files": n, "bytes": b})
                        total_files += n
                        total_bytes += b
                        out("  %-18s %d 个文件，%s" % (akind + ":", n, human_size(b)))
                    else:
                        zf.write(path, "aux/%s/%s%s" % (akind, sid, os.path.splitext(path)[1]))
                        entry["aux"].append({"kind": akind, "type": "file",
                                             "bytes": os.path.getsize(path)})
                        total_files += 1
                        total_bytes += os.path.getsize(path)

                # 工作空间
                if not args.no_workspace and s["cwd"] and os.path.isdir(s["cwd"]):
                    files, skipped = walk_workspace(s["cwd"], max_bytes, exclude=(tmp, out_path))
                    wbytes = sum(x[2] for x in files)
                    total_n = len(files)
                    for i, (full, rel, _size) in enumerate(files, 1):
                        zf.write(full, "workspace/%s/%s" % (sid, rel.replace("\\", "/")))
                        if total_n > 200 and i % 200 == 0:
                            out("    已写入 %d/%d …" % (i, total_n))
                    entry["workspace"] = {
                        "included": True,
                        "root_name": os.path.basename(os.path.normpath(s["cwd"])),
                        "files": len(files),
                        "bytes": wbytes,
                        "skipped": skipped[:50],
                        "skipped_count": len(skipped),
                    }
                    total_files += len(files)
                    total_bytes += wbytes
                    out("  工作空间：%d 个文件，%s" % (len(files), human_size(wbytes)))
                    if skipped:
                        warn("跳过 %d 个超过 %d MB 的大文件（清单见 manifest）"
                             % (len(skipped), args.max_file_mb))
                elif s["cwd"]:
                    warn("工作空间目录已不存在：%s" % s["cwd"])
                    entry["workspace"] = {"included": False, "reason": "missing"}

                manifest["sessions"].append(entry)

            zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

            readme = build_archive_readme(manifest)
            zf.writestr("README.txt", readme)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise

    os.replace(tmp, out_path)
    out("")
    if no_transcript:
        out("完成。实际导出 %d 个会话，%d 个因缺少对话记录被跳过。"
            % (exported, no_transcript))
    else:
        out("完成。导出 %d 个会话。" % exported)
    out("%d 个文件，原始数据 %s，包体积 %s"
        % (total_files, human_size(total_bytes), human_size(os.path.getsize(out_path))))
    out("包路径：%s" % out_path)
    out("")
    out("下一步：把这个文件拷贝到另一台电脑，然后运行")
    out("  wb-migrate import \"%s\"" % os.path.basename(out_path))
    return out_path


def build_archive_readme(manifest):
    lines = [
        "WorkBuddy 会话迁移包",
        "=" * 40,
        "生成时间：%s" % manifest["created_at"],
        "来源机器：%s" % manifest["source"].get("host"),
        "来源用户目录：%s" % manifest["source"].get("workbuddy_home"),
        "",
        "包含会话：",
    ]
    for s in manifest["sessions"]:
        lines.append("  - %s" % short_title(s["title"], 60))
        lines.append("      工作空间：%s" % (s["cwd"] or "-"))
        lines.append("      消息条数：%s" % s["message_count"])
        if s.get("workspace") and s["workspace"].get("included"):
            lines.append("      文件数量：%s" % s["workspace"]["files"])
    lines += [
        "",
        "导入方法（在目标电脑上）：",
        "  打开「工作台迁移.exe」，在「接收进来」里选中这个文件；",
        "  或命令行 wb-migrate import <本压缩包路径>（先预演，再加 --yes 执行）。",
        "",
        "导入前会自动备份目标机的 workbuddy.db。",
        "",
        "包内结构：",
        "  manifest.json          清单与会话元数据",
        "  transcript/<sid>.jsonl 对话正文",
        "  aux/<类型>/<sid>/...   文件历史、变更记录、产物索引、任务清单等",
        "  workspace/<sid>/...    工作空间目录的完整文件",
    ]
    return "\n".join(lines) + "\n"


def select_sessions(items, spec):
    """支持 all / latest / 序号 / id 前缀 / 标题关键字；多个用逗号分隔取并集"""
    if not spec or spec == "all":
        return items if spec == "all" else items
    spec = spec.strip()

    # 逗号分隔的多选（图形界面用；命令行同样可用，如 --session "3,5,7"）
    if "," in spec:
        picked = {}
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            for s in select_sessions(items, part):
                picked[s["id"]] = s
        if not picked:
            die("未选中任何会话。")
        return [s for s in items if s["id"] in picked]

    if spec == "latest":
        return items[:1]
    if spec.isdigit():
        i = int(spec)
        if 1 <= i <= len(items):
            return [items[i - 1]]
        die("序号 %d 超出范围（1-%d）" % (i, len(items)))
    exact = [x for x in items if x["id"] == spec]
    if exact:
        return exact
    pref = [x for x in items if x["id"].startswith(spec)]
    if len(pref) == 1:
        return pref
    if len(pref) > 1:
        die("ID 前缀 %s 匹配到多个会话，请写得更完整。" % spec)
    by_title = [x for x in items if spec.lower() in (x["title"] or "").lower()]
    if len(by_title) == 1:
        return by_title
    if len(by_title) > 1:
        die("标题关键字 %s 匹配到 %d 个会话，请用序号或完整 ID。" % (spec, len(by_title)))
    die("找不到会话：%s" % spec)


# ---------------------------------------------------------------- inspect

def read_manifest(zf):
    try:
        raw = zf.read(MANIFEST_NAME)
    except KeyError:
        die("这不是有效的迁移包：缺少 %s" % MANIFEST_NAME)
    try:
        m = json.loads(raw.decode("utf-8"))
    except ValueError:
        die("manifest.json 解析失败，包可能损坏。")
    if m.get("format") != FORMAT_NAME:
        die("包格式不匹配：%s" % m.get("format"))
    if int(m.get("format_version", 0)) > FORMAT_VERSION:
        warn("包版本 %s 比当前工具（%s）新，部分内容可能无法识别。"
             % (m.get("format_version"), FORMAT_VERSION))
    return m


def cmd_inspect(args):
    if not os.path.isfile(args.package):
        die("文件不存在：%s" % args.package)
    with zipfile.ZipFile(args.package) as zf:
        m = read_manifest(zf)
        out("包格式：%s v%s（工具 %s）" % (m["format"], m["format_version"], m.get("tool_version")))
        out("生成时间：%s" % m.get("created_at"))
        src = m.get("source", {})
        out("来源机器：%s   系统：%s" % (src.get("host"), src.get("os")))
        out("来源目录：%s" % src.get("workbuddy_home"))
        out("")
        out("会话 %d 个：" % len(m["sessions"]))
        for i, s in enumerate(m["sessions"], 1):
            out("")
            out("%d. %s" % (i, short_title(s["title"], 50)))
            out("   ID：%s" % s["id"])
            out("   工作空间：%s" % (s.get("cwd") or "-"))
            out("   消息条数：%s" % s.get("message_count"))
            w = s.get("workspace") or {}
            if w.get("included"):
                out("   工作空间文件：%d 个，%s" % (w.get("files", 0), human_size(w.get("bytes"))))
            else:
                out("   工作空间文件：未包含")
            if s.get("aux"):
                out("   附属数据：%s" % "、".join(a["kind"] for a in s["aux"]))
        total = sum(i.file_size for i in zf.infolist())
        out("")
        out("解压后总大小：%s" % human_size(total))


# ---------------------------------------------------------------- import

def backup_db():
    src = db_file()
    dst = src + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(src, dst)
    return dst


def cmd_import(args):
    pkg = os.path.abspath(args.package)
    if not os.path.isfile(pkg):
        die("文件不存在：%s" % pkg)

    with zipfile.ZipFile(pkg) as zf:
        manifest = read_manifest(zf)
        sessions = manifest["sessions"]
        if not sessions:
            die("包里没有会话。")

        old_home = (manifest.get("source") or {}).get("home") or ""
        new_home = os.path.expanduser("~")
        new_root = os.path.abspath(os.path.expanduser(args.workspace_root)) if args.workspace_root \
            else os.path.join(new_home, "WorkBuddy")

        plan = []
        for s in sessions:
            old_cwd = s.get("cwd") or ""
            if old_cwd:
                base = os.path.basename(os.path.normpath(old_cwd.replace("\\", "/")))
            else:
                base = "wb-import-%s" % s["id"][:8]
            if args.keep_cwd:
                new_cwd = old_cwd
            else:
                new_cwd = os.path.join(new_root, base)
            plan.append({"src": s, "old_cwd": old_cwd, "new_cwd": new_cwd})

        # 冲突检测
        con = connect(True)
        try:
            existing = {r["id"] for r in con.execute("SELECT id FROM sessions")}
        finally:
            con.close()

        out("导入包：%s" % pkg)
        out("目标 WorkBuddy 目录：%s" % wb_home())
        out("目标工作空间根目录：%s" % new_root)
        out("")
        conflicts = 0
        for i, p in enumerate(plan, 1):
            s = p["src"]
            exists = s["id"] in existing
            if exists:
                conflicts += 1
            out("%d. %s" % (i, short_title(s["title"], 50)))
            out("   ID：%s%s" % (s["id"], "   [目标机已存在同名会话]" if exists else ""))
            out("   工作空间：%s" % p["old_cwd"])
            out("        -> %s" % p["new_cwd"])
            w = s.get("workspace") or {}
            if w.get("included"):
                out("   将写入 %d 个工作空间文件（%s）" % (w.get("files", 0), human_size(w.get("bytes"))))
            else:
                out("   工作空间文件未包含，仅迁移对话")
            out("")

        if old_home and norm_path(old_home) != norm_path(new_home):
            warn("两台机器的用户目录不同：%s -> %s，对话内含的绝对路径会被改写。"
                 % (old_home, new_home))
        if conflicts:
            warn("有 %d 个会话在目标机已存在，默认跳过；加 --force 覆盖，加 --new-id 复制成新会话。"
                 % conflicts)

        if not args.yes:
            out("这是预演（dry-run）。确认无误后加 --yes 真正写入：")
            out("  wb-migrate import \"%s\" --yes" % pkg)
            return

        if is_app_running() and not args.force and not getattr(args, "allow_running", False):
            die("WorkBuddy 似乎正在运行。请先完全退出应用（否则数据库会被占用/覆盖），"
                "或加 --force 强行继续。")

        bak = backup_db()
        out("已备份数据库：%s" % bak)

        con = connect(False)
        try:
            table_cols = [r["name"] for r in con.execute("PRAGMA table_info(sessions)")]
            uid = local_user_id(con) or (manifest.get("source") or {}).get("user_id")
            written = 0
            for p in plan:
                s = p["src"]
                sid = s["id"]
                old_cwd = p["old_cwd"]
                new_cwd = p["new_cwd"]

                row_exists = con.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone()
                if row_exists and not args.force and not args.new_id:
                    out("跳过（已存在）：%s" % short_title(s["title"]))
                    continue

                if row_exists and args.new_id:
                    sid = new_session_id()
                if row_exists and args.force and not args.new_id:
                    con.execute("DELETE FROM sessions WHERE id=?", (sid,))

                # 路径映射：先工作空间，再用户目录
                pairs = []
                if old_cwd and new_cwd and norm_path(old_cwd) != norm_path(new_cwd):
                    pairs.append((old_cwd, new_cwd))
                if old_home and norm_path(old_home) != norm_path(new_home):
                    pairs.append((old_home, new_home))
                for extra in args.map or []:
                    if "=" in extra:
                        a, b = extra.split("=", 1)
                        pairs.append((a, b))
                table = build_maps(pairs)
                if os.environ.get("WB_DEBUG_MAPS"):
                    out("[调试] 路径映射对：%r" % (pairs,))
                    out("[调试] 路径替换规则：")
                    for lit, rep in table:
                        out("   %r -> %r" % (lit, rep))

                # 1) 对话正文
                pdir = project_dir_for(new_cwd)
                os.makedirs(pdir, exist_ok=True)
                dst_jsonl = os.path.join(pdir, sid + ".jsonl")
                raw = zf.read("transcript/%s.jsonl" % s["id"])
                if manifest.get("sessions") and s.get("transcript_sha256"):
                    got = hashlib.sha256(raw).hexdigest()
                    if got != s["transcript_sha256"]:
                        die("对话记录校验失败（包可能损坏）：%s" % s["id"])
                text = raw.decode("utf-8")
                text = rewrite_text(text, table)
                if sid != s["id"]:
                    text = text.replace('"sessionId":"%s"' % s["id"], '"sessionId":"%s"' % sid)
                    text = text.replace('"sessionId": "%s"' % s["id"], '"sessionId": "%s"' % sid)
                with open(dst_jsonl, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
                for suffix in (".meta.json", ".file-rollback.ndjson"):
                    arc = "transcript/%s%s" % (s["id"], suffix)
                    if arc in zf.namelist():
                        dst = os.path.join(pdir, sid + suffix)
                        with open(dst, "wb") as f:
                            f.write(rewrite_text_bytes(zf.read(arc), table))

                # 2) 附属数据
                for a in s.get("aux") or []:
                    kind = a["kind"]
                    if a["type"] == "dir":
                        prefix = "aux/%s/%s/" % (kind, s["id"])
                        dest_root = os.path.join(wb_home(), aux_rel(kind), sid)
                        for name in zf.namelist():
                            if not name.startswith(prefix) or name.endswith("/"):
                                continue
                            rel = name[len(prefix):]
                            dst = safe_join(dest_root, rel)
                            os.makedirs(os.path.dirname(dst), exist_ok=True)
                            with open(dst, "wb") as f:
                                f.write(rewrite_text_bytes(zf.read(name), table))
                    else:
                        prefix = "aux/%s/%s" % (kind, s["id"])
                        for name in zf.namelist():
                            if name.startswith(prefix) and not name.endswith("/"):
                                ext = os.path.splitext(name)[1]
                                dst_root = os.path.join(wb_home(), aux_rel(kind))
                                os.makedirs(dst_root, exist_ok=True)
                                dst = os.path.join(dst_root, sid + ext)
                                with open(dst, "wb") as f:
                                    f.write(rewrite_text_bytes(zf.read(name), table))

                # 3) 工作空间文件
                w = s.get("workspace") or {}
                if w.get("included") and not args.no_workspace:
                    prefix = "workspace/%s/" % s["id"]
                    copied = 0
                    for name in zf.namelist():
                        if not name.startswith(prefix) or name.endswith("/"):
                            continue
                        rel = name[len(prefix):]
                        dst = safe_join(new_cwd, rel)
                        if os.path.exists(dst) and not args.overwrite_files:
                            continue
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with open(dst, "wb") as f:
                            f.write(zf.read(name))
                        copied += 1
                    os.makedirs(new_cwd, exist_ok=True)
                    out("工作空间写入 %d 个文件 -> %s" % (copied, new_cwd))

                # 包里没带工作空间时也把目录建出来，保证会话能正常打开
                try:
                    os.makedirs(new_cwd, exist_ok=True)
                except OSError as e:
                    warn("工作空间目录无法创建：%s（%s）" % (new_cwd, e))

                # 4) sessions 表
                dbrow = dict(s.get("db_row") or {})
                dbrow["id"] = sid
                dbrow["cwd"] = new_cwd
                dbrow["user_id"] = uid
                dbrow.setdefault("status", "completed")
                dbrow.setdefault("transport", "local")
                dbrow.setdefault("created_at", int(time.time() * 1000))
                dbrow["updated_at"] = int(time.time() * 1000)
                dbrow["deleted_at"] = None  # 导出时可能是回收站状态，导入后恢复为正常会话
                if "title" not in dbrow or not dbrow.get("title"):
                    dbrow["title"] = s.get("title")
                insert_row(con, dbrow, table_cols)

                # 5) workspaces 表登记，保证侧边栏能看到
                try:
                    con.execute(
                        "INSERT INTO workspaces(path,last_opened_at) VALUES(?,?) "
                        "ON CONFLICT(path) DO UPDATE SET last_opened_at=excluded.last_opened_at",
                        (new_cwd, int(time.time() * 1000)),
                    )
                except sqlite3.Error:
                    pass

                written += 1
                out("已导入：%s" % short_title(s["title"]))
        finally:
            con.close()

    update_sidebar_snapshot()
    out("")
    out("导入完成，共 %d 个会话。" % written)
    out("请完全退出并重新打开 WorkBuddy，新会话会出现在左侧列表里。")


def aux_rel(kind):
    for k, rel in AUX_DIRS:
        if k == kind:
            return rel
    for k, rel, _ext in AUX_FILES:
        if k == kind:
            return rel
    return kind


def insert_row(con, row, table_cols):
    cols = [c for c in row.keys() if c in table_cols]
    placeholders = ",".join("?" * len(cols))
    sql = "INSERT INTO sessions(%s) VALUES(%s)" % (",".join(cols), placeholders)
    con.execute(sql, [row[c] for c in cols])


def new_session_id():
    import uuid
    return str(uuid.uuid4())


def rewrite_text_bytes(data, table):
    try:
        return rewrite_text(data.decode("utf-8"), table).encode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return data


def safe_join(root, rel):
    """防 zip-slip：解压路径必须落在 root 内。"""
    root_abs = os.path.abspath(root)
    dst = os.path.abspath(os.path.join(root_abs, rel))
    if not dst.startswith(root_abs + os.sep) and dst != root_abs:
        die("包内路径越界，已中止：%s" % rel)
    return dst


def update_sidebar_snapshot():
    """尽力把导入的会话塞进侧边栏缓存，让用户重启后立刻看到。失败不影响主流程。"""
    try:
        con = connect(True)
        try:
            uid = local_user_id(con)
        finally:
            con.close()
        if not uid:
            return
        path = os.path.join(wb_home(), uid, "sidebar-list-snapshot.json")
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            snap = json.load(f)
        items = {i.get("id"): i for i in snap.get("items", [])}
        con = connect(True)
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT id,title,custom_title,cwd,status,transport,updated_at,last_activity_at "
                "FROM sessions WHERE deleted_at IS NULL")]
        finally:
            con.close()
        for r in rows:
            if r["id"] in items:
                continue
            items[r["id"]] = {
                "id": r["id"],
                "title": r.get("custom_title") or r.get("title") or "(无标题)",
                "state": "idle",
                "transport": r.get("transport") or "local",
                "kind": "task",
                "lastActivityAt": r.get("last_activity_at") or r.get("updated_at"),
                "isPinned": False,
                "conversationOrigin": "local",
                "updatedAt": r.get("updated_at"),
                "space": {"type": "local", "name": r.get("cwd") or ""},
            }
        snap["items"] = list(items.values())
        snap["savedAt"] = int(time.time() * 1000)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
    except (OSError, ValueError, sqlite3.Error):
        pass


# ---------------------------------------------------------------- list / doctor

def cmd_list(args):
    items = list_sessions(include_cloud=args.include_cloud,
                          include_deleted=args.include_deleted)
    if not items:
        die("没有本地会话。")
    out("本机 WorkBuddy 会话（%d 个）" % len(items))
    out("")
    for i, s in enumerate(items, 1):
        out("%2d. %s" % (i, short_title(s["title"], 50)))
        out("    ID：%s" % s["id"])
        out("    时间：%s    消息：%d 条（%s）" % (ts_to_str(s["last_activity_at"]),
                                              s["lines"], human_size(s["transcript_bytes"])))
        out("    工作空间：%s%s" % (s["cwd"] or "-",
                                   "" if s["workspace_exists"] else "   [目录已不存在]"))
        if s["workspace_exists"]:
            out("    工作空间体积：%s" % human_size(s["workspace_bytes"]))
        if s["aux"]:
            out("    附属数据：%s" % "、".join(sorted(s["aux"].keys())))
        if s.get("deleted"):
            out("    [已删除] 这个会话在列表里是回收站状态，导出后导入会恢复为正常会话")
        if not s["transcript"]:
            out("    [警告] 找不到对话记录文件")
        out("")
    out("导出示例：")
    out("  wb-migrate export --session %d" % 1)
    out("  wb-migrate export --session all")


def cmd_doctor(args):
    out("WorkBuddy 目录：%s" % wb_home())
    ok = True
    dbp = db_file()
    out("数据库：%s  %s" % (dbp, "存在" if os.path.isfile(dbp) else "缺失"))
    if not os.path.isfile(dbp):
        ok = False
    pr = projects_dir()
    out("对话目录：%s  %s" % (pr, "存在" if os.path.isdir(pr) else "缺失"))
    if not os.path.isdir(pr):
        ok = False

    # 校验路径编码规则与磁盘目录是否一致
    try:
        con = connect(True)
        rows = [dict(r) for r in con.execute("SELECT id,cwd FROM sessions WHERE cwd<>''")]
        con.close()
        hit = miss = 0
        for r in rows:
            if os.path.isdir(os.path.join(pr, encode_cwd(r["cwd"]))):
                hit += 1
            else:
                miss += 1
        out("")
        out("路径编码自检：%d 命中 / %d 未命中" % (hit, miss))
        if miss and hit == 0:
            warn("编码规则与磁盘不一致，导入时请用 --workspace-root 指定已存在的目录，"
                 "或反馈此问题。")
            ok = False
        else:
            out("编码规则工作正常。")
    except sqlite3.Error as e:
        warn("读取数据库失败：%s" % e)
        ok = False

    out("")
    out("应用运行状态：%s" % ("运行中（导入前请先退出）" if is_app_running() else "未运行"))
    out("Python：%s" % sys.version.split()[0])
    out("")
    out("结论：%s" % ("环境正常，可以使用。" if ok else "环境有异常，请先处理上面的问题。"))


# ---------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(
        prog="wb-migrate",
        description="WorkBuddy 对话跨电脑迁移工具（对话记录 + 附属数据 + 工作空间）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用流程
  1) 旧电脑：wb-migrate list                      查看会话
  2) 旧电脑：wb-migrate export --session 1        打包成 zip
  3) 拷贝包到新电脑
  4) 新电脑：wb-migrate import 包.zip            预演，看计划
  5) 新电脑：wb-migrate import 包.zip --yes      真正写入
  6) 重启 WorkBuddy

其他
  wb-migrate export --session all                 导出全部本地会话
  wb-migrate inspect 包.zip                       只看包里有什么，不写入
  wb-migrate doctor                               环境自检
""")
    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser("list", help="列出本机会话")
    pl.add_argument("--include-cloud", action="store_true", help="同时列出云端会话")
    pl.add_argument("--include-deleted", action="store_true", help="同时列出回收站里的会话")
    pl.set_defaults(func=cmd_list)

    pe = sub.add_parser("export", help="导出会话为迁移包")
    pe.add_argument("--session", "-s", default="latest",
                    help="会话序号 / ID / ID前缀 / 标题关键字 / all / latest（默认 latest）")
    pe.add_argument("--out", "-o", help="输出文件路径（默认自动命名）")
    pe.add_argument("--no-workspace", action="store_true", help="只导出对话，不打包工作空间文件")
    pe.add_argument("--max-file-mb", type=int, default=MAX_FILE_MB_DEFAULT,
                    help="单文件体积上限，超过则跳过（默认 %d MB）" % MAX_FILE_MB_DEFAULT)
    pe.add_argument("--include-cloud", action="store_true", help="允许选择云端会话")
    pe.add_argument("--include-deleted", action="store_true", help="允许选择回收站里的会话")
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("import", help="导入迁移包")
    pi.add_argument("package", help="迁移包路径")
    pi.add_argument("--yes", "-y", action="store_true", help="真正写入（不加则只预演）")
    pi.add_argument("--workspace-root", help="工作空间放到哪个根目录（默认 ~/WorkBuddy）")
    pi.add_argument("--keep-cwd", action="store_true", help="保留原来的绝对路径不重映射")
    pi.add_argument("--force", action="store_true", help="覆盖已存在的同 ID 会话；应用运行中也强行写入")
    pi.add_argument("--new-id", action="store_true", help="已存在时复制成新 ID 的会话，不覆盖原会话")
    pi.add_argument("--no-workspace", action="store_true", help="只导入对话，不写工作空间文件")
    pi.add_argument("--overwrite-files", action="store_true", help="目标目录已有同名文件时覆盖")
    pi.add_argument("--map", action="append", metavar="旧=新", help="额外的路径替换规则，可多次指定")
    pi.set_defaults(func=cmd_import)

    pn = sub.add_parser("inspect", help="查看迁移包内容")
    pn.add_argument("package")
    pn.set_defaults(func=cmd_inspect)

    pd = sub.add_parser("doctor", help="环境自检")
    pd.set_defaults(func=cmd_doctor)

    p.add_argument("--version", action="version", version="wb-migrate " + VERSION)
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
