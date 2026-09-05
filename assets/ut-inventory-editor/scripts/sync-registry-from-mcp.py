#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""从 GitNexus MCP list_repos 同步项目注册表 (projects.json)

- 新项目: 自动加入（mcp_name=GitNexus 仓库名 / branch）
- 已有项目: 更新 branch, 保留 enabled/source/build/size 等字段
- GitNexus 已不存在的项目: 保留不动（报告中标注 stale）

GitNexus 迁移说明（相对旧 codebase-memory-mcp）：
  - list_projects → list_repos（分页遍历，limit ≤ 200，offset 翻页）
  - 仓库名即 gh 风格名（如 dde-file-manager），无路径前缀
  - nodes 计数取 list_repos 返回的 stats.nodes（规模阈值逻辑不变）
用法:
  python3 sync-registry-from-mcp.py [--dry-run] [--keep-size] [--json] [--mcp-url URL]
"""
import json
import os
import sys
import argparse
import importlib.util
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REGISTRY = SCRIPT_DIR.parent / "projects.json"
CONFIG = SCRIPT_DIR.parent / "config.json"
DEFAULT_MCP_URL = "https://codegraph.uniontech.com/api/mcp"

_LEGACY_PREFIX = "home-uos-service-codebase-repos-"  # 旧 MCP 路径前缀，防残留


_MCP_SCAN = None


def _load_mcp_scan():
    """加载同目录 mcp-scan.py，复用 MCPClient/list_repos_all（进程内缓存）。"""
    global _MCP_SCAN
    if _MCP_SCAN is not None:
        return _MCP_SCAN
    path = SCRIPT_DIR / "mcp-scan.py"
    spec = importlib.util.spec_from_file_location("mcp_scan", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mcp_scan"] = mod
    spec.loader.exec_module(mod)
    _MCP_SCAN = mod
    return mod


def _mcp_url_from_config():
    try:
        return json.loads(CONFIG.read_text("utf-8")).get("mcp_url")
    except (OSError, ValueError):
        return None


def size_from_nodes(n: int) -> str:
    if n < 1000: return "S"
    if n < 5000: return "M"
    if n < 15000: return "L"
    return "XL"


def list_gitnexus_repos(client):
    """list_repos 全量遍历（mcp-scan.list_repos_all：有 total 时并发拉取）
    → [{name, gh_name, branch, lastCommit, nodes}]。"""
    repos = _load_mcp_scan().list_repos_all(client)
    out = []
    for r in repos:
        name = r.get("name", "")
        stats = r.get("stats") or {}
        out.append({
            "name": name,
            "gh_name": (name[len(_LEGACY_PREFIX):]
                        if name.startswith(_LEGACY_PREFIX) else name),
            "branch": r.get("branch", "") or "",
            "lastCommit": r.get("lastCommit", "") or "",
            "nodes": int(stats.get("nodes", 0) or 0),
        })
    return out


def load_registry():
    if REGISTRY.is_file():
        try:
            return json.loads(REGISTRY.read_text("utf-8"))
        except ValueError:
            pass
    return {"defaults": {"org": "linuxdeepin", "branch": "master", "test_dir": "autotests"},
            "projects": []}


def build_default_entry(gh, mcp, nodes, size, branch, defaults):
    return {
        "name": gh, "mcp_name": mcp, "enabled": False, "size": size, "nodes": nodes,
        "git": {"org": defaults.get("org", "linuxdeepin"),
                "branch": branch or defaults.get("branch", "master")},
        "source": {"type": "mcp", "path": ""},
        "build": {"system": "cmake", "framework": "gtest", "configure": "",
                  "build_cmd": "", "test_cmd": "",
                  "test_dir": defaults.get("test_dir", "autotests"),
                  "env": {"QT_QPA_PLATFORM": "offscreen"}, "timeout": 600},
    }


def sync(dry_run=False, keep_size=False, mcp_url=None):
    url = mcp_url or os.environ.get("QTAG_MCP_URL") or _mcp_url_from_config() \
        or DEFAULT_MCP_URL
    mcp_scan = _load_mcp_scan()
    client = mcp_scan.MCPClient(url=url)
    client.initialize()
    mcp_projects = list_gitnexus_repos(client)

    reg = load_registry()
    defaults = reg.get("defaults", {})
    by_name = {p["name"]: p for p in reg["projects"]}

    added, size_changed, branch_changed = [], [], []
    for mp in mcp_projects:
        gh, size = mp["gh_name"], size_from_nodes(mp["nodes"])
        cur = by_name.get(gh)
        if cur is None:
            entry = build_default_entry(gh, mp["name"], mp["nodes"], size,
                                        mp["branch"], defaults)
            reg["projects"].append(entry)
            by_name[gh] = entry
            added.append({"name": gh, "size": size, "nodes": mp["nodes"]})
            continue
        cur["mcp_name"] = mp["name"]
        cur["nodes"] = mp["nodes"]
        if not keep_size and cur.get("size") != size:
            size_changed.append({"name": gh, "old": cur.get("size"),
                                 "new": size, "nodes": mp["nodes"]})
            cur["size"] = size
        if mp["branch"] and (cur.get("git") or {}).get("branch") != mp["branch"]:
            branch_changed.append({"name": gh,
                                   "old": (cur.get("git") or {}).get("branch"),
                                   "new": mp["branch"]})
            cur.setdefault("git", {})["branch"] = mp["branch"]

    mcp_names = {mp["gh_name"] for mp in mcp_projects}
    stale = [p["name"] for p in reg["projects"] if p["name"] not in mcp_names]

    if not dry_run:
        tmp = REGISTRY.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", "utf-8")
        bak = REGISTRY.with_suffix(".json.bak")
        if REGISTRY.is_file():
            bak.write_text(REGISTRY.read_text("utf-8"), "utf-8")
        tmp.replace(REGISTRY)

    return {
        "ok": True,
        "mcp_url": url,
        "mcp_total": len(mcp_projects),
        "registry_total": len(reg["projects"]),
        "added": added, "size_changed": size_changed,
        "branch_changed": branch_changed,
        "stale": stale,
        "dry_run": dry_run,
    }


def main():
    ap = argparse.ArgumentParser(description="从 GitNexus MCP 同步项目注册表")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写盘")
    ap.add_argument("--keep-size", action="store_true", help="不按 nodes 重算已有项目规模")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--mcp-url", default=None)
    args = ap.parse_args()
    try:
        r = sync(dry_run=args.dry_run, keep_size=args.keep_size, mcp_url=args.mcp_url)
    except Exception as e:
        r = {"ok": False, "msg": str(e)}
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r.get("ok") else 1)
    if not r.get("ok"):
        print(f"❌ {r['msg']}"); sys.exit(1)
    print(f"GitNexus 仓库 {r['mcp_total']} 个, 注册表现有 {r['registry_total']} 个"
          f"{' (dry-run)' if r['dry_run'] else ''}")
    for a in r["added"]:
        print(f"  + {a['name']:<28} {a['size']:<3} nodes={a['nodes']}")
    for c in r["size_changed"]:
        print(f"  ~ {c['name']:<28} 规模 {c['old']} → {c['new']} (nodes={c['nodes']})")
    for b in r["branch_changed"]:
        print(f"  🌿 {b['name']:<26} 分支 {b['old']} → {b['new']}")
    if r["stale"]:
        print(f"  ⚠ GitNexus 已无: {', '.join(r['stale'])}")


if __name__ == "__main__":
    main()
