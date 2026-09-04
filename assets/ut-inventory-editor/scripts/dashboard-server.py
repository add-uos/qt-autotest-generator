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
import queue
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
import xml.etree.ElementTree as ET
import signal

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
    for sec in ("server", "github", "sync", "test", "coverage"):
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


def _normalize_project_phases(p):
    """归一化项目 build：把旧 phases 结构折算回 flat 字段并删除 phases，
    使表格成为唯一事实来源（flat: configure/build_cmd/test_cmd）。
    coverage/summary 命令交回 infer_phases 默认（标准化命令，不暴露到表格）。"""
    b = p.get("build") or {}
    phases = b.pop("phases", None)
    if isinstance(phases, dict):
        if phases.get("configure") and not b.get("configure"):
            b["configure"] = phases["configure"]
        if phases.get("build") and not b.get("build_cmd"):
            b["build_cmd"] = phases["build"]
        if phases.get("test") and not b.get("test_cmd"):
            b["test_cmd"] = phases["test"]
    p["build"] = b


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
        _normalize_project_phases(p)  # phases→flat 迁移，统一 schema
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
    # 测试目录推测（返回全部候选供前端下拉）
    test_dir_candidates = [c for c in ("autotests", "tests", "test")
                          if (root / c).is_dir()]
    test_dir = test_dir_candidates[0] if test_dir_candidates else ""
    gtest = any((root / test_dir).rglob("*test*.cpp")) if test_dir else False
    # 构建目录候选：真实存在的 build-* 目录（相对名）
    build_dir_candidates = sorted(p.name for p in root.glob("build-*") if p.is_dir())
 # 选一个推荐 build_dir：优先有 report/*.xml（真实测试产物），否则取第一个
    build_dir = ""
    for d in build_dir_candidates:
        if any((root / d).glob("report/*.xml")):
            build_dir = d
            break
    if not build_dir and build_dir_candidates:
        build_dir = build_dir_candidates[0]
    # 测试脚本探测（相对项目根）
    script = ""
    for cand in ("autotests/run-ut.sh", "tests/run-ut.sh", "autotests/run-tests.sh",
                 "tests/test-prj-running.sh", "test/test-prj-running.sh"):
        if (root / cand).is_file():
            script = cand
            break
    return {
        "ok": bool(system),
        "msg": "" if system else "未识别到构建文件（CMakeLists.txt/.pro/meson.build/Makefile）",
        "system": system or "custom",
        "framework": "gtest" if gtest else "gtest",
        "test_dir": test_dir,
        "test_dir_candidates": test_dir_candidates,
        "build_dir": build_dir,
        "build_dir_candidates": build_dir_candidates,
        "script": script,
        "found": found,
        "name_guess": root.name,
    }

# ── 本地测试结果采集（Phase 1）──

def _project_source_path(name):
    """从注册表取项目的本地 source.path（展开 ~，返回绝对路径字符串）。"""
    e = registry_index().get(name)
    if e:
        src = e.get("source") or {}
        path = src.get("path") or ""
        if path:
            try:
                return str(Path(path).expanduser())
            except OSError:
                return path
    return None


def _project_build_dir_config(name):
    """从注册表读 build.build_dir 配置（显式指定的编译目录）。"""
    e = registry_index().get(name) if name else None
    if e:
        b = e.get("build") or {}
        bd = b.get("build_dir") or ""
        if bd:
            return bd
    return None


def _test_dir_name(name):
    """从注册表读 build.test_dir（测试目录名，如 autotests），用于派生 build-<test_dir> 候选。"""
    e = registry_index().get(name) if name else None
    if e:
        b = e.get("build") or {}
        return (b.get("test_dir") or "").strip() or None
    return None


def _find_project_script(project_path, name=None):
    """探测项目的测试脚本。
    支持 registry build.script 显式指定；否则按常见命名探测：
    autotests/run-ut.sh (ATUT 约定)、tests/run-ut.sh、autotests/run-tests.sh、
    tests/test-prj-running.sh 等。"""
    e = registry_index().get(name) if name else None
    if e:
        s = (e.get("build") or {}).get("script") or ""
        s = s.strip()
        if s:
            f = Path(project_path) / s
            if f.is_file():
                return str(f)
    for cand in ("autotests/run-ut.sh", "tests/run-ut.sh",
                 "autotests/run-tests.sh", "tests/test-prj-running.sh",
                 "test/test-prj-running.sh"):
        f = Path(project_path) / cand
        if f.is_file():
            return str(f)
    return None


def find_build_dir(project_path, candidates=None, name=None):
    """探测项目的构建目录。
    优先级：① 有真实测试产物(report/*.xml)的目录（避免被旧 build-ut 里空壳
          ut-summary.json 误导）② 有 ut-summary/CMakeCache 的目录
          ③ registry 显式 build_dir 配置且目录存在（即使无产物，供 run/build 使用）
          ④ 任意存在的候选
    候选有序表：registry 显式 build_dir → build-<test_dir>(ATUT 约定) → 默认候选。
    这样能识别 run-ut.sh 产出的 build-autotests，也兼容 build-ut 旧约定。"""
    if not project_path:
        return None
    root = Path(project_path).expanduser()
    if not root.is_dir():
        return None
    cfg_dir = _project_build_dir_config(name)
    test_dir = _test_dir_name(name)
    if candidates is None:
        candidates = load_config().get("test", {}).get(
            "build_dir_candidates", ["build-ut", "build-test", "build-ut-m3", "build"])
    # 候选有序表：显式配置 → build-<test_dir>(ATUT) → 默认候选，去重
    ordered = []
    if cfg_dir:
        ordered.append(cfg_dir)
    if test_dir:
        td = f"build-{test_dir}"
        if td not in ordered:
            ordered.append(td)
    for d in candidates:
        if d not in ordered:
            ordered.append(d)
    def _has_real_tests(p):
        return any(p.glob("report/*.xml"))
    def _has_artifacts(p):
        return ((p / "ut-summary.json").exists()
                or _has_real_tests(p)
                or (p / "CMakeCache.txt").exists())
    # ① 有真实测试产物(report/*.xml)的目录优先，避免旧空壳 summary 误导
    for d in ordered:
        p = root / d
        if p.is_dir() and _has_real_tests(p):
            return d
    # ② 有 ut-summary 或 CMakeCache 的目录
    for d in ordered:
        p = root / d
        if p.is_dir() and _has_artifacts(p):
            return d
    # ③ registry 显式配置且目录存在（即使无产物，也供 run/build 使用）
    if cfg_dir and (root / cfg_dir).is_dir():
        return cfg_dir
    # ④ 任意存在的候选
    for d in ordered:
        if (root / d).is_dir():
            return d
    return None


def parse_gtest_xml(xml_path):
    """解析 gtest XML，返回 (suites, failed_cases, timestamp)。
    兼容 <testsuites> 和 <testsuite> 根。"""
    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, OSError):
        return [], [], None
    root_el = tree.getroot()
    if root_el.tag == "testsuites":
        suites_el = root_el.findall("testsuite")
        timestamp = root_el.get("timestamp")
    elif root_el.tag == "testsuite":
        suites_el = [root_el]
        timestamp = root_el.get("timestamp")
    else:
        suites_el = root_el.findall("testsuite")
        timestamp = None
    suites, failed_cases = [], []
    for ts in suites_el:
        ts_name = ts.get("name", "")
        cases = []
        for tc in ts.findall("testcase"):
            case = {
                "name": tc.get("name", ""),
                "status": tc.get("status", ""),
                "result": tc.get("result", ""),
                "time": tc.get("time", "0"),
                "file": tc.get("file", ""),
                "line": int(tc.get("line", 0) or 0),
                "classname": tc.get("classname", ""),
            }
            fail_el = tc.find("failure")
            if fail_el is not None:
                case["failure"] = (fail_el.text or fail_el.get("message") or "").strip()
                failed_cases.append({"suite": ts_name, **case})
            cases.append(case)
        suites.append({
            "name": ts_name,
            "tests": int(ts.get("tests", 0) or 0),
            "failures": int(ts.get("failures", 0) or 0),
            "time": ts.get("time", "0"),
            "cases": cases,
        })
        if not timestamp:
            timestamp = ts.get("timestamp")
    return suites, failed_cases, timestamp


def _find_summary_path(root, build_dir, name=None):
    """查找 ut-summary.json：优先选中的 build_dir，其次各候选构建目录。
    适配 summary 与构建产物分布在不同目录的情况（如构建在 build/、
    summary+coverage 在 build-ut/）。返回 (Path|None, summary_bd)。"""
    bd = root / build_dir
    sp = bd / "ut-summary.json"
    if sp.is_file():
        return sp, bd
    cands = load_config().get("test", {}).get(
        "build_dir_candidates", ["build-ut", "build-test", "build-ut-m3", "build"])
    cfg = _project_build_dir_config(name)
    test_dir = _test_dir_name(name)
    ordered = []
    if cfg:
        ordered.append(cfg)
    if test_dir:
        td = f"build-{test_dir}"
        if td not in ordered:
            ordered.append(td)
    for d in cands:
        if d not in ordered:
            ordered.append(d)
    for d in ordered:
        if d == build_dir:
            continue
        p = root / d
        f = p / "ut-summary.json"
        if f.is_file():
            return f, p
    return None, bd


def collect_test_results(name):
    """读本地项目测试结果（ut-summary + gtest XML + html 可用性）。
    ut-summary.json 缺失时从 gtest XML 聚合 + coverage.info 解析。返回 dict 或 None。"""
    path = _project_source_path(name)
    if not path:
        return None
    build_dir = find_build_dir(path, name=name)
    if not build_dir:
        return {"project": name, "local_path": path, "build_dir": None,
                "available": False, "reason": "no build dir"}
    bd = Path(path) / build_dir
    # gtest XML（per-target 多文件：聚合全部套件，last_run 取最新时间戳）
    suites, failed_cases, last_run = [], [], None
    report_dir = bd / "report"
    if report_dir.is_dir():
        xmls = sorted(report_dir.glob("*.xml"), key=lambda f: f.stat().st_mtime, reverse=True)
        for xf in xmls:
            s, fc, ts = parse_gtest_xml(xf)
            suites.extend(s)
            failed_cases.extend(fc)
            if ts and (last_run is None or ts > last_run):
                last_run = ts
    # ut-summary.json（有则用，无则从 gtest XML + coverage.info 现算）
    # 优先读选中 build_dir 下的；缺失则在候选目录里找（适配 summary 与构建产物分离）
    summary = {}
    sp, summary_bd = _find_summary_path(Path(path), build_dir, name)
    if sp:
        try:
            summary = json.loads(sp.read_text("utf-8"))
        except (OSError, ValueError):
            pass
    tc = summary.get("test_cases", {})
    lc = summary.get("line_coverage", {})
    fc = summary.get("function_coverage", {})
    # fallback: ut-summary 缺失时从 gtest XML 聚合测试统计
    if not tc and suites:
        total = sum(s["tests"] for s in suites)
        failed = sum(s["failures"] for s in suites)
        tc = {"total": total, "passed": total - failed, "failed": failed}
    # fallback: 覆盖率从 coverage.info 解析（选中目录或 summary 所在目录）
    if not lc or not fc:
        info = _parse_lcov_info_summary(bd / "coverage.info")
        if info is None and summary_bd != bd:
            info = _parse_lcov_info_summary(summary_bd / "coverage.info")
        if info:
            if not lc:
                lc = info["line"]
            if not fc:
                fc = info["function"]
    # coverage html（优先选中目录，其次 summary 所在目录）
    html_dir = bd / "html"
    if not (html_dir.is_dir() and (html_dir / "index-sort-f.html").is_file()) and summary_bd != bd:
        alt = summary_bd / "html"
        if alt.is_dir() and (alt / "index-sort-f.html").is_file():
            html_dir = alt
    cov_avail = html_dir.is_dir() and (html_dir / "index-sort-f.html").is_file()
    return {
        "project": name,
        "local_path": path,
        "build_dir": build_dir,
        "available": True,
        "last_run": last_run,
        "test_summary": {
            "total": tc.get("total", 0),
            "passed": tc.get("passed", 0),
            "failed": tc.get("failed", 0),
            "line_coverage": lc.get("coverage", ""),
            "function_coverage": fc.get("coverage", ""),
        },
        "test_suites": suites,
        "failed_cases": failed_cases,
        "coverage_html_available": cov_avail,
        "coverage_html_path": "html" if cov_avail else None,
    }


def _parse_lcov_info_summary(info_path):
    """解析 lcov .info 文件，聚合所有 record 的 LF/LH/FNF/FNH，返回覆盖率 dict。"""
    try:
        text = Path(info_path).read_text("utf-8", errors="ignore")
    except OSError:
        return None
    lf = lh = fnf = fnh = 0
    for line in text.splitlines():
        if line.startswith("LF:"):
            lf += int(line[3:].split(",")[0] or 0)
        elif line.startswith("LH:"):
            lh += int(line[3:].split(",")[0] or 0)
        elif line.startswith("FNF:"):
            fnf += int(line[4:].split(",")[0] or 0)
        elif line.startswith("FNH:"):
            fnh += int(line[4:].split(",")[0] or 0)
    if lf == 0 and fnf == 0:
        return None
    def pct(p, t):
        return f"{round(p / t * 100, 2)}%" if t else "0%"
    return {
        "line": {"total": lf, "passed": lh, "failed": lf - lh, "coverage": pct(lh, lf)},
        "function": {"total": fnf, "passed": fnh, "failed": fnf - fnh, "coverage": pct(fnh, fnf)},
    }


def infer_phases(project_path, build_dir, project_name):
    """自动发现各阶段命令（phases 字段缺失时回退）。"""
    build_path = Path(project_path) / build_dir
    nproc = os.cpu_count() or 8
    phases = {
        "configure": "cmake -DCMAKE_BUILD_TYPE=Debug ..",
        "build": f"make -j{min(nproc, 16)}",
    }
    # test: 探测测试二进制（ATUT 约定 <test_dir>/src/test_*，兼容 tests/、build 根 ut_*、
    # 以及 deepin-image-viewer 的 <name>-test 后缀命名）
    def _is_test_bin(f):
        nm = f.name
        return (nm.startswith("test_") or nm.endswith("-test")
                or nm.endswith("_test"))
    test_cmd = ""
    test_dir = _test_dir_name(project_name) or "tests"
    for cand_dir in (f"{test_dir}/src", "tests", "test"):
        d = build_path / cand_dir
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and os.access(f, os.X_OK) and _is_test_bin(f):
                    test_cmd = f"./{cand_dir}/{f.name} --gtest_output=xml:./report/report_{project_name}.xml"
                    break
            if test_cmd:
                break
    if not test_cmd and (build_path / "CTestTestfile.cmake").exists():
        test_cmd = "ctest --output-on-failure"
    if not test_cmd:
        for f in build_path.glob("ut_*"):
            if f.is_file() and os.access(f, os.X_OK):
                test_cmd = f"./{f.name} --gtest_output=xml:./report/report_{project_name}.xml"
                break
    phases["test"] = test_cmd
    phases["coverage"] = "lcov -d . -c -o coverage.info && genhtml -o html coverage.info"
    # summary: 探测 gen-ut-summary.py
    summary_cmd = ""
    for cand in ["tests/gen-ut-summary.py", "../tests/gen-ut-summary.py"]:
        if (Path(project_path) / cand).exists():
            summary_cmd = f"python3 {cand}"
            break
    phases["summary"] = summary_cmd
    return phases


def _get_build_phases(name, project_path, build_dir):
    """取项目 phases：registry 配置优先，缺失时 infer_phases 补全。"""
    e = registry_index().get(name)
    phases = {}
    if e:
        b = e.get("build") or {}
        phases = dict(b.get("phases") or {})
        # 兼容旧 flat 字段
        if not phases.get("configure") and b.get("configure"):
            phases["configure"] = b["configure"]
        if not phases.get("build") and b.get("build_cmd"):
            phases["build"] = b["build_cmd"]
        if not phases.get("test") and b.get("test_cmd"):
            phases["test"] = b["test_cmd"]
    inferred = infer_phases(project_path, build_dir, name)
    for k in ("configure", "build", "test", "coverage", "summary"):
        if not phases.get(k):
            phases[k] = inferred.get(k, "")
    return phases


def _generate_ut_summary(bd, name):
    """summary 阶段：聚合 gtest XML + coverage.info，生成 ut-summary.json。"""
    bd = Path(bd)
    suites, _, _ = [], [], None
    report_dir = bd / "report"
    if report_dir.is_dir():
        xmls = sorted(report_dir.glob("*.xml"), key=lambda f: f.stat().st_mtime, reverse=True)
        if xmls:
            suites, _, _ = parse_gtest_xml(xmls[0])
    total = sum(s["tests"] for s in suites)
    failed = sum(s["failures"] for s in suites)
    info = _parse_lcov_info_summary(bd / "coverage.info") or {}
    line = info.get("line", {"total": 0, "passed": 0, "failed": 0, "coverage": ""})
    func = info.get("function", {"total": 0, "passed": 0, "failed": 0, "coverage": ""})
    summary = {
        "project": name,
        "test_cases": {"total": total, "passed": total - failed, "failed": failed},
        "line_coverage": line,
        "function_coverage": func,
    }
    try:
        (bd / "ut-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass
    return summary


# ── 测试运行器（Phase 2：分阶段、可并行）──

class TestRunner:
    MODE_PHASES = {
        "full": ["configure", "build", "test", "coverage", "summary"],
        "test-only": ["test", "summary"],
        "test+coverage": ["test", "coverage", "summary"],
        "build+test": ["build", "test", "summary"],
        "coverage-only": ["coverage", "summary"],
        "script": ["script"],
    }
    PHASE_LABEL = {"configure": "⚙ 配置", "build": "🔨 编译",
                   "test": "🧪 测试", "coverage": "📊 采集", "summary": "📝 汇总",
                   "script": "📜 脚本"}

    def __init__(self):
        cfg = load_config().get("test", {})
        self.max_concurrent = cfg.get("max_concurrent", 2)
        self.default_timeout = cfg.get("default_timeout", 600)
        self.executor = ThreadPoolExecutor(max_workers=max(1, self.max_concurrent))
        self.running = {}
        self.lock = threading.Lock()

    def run(self, project_name, mode="full"):
        with self.lock:
            r = self.running.get(project_name)
            if r and r["state"] in ("running", "queued"):
                return {"error": "该项目已在运行", "started": False}
            if r and r["state"] in ("done", "failed", "error", "stopped"):
                del self.running[project_name]  # 清理旧记录
            active = sum(1 for x in self.running.values()
                         if x["state"] in ("running", "queued"))
            if active >= self.max_concurrent:
                return {"error": f"已达最大并行数 {self.max_concurrent}",
                        "queued": True, "started": False}
        phases = self.MODE_PHASES.get(mode, self.MODE_PHASES["full"])
        future = self.executor.submit(self._run_project, project_name, mode, phases)
        with self.lock:
            self.running[project_name] = {
                "future": future, "state": "queued", "phase": "",
                "progress": "", "log_tail": "", "started_at": time.time(),
                "elapsed": 0, "mode": mode, "result": None, "finished_at": 0,
            }
        return {"started": True, "mode": mode}

    def _run_project(self, name, mode, phase_seq):
        path = _project_source_path(name)
        if not path:
            self._finish(name, "error", result={"error": "no local source path"})
            return
        e = registry_index().get(name) or {}
        build_cfg = e.get("build") or {}
        env = dict(os.environ)
        env.update(build_cfg.get("env") or {})
        timeout = build_cfg.get("timeout") or self.default_timeout
        # 方案A：配了 build.script 时所有模式走脚本 --from-step 映射
        # （脚本自己最懂项目，旁路服务端推断的 cmake/make/gtest 命令）
        script = (build_cfg.get("script") or "").strip()
        if script:
            sp = Path(path) / script
            if not sp.is_file():
                self._finish(name, "failed", result={"error": f"脚本不存在: {script}"})
                return
            # script 模式 = from-step 1（先 clean 再全跑）；其余按步骤映射
            # run-ut.sh 步骤: 1 Prepare 2 Configure 3 Compile 4 Test 5 Coverage 6 Summary
            from_step = {"script": 1, "full": 2, "build+test": 3,
                         "test-only": 4, "test+coverage": 4,
                         "coverage-only": 5}.get(mode, 2)
            # cwd=脚本所在目录：老式脚本(test-prj-running.sh)用 ../build 等相对 cwd 的路径，
            # 必须 cwd=脚本目录；ATUT run-ut.sh 自定位($SCRIPT_DIR/$PROJECT_ROOT)不受影响。
            # --from-step 仅 ATUT 脚本支持；老式脚本不识别，不传。
            supports_step = "--from-step" in sp.read_text(encoding="utf-8", errors="ignore")
            cmd = f"bash {sp}"
            if from_step != 1 and supports_step:
                cmd += f" --from-step {from_step}"
            self._update(name, state="running", phase="script", progress="",
                         log_tail=f"$ {cmd}")
            ok = self._exec(name, cmd, sp.parent, env, timeout, "script")
            with self.lock:
                r = self.running.get(name)
                if r and r["state"] == "stopped":
                    return
            res = collect_test_results(name)
            self._finish(name, "done" if ok else "failed", result=res)
            return
        build_dir = find_build_dir(path, name=name) or _project_build_dir_config(name) or "build-ut"
        bd = Path(path) / build_dir
        # test-only / coverage-only 模式要求 build_dir 已存在
        if mode in ("test-only", "coverage-only") and not bd.is_dir():
            self._finish(name, "failed", result={
                "error": f"构建目录 {build_dir} 不存在，请先用 full 或 build+test 模式编译"})
            return
        phases = _get_build_phases(name, path, build_dir)

        def clean(*subs):
            for sub in subs:
                shutil.rmtree(bd / sub, ignore_errors=True)

        for phase in phase_seq:
            # script 模式：直接跑项目脚本（autotests/run-ut.sh 等）
            if phase == "script":
                script = _find_project_script(path, name)
                if not script:
                    self._finish(name, "failed", result={
                        "phase": "script",
                        "error": "未找到项目测试脚本（autotests/run-ut.sh 等）"})
                    return
                cmd = f"bash {script}"
                self._update(name, state="running", phase="script", progress="",
                             log_tail=f"$ {cmd}")
                ok = self._exec(name, cmd, Path(script).parent, env, timeout, "script")
                if not ok:
                    with self.lock:
                        r = self.running.get(name)
                        if r and r["state"] == "stopped":
                            return
                    self._finish(name, "failed", result={
                        "phase": "script", "error": "exit code != 0"})
                    return
                continue
            cmd = phases.get(phase, "")
            # configure 特殊：build_dir 不存在则创建；有 CMakeCache 则跳过（增量）
            if phase == "configure":
                if not bd.is_dir():
                    bd.mkdir(parents=True, exist_ok=True)
                if (bd / "CMakeCache.txt").exists():
                    continue
            if phase == "test":
                clean("report")
            if phase == "coverage":
                clean("html")
            if phase == "summary":
                if not cmd:
                    _generate_ut_summary(bd, name)
                    continue
            if not cmd:
                continue
            self._update(name, state="running", phase=phase, progress="",
                         log_tail=f"$ {cmd}")
            ok = self._exec(name, cmd, bd, env, timeout, phase)
            if not ok:
                with self.lock:
                    r = self.running.get(name)
                    # stop() 已标记 stopped，不覆盖
                    if r and r["state"] == "stopped":
                        return
                self._finish(name, "failed", result={"phase": phase, "error": "exit code != 0"})
                return
        # 全部完成
        res = collect_test_results(name)
        self._finish(name, "done", result=res)

    def _exec(self, name, cmd, cwd, env, timeout, phase):
        try:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=str(cwd), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True, text=True, bufsize=1)
        except OSError as ex:
            self._update(name, log_tail=f"启动失败: {ex}")
            return False
        with self.lock:
            if name in self.running:
                self.running[name]["process"] = proc
        log_lines = []
        t0 = time.time()
        # 后台线程读 stdout → queue：主循环可轮询超时，
        # 避免测试挂死无输出时 for-line 阻塞导致 timeout 失效
        q: "queue.Queue[str | None]" = queue.Queue()
        def _pump():
            try:
                for line in proc.stdout:
                    q.put(line)
            finally:
                q.put(None)
        threading.Thread(target=_pump, daemon=True).start()
        timed_out = False
        while True:
            try:
                line = q.get(timeout=1.0)
            except queue.Empty:
                if time.time() - t0 > timeout:
                    timed_out = True
                    break
                continue          # 进程仍活着但暂无输出
            if line is None:
                break             # stdout EOF
            line = line.rstrip()
            log_lines.append(line)
            if len(log_lines) > 500:
                log_lines = log_lines[-500:]
            progress = self._parse_progress(line, phase)
            self._update(name, phase=phase, progress=progress,
                         log_tail="\n".join(log_lines[-30:]),
                         elapsed=round(time.time() - t0, 1))
        if timed_out:
            self._kill(proc)
            self._update(name, log_tail="\n".join(log_lines[-30:]) + "\n⏱ 超时终止")
            return False
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            return False
        return proc.returncode == 0

    def _parse_progress(self, line, phase):
        if phase == "build":
            m = re.match(r"\[\s*(\d+)%\]", line)
            if m:
                return m.group(1) + "%"
        return ""

    def _kill(self, proc):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    def _update(self, name, **kw):
        with self.lock:
            r = self.running.get(name)
            if r:
                r.update(kw)

    def _finish(self, name, state, result=None):
        with self.lock:
            r = self.running.get(name)
            if r:
                r["state"] = state
                r["result"] = result
                r["elapsed"] = round(time.time() - r.get("started_at", time.time()), 1)
                r["finished_at"] = time.time()

    def status(self, name):
        with self.lock:
            r = self.running.get(name)
            if not r:
                return {"project": name, "state": "idle"}
            snap = {
                "project": name, "state": r["state"], "phase": r.get("phase", ""),
                "progress": r.get("progress", ""), "mode": r.get("mode", ""),
                "log_tail": r.get("log_tail", ""), "elapsed": r.get("elapsed", 0),
                "label": self.PHASE_LABEL.get(r.get("phase", ""), ""),
            }
            if r["state"] in ("done", "failed", "error", "stopped"):
                snap["result"] = r.get("result")
        return snap

    def status_all(self):
        with self.lock:
            names = list(self.running.keys())
        return {
            "running": [self.status(n) for n in names],
            "max_concurrent": self.max_concurrent,
            "slots_used": len(names),
        }

    def stop(self, name):
        with self.lock:
            r = self.running.get(name)
            if not r or r["state"] not in ("running", "queued"):
                return {"ok": False, "msg": "项目未在运行"}
            proc = r.get("process")
        if proc:
            self._kill(proc)
        self._finish(name, "stopped", result={"error": "用户终止"})
        return {"ok": True}

    def cleanup_finished(self, max_age=60):
        """清理完成超过 max_age 秒的记录（避免 status 列表无限增长）。"""
        now = time.time()
        with self.lock:
            stale = [n for n, r in self.running.items()
                     if r["state"] in ("done", "failed", "error", "stopped")
                     and now - r.get("finished_at", now) > max_age]
            for n in stale:
                del self.running[n]


TEST_RUNNER = None


def get_test_runner():
    global TEST_RUNNER
    if TEST_RUNNER is None:
        TEST_RUNNER = TestRunner()
    return TEST_RUNNER


COVERAGE_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".gif": "image/gif",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _resolve_coverage_theme(query_theme=None):
    """解析覆盖率主题，返回 (css_path, override)。
    utie-auto 跟随 ?theme=dark|light 参数（或 cookie）；override=False 时不拦截。
    """
    cfg = load_config().get("coverage", {})
    themes = cfg.get("themes", {})
    theme_key = cfg.get("theme", "utie-auto")
    if theme_key == "utie-auto":
        if (query_theme or "").lower() == "dark":
            theme_key = "utie-dark"
        else:
            theme_key = "utie-auto"  # light 默认
    entry = themes.get(theme_key)
    if not entry:
        return None, False
    css = entry.get("css")
    override = bool(entry.get("override", False))
    if not css:
        return None, override
    css_path = Path(css)
    if not css_path.is_absolute():
        css_path = (DEFAULT_ROOT / css).resolve()
    if css_path.is_file():
        return css_path, override
    return None, override


# ── HTTP Handler ──

def _find_coverage_entry(html_root):
    """自动探测 LCOV html/ 入口页。
    优先级：index.html → cov_*.html → index-sort-f.html。
    有些项目脚本会把 index.html 重命名为 cov_<name>.html。"""
    root = Path(html_root)
    if (root / "index.html").is_file():
        return "index.html"
    # cov_*.html（项目脚本重命名后的入口）
    for f in sorted(root.glob("cov_*.html")):
        return f.name
    if (root / "index-sort-f.html").is_file():
        return "index-sort-f.html"
    return "index.html"  # 不存在则 404


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

    def _file(self, path, mime, set_cookie=None):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
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

    def _serve_coverage(self, p, query_str):
        """代理项目 LCOV html/ 静态资源，拦截 gcov.css 注入 UTIE 主题。
        路径形如 /api/coverage/<name>/[<sub-path>]，默认 index-sort-f.html。
        主题传递：URL ?theme=dark|light → cookie utie-cov-theme → 默认 light。
        """
        rest = p[len("/api/coverage/"):]
        parts = rest.split("/", 1)
        name = parts[0]
        if not re.match(r"^[\w.\-]+$", name):
            self._json({"error": "bad name"}, 400)
            return
        sub = parts[1] if len(parts) > 1 else ""
        # 路径遍历保护
        if ".." in sub.split("/"):
            self._json({"error": "bad path"}, 400)
            return
        path = _project_source_path(name)
        if not path:
            self._json({"error": "no local source path", "project": name}, 404)
            return
        bd = find_build_dir(path, name=name)
        if not bd:
            self._json({"error": "no build dir", "project": name}, 404)
            return
        html_root = (Path(path) / bd / "html").resolve()
        if not sub:
            # 自动探测入口页：index.html → cov_*.html → index-sort-f.html
            sub = _find_coverage_entry(html_root)

        try:
            target = (html_root / sub).resolve()
        except OSError:
            self._json({"error": "bad path"}, 400)
            return
        try:
            target.relative_to(html_root)
        except ValueError:
            self._json({"error": "out of bounds"}, 400)
            return
        q = parse_qs(query_str)
        req_theme = (q.get("theme") or [""])[0].lower()
        # 解析主题：URL 参数 → cookie → 默认
        cookie_theme = ""
        ck = self.headers.get("Cookie") or ""
        for pair in ck.split(";"):
            k, _, v = pair.strip().partition("=")
            if k == "utie-cov-theme":
                cookie_theme = v.lower()
        eff_theme = req_theme or cookie_theme or "light"
        # HTML 页面带 ?theme= 时设 cookie，供后续 gcov.css 读取
        set_cookie = None
        if target.suffix == ".html" and req_theme in ("dark", "light"):
            set_cookie = f"utie-cov-theme={req_theme}; Path=/api/coverage/{name}; Max-Age=3600; SameSite=Lax"
        # gcov.css 拦截：注入 UTIE 主题
        if sub == "gcov.css":
            css_path, override = _resolve_coverage_theme(eff_theme)
            if override and css_path:
                self._file(css_path, "text/css; charset=utf-8", set_cookie=set_cookie)
                return
            # override=False 或主题文件丢失：透传原始 gcov.css
        if not target.is_file():
            self._json({"error": "not found", "path": sub}, 404)
            return
        mime = COVERAGE_MIME.get(target.suffix.lower(), "application/octet-stream")
        self._file(target, mime, set_cookie=set_cookie)

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
        elif p.startswith("/api/test/results/"):
            name = p[len("/api/test/results/"):].split("/")[0]
            if not re.match(r"^[\w.\-]+$", name):
                self._json({"error": "bad name"}, 400)
                return
            res = collect_test_results(name)
            if res is None:
                self._json({"project": name, "available": False,
                            "reason": "no local source path"}, 200)
            else:
                self._json(res)
        elif p == "/api/test/status":
            tr = get_test_runner()
            tr.cleanup_finished()
            self._json(tr.status_all())
        elif p.startswith("/api/test/status/"):
            name = p[len("/api/test/status/"):].split("/")[0]
            if not re.match(r"^[\w.\-]+$", name):
                self._json({"error": "bad name"}, 400)
                return
            self._json(get_test_runner().status(name))
        elif p == "/api/test/phases":
            q = parse_qs(u.query)
            name = (q.get("name") or [""])[0]
            if not name or not re.match(r"^[\w.\-]+$", name):
                self._json({"error": "bad name"}, 400)
                return
            path = _project_source_path(name)
            if not path:
                self._json({"error": "no local source path"}, 404)
                return
            bd = find_build_dir(path, name=name) or "build-ut"
            self._json({"name": name, "build_dir": bd,
                        "phases": _get_build_phases(name, path, bd)})
        elif p.startswith("/api/coverage/"):
            self._serve_coverage(p, urlparse(self.path).query)
        elif p in ("/styles.css",) or p.startswith("/js/") or p.startswith("/vendor/"):
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
        elif u.path.startswith("/api/test/run/"):
            name = u.path[len("/api/test/run/"):]
            if not re.match(r"^[\w.\-]+$", name):
                self._json({"error": "bad name"}, 400)
                return
            body = self._body_json()
            mode = (body.get("mode") or "full").strip()
            if mode not in TestRunner.MODE_PHASES:
                self._json({"error": f"unknown mode: {mode}"}, 400)
                return
            res = get_test_runner().run(name, mode)
            code = 200 if res.get("started") or res.get("queued") else 409
            self._json(res, code)
        elif u.path.startswith("/api/test/stop/"):
            name = u.path[len("/api/test/stop/"):]
            if not re.match(r"^[\w.\-]+$", name):
                self._json({"error": "bad name"}, 400)
                return
            self._json(get_test_runner().stop(name))
        else:
            self._json({"error": "not found"}, 404)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """客户端提前断开（页面刷新/关闭时取消轮询请求）属正常现象，
    BrokenPipe/ConnectionReset 静默处理，避免日志被 traceback 刷屏；其余错误照常打印。"""

    def handle_error(self, request, client_address):
        _, exc, _tb = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            self.close_request(request)
            return
        super().handle_error(request, client_address)


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
            httpd = QuietThreadingHTTPServer(("127.0.0.1", port), Handler)
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
