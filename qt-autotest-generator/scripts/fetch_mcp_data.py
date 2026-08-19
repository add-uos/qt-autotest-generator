#!/usr/bin/env python3
"""
fetch_mcp_data.py — 端到端：MCP 知识图谱 → .ut-inventory.json

一条命令完成函数重要性探测的全流程数据采集与评分：

  1. HTTP MCP 收集所有 Method 节点（search_graph 分页，file_pattern 过滤 3rdparty）
  2. query_graph 检测继承链（QDBusAbstractAdaptor 服务端 / QThread 并发基类 / GUI 基类）
  3. query_graph 获取 DBus Adaptor 类方法 → dbus_slots
  4. search_code 检测 Q_INVOKABLE / Q_PLUGIN_METADATA（best-effort）
  5. 客户端计算 P75 非零 in_degree
  6. 调用 scan_inventory.build_inventory() 生成 .ut-inventory.json

用法:
  python3 fetch_mcp_data.py \\
    --project home-uos-service-codebase-repos-dde-file-manager \\
    --file-pattern "src/**" \\
    --output .ut-inventory.json

示例:
  # dde-file-manager（排除 3rdparty）
  python3 fetch_mcp_data.py \\
    --project home-uos-service-codebase-repos-dde-file-manager \\
    --file-pattern "src/**" \\
    --output /tmp/dde-file-manager/.ut-inventory.json

  # deepin-reader（排除 pdfium 等 3rdparty）
  python3 fetch_mcp_data.py \\
    --project home-uos-service-codebase-repos-deepin-reader \\
    --file-pattern "reader/**" \\
    --output /tmp/deepin-reader/.ut-inventory.json

  # deepin-calculator（无 3rdparty，无需 file-pattern）
  python3 fetch_mcp_data.py \\
    --project home-uos-service-codebase-repos-deepin-calculator \\
    --output /tmp/deepin-calculator/.ut-inventory.json

依赖: scan_inventory.py（同目录，提供 build_inventory() 评分逻辑）
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.error

# ── 配置 ──

MCP_URL = "http://10.8.12.80:13626/mcp"

# 服务端 DBus Adaptor — 契约级测试目标，其 public 方法 → dbus_slot (+3)
DBUS_ADAPTOR_BASES = ["QDBusAbstractAdaptor"]
# 客户端 DBus Interface — 自动生成 call() 包装，不加 dbus_slot
DBUS_INTERFACE_BASES = ["QDBusAbstractInterface"]

CONCURRENT_BASES = [
    "QThread", "QThreadPool", "QMutex", "QReadWriteLock",
    "QSemaphore", "QAtomicInt", "QAtomicInteger", "QWaitCondition",
]

# GUI 基类 — 继承自这些基类的类在测试中需特殊处理（QCoreApplication / 不直接实例化 / 链 Widgets）
GUI_BASES = [
    "QWidget", "QDialog", "QMainWindow",
    "DMainWindow", "DFrame", "DWidget", "DAbstractDialog",
]


# ── MCP HTTP 客户端 ──

class MCPClient:
    """Minimal MCP HTTP JSON-RPC 2.0 client."""

    def __init__(self, url=MCP_URL, timeout=120):
        self.url = url
        self.timeout = timeout
        self.session_id = None
        self._id = 0

    def _next_id(self):
        self._id += 1
        return self._id

    def initialize(self):
        """Initialize MCP session and capture session ID."""
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0", "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fetch-mcp-data", "version": "1.0"},
            },
            "id": self._next_id(),
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers)
        resp = urllib.request.urlopen(req, timeout=self.timeout)
        self.session_id = resp.headers.get("Mcp-Session-Id")
        body = resp.read().decode()
        result = json.loads(body)
        if "error" in result:
            raise RuntimeError(f"Initialize error: {result['error']}")
        # Send initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        req2 = urllib.request.Request(
            self.url, data=json.dumps(notif).encode(),
            headers={**headers, "Mcp-Session-Id": self.session_id})
        urllib.request.urlopen(req2, timeout=self.timeout)
        return result

    def call_tool(self, name, arguments, retries=3):
        """Call an MCP tool with retry on session expiry."""
        for attempt in range(retries):
            try:
                payload = {
                    "jsonrpc": "2.0", "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                    "id": self._next_id(),
                }
                headers = {
                    "Content-Type": "application/json",
                    "Mcp-Session-Id": self.session_id,
                }
                req = urllib.request.Request(
                    self.url, data=json.dumps(payload).encode(), headers=headers)
                resp = urllib.request.urlopen(req, timeout=self.timeout)
                result = json.loads(resp.read().decode())
                if "error" in result:
                    raise RuntimeError(
                        f"RPC error: {json.dumps(result['error'], ensure_ascii=False)[:300]}")
                # MCP returns content as text blocks
                content = result.get("result", {}).get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        try:
                            return json.loads(block["text"])
                        except json.JSONDecodeError:
                            return block["text"]
                return content
            except Exception as e:
                if attempt < retries - 1:
                    wait = 2 * (attempt + 1)
                    print(f"   ⚠️  {name} error (retry {attempt + 1}/{retries}): {e}")
                    time.sleep(wait)
                    # Re-initialize session
                    try:
                        self.initialize()
                    except Exception:
                        pass
                else:
                    raise


# ── 数据采集步骤 ──

def collect_methods(client, project, file_pattern=None, limit=2000):
    """Step 1: Paginated search_graph to collect all Method + Function nodes.

    Function nodes include free C/C++ functions (main, helpers, etc.).
    Some Function entries are noise (macros, using-declarations, misclassified
    constructors) — filtered later by scan_inventory.py.
    """
    all_methods = []
    all_functions = []
    offset = 0
    page = 0
    total = 0

    args_base = {"project": project, "limit": limit, "offset": 0}
    if file_pattern:
        args_base["file_pattern"] = file_pattern

    print(f"\n📊 [1/5] Collecting Method + Function nodes...")
    if file_pattern:
        print(f"   file_pattern: {file_pattern}")

    # 1A. Method nodes (class methods)
    args = {**args_base, "label": "Method"}
    while True:
        page += 1
        args["offset"] = offset
        data = client.call_tool("search_graph", args)
        results = data.get("results", [])
        total = data.get("total", 0)
        has_more = data.get("has_more", False)

        all_methods.extend(results)
        if page == 1:
            print(f"   Method: total={total}")

        if not has_more or not results:
            break
        offset += limit

    # 1B. Function nodes (free functions)
    offset = 0
    page = 0
    args = {**args_base, "label": "Function"}
    while True:
        page += 1
        args["offset"] = offset
        data = client.call_tool("search_graph", args)
        results = data.get("results", [])
        total = data.get("total", 0)
        has_more = data.get("has_more", False)

        all_functions.extend(results)
        if page == 1:
            print(f"   Function: total={total}")

        if not has_more or not results:
            break
        offset += limit

    print(f"   ✅ {len(all_methods)} methods + {len(all_functions)} functions collected")
    return all_methods, all_functions


def collect_inheritance(client, project):
    """Step 2: query_graph to detect DBus / concurrent / GUI base classes.

    Returns (dbus_adaptor_classes, dbus_interface_classes, concurrent_classes, gui_classes).
    Each is a list of dicts: {name, qualified_name, file_path, base_classes}.
    """
    print(f"\n📊 [2/5] Detecting inheritance chains...")

    dbus_adaptor = []
    dbus_interface = []
    concurrent = []
    gui = []

    # DBus Adaptor (server-side, contract-level)
    for base in DBUS_ADAPTOR_BASES:
        query = (
            f"MATCH (c:Class) WHERE c.base_classes CONTAINS '{base}' "
            f"RETURN c.name, c.qualified_name, c.file_path, c.base_classes"
        )
        data = client.call_tool("query_graph", {"project": project, "query": query})
        for row in data.get("rows", []):
            dbus_adaptor.append({
                "name": row[0], "qualified_name": row[1],
                "file_path": row[2], "base_classes": _parse_bases(row[3]),
            })

    # DBus Interface (client-side proxy — recorded but NOT contract-level)
    for base in DBUS_INTERFACE_BASES:
        query = (
            f"MATCH (c:Class) WHERE c.base_classes CONTAINS '{base}' "
            f"RETURN c.name, c.qualified_name, c.file_path, c.base_classes"
        )
        data = client.call_tool("query_graph", {"project": project, "query": query})
        for row in data.get("rows", []):
            dbus_interface.append({
                "name": row[0], "qualified_name": row[1],
                "file_path": row[2], "base_classes": _parse_bases(row[3]),
            })

    # Concurrent base classes
    for base in CONCURRENT_BASES:
        query = (
            f"MATCH (c:Class) WHERE c.base_classes CONTAINS '{base}' "
            f"RETURN c.name, c.qualified_name, c.file_path, c.base_classes"
        )
        data = client.call_tool("query_graph", {"project": project, "query": query})
        for row in data.get("rows", []):
            concurrent.append({
                "name": row[0], "qualified_name": row[1],
                "file_path": row[2], "base_classes": _parse_bases(row[3]),
            })

    # GUI base classes → is_gui（Mode 2 测试生成的环境约束依据）
    for base in GUI_BASES:
        query = (
            f"MATCH (c:Class) WHERE c.base_classes CONTAINS '{base}' "
            f"RETURN c.name, c.qualified_name, c.file_path, c.base_classes"
        )
        data = client.call_tool("query_graph", {"project": project, "query": query})
        for row in data.get("rows", []):
            gui.append({
                "name": row[0], "qualified_name": row[1],
                "file_path": row[2], "base_classes": _parse_bases(row[3]),
            })

    # Deduplicate
    dbus_adaptor = _dedup_classes(dbus_adaptor)
    dbus_interface = _dedup_classes(dbus_interface)
    concurrent = _dedup_classes(concurrent)
    gui = _dedup_classes(gui)

    print(f"   DBus Adaptor (server): {len(dbus_adaptor)}")
    print(f"   DBus Interface (client): {len(dbus_interface)}")
    print(f"   Concurrent: {len(concurrent)}")
    print(f"   GUI: {len(gui)}")
    return dbus_adaptor, dbus_interface, concurrent, gui


def collect_dbus_slots(client, project, dbus_adaptor_classes):
    """Step 3: Get methods of each DBus Adaptor class → dbus_slots map.

    Only QDBusAbstractAdaptor (server-side) slots are contract-level test targets.
    QDBusAbstractInterface (client proxy) methods are auto-generated call() wrappers.

    Returns dbus_slots = {class_qualified_name: [method_name, ...]}.
    """
    print(f"\n📊 [3/5] Collecting DBus Adaptor slots...")
    dbus_slots = {}

    for cls in dbus_adaptor_classes:
        cls_name = cls["name"]
        cls_qn = cls["qualified_name"]
        # query_graph: all methods whose parent_class contains the class name
        query = (
            f"MATCH (m:Method) WHERE m.parent_class CONTAINS '{cls_name}' "
            f"RETURN m.name, m.qualified_name"
        )
        data = client.call_tool("query_graph", {"project": project, "query": query})
        slots = []
        for row in data.get("rows", []):
            method_name = row[0]
            # Filter out constructors, destructors, and Q_SIGNALS (emit*)
            if method_name == cls_name:
                continue  # constructor
            if method_name.startswith("~"):
                continue  # destructor
            if method_name.startswith("emit"):
                continue  # likely Q_SIGNALS
            slots.append(method_name)

        if slots:
            dbus_slots[cls_qn] = slots
            print(f"   {cls_name}: {len(slots)} slots")

    print(f"   ✅ {sum(len(v) for v in dbus_slots.values())} DBus slots total")
    return dbus_slots


def collect_qt_macros(client, project):
    """Step 4: search_code for Q_INVOKABLE and Q_PLUGIN_METADATA (best-effort).

    Returns (q_invokables, q_plugins):
      q_invokables = {class_short_name: [method_name, ...]}
      q_plugins = {class_short_name: True}
    """
    print(f"\n📊 [4/5] Detecting Q_INVOKABLE / Q_PLUGIN_METADATA...")
    q_invokables = {}
    q_plugins = {}

    # Q_INVOKABLE — full mode to extract method names from source
    try:
        data = client.call_tool("search_code", {
            "project": project, "pattern": "Q_INVOKABLE",
            "mode": "full", "limit": 100,
        })
        for res in data.get("results", []):
            src = res.get("source", "")
            qn = res.get("qualified_name", "")
            # Extract method name: Q_INVOKABLE [virtual] <type> <name>(
            for m in re.finditer(
                r"Q_INVOKABLE\s+(?:virtual\s+)?[\w:<>&*\s,]+?\s+(\w+)\s*\(", src
            ):
                method_name = m.group(1)
                # Extract class short name from qualified_name (second-to-last segment)
                class_name = _extract_class_from_qn(qn)
                if class_name:
                    q_invokables.setdefault(class_name, []).append(method_name)
        print(f"   Q_INVOKABLE: {sum(len(v) for v in q_invokables.values())} methods "
              f"in {len(q_invokables)} classes")
    except Exception as e:
        print(f"   ⚠️  Q_INVOKABLE search failed: {e}")

    # Q_PLUGIN_METADATA — files mode, mark classes in those files
    try:
        data = client.call_tool("search_code", {
            "project": project, "pattern": "Q_PLUGIN_METADATA",
            "mode": "files", "limit": 50,
        })
        for file_path in data.get("files", []):
            # Extract class name from file path (filename without extension)
            basename = os.path.basename(file_path)
            class_name = os.path.splitext(basename)[0]
            q_plugins[class_name] = True
        print(f"   Q_PLUGIN_METADATA: {len(q_plugins)} plugin classes")
    except Exception as e:
        print(f"   ⚠️  Q_PLUGIN_METADATA search failed: {e}")

    return q_invokables, q_plugins


def compute_p75_nonzero(methods):
    """Step 5: Client-side P75 on non-zero in_degree values.

    P75 must exclude zero in_degree — including zeros makes P75=0 or 1,
    causing almost all methods with callers to be flagged as "high fan-in".
    """
    print(f"\n📊 [5/5] Computing P75 (non-zero in_degree)...")
    in_degrees = sorted(
        m.get("in_degree", 0) or 0 for m in methods
        if m.get("in_degree", 0) and m.get("in_degree", 0) > 0
    )
    n = len(in_degrees)
    if n == 0:
        p75 = 5  # fallback
        print(f"   No non-zero in_degree values, fallback P75={p75}")
        return p75

    p75 = in_degrees[math.ceil(0.75 * n) - 1]
    print(f"   {n} non-zero values, P75={p75} "
          f"(range {in_degrees[0]}–{in_degrees[-1]})")
    return p75


# ── 辅助函数 ──

def _parse_bases(base_classes_raw):
    """Parse base_classes from query_graph (JSON string or list)."""
    if isinstance(base_classes_raw, list):
        return base_classes_raw
    if isinstance(base_classes_raw, str) and base_classes_raw:
        try:
            parsed = json.loads(base_classes_raw)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        # Comma-separated fallback
        return [b.strip() for b in base_classes_raw.split(",") if b.strip()]
    return []


def _dedup_classes(classes):
    """Deduplicate classes by qualified_name."""
    seen = set()
    result = []
    for cls in classes:
        qn = cls.get("qualified_name", "")
        if qn not in seen:
            seen.add(qn)
            result.append(cls)
    return result


def _extract_class_from_qn(qn):
    """Extract class short name from qualified_name.

    e.g. 'proj.path.ClassName.methodName' → 'ClassName'
    """
    if not qn:
        return None
    parts = qn.split(".")
    if len(parts) >= 2:
        return parts[-2]  # second-to-last segment
    return parts[-1] if parts else None


# ── 主流程 ──

def build_mcp_dump(project, methods, functions, dbus_adaptor, dbus_interface,
                   concurrent, gui, dbus_slots, q_invokables, q_plugins, p75):
    """Assemble mcp_dump dict for scan_inventory.build_inventory()."""
    return {
        "project": project,
        "methods": methods,
        "functions": functions,     # free C/C++ functions (main, helpers, etc.)
        "classes": [],
        "dbus_classes": dbus_adaptor,       # server-side Adaptor (contract-level)
        "dbus_interface_classes": dbus_interface,  # client-side (not contract-level)
        "concurrent_classes": concurrent,
        "gui_classes": gui,                 # → inventory.classes[].is_gui
        "dbus_slots": dbus_slots,
        "dbus_signals": {},
        "q_invokables": q_invokables,
        "q_plugins": q_plugins,
        "in_degree_p75_nonzero": p75,
    }


def main():
    parser = argparse.ArgumentParser(
        description="端到端 MCP → .ut-inventory.json 函数重要性探测")
    parser.add_argument("--project", required=True, help="MCP 项目名")
    parser.add_argument("--file-pattern", default=None,
                        help="Glob 过滤源码目录 (e.g. 'src/**', 'reader/**')")
    parser.add_argument("--output", "-o", required=True,
                        help="输出 .ut-inventory.json 路径")
    parser.add_argument("--mcp-url", default=MCP_URL, help="MCP HTTP 端点")
    parser.add_argument("--limit", type=int, default=2000,
                        help="search_graph 分页大小 (default: 2000)")
    parser.add_argument("--base-sha", default="unknown", help="Git base SHA")
    parser.add_argument("--summary", action="store_true",
                        help="同时输出 Markdown 摘要")
    parser.add_argument("--keep-dump", action="store_true",
                        help="保留中间 mcp_dump.json 文件")
    args = parser.parse_args()

    # 确保能 import scan_inventory（同目录）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        import scan_inventory
    except ImportError as e:
        print(f"❌ 无法导入 scan_inventory.py: {e}", file=sys.stderr)
        print(f"   请确保 scan_inventory.py 在同一目录: {script_dir}", file=sys.stderr)
        sys.exit(1)

    # 连接 MCP
    client = MCPClient(url=args.mcp_url)
    print(f"🔗 Connecting to {args.mcp_url}...")
    client.initialize()
    print(f"✅ Session: {client.session_id[:12]}...")
    print(f"📋 Project: {args.project}")

    # Step 1-5: 采集数据
    methods, functions = collect_methods(client, args.project, args.file_pattern, args.limit)
    dbus_adaptor, dbus_interface, concurrent, gui = collect_inheritance(client, args.project)
    dbus_slots = collect_dbus_slots(client, args.project, dbus_adaptor)
    q_invokables, q_plugins = collect_qt_macros(client, args.project)
    p75 = compute_p75_nonzero(methods + functions)

    # 构建 mcp_dump
    mcp_dump = build_mcp_dump(
        args.project, methods, functions, dbus_adaptor, dbus_interface,
        concurrent, gui, dbus_slots, q_invokables, q_plugins, p75)

    if args.keep_dump:
        dump_path = os.path.join(
            os.path.dirname(args.output) or ".",
            os.path.basename(args.output).replace(".json", "") + "_mcp_dump.json")
        with open(dump_path, "w") as f:
            json.dump(mcp_dump, f, ensure_ascii=False)
        print(f"\n💾 mcp_dump saved to {dump_path}")

    # 调用 scan_inventory 评分
    print(f"\n🔧 Scoring methods via scan_inventory.build_inventory()...")
    inventory = scan_inventory.build_inventory(mcp_dump, args.project, args.base_sha)

    # 写 .ut-inventory.json
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2, ensure_ascii=False)
    print(f"✅ 写入 {args.output} ({len(inventory['methods'])} 方法)")

    # 写摘要
    if args.summary:
        summary_path = args.output.replace(".json", "-summary.md")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(scan_inventory.generate_summary(inventory))
        print(f"✅ 写入 {summary_path}")

    # 打印概要
    stats = inventory["scan_stats"]
    print(f"\n{'=' * 60}")
    print(f"项目: {args.project}")
    print(f"可测试: {stats['testable']}  不可测试: {stats['non_testable']}")
    print(f"high: {stats['high']}  mid: {stats['mid']}  low: {stats['low']}")
    print(f"待复核: {stats['review_pending']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
