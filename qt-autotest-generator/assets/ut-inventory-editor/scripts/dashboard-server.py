#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Uniontech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later
"""
dashboard-server.py — UT 看板伴随服务

调 batch-collect.py 刷新数据 + 托管 index.html。仅标准库。

用法:
  python3 dashboard-server.py                     # 默认端口 8765
  python3 dashboard-server.py --port 9000
  python3 dashboard-server.py --base ../mcp-projects

端点:
  GET  /                      静态 index.html
  GET  /api/status            {server,mcp,base_dir,last_summary_ts,projects_cached}
  POST /api/sync              {filter?,size?,skip_fetch_mcp?,skip_test_mapping?} → {task_id}
  GET  /api/task/<id>         {state,done_n,total_n,current,log_tail,elapsed}
  GET  /api/projects          26 项目聚合统计
  GET  /api/inventory/<name>  单项目完整 inventory JSON
  GET  /api/mapping/<name>    test-mapping.json
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent            # 独立项目: scripts/ 上一级就是 index.html 所在目录
DEFAULT_BASE = SCRIPT_DIR.parent / "mcp-projects"   # 与 batch-collect.py 的 BASE_DIR 一致
BATCH_COLLECT = SCRIPT_DIR / "batch-collect.py"
FETCH_MCP = SCRIPT_DIR / "fetch-mcp-data.py"
MCP_PROBE_HOST = "10.8.12.80"
MCP_PROBE_PORT = 13626
CONFIG_FILE = DEFAULT_ROOT / "config.json"
REGISTRY_FILE = DEFAULT_ROOT / "projects.json"


def load_config():
    """全局配置 config.json（损坏时退回默认）。"""
    cfg = {"server": {"port": 8765, "host": "127.0.0.1"},
           "mcp_url": f"http://{MCP_PROBE_HOST}:{MCP_PROBE_PORT}/mcp",
           "github": {"org": "linuxdeepin"}, "sync": {"concurrency": 1}}
    try:
        data = json.loads(CONFIG_FILE.read_text("utf-8"))
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def _parse_mcp_host(url):
    """从 mcp_url 提取 (host, port) 用于 TCP 探测。"""
    m = re.match(r"https?://([^/:]+)(?::(\d+))?", url or "")
    if not m:
        return MCP_PROBE_HOST, MCP_PROBE_PORT
    return m.group(1), int(m.group(2) or 80)

# ── batch_collect 模块加载（文件名含连字符，仅复用 collect_project 函数）──
_bc = None

def load_batch_collect():
    global _bc
    if _bc is not None:
        return _bc
    spec = importlib.util.spec_from_file_location("batch_collect", BATCH_COLLECT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _bc = mod
    return mod

# ── 后台任务管理 ──
TASKS = {}          # task_id → {state, done_n, total_n, current, log_tail, t0, err}
TASKS_LOCK = threading.Lock()
_SYNC_POOL = ThreadPoolExecutor(max_workers=1)   # 同时只允许一个同步任务

def probe_mcp(timeout=2.0):
    """TCP 探测 MCP 端口可达性（地址取自 config.json mcp_url）。"""
    host, port = _parse_mcp_host(load_config().get("mcp_url", ""))
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False

_REGISTRY = None
_REGISTRY_MTIME = None


def load_registry(force=False):
    """项目注册表 projects.json（支持热更新：文件变更后重新加载）。"""
    global _REGISTRY, _REGISTRY_MTIME
    try:
        mt = REGISTRY_FILE.stat().st_mtime
    except OSError:
        return {"defaults": {}, "projects": []}
    if not force and _REGISTRY is not None and mt == _REGISTRY_MTIME:
        return _REGISTRY
    try:
        _REGISTRY = json.loads(REGISTRY_FILE.read_text("utf-8"))
        _REGISTRY_MTIME = mt
    except (OSError, ValueError):
        _REGISTRY = _REGISTRY or {"defaults": {}, "projects": []}
        _REGISTRY_MTIME = mt
    return _REGISTRY


def registry_index():
    """{name: entry} 视图。"""
    return {p.get("name"): p for p in load_registry().get("projects", []) if p.get("name")}


def project_git_info(name, inventory_data):
    """优先 inventory 自带 git 字段，其次注册表，最后 fallback master"""
    g = inventory_data.get("git")
    if isinstance(g, dict) and g.get("branch"):
        return {"branch": g["branch"], "org": g.get("org", "linuxdeepin"),
                "source": g.get("branch_source", "inventory")}
    e = registry_index().get(name)
    if e:
        git = e.get("git") or {}
        return {"branch": git.get("branch", "master"), "org": git.get("org", "linuxdeepin"),
                "source": "registry"}
    e = load_branch_table().get(name)
    if e:
        return {"branch": e["branch"], "org": e.get("org", "linuxdeepin"),
                "source": e.get("source", "table")}
    return {"branch": "master", "org": "linuxdeepin", "source": "fallback"}


def load_branch_table():
    """project-branches.json 兼容层（注册表优先，此表兜底）。"""
    p = SCRIPT_DIR / "project-branches.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("projects", {})
    except (OSError, ValueError):
        return {}


def collect_stats(base_dir):
    """聚合 base_dir/*/.ut-inventory.json 的统计（size/mcp 名取自注册表）。"""
    reg = registry_index()
    size_map = {n: e.get("size", "?") for n, e in reg.items()}
    mcp_map = {n: e.get("mcp_name", n) for n, e in reg.items()}
    projects = []
    if not base_dir.is_dir():
        return projects
    for d in sorted(base_dir.iterdir()):
        if not d.is_dir():
            continue
        inv = d / ".ut-inventory.json"
        if not inv.is_file():
            continue
        try:
            with open(inv, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        stats = data.get("scan_stats", {})
        methods = data.get("methods", [])
        testable = [m for m in methods if m.get("testable", True)]
        high = [m for m in testable if m.get("level") == "high"]
        mid = [m for m in testable if m.get("level") == "mid"]
        with_cover = [m for m in testable if m.get("test_cover_count", 0) > 0]
        no_h = [m for m in high if m.get("test_cover_count", 0) == 0]
        no_m = [m for m in mid if m.get("test_cover_count", 0) == 0]
        # 高优无覆盖 Top10（按分数降序，服务端预排）
        top_gap = sorted(
            ({"qn": m.get("qualified_name"), "name": m.get("name"),
              "class": m.get("class_qn"), "file": m.get("file_path"),
              "score": m.get("score", 0)}
             for m in no_h),
            key=lambda x: -x["score"])[:10]
        # git 分支信息：inventory 自带 > project-branches.json > master
        git_info = project_git_info(d.name, data)
        projects.append({
            "name": d.name,
            "mcp_name": mcp_map.get(d.name, d.name),
            "size": size_map.get(d.name, "?"),
            "github": d.name,
            "branch": git_info["branch"],
            "org": git_info["org"],
            "branch_source": git_info["source"],
            "generated_at": data.get("generated_at", ""),
            "base_sha": (data.get("base_sha") or "")[:10],
            "stats": {
                "total_methods": len(methods),
                "testable": len(testable),
                "high": len(high), "mid": len(mid),
                "low": stats.get("low", 0),
                "non_testable": stats.get("non_testable", 0),
                "review_pending": stats.get("review_pending", 0),
                "with_test_cover": len(with_cover),
                "no_cover_high": len(no_h),
                "no_cover_mid": len(no_m),
            },
            "top_gap": top_gap,
        })
    return projects

def run_sync(task_id, opts, base_dir):
    """后台逐项目调 collect_project（复用 batch_collect 模块的收集函数），更新 TASKS。
    项目清单唯一来源: 注册表 enabled 项目。"""
    bc = load_batch_collect()  # 仅用其 collect_project 函数
    reg = registry_index()
    projects = [(e.get("mcp_name") or n, n, e.get("size", "?"))
                for n, e in reg.items() if e.get("enabled", True)]
    if not projects:
        with TASKS_LOCK:
            TASKS[task_id].update(state="error", log_tail="注册表无启用项目")
        return
    if opts.get("filter"):
        projects = [p for p in projects if opts["filter"].lower() in p[1].lower()]
    if opts.get("size"):
        projects = [p for p in projects if p[2] == opts["size"].upper()]
    with TASKS_LOCK:
        TASKS[task_id].update(state="running", total_n=len(projects), done_n=0)
    for mcp_name, gh_name, _sz in projects:
        if not mcp_name:
            with TASKS_LOCK:
                TASKS[task_id]["log_tail"] += f"\n⚠ {gh_name}: 注册表缺 mcp_name，跳过"
            continue
        with TASKS_LOCK:
            TASKS[task_id]["current"] = gh_name
        t0 = time.time()
        try:
            # collect_project 内部已有日志/文件产出；此处仅收敛结果
            bc.collect_project(
                mcp_name, gh_name,
                skip_fetch_mcp=opts.get("skip_fetch_mcp", False),
                skip_tm=opts.get("skip_test_mapping", False),
            )
            ok = True
        except Exception as e:  # noqa: BLE001
            ok = False
            with TASKS_LOCK:
                TASKS[task_id]["log_tail"] = f"{gh_name}: {e}"
        log_path = base_dir / gh_name / "collect.log"
        tail = ""
        try:
            if log_path.is_file():
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-400:]
        except OSError:
            pass
        with TASKS_LOCK:
            t = TASKS[task_id]
            t["done_n"] += 1
            t["log_tail"] = tail
            t["last_elapsed"] = round(time.time() - t0, 1)
    with TASKS_LOCK:
        t = TASKS[task_id]
        t["state"] = "done"
        t["current"] = ""
        t["elapsed"] = round(time.time() - t["t0"], 1)

def start_sync(opts, base_dir):
    """启动同步任务；若已有运行中任务则复用其 task_id（防重入）。"""
    with TASKS_LOCK:
        for tid, t in TASKS.items():
            if t["state"] == "running":
                return tid, False   # 复用
        task_id = uuid.uuid4().hex[:12]
        TASKS[task_id] = {"state": "running", "done_n": 0, "total_n": 0,
                          "current": "", "log_tail": "", "t0": time.time(),
                          "elapsed": 0}
    _SYNC_POOL.submit(run_sync, task_id, opts, base_dir)
    return task_id, True


# ── 配置管理（/api/config）──

def _backup_and_write(path, data):
    if path.is_file():
        shutil.copy(path, str(path) + ".bak")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def save_config(body):
    """保存全局配置（仅接受已知字段，端口等重启后生效）。"""
    if not isinstance(body, dict):
        return False, "body 必须是对象"
    cfg = load_config()
    for k in ("mcp_url",):
        if k in body:
            v = body[k]
            if not isinstance(v, str):
                return False, f"{k} 必须是字符串"
            cfg[k] = v
    for sec in ("server", "github", "sync"):
        if sec in body:
            if not isinstance(body[sec], dict):
                return False, f"{sec} 必须是对象"
            cfg.setdefault(sec, {}).update(body[sec])
    if cfg.get("server", {}).get("port"):
        try:
            cfg["server"]["port"] = int(cfg["server"]["port"])
        except (TypeError, ValueError):
            return False, "server.port 必须是整数"
    _backup_and_write(CONFIG_FILE, cfg)
    return True, "已保存（端口等改动重启服务后生效）"


def save_registry(body):
    """保存项目注册表 projects.json（结构校验 + 备份）。"""
    if not isinstance(body, dict) or not isinstance(body.get("projects"), list):
        return False, "需要 {projects: [...]}"
    names = set()
    for i, p in enumerate(body["projects"]):
        if not isinstance(p, dict) or not p.get("name") or not re.match(r"^[\w.\-]+$", str(p["name"])):
            return False, f"第 {i+1} 项缺少合法 name"
        if p["name"] in names:
            return False, f"项目名重复: {p['name']}"
        names.add(p["name"])
        for sec in ("git", "source", "build"):
            if sec in p and not isinstance(p[sec], dict):
                return False, f"{p['name']}.{sec} 必须是对象"
        if p.get("source", {}).get("path") and not Path(p["source"]["path"]).expanduser().is_dir():
            return False, f"{p['name']} 本地路径不存在: {p['source']['path']}"
    out = {"defaults": body.get("defaults") or load_registry().get("defaults", {}),
           "projects": body["projects"]}
    _backup_and_write(REGISTRY_FILE, out)
    load_registry(force=True)
    return True, f"已保存 {len(body['projects'])} 个项目"


BUILD_FILES = [
    ("CMakeLists.txt", "cmake"), ("Makefile", "make"), ("meson.build", "meson"),
    ("qmake.pro", "qmake"),
]


def detect_build(path):
    """探测本地项目路径的构建系统与测试目录（不执行任何命令，只读文件）。"""
    path = (path or "").strip()
    if not path:
        return {"ok": False, "msg": "请先填写本地路径"}
    root = Path(path).expanduser()
    if not root.is_dir():
        return {"ok": False, "msg": f"路径不存在: {path}"}
    system = ""
    found = []
    for fn, sysname in BUILD_FILES:
        if (root / fn).is_file():
            found.append(fn)
            if not system:
                system = sysname
    # .pro 任意匹配（qmake 项目文件名不一定叫 qmake.pro）
    pros = list(root.glob("*.pro"))
    if pros and not system:
        system = "qmake"
        found.append(pros[0].name)
    # 测试目录推测
    test_dir = ""
    for cand in ("autotests", "tests", "test"):
        if (root / cand).is_dir():
            test_dir = cand
            break
    gtest = any((root / test_dir).rglob("*test*.cpp")) if test_dir else False
    return {
        "ok": bool(system),
        "msg": "" if system else "未识别到构建文件（CMakeLists.txt/.pro/meson.build/Makefile）",
        "system": system or "custom",
        "framework": "gtest" if gtest else "gtest",
        "test_dir": test_dir,
        "found": found,
        "name_guess": root.name,
    }

# ── HTTP Handler ──

class Handler(BaseHTTPRequestHandler):
    server_version = "UTDash/1.0"
    root_dir = DEFAULT_ROOT
    base_dir = DEFAULT_BASE

    def log_message(self, fmt, *args):  # 安静模式
        pass

    # -- helpers --
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, mime):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self._cors()
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # -- routes --
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            self._file(self.root_dir / "index.html", "text/html; charset=utf-8")
        elif p == "/api/fs/list":
            q = parse_qs(u.query)
            raw = (q.get("path") or ["~"])[0]
            try:
                target = Path(raw).expanduser().resolve()
            except OSError:
                target = Path(raw).expanduser().absolute()
            if not target.is_dir():
                self._json({"error": f"不是目录: {target}"}, 400)
                return
            entries = []
            try:
                with os.scandir(target) as it:
                    for e in it:
                        try:
                            is_dir = e.is_dir(follow_symlinks=True)
                            entries.append({"name": e.name, "dir": is_dir, "symlink": e.is_symlink()})
                        except OSError:
                            continue
            except PermissionError:
                self._json({"error": f"无权限: {target}"}, 400)
                return
            entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
            if len(entries) > 800:
                entries = entries[:800]
            self._json({
                "path": str(target),
                "parent": str(target.parent) if target.parent != target else "",
                "entries": entries,
                "truncated": False,
            })
        elif p == "/api/status":
            summary = self.base_dir / "_summary.json"
            last_ts = None
            if summary.is_file():
                try:
                    last_ts = summary.stat().st_mtime
                except OSError:
                    pass
            self._json({
                "server": True,
                "mcp": probe_mcp(),
                "base_dir": str(self.base_dir),
                "last_summary_ts": last_ts,
                "projects_cached": len(collect_stats(self.base_dir)),
            })
        elif p == "/api/projects":
            self._json({"projects": collect_stats(self.base_dir)})
        elif p.startswith("/api/task/"):
            tid = p.split("/")[-1]
            with TASKS_LOCK:
                t = TASKS.get(tid)
                snap = dict(t) if t else None
            if snap is None:
                self._json({"error": "no such task"}, 404)
            else:
                snap["elapsed"] = round(time.time() - snap["t0"], 1) if snap["state"] == "running" else snap.get("elapsed", 0)
                self._json(snap)
        elif p.startswith("/api/inventory/"):
            name = p.split("/")[-1]
            if not re.match(r"^[\w.\-]+$", name):
                self._json({"error": "bad name"}, 400)
                return
            f = self.base_dir / name / ".ut-inventory.json"
            self._file(f, "application/json; charset=utf-8")
        elif p.startswith("/api/mapping/"):
            name = p.split("/")[-1]
            if not re.match(r"^[\w.\-]+$", name):
                self._json({"error": "bad name"}, 400)
                return
            f = self.base_dir / name / "test-mapping.json"
            self._file(f, "application/json; charset=utf-8")
        elif p == "/api/config":
            self._json({"config": load_config(), "projects": load_registry(force=True)})
        elif p in ("/styles.css",) or p.startswith("/js/"):
            # 静态资源（拆分后的前端模块）
            rel = p.lstrip("/")
            f = (self.root_dir / rel).resolve()
            if not str(f).startswith(str(self.root_dir.resolve())) or not f.is_file():
                self._json({"error": "not found"}, 404)
                return
            ctype = "text/javascript; charset=utf-8" if f.suffix == ".js" else "text/css; charset=utf-8"
            self._file(f, ctype)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/sync":
            body = self._body_json()
            task_id, started = start_sync(body, self.base_dir)
            self._json({"task_id": task_id, "started": started})
        elif u.path == "/api/config/sync-registry":
            body = self._body_json()
            cfg = load_config()
            cmd = [sys.executable, str(SCRIPT_DIR / "sync-registry-from-mcp.py"), "--json"]
            if body.get("keep_size"):
                cmd.append("--keep-size")
            if cfg.get("mcp_url"):
                cmd += ["--mcp-url", cfg["mcp_url"]]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired:
                self._json({"ok": False, "msg": "MCP 同步超时 (180s)"}, 504)
                return
            try:
                data = json.loads(r.stdout)
            except ValueError:
                data = {"ok": False, "msg": (r.stdout + r.stderr)[-400:]}
            self._json(data)
        elif u.path == "/api/config/global":
            body = self._body_json()
            ok, msg = save_config(body)
            self._json({"ok": ok, "msg": msg}, 200 if ok else 400)
        elif u.path == "/api/config/projects":
            body = self._body_json()
            ok, msg = save_registry(body)
            self._json({"ok": ok, "msg": msg}, 200 if ok else 400)
        elif u.path == "/api/config/detect":
            body = self._body_json()
            self._json(detect_build(body.get("path", "")))
        else:
            self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser(description="UT 看板伴随服务")
    ap.add_argument("--port", type=int, default=None, help="覆盖 config.json 端口")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="index.html 目录")
    ap.add_argument("--base", default=str(DEFAULT_BASE), help="mcp-projects 数据目录")
    args = ap.parse_args()
    port = args.port or int(load_config().get("server", {}).get("port") or 8765)

    Handler.root_dir = Path(args.root)
    Handler.base_dir = Path(args.base)

    if not BATCH_COLLECT.is_file():
        print(f"❌ 找不到 batch-collect.py: {BATCH_COLLECT}", file=sys.stderr)
        sys.exit(1)

    load_batch_collect()  # 预加载 collect_project, 失败早退

    # 端口占用自动顺延（最多试 10 个）
    base_port = port
    httpd = None
    for _ in range(10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError as e:
            if e.errno == 98:  # Address already in use
                print(f"⚠ 端口 {port} 已被占用，尝试 {port + 1} …")
                port += 1
            else:
                raise
    if httpd is None:
        print("❌ 连续 10 个端口均被占用，请用 --port 指定", file=sys.stderr)
        sys.exit(1)
    if port != base_port:
        print(f"ℹ 实际使用端口: {port}（原端口 {base_port} 被占用）")

    print(f"✅ UT Dashboard Server")
    print(f"   http://localhost:{port}/")
    print(f"   HTML : {Handler.root_dir}")
    print(f"   数据 : {Handler.base_dir}")
    murl = load_config().get("mcp_url", "")
    print(f"   MCP  : {murl} {'✓可达' if probe_mcp() else '✗不可达'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
