#!/usr/bin/env python3
"""
从 MCP list_projects 同步项目注册表 (projects.json)

- 新项目: 自动加入 (mcp_name/nodes→size/git.branch)
- 已有项目: 更新 nodes/branch (可选按 nodes 重算规模), 保留 enabled/source/build 等手工字段
- MCP 已不存在的项目: 保留不动 (报告中标注)

规模阈值: nodes <1000=S, <5000=M, <15000=L, >=15000=XL
用法:
  python3 sync-registry-from-mcp.py [--dry-run] [--keep-size] [--json] [--mcp-url URL]
"""
import json, os, sys, argparse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REGISTRY = SCRIPT_DIR.parent / "projects.json"
CONFIG = SCRIPT_DIR.parent / "config.json"
MCP_PREFIX = "home-uos-service-codebase-repos-"


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


def list_mcp_projects(mcp_url: str, timeout: int = 60):
    """调 MCP list_projects, 返回 [{name, nodes, branch}]"""
    headers = {"Content-Type": "application/json"}

    def rpc(payload, extra=None):
        req = urllib.request.Request(
            mcp_url, data=json.dumps(payload).encode(),
            headers={**headers, **(extra or {})})
        resp = urllib.request.urlopen(req, timeout=timeout)
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            rpc._session_id = sid
        return json.loads(resp.read().decode())

    init = rpc({
        "jsonrpc": "2.0", "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "sync-registry", "version": "1.0"}},
        "id": 1,
    })
    if "error" in init:
        raise RuntimeError(f"initialize: {init['error']}")
    # 发送 initialized 通知 + 带 session 调 list_projects
    sid = getattr(rpc, "_session_id", None)
    if sid:
        try:
            urllib.request.urlopen(urllib.request.Request(
                mcp_url, data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
                headers={**headers, "Mcp-Session-Id": sid}), timeout=timeout)
        except Exception:
            pass
    req = urllib.request.Request(mcp_url, data=json.dumps({
        "jsonrpc": "2.0", "method": "tools/call",
        "params": {"name": "list_projects", "arguments": {}}, "id": 2,
    }).encode(), headers={**headers, **({"Mcp-Session-Id": sid} if sid else {})})
    try:
        body = urllib.request.urlopen(req, timeout=timeout).read().decode()
    except urllib.error.HTTPError as e:
        # 带 session 重试 (初始化响应头里有)
        raise RuntimeError(f"MCP 调用失败: HTTP {e.code}") from e
    result = json.loads(body)
    if "error" in result:
        raise RuntimeError(f"list_projects: {result['error']}")
    content = result["result"]["content"][0]["text"]
    data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("projects", [])
    out = []
    for p in data:
        out.append({
            "mcp_name": p["name"],
            "gh_name": p["name"][len(MCP_PREFIX):] if p["name"].startswith(MCP_PREFIX) else p["name"],
            "nodes": p.get("nodes", 0),
            "branch": (p.get("git") or {}).get("branch", ""),
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
          or "http://10.8.12.80:13626/mcp"
    mcp_projects = list_mcp_projects(url)
    reg = load_registry()
    defaults = reg.get("defaults", {})
    by_name = {p["name"]: p for p in reg["projects"]}

    added, size_changed, branch_changed, nodes_updated = [], [], [], []
    for mp in mcp_projects:
        gh, size = mp["gh_name"], size_from_nodes(mp["nodes"])
        cur = by_name.get(gh)
        if cur is None:
            entry = build_default_entry(gh, mp["mcp_name"], mp["nodes"], size, mp["branch"], defaults)
            reg["projects"].append(entry)
            by_name[gh] = entry
            added.append({"name": gh, "size": size, "nodes": mp["nodes"]})
            continue
        cur["nodes"] = mp["nodes"]
        cur["mcp_name"] = mp["mcp_name"]
        nodes_updated.append(gh)
        if not keep_size and cur.get("size") != size:
            size_changed.append({"name": gh, "old": cur.get("size"), "new": size, "nodes": mp["nodes"]})
            cur["size"] = size
        if mp["branch"] and (cur.get("git") or {}).get("branch") != mp["branch"]:
            branch_changed.append({"name": gh, "old": (cur.get("git") or {}).get("branch"), "new": mp["branch"]})
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
        "branch_changed": branch_changed, "nodes_updated": len(nodes_updated),
        "stale": stale,
        "dry_run": dry_run,
    }


def main():
    ap = argparse.ArgumentParser(description="从 MCP 同步项目注册表")
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
    print(f"MCP 项目 {r['mcp_total']} 个, 注册表现有 {r['registry_total']} 个"
          f"{' (dry-run)' if r['dry_run'] else ''}")
    for a in r["added"]:
        print(f"  + {a['name']:<28} {a['size']:<3} nodes={a['nodes']}")
    for c in r["size_changed"]:
        print(f"  ~ {c['name']:<28} 规模 {c['old']} → {c['new']} (nodes={c['nodes']})")
    for b in r["branch_changed"]:
        print(f"  🌿 {b['name']:<26} 分支 {b['old']} → {b['new']}")
    if r["stale"]:
        print(f"  ⚠ MCP 已无: {', '.join(r['stale'])}")


if __name__ == "__main__":
    main()
