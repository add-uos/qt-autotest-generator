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

# ── batch_collect 模块加载（文件名含连字符） ──
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
    """TCP 探测 MCP 端口可达性。"""
    try:
        s = socket.create_connection((MCP_PROBE_HOST, MCP_PROBE_PORT), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False

_BRANCH_TABLE = None


def load_branch_table():
    """project-branches.json（与本脚本同目录），供旧 inventory 兼并分支"""
    global _BRANCH_TABLE
    if _BRANCH_TABLE is None:
        p = Path(__file__).resolve().parent / "project-branches.json"
        try:
            _BRANCH_TABLE = json.loads(p.read_text(encoding="utf-8")).get("projects", {})
        except (OSError, ValueError):
            _BRANCH_TABLE = {}
    return _BRANCH_TABLE


def project_git_info(name, inventory_data):
    """优先 inventory 自带 git 字段，其次表，最后 fallback master"""
    g = inventory_data.get("git")
    if isinstance(g, dict) and g.get("branch"):
        return {"branch": g["branch"], "org": g.get("org", "linuxdeepin"),
                "source": g.get("branch_source", "inventory")}
    e = load_branch_table().get(name)
    if e:
        return {"branch": e["branch"], "org": e.get("org", "linuxdeepin"),
                "source": e.get("source", "table")}
    return {"branch": "master", "org": "linuxdeepin", "source": "fallback"}


def collect_stats(base_dir):
    """聚合 base_dir/*/.ut-inventory.json 的统计。"""
    bc = load_batch_collect()
    size_map = {gh: size for _mcp, gh, size in bc.PROJECTS}
    mcp_map = {gh: mcp for mcp, gh, _s in bc.PROJECTS}
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
    """后台逐项目调 collect_project（复用 batch_collect 模块），更新 TASKS。"""
    bc = load_batch_collect()
    projects = [p for p in bc.PROJECTS]
    if opts.get("filter"):
        projects = [p for p in projects if opts["filter"].lower() in p[1].lower()]
    if opts.get("size"):
        projects = [p for p in projects if p[2] == opts["size"].upper()]
    with TASKS_LOCK:
        TASKS[task_id].update(state="running", total_n=len(projects), done_n=0)
    for mcp_name, gh_name, _sz in projects:
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
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/sync":
            body = self._body_json()
            task_id, started = start_sync(body, self.base_dir)
            self._json({"task_id": task_id, "started": started})
        else:
            self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser(description="UT 看板伴随服务")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="index.html 目录")
    ap.add_argument("--base", default=str(DEFAULT_BASE), help="mcp-projects 数据目录")
    args = ap.parse_args()

    Handler.root_dir = Path(args.root)
    Handler.base_dir = Path(args.base)

    if not BATCH_COLLECT.is_file():
        print(f"❌ 找不到 batch-collect.py: {BATCH_COLLECT}", file=sys.stderr)
        sys.exit(1)

    load_batch_collect()  # 预加载，失败早退

    # 端口占用自动顺延（最多试 10 个）
    port = args.port
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
    if port != args.port:
        print(f"ℹ 实际使用端口: {port}（原端口 {args.port} 被占用）")

    print(f"✅ UT Dashboard Server")
    print(f"   http://localhost:{port}/")
    print(f"   HTML : {Handler.root_dir}")
    print(f"   数据 : {Handler.base_dir}")
    print(f"   MCP  : {MCP_PROBE_HOST}:{MCP_PROBE_PORT} {'✓可达' if probe_mcp() else '✗不可达'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
