#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
fetch-test-mapping.py — MCP 知识图谱 → 函数↔单元测试映射 → 回写 .ut-inventory.json

从 MCP codebase-memory-mcp 的 CALLS 关系中提取「测试文件 → 被测源码函数」映射，
统计每个源码方法/函数被多少个测试文件覆盖，回写 inventory 的 test_cover_count
（并更新 usecase_count 为 max(原值, test_cover_count) 作为下界估计）。

数据来源：
  - search_graph(file_pattern=tests/**, label=Module) → 发现所有测试模块
  - query_graph MATCH (m:Module {name:'...'})-[:CALLS]->(target) → 调用关系
  - 过滤掉 target 在 tests/ 目录的（stub 类噪声）和 Field 节点
  - 按目标 qualified_name 聚合 → test_cover_count = 覆盖测试文件数

字段说明：
  - test_cover_count: 调用该方法的测试文件数（MCP CALLS 静态分析）
  - test_files: 调用该方法的测试文件列表
  - test_source: "mcp_calls" 表示来源是 MCP 静态分析
  - usecase_count: GTest TEST_F 用例数（Mode 2 写入），
    当 test_cover_count > usecase_count 时提升为 test_cover_count（下界估计）

用法:
  # 基本用法：从 MCP 获取映射并回写 inventory
  python3 fetch-test-mapping.py \\
    --project home-uos-service-codebase-repos-deepin-image-viewer \\
    --inventory .ut-inventory.json

  # 同时输出详细映射到 JSON 文件
  python3 fetch-test-mapping.py \\
    --project home-uos-service-codebase-repos-deepin-image-viewer \\
    --inventory .ut-inventory.json \\
    --mapping-out test-mapping.json

  # dry-run：只打印映射结果不写回
  python3 fetch-test-mapping.py \\
    --project home-uos-service-codebase-repos-deepin-image-viewer \\
    --inventory .ut-inventory.json \\
    --dry-run

  # 使用已保存的映射 JSON（跳过 MCP 查询，直接回写）
  python3 fetch-test-mapping.py \\
    --inventory .ut-inventory.json \\
    --mapping-in test-mapping.json

依赖: Python 3.8+ 标准库（urllib, json, argparse）
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict

# ── 配置 ──

MCP_URL = os.environ.get("QTAG_MCP_URL", "http://10.8.12.80:13626/mcp")

# inventory 中 qualified_name 的项目前缀（MCP 图谱用完整项目名，inventory 可能用短名）
PROJECT_PREFIX = "home-uos-service-codebase-repos-"

# 测试文件目录标记（用于过滤 CALLS 目标中的 stub/辅助类）
TEST_DIR_MARKERS = ("tests/", "test/")

# 测试文件名模式（ut_ 前缀）
UT_FILE_PATTERN = re.compile(r'(?:^|/)ut_\w+\.(?:cpp|h)$')


# ── MCP HTTP 客户端（复用 fetch-mcp-data.py 的实现） ──

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
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0", "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fetch-test-mapping", "version": "1.0"},
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
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        req2 = urllib.request.Request(
            self.url, data=json.dumps(notif).encode(),
            headers={**headers, "Mcp-Session-Id": self.session_id})
        urllib.request.urlopen(req2, timeout=self.timeout)
        return result

    def call_tool(self, name, arguments, retries=3):
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
                    try:
                        self.initialize()
                    except Exception:
                        pass
                else:
                    raise


# ── 数据采集 ──

def discover_test_modules(client, project):
    """发现项目中的所有单元测试模块文件。

    使用 search_graph(label=Module, file_pattern=tests/**) 获取所有 tests/ 下的 Module 节点，
    然后过滤出 ut_*.cpp/h 文件（即 GTest 单元测试文件）。

    返回 [{name, file_path, out_degree}, ...]，其中 out_degree > 0 表示有 CALLS 关系。
    """
    print(f"\n📊 [1/4] 发现测试模块...")
    data = client.call_tool("search_graph", {
        "project": project,
        "label": "Module",
        "file_pattern": "tests/**",
        "limit": 200,
    })
    results = data.get("results", [])
    total = data.get("total", 0)

    # 过滤出 ut_*.cpp/h 文件
    test_modules = []
    for r in results:
        file_path = r.get("file_path", "")
        name = r.get("name", "")
        out_degree = r.get("out_degree", 0) or 0
        if UT_FILE_PATTERN.search(file_path) or UT_FILE_PATTERN.search(name):
            test_modules.append({
                "name": name,
                "file_path": file_path,
                "out_degree": out_degree,
            })

    # 按 out_degree 降序排列（有 CALLS 关系的排在前面）
    test_modules.sort(key=lambda x: x["out_degree"], reverse=True)

    with_calls = [m for m in test_modules if m["out_degree"] > 0]
    without_calls = [m for m in test_modules if m["out_degree"] == 0]

    print(f"   tests/ Module 总数: {total}")
    print(f"   ut_* 单元测试文件: {len(test_modules)}")
    print(f"   有 CALLS 关系: {len(with_calls)}")
    print(f"   无 CALLS 关系: {len(without_calls)}")

    return test_modules


def collect_calls_for_module(client, project, module_name):
    """查询单个测试模块的 CALLS 关系目标。

    返回 [(target_name, target_qn, target_file, target_labels), ...]。
    """
    query = (
        f"MATCH (m:Module {{name:'{module_name}'}})-[:CALLS]->(target) "
        f"RETURN target.name, target.qualified_name, target.file_path, labels(target)"
    )
    data = client.call_tool("query_graph", {
        "project": project,
        "query": query,
    })
    rows = data.get("rows", [])
    result = []
    for row in rows:
        if len(row) >= 3:
            name = row[0] or ""
            qn = row[1] or ""
            file_path = row[2] or ""
            labels = row[3] if len(row) > 3 else []
            if isinstance(labels, str):
                try:
                    labels = json.loads(labels)
                except json.JSONDecodeError:
                    labels = [labels]
            result.append((name, qn, file_path, labels))
    return result


def collect_all_calls(client, project, test_modules):
    """批量收集所有测试模块的 CALLS 目标。

    过滤规则：
      1. 去掉 Field 节点（不是可测试的函数）
      2. 去掉 file_path 含 tests/ 的目标（stub/辅助类）
      3. 只保留 Method 和 Function 节点

    返回 {source_qn: set(test_files)} 映射。
    """
    print(f"\n📊 [2/4] 收集 CALLS 关系...")

    source_to_tests = defaultdict(set)
    modules_with_calls = [m for m in test_modules if m["out_degree"] > 0]
    total_modules = len(modules_with_calls)

    for idx, module in enumerate(modules_with_calls, 1):
        module_name = module["name"]
        module_file = module["file_path"]

        if idx % 5 == 0 or idx == total_modules:
            print(f"   [{idx}/{total_modules}] {module_name} "
                  f"(out_degree={module['out_degree']})")

        try:
            targets = collect_calls_for_module(client, project, module_name)
        except Exception as e:
            print(f"   ⚠️  {module_name} 查询失败: {e}")
            continue

        for target_name, target_qn, target_file, target_labels in targets:
            # 过滤 1: 去掉 Field 节点
            label_strs = [l.strip('"') for l in (target_labels or [])]
            if "Field" in label_strs and "Method" not in label_strs and "Function" not in label_strs:
                continue

            # 过滤 2: 去掉 tests/ 目录中的目标（stub/辅助类）
            if any(marker in target_file for marker in TEST_DIR_MARKERS):
                continue

            # 过滤 3: 确保至少是 Method 或 Function
            if not any(l in label_strs for l in ("Method", "Function")):
                continue

            if target_qn:
                source_to_tests[target_qn].add(module_file)

        # 节流
        if idx < total_modules:
            time.sleep(0.1)

    total_targets = sum(len(v) for v in source_to_tests.values())
    covered_sources = len(source_to_tests)
    max_coverage = max((len(v) for v in source_to_tests.values()), default=0)

    print(f"   ✅ {total_modules} 个测试模块已查询")
    print(f"   被测源码节点: {covered_sources}")
    print(f"   总 CALLS 边: {total_targets}")
    print(f"   最大覆盖: {max_coverage} 个测试文件")

    return source_to_tests


def fetch_test_cases(client, project, test_modules):
    """采集每个测试文件中的 TEST_F 用例名。

    使用 search_graph 搜索 name_pattern='TEST_F', label='Function', file_pattern='tests/**'
    获取所有 GTest TEST_F 函数节点。signature 格式为 (test_class, test_case_name)，
    例如 (ut_commandparser, AddOption_CustomOption_BecomesRecognized)。

    返回 {test_file_path: [test_case_name, ...]} 映射。
    """
    print(f"\n📊 [3/4] 采集 TEST_F 用例名...")
    data = client.call_tool("search_graph", {
        "project": project,
        "label": "Function",
        "name_pattern": "TEST_F",
        "file_pattern": "tests/**",
        "page_size": 500,
    })
    results = data.get("results", [])
    total = data.get("total", 0)

    # 构建 file → test_cases 映射
    file_to_cases = defaultdict(list)
    for r in results:
        sig = r.get("signature", "")
        file_path = r.get("file_path", "")
        if not sig or not file_path:
            continue

        # 解析 signature: (test_class, test_case_name)
        match = re.match(r'\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', sig)
        if match:
            test_class = match.group(1).strip()
            test_case = match.group(2).strip()
            full_name = f"{test_class}.{test_case}"
            # 追加 docstring 注释（如有）
            doc = r.get("docstring", "")
            comment = doc.lstrip('/ ').strip() if doc else ""
            if comment:
                full_name = f"{full_name}  // {comment}"
            file_to_cases[file_path].append(full_name)

    # 统计
    total_cases = sum(len(v) for v in file_to_cases.values())
    files_with_cases = len(file_to_cases)
    print(f"   TEST_F 节点总数: {total}")
    print(f"   含用例名的文件: {files_with_cases}")
    print(f"   用例名总数: {total_cases}")

    # 打印每个 ut_ 文件的用例
    if test_modules:
        ut_files = {m["file_path"] for m in test_modules}
        for fp in sorted(file_to_cases.keys()):
            if fp in ut_files:
                cases = file_to_cases[fp]
                print(f"   {os.path.basename(fp)}: {len(cases)} 个用例")
                for tc in cases[:5]:
                    print(f"     \U0001f9ea {tc}")
                if len(cases) > 5:
                    print(f"     ... 还有 {len(cases) - 5} 个")

    return file_to_cases


# ── 归一化与映射构建 ──

def normalize_qn(qn):
    """去掉 home-uos-service-codebase-repos- 前缀，得到归一化的 qualified_name。

    MCP 图谱: home-uos-service-codebase-repos-deepin-image-viewer.src.src.Foo.method
    Inventory:  home-uos-service-codebase-repos-deepin-image-viewer.src.src.Foo.method
               （inventory 可能保留前缀也可能不保留，取决于 fetch-mcp-data.py 的 scan-inventory 实现）
    归一化后:   deepin-image-viewer.src.src.Foo.method
    """
    if qn.startswith(PROJECT_PREFIX):
        return qn[len(PROJECT_PREFIX):]
    return qn


def build_mapping(source_to_tests, file_to_cases=None):
    """构建归一化 qualified_name → {test_cover_count, test_files, test_cases} 映射。

    test_cover_count: 调用该方法的独立测试文件数（MCP CALLS 静态分析）。
    test_cases: 该方法所涉测试文件中的全部 TEST_F 用例名列表。
    """
    mapping = {}
    for qn, test_files in source_to_tests.items():
        nqn = normalize_qn(qn)
        cases = []
        if file_to_cases:
            for tf in sorted(test_files):
                cases.extend(file_to_cases.get(tf, []))
        mapping[nqn] = {
            "test_cover_count": len(test_files),
            "test_files": sorted(test_files),
            "test_cases": cases,
        }
    return mapping


def update_inventory(inventory, mapping):
    """将测试覆盖映射回写到 .ut-inventory.json。

    匹配规则：inventory.methods[].qualified_name 归一化后与 mapping key 匹配。

    回写策略：
      - 匹配到且 test_cover_count > 0 → 写入 test_cover_count + test_files + test_source
      - usecase_count 取 max(原值, test_cover_count)（已有 Mode 2 精确计数保留，否则用覆盖数作下界）
      - 未匹配到 → 保留原值不动

    返回 (updated_count, unmatched_count, updated_methods_list)
    """
    updated = 0
    unmatched = 0
    updated_methods = []

    for method in inventory.get("methods", []):
        qn = method.get("qualified_name", "")
        nqn = normalize_qn(qn)
        if nqn in mapping:
            new_cover = mapping[nqn]["test_cover_count"]
            old_cover = method.get("test_cover_count", 0)
            old_uc = method.get("usecase_count", 0)
            if new_cover > 0:
                method["test_cover_count"] = new_cover
                method["test_files"] = mapping[nqn]["test_files"]
                method["test_cases"] = mapping[nqn].get("test_cases", [])
                method["test_source"] = "mcp_calls"
                # usecase_count 取 max
                new_uc = max(old_uc, new_cover)
                method["usecase_count"] = new_uc
                updated += 1
                updated_methods.append({
                    "name": method.get("name"),
                    "qn": qn,
                    "old_cover": old_cover,
                    "new_cover": new_cover,
                    "old_uc": old_uc,
                    "new_uc": new_uc,
                    "test_files": mapping[nqn]["test_files"],
                })
        else:
            unmatched += 1

    return updated, unmatched, updated_methods


# ── 报告渲染 ──

def render_report(updated_methods, unmatched, project, test_summary):
    """渲染 Markdown 格式的测试覆盖报告。"""
    lines = [
        "# 函数↔单元测试映射报告",
        "",
        f"- 项目: `{project}`",
        f"- 测试模块: {test_summary['total_modules']}",
        f"- 有 CALLS 关系: {test_summary['with_calls']}",
        f"- 被测源码节点: {test_summary['covered_sources']}",
        f"- 总 CALLS 边: {test_summary['total_calls']}",
        "",
        "## 已更新方法",
        "",
        f"共 {len(updated_methods)} 个方法的 `test_cover_count` 已从 MCP CALLS 关系更新。",
        "",
    ]

    sorted_methods = sorted(updated_methods,
                            key=lambda x: x["new_cover"], reverse=True)

    by_count = defaultdict(list)
    for m in sorted_methods:
        by_count[m["new_cover"]].append(m)

    for count in sorted(by_count.keys(), reverse=True):
        methods = by_count[count]
        lines.append(f"### 覆盖 {count} 个测试文件 ({len(methods)} 个方法)")
        lines.append("")
        lines.append("| 方法名 | qualified_name | usecase_count | 测试文件 |")
        lines.append("|--------|---------------|---------------|----------|")
        for m in methods:
            files = ", ".join(os.path.basename(f) for f in m["test_files"])
            lines.append(f"| {m['name']} | `{m['qn']}` | {m['new_uc']} | {files} |")
        lines.append("")

    if unmatched:
        lines.append("## 未匹配方法")
        lines.append("")
        lines.append(f"共 {unmatched} 个 inventory 方法的 qualified_name "
                     "在 MCP CALLS 映射中未找到。")
        lines.append("可能原因：方法未被任何测试调用、或 qualified_name 格式差异。")
        lines.append("")

    return "\n".join(lines)


# ── 从 JSON 文件加载映射 ──

def load_mapping_from_file(path):
    """从 JSON 文件加载已保存的测试映射。

    兼容格式：
      - 新版: {nqn: {"test_cover_count": int, "test_files": [str]}}
      - 旧版: {qn: {"usecase_count": int, "test_files": [str]}}
      - 原始: {qn: [test_file, ...]}
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    mapping = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and "test_cover_count" in value:
                nqn = normalize_qn(key)
                mapping[nqn] = value  # 保留 test_cases 等全部字段
            elif isinstance(value, dict) and "usecase_count" in value:
                nqn = normalize_qn(key)
                mapping[nqn] = {
                    "test_cover_count": value.get("usecase_count", 0),
                    "test_files": value.get("test_files", []),
                }
            elif isinstance(value, (list, set)):
                nqn = normalize_qn(key)
                mapping[nqn] = {
                    "test_cover_count": len(value),
                    "test_files": sorted(value),
                }
    return mapping


# ── 主流程 ──

def main():
    parser = argparse.ArgumentParser(
        description="MCP 知识图谱 → 函数↔单元测试映射 → 回写 .ut-inventory.json")
    parser.add_argument("--project", required=False,
                        help="MCP 项目名（与 fetch-mcp-data.py --project 一致）"
                             "使用 --mapping-in 时可省略")
    parser.add_argument("--inventory", "-i", required=True,
                        help=".ut-inventory.json 路径")
    parser.add_argument("--mapping-in", default=None,
                        help="已保存的映射 JSON 文件（跳过 MCP 查询，直接回写）")
    parser.add_argument("--mapping-out", default=None,
                        help="保存映射到 JSON 文件（供后续 --mapping-in 使用）")
    parser.add_argument("--report", default=None,
                        help="输出 Markdown 测试覆盖报告路径")
    parser.add_argument("--mcp-url", default=MCP_URL, help="MCP HTTP 端点")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印映射结果不写回 inventory")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="打印每个测试模块的详细 CALLS 目标")
    args = parser.parse_args()

    # 参数校验
    if not args.mapping_in and not args.project:
        print("❌ 必须指定 --project 或 --mapping-in（至少一项）",
              file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.inventory):
        print(f"❌ inventory 不存在: {args.inventory}", file=sys.stderr)
        sys.exit(1)

    # 读 inventory
    with open(args.inventory, "r", encoding="utf-8") as f:
        try:
            inventory = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ inventory JSON 损坏: {e}", file=sys.stderr)
            sys.exit(1)

    project_name = args.project or inventory.get("project", "")

    # ── 获取映射 ──
    if args.mapping_in:
        print(f"\n📂 从文件加载映射: {args.mapping_in}")
        mapping = load_mapping_from_file(args.mapping_in)
        test_summary = {
            "total_modules": 0,
            "with_calls": 0,
            "covered_sources": len(mapping),
            "total_calls": sum(m["test_cover_count"] for m in mapping.values()),
        }
    else:
        # 连接 MCP
        client = MCPClient(url=args.mcp_url)
        print(f"🔗 Connecting to {args.mcp_url}...")
        client.initialize()
        print(f"✅ Session: {client.session_id[:12]}...")
        print(f"📋 Project: {project_name}")

        # Step 1: 发现测试模块
        test_modules = discover_test_modules(client, project_name)

        # Step 2: 收集 CALLS 关系
        source_to_tests = collect_all_calls(client, project_name, test_modules)

        # Step 3: 采集 TEST_F 用例名
        file_to_cases = fetch_test_cases(client, project_name, test_modules)

        # Step 4: 构建映射
        print(f"\n📊 [4/4] 构建函数↔测试映射...")
        mapping = build_mapping(source_to_tests, file_to_cases)

        test_summary = {
            "total_modules": len(test_modules),
            "with_calls": len([m for m in test_modules if m["out_degree"] > 0]),
            "covered_sources": len(mapping),
            "total_calls": sum(len(v) for v in source_to_tests.values()),
        }

        if args.verbose:
            print(f"\n{'=' * 60}")
            print("详细映射:")
            for qn, info in sorted(mapping.items(),
                                   key=lambda x: x[1]["test_cover_count"],
                                   reverse=True):
                files = ", ".join(os.path.basename(f) for f in info["test_files"])
                print(f"  {qn}: {info['test_cover_count']} tests → {files}")

    # ── 回写 inventory ──
    print(f"\n🔧 回写 inventory...")
    print(f"   映射中源码节点: {len(mapping)}")
    print(f"   inventory 方法数: {len(inventory.get('methods', []))}")

    updated, unmatched, updated_methods = update_inventory(inventory, mapping)

    print(f"   已更新: {updated}")
    print(f"   未匹配: {unmatched}")

    # 打印更新摘要（top 10 by test_cover_count）
    if updated_methods:
        top = sorted(updated_methods,
                      key=lambda x: x["new_cover"], reverse=True)[:10]
        print(f"\n   Top {min(10, len(top))} 覆盖最多的方法:")
        for m in top:
            files = ", ".join(os.path.basename(f) for f in m["test_files"])
            print(f"     {m['name']}: {m['new_cover']} test_files, "
                  f"usecase_count {m['old_uc']}→{m['new_uc']} ({files})")

    # 打印仍未覆盖的方法
    zero_cover = [m for m in inventory.get("methods", [])
                  if m.get("testable", True) and m.get("test_cover_count", 0) == 0
                  and m.get("level") in ("high", "mid")]
    if zero_cover:
        print(f"\n   ⚠️  {len(zero_cover)} 个 high/mid 可测方法无测试覆盖:")
        for m in zero_cover[:15]:
            print(f"     {m.get('name')} ({m.get('level')}) in {m.get('file_path','?')}")
        if len(zero_cover) > 15:
            print(f"     ... 还有 {len(zero_cover) - 15} 个")

    # 保存映射文件
    if args.mapping_out:
        os.makedirs(os.path.dirname(args.mapping_out) or ".", exist_ok=True)
        with open(args.mapping_out, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        print(f"\n💾 映射已保存到 {args.mapping_out}")

    # 写回 inventory
    if not args.dry_run:
        bak = args.inventory + ".bak"
        if os.path.isfile(args.inventory):
            shutil.copyfile(args.inventory, bak)
            print(f"💾 已备份到 {bak}")

        with open(args.inventory, "w", encoding="utf-8") as f:
            json.dump(inventory, f, ensure_ascii=False, indent=2)
        print(f"✅ inventory 已写入 {args.inventory}")
    else:
        print(f"📋 dry-run: inventory 未写入")

    # 生成报告
    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        report = render_report(updated_methods, unmatched,
                               project_name, test_summary)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"✅ 报告已写入 {args.report}")

    # 概要
    print(f"\n{'=' * 60}")
    print(f"项目: {project_name}")
    print(f"测试模块: {test_summary['total_modules']}")
    print(f"有 CALLS 关系: {test_summary['with_calls']}")
    print(f"被测源码节点: {test_summary['covered_sources']}")
    print(f"总 CALLS 边: {test_summary['total_calls']}")
    print(f"inventory 更新: {updated} / 未匹配: {unmatched}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
