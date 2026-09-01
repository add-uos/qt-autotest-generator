#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""mcp-scan.py — MCP 知识图谱 → .ut-inventory.json 端到端探测

合并自 scan-inventory.py + fetch-mcp-data.py + fetch-test-mapping.py。四个子命令：
  scan             — 评分建表（原 scan-inventory.py）
  fetch            — 端到端 MCP 采集 + 评分 + test_* 覆盖回写（原 fetch-mcp-data.py）
  extract-branches — 分支清单交叉验证（原 fetch-mcp-data.py 子命令）
  test-mapping     — 仅回写 test_* 字段（原 fetch-test-mapping.py；inventory 已存在时增量刷）

fetch 的 test_* 采集：build_inventory 之后、写文件之前，复用 MCPClient 采集
CALLS 边（测试文件 → 被测函数），回写 test_cover_count/test_files/test_cases/
test_source。增量模式不重采（overlay 已保留旧 test_*）。

用法:
  # scan: 从预采集的 MCP 数据 JSON 评分建表
  python3 mcp-scan.py scan \
    --project home-uos-service-codebase-repos-deepin-calculator \
    --output /tmp/inventory-calculator.json \
    --mcp-dump /tmp/mcp_dump.json

  # fetch: 端到端 MCP 采集 + 评分
  python3 mcp-scan.py fetch \
    --project home-uos-service-codebase-repos-dde-file-manager \
    --file-pattern "src/**" \
    --output .ut-inventory.json

  # extract-branches: 分支清单交叉验证
  python3 mcp-scan.py extract-branches \
    --project <name> --test-file autotests/core/test_foo.cpp \
    --inventory autotests/.ut-inventory.json [--class Foo] [--json] [-o out.json]

示例:
  # dde-file-manager（排除 3rdparty）
  python3 mcp-scan.py fetch \
    --project home-uos-service-codebase-repos-dde-file-manager \
    --file-pattern "src/**" \
    --output /tmp/dde-file-manager/.ut-inventory.json

  # deepin-reader（排除 pdfium 等 3rdparty）
  python3 mcp-scan.py fetch \
    --project home-uos-service-codebase-repos-deepin-reader \
    --file-pattern "reader/**" \
    --output /tmp/deepin-reader/.ut-inventory.json

  # deepin-calculator（无 3rdparty，无需 file-pattern）
  python3 mcp-scan.py fetch \
    --project home-uos-service-codebase-repos-deepin-calculator \
    --output /tmp/deepin-calculator/.ut-inventory.json

  # 多目录项目（源码 + 插件，分别指定或逗号分隔，结果自动去重合并）
  python3 mcp-scan.py fetch \
    --project home-uos-service-codebase-repos-xxx \
    --file-pattern "src/**" --file-pattern "plugins/**" \
    --output /tmp/xxx/.ut-inventory.json
  # 等价写法：
  #   --file-pattern "src/**,plugins/**"

fetch 子命令一条命令完成函数重要性探测的全流程数据采集与评分：

  1. HTTP MCP 收集所有 Method 节点（search_graph 分页，file_pattern 过滤 3rdparty）
  2. query_graph 检测继承链（QDBusAbstractAdaptor 服务端 / QThread 并发基类 / GUI 基类）
  3. query_graph 获取 DBus Adaptor 类方法 → dbus_slots
  4. search_code 检测 Q_INVOKABLE / Q_PLUGIN_METADATA（best-effort）
  5. 客户端计算 P75 非零 in_degree
  6. 调用 build_inventory() 生成 .ut-inventory.json

本脚本通过 MCP 工具扫描知识图谱，为每个方法/函数评分并生成 .ut-inventory.json。
由于 MCP 工具只能由 Agent 调用，scan 子命令作为"模拟实现"供验证设计，
实际生产中由 Agent 按 importance_inventory.md 阶段文档执行。
"""

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone


# ── 配置 ──

SCOPE_RULES = [
    {"pattern": "3rdparty/**",    "scope": "exempt", "reason": "第三方库"},
    {"pattern": "3rd_party/**",   "scope": "exempt", "reason": "第三方库"},
    {"pattern": "third_party/**", "scope": "exempt", "reason": "第三方库"},
    {"pattern": "**/external/**", "scope": "exempt", "reason": "第三方库(external)"},
    {"pattern": "**/vendor/**",   "scope": "exempt", "reason": "第三方库(vendor)"},
    {"pattern": "**/moc_*.cpp",   "scope": "exempt", "reason": "MOC 生成"},
    {"pattern": "**/moc_*.h",     "scope": "exempt", "reason": "MOC 生成"},
    {"pattern": "**/ui_*.h",      "scope": "exempt", "reason": "UI 生成"},
    {"pattern": "**/ui_*.cpp",    "scope": "exempt", "reason": "UI 生成"},
    {"pattern": "**/.pb.",        "scope": "exempt", "reason": "Protobuf 生成"},
    {"pattern": "**/generated/**","scope": "exempt", "reason": "生成代码"},
    {"pattern": "tests/**",       "scope": "exempt", "reason": "测试代码本身"},
    {"pattern": "autotests/**",   "scope": "exempt", "reason": "测试代码本身"},
    {"pattern": "test/**",        "scope": "exempt", "reason": "测试代码本身"},
]

# 默认门禁阈值（仅首次建表使用；已有 inventory 时从 inventory 读取，不覆盖外部设定）
DEFAULT_GATE_THRESHOLDS = {
    "high": {"line": 90, "branch": 80, "function": 100},
    "mid":  {"line": 60, "branch": 0, "function": 100},
    "low":  {"line": 60, "branch": 0, "function": 100},
}

DESTRUCTIVE_PATTERN = re.compile(
    r'(delete|remove|destroy|truncate|write|save|persist|erase|clear|reset|wipe)',
    re.IGNORECASE
)

# HIGH_FACTOR_PREFIXES removed — replaced by weighted scoring in score_method()

CANDIDATE_CLASS_PATTERN = re.compile(
    r'(Plugin|Adaptor|Adapter|Interface|Manager|Service|Handler|Controller)$'
)

DBUS_BASE_CLASSES = [
    'QDBusAbstractAdaptor', 'QDBusAbstractInterface',
    'QDBusAbstractService', 'QDBusServer'
]

CONCURRENT_BASE_CLASSES = [
    'QThread', 'QThreadPool', 'QMutex', 'QReadWriteLock',
    'QSemaphore', 'QAtomicInt', 'QAtomicInteger', 'QWaitCondition'
]



# ── 配置 ──
#
# 优先级：环境变量 > 代码默认值
# - QTAG_MCP_URL: 远端 MCP HTTP 端点（覆盖默认值）

MCP_URL = os.environ.get("QTAG_MCP_URL", "http://10.8.12.80:13626/mcp")

# 可选认证头：本地 mcp-proxy 等需 X-API-Key 认证时通过环境变量设置（向后兼容，不设则无）。
#   QTAG_MCP_HEADERS: JSON 字符串，如 '{"X-API-Key":"xxx"}'
#   QTAG_MCP_API_KEY: 单独 apiKey → 自动构造 {"X-API-Key": "<key>"}
MCP_EXTRA_HEADERS = {}
_json_headers = os.environ.get("QTAG_MCP_HEADERS")
if _json_headers:
    try:
        MCP_EXTRA_HEADERS = json.loads(_json_headers) or {}
    except Exception:
        MCP_EXTRA_HEADERS = {}
if not MCP_EXTRA_HEADERS and os.environ.get("QTAG_MCP_API_KEY"):
    MCP_EXTRA_HEADERS = {"X-API-Key": os.environ["QTAG_MCP_API_KEY"]}

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



# ════════════════════════════════════════════════════════════════════════════
# === 评分逻辑 (from scan-inventory.py) ===
# ════════════════════════════════════════════════════════════════════════════

# ── Glob 匹配 ──

def glob_to_regex(pattern: str) -> str:
    """Convert glob pattern to regex. Supports ** and *."""
    result = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == '*':
            if i + 1 < len(pattern) and pattern[i + 1] == '*':
                # ** → match any depth (including zero)
                result += ".*"
                i += 2
                if i < len(pattern) and pattern[i] == '/':
                    i += 1  # skip trailing /
            else:
                result += "[^/]*"
                i += 1
        elif c == '?':
            result += "[^/]"
            i += 1
        elif c in '.+^${}|()\\':
            result += '\\' + c
            i += 1
        else:
            result += c
            i += 1
    return "^" + result + "$"


def scope_match(file_path: str | None, scope_rules: list) -> tuple[bool, str | None]:
    """Check if file_path matches any scope rule. Returns (testable, exempt_reason)."""
    if file_path is None:
        # Methods without a file path are testable by default (no scope exclusion)
        return (True, None)
    for rule in scope_rules:
        regex = glob_to_regex(rule["pattern"])
        if re.match(regex, file_path):
            if rule.get("scope") == "exempt":
                return (False, f"scope:{rule['pattern']}")
    return (True, None)


# ── 评分 ──


# ── 评分 ──

def score_method(name: str, factors: list[str]) -> tuple[str, str, int]:
    """Return (level, source, score) using weighted scoring.
    
    Score ≥ 3 → high, ≥ 1 → mid, < 1 → low.
    
    因子体系（3 层）：
    - 主因子：complexity（圈复杂度）— 与缺陷率最相关
    - 辅助因子：cognitive + lines — 补充圈复杂度无法捕获的嵌套深度和规模风险
    - 风险因子：loop_count / alloc_in_loop / recursive / linear_scan_in_loop
    - Mid-booster：in_degree — 仅对工具/库函数有效，Qt 回调函数基本无效
    
    核心原则：辅助因子不能独立推到 high。cognitive≥30 (+2) 或 lines≥50 (+1)
    单独只能到 mid，需叠加 complexity≥5 才能到 high。
    """
    score = 0
    has_suggested = False

    for f in factors:
        if f.startswith("name_pattern:"):
            has_suggested = True
            continue
        if f == "dbus_slot": score += 3
        elif f == "q_invokable": score += 3
        elif f == "plugin_export": score += 3
        elif f == "concurrent_class" or f.startswith("concurrent_base:"): score += 1
        # ── 主因子：complexity（圈复杂度） ──
        elif f.startswith("complexity:"):
            val = int(f.split(":")[1])
            if val >= 20: score += 3
            elif val >= 8: score += 2
            elif val >= 5: score += 1
        # ── 辅助因子：cognitive（认知复杂度） ──
        # 与圈复杂度互补：对深层嵌套和逻辑中断更敏感
        elif f.startswith("cognitive:"):
            val = int(f.split(":")[1])
            if val >= 30: score += 2
            elif val >= 15: score += 1
        # ── 辅助因子：lines（代码行数） ──
        # 保守加分：长函数不一定复杂（如纯数据组装），需叠加其他因子
        elif f.startswith("lines:"):
            val = int(f.split(":")[1])
            if val >= 150: score += 1
            elif val >= 50: score += 1
        # ── 风险因子 ──
        elif f.startswith("transitive_loop_depth:"):
            val = int(f.split(":")[1])
            if val >= 3: score += 3
        elif f.startswith("linear_scan_in_loop:"):
            score += 1
        elif f.startswith("loop_count:"):
            val = int(f.split(":")[1])
            if val >= 5: score += 1
        elif f.startswith("alloc_in_loop:"):
            score += 1
        elif f == "recursive":
            score += 1
        # ── Mid-booster：in_degree ──
        # Qt 项目中仅衡量跨文件被引用数，信号槽/虚函数回调不产生 CALLS 边
        # 对工具/库函数有效，对 Qt 回调函数基本无效
        elif f.startswith("in_degree:"):
            score += 1
        # ── 降级因子 ──
        elif f == "destructor": score -= 1
        elif f == "operator": score -= 1

    if score >= 3:
        return ("high", "auto", score)
    elif score >= 1 or has_suggested:
        return ("mid", "suggested" if has_suggested and score < 1 else "auto", score)
    else:
        return ("low", "auto", score)


# ── MCP 数据载入（从 stdin 读取 JSON dump） ──


# ── MCP 数据载入（从 stdin 读取 JSON dump） ──

def load_mcp_dump(path: str) -> dict:
    """Load pre-fetched MCP data from JSON file."""
    with open(path, 'r') as f:
        return json.load(f)


# ── 主流程 ──


# ── 主流程 ──

def build_inventory(mcp_data: dict, project_name: str, base_sha: str,
                    gate_thresholds: dict | None = None,
                    project_root: str = "",
                    qt_version: str | None = None) -> dict:
    """Build .ut-inventory.json from pre-fetched MCP data.

    Args:
        gate_thresholds: 外部门禁阈值；为 None 时使用 DEFAULT_GATE_THRESHOLDS。
            增量模式应传入旧 inventory 的 gate_thresholds，避免覆盖外部设定。
        project_root: 本地项目根目录（如 /home/user/deepin-picker），
            由 fetch-mcp-data 从 --output 路径自动推导，写入 JSON 供编辑器使用。
    """

    all_methods = mcp_data.get("methods", [])
    all_functions = mcp_data.get("functions", [])
    all_classes = mcp_data.get("classes", [])
    dbus_classes = mcp_data.get("dbus_classes", [])
    concurrent_classes = mcp_data.get("concurrent_classes", [])
    gui_classes = mcp_data.get("gui_classes", [])
    dbus_slots_map = mcp_data.get("dbus_slots", {})
    q_invokables_map = mcp_data.get("q_invokables", {})
    q_plugins_map = mcp_data.get("q_plugins", {})
    # ⚠️ P75 必须基于非零 in_degree 值计算（排除 in_degree=0 的方法）
    # 原因：大多数方法 in_degree=0，包含零值会使 P75=0 或 1，
    # 导致几乎所有有调用者的方法都被标为"高热度"，分类失真。
    # 传入时应为 p75_in_degree_nonzero 字段。
    in_degree_p75 = mcp_data.get("in_degree_p75_nonzero",
                                 mcp_data.get("in_degree_p75", 5))

    # 合并方法+函数
    all_callable = []
    for m in all_methods:
        m["_node_type"] = "Method"
        all_callable.append(m)
    for f in all_functions:
        f["_node_type"] = "Function"
        all_callable.append(f)

    # 过滤 is_test=true + Function 噪音（宏/using 声明/误分类构造函数）
    NOISE_PATTERNS = re.compile(
        r'^(D[A-Z].*_USE_NAMESPACE|Q_[A-Z_]+|_[A-Z_]+|'
        r'[A-Z][a-z]+[A-Z][A-Za-z]*$)',  # PascalCase 单词 = 可能是误分类的类名
    )
    def is_function_noise(f):
        """Filter out Function nodes that are macros, using-declarations,
        or misclassified constructors (PascalCase with no params)."""
        name = f.get("name", "")
        # 全大写 = 宏/using 声明 (DGUI_USE_NAMESPACE, DWIDGET_USE_NAMESPACE, etc.)
        if name.isupper() or '_USE_NAMESPACE' in name or '_USE_WIDGET' in name:
            return True
        # Q_ 开头 = Qt 宏 (Q_DECLARE_METATYPE, Q_OBJECT, etc.)
        if name.startswith('Q_') and name.isupper():
            return True
        # D 前缀 PascalCase + param_count=0 + complexity=0 = DTK using 声明
        # (DArrowRectangle, DListView, DMainWindow, DWidget, etc.)
        if (name.startswith('D') and re.match(r'^D[A-Z][a-z]+', name) and
                f.get('param_count', -1) == 0 and f.get('complexity', 0) == 0):
            return True
        # Q 前缀 PascalCase + param_count=0 + complexity=0 = Qt using 声明
        # (QListWidget, QStyledItemDelegate, QWidget, etc.)
        if (name.startswith('Q') and re.match(r'^Q[A-Z][a-z]+', name) and
                f.get('param_count', -1) == 0 and f.get('complexity', 0) == 0):
            return True
        # PascalCase + param_count=0 + complexity=0 = 可能是误分类的类名/构造函数
        if (re.match(r'^[A-Z][a-z]+[A-Z]', name) and
                f.get('param_count', -1) == 0 and f.get('complexity', 0) == 0):
            return True
        return False

    filtered = [m for m in all_callable
                if not m.get("is_test", False)
                and not (m.get("_node_type") == "Function" and is_function_noise(m))]

    # 构建 class_qn → base_classes 映射
    class_bases = {}
    for cls in all_classes:
        bases = cls.get("base_classes", "")
        if isinstance(bases, str) and bases:
            class_bases[cls.get("qualified_name", cls.get("name", ""))] = bases
        elif isinstance(bases, list):
            class_bases[cls.get("qualified_name", cls.get("name", ""))] = ",".join(bases)

    # DBus 类集合
    dbus_class_qns = set()
    for dc in dbus_classes:
        dbus_class_qns.add(dc.get("qualified_name", dc.get("name", "")))

    # 并发类集合
    concurrent_class_qns = set()
    for cc in concurrent_classes:
        concurrent_class_qns.add(cc.get("qualified_name", cc.get("name", "")))

    # 逐方法评分
    inventory_methods = []
    review_queue = []
    stats = {"total_nodes": len(all_callable), "filtered_methods": len(filtered),
             "testable": 0, "non_testable": 0,
             "high": 0, "mid": 0, "low": 0,
             "review_pending": 0,
             "usecase_covered": 0, "usecase_not_covered": 0}

    for method in filtered:
        qn = method.get("qualified_name", "")
        name = method.get("name", "")
        signature = method.get("signature", "") or ""
        file_path = method.get("file_path", "")
        parent_class = method.get("parent_class", None)

        # 为自由函数设置 class_qn
        class_qn = None
        if method["_node_type"] == "Method" and parent_class:
            # 取 parent_class 的短名（最后一个 . 后的部分）
            class_qn = parent_class.rsplit(".", 1)[-1] if "." in parent_class else parent_class

        # scope_rules
        testable, exempt_reason = scope_match(file_path, SCOPE_RULES)

        # 因子检测
        factors = []

        if testable:
            # DBus 契约槽 — 匹配时尝试短名和全限定名
            matched_dbus_class = None
            if dbus_slots_map:
                if class_qn and class_qn in dbus_slots_map:
                    matched_dbus_class = class_qn
                elif parent_class and parent_class in dbus_slots_map:
                    matched_dbus_class = parent_class
                elif parent_class:
                    # 后缀匹配：parent_class 以 dbus_slots_map 某个 key 结尾
                    for dk in dbus_slots_map:
                        if parent_class.endswith("." + dk.split(".")[-1]) or parent_class == dk.split(".")[-1]:
                            matched_dbus_class = dk
                            break
            if matched_dbus_class and name in dbus_slots_map[matched_dbus_class]:
                factors.append("dbus_slot")

            # Q_INVOKABLE — 同样用后缀匹配
            matched_invokable_class = None
            if q_invokables_map:
                if class_qn and class_qn in q_invokables_map:
                    matched_invokable_class = class_qn
                elif parent_class and parent_class in q_invokables_map:
                    matched_invokable_class = parent_class
                elif parent_class:
                    for ik in q_invokables_map:
                        if parent_class.endswith("." + ik.split(".")[-1]) or parent_class == ik.split(".")[-1]:
                            matched_invokable_class = ik
                            break
            if matched_invokable_class and name in q_invokables_map[matched_invokable_class]:
                factors.append("q_invokable")

            # 插件导出 — 同样用后缀匹配
            matched_plugin_class = None
            if q_plugins_map:
                if class_qn and class_qn in q_plugins_map:
                    matched_plugin_class = class_qn
                elif parent_class and parent_class in q_plugins_map:
                    matched_plugin_class = parent_class
            if matched_plugin_class:
                factors.append("plugin_export")

            # 并发基类
            parent_qn_full = parent_class or ""
            # 并发基类 — 也用后缀匹配
            matched_concurrent = False
            if parent_qn_full in concurrent_class_qns:
                matched_concurrent = True
            elif parent_qn_full:
                for cq in concurrent_class_qns:
                    if parent_qn_full.endswith("." + cq.split(".")[-1]) or parent_qn_full == cq.split(".")[-1]:
                        matched_concurrent = True
                        break
            if matched_concurrent:
                factors.append("concurrent_class")
            # 也检查 base_classes 属性
            bases = class_bases.get(parent_qn_full, [])
            if not bases and parent_qn_full:
                for bk in class_bases:
                    if parent_qn_full.endswith("." + bk.split(".")[-1]) or parent_qn_full == bk.split(".")[-1]:
                        bases = class_bases[bk]
                        break
            if bases:
                for cb in CONCURRENT_BASE_CLASSES:
                    if cb in bases:
                        factors.append(f"concurrent_base:{cb}")
                        break

            # 复杂度（分级：≥20 high, ≥8 mid, ≥5 low）
            complexity = method.get("complexity", 0) or 0
            if complexity >= 20:
                factors.append(f"complexity:{complexity}")
            elif complexity >= 8:
                factors.append(f"complexity:{complexity}")
            elif complexity >= 5:
                factors.append(f"complexity:{complexity}")

            # 认知复杂度（与圈复杂度互补：对嵌套深度和逻辑中断更敏感）
            # ≥30 → +2, ≥15 → +1；单独只能到 mid，需叠加 complexity 才到 high
            cognitive = method.get("cognitive", 0) or 0
            if cognitive >= 30:
                factors.append(f"cognitive:{cognitive}")
            elif cognitive >= 15:
                factors.append(f"cognitive:{cognitive}")

            # 代码行数（保守加分：长函数不一定复杂）
            # ≥150 → +1, ≥50 → +1；单独只能到 mid
            lines = method.get("lines", 0) or 0
            if lines >= 150:
                factors.append(f"lines:{lines}")
            elif lines >= 50:
                factors.append(f"lines:{lines}")

            # 隐蔽 O(n²)
            tld = method.get("transitive_loop_depth", 0) or 0
            if tld >= 3:
                factors.append(f"transitive_loop_depth:{tld}")

            lsl = method.get("linear_scan_in_loop", 0) or 0
            if lsl >= 1:
                factors.append(f"linear_scan_in_loop:{lsl}")

            # 循环风险（≥5 个循环 → +1）
            lc = method.get("loop_count", 0) or 0
            if lc >= 5:
                factors.append(f"loop_count:{lc}")

            # 循环内分配（性能缺陷强信号 → +1）
            ail = method.get("alloc_in_loop", 0) or 0
            if ail >= 1:
                factors.append(f"alloc_in_loop:{ail}")

            # 递归（需额外测试 → +1）
            if method.get("recursive", False):
                factors.append("recursive")

            # 调用热度（in_degree ≥ P75_非零，得分 +1，mid-booster）
            # Qt 项目中仅对工具/库函数有效，信号槽/虚函数回调不产生 CALLS 边
            in_deg = method.get("in_degree", 0) or 0
            if in_deg >= in_degree_p75 and in_degree_p75 > 0:
                factors.append(f"in_degree:{in_deg}")

            # 析构函数/运算符重载 降级
            if name.startswith('~'):
                factors.append("destructor")
            elif name.startswith('operator'):
                factors.append("operator")

            # 不可逆操作
            if DESTRUCTIVE_PATTERN.search(name):
                factors.append(f"name_pattern:{name}")

            # 评分
            level, source, score = score_method(name, factors)
        else:
            level = None        # 不可测试：level 为 null（由 testable=false 表达豁免）
            source = "auto"
            score = 0

        # review_status：pending 需人工复核，auto 表示自动分级无需复核
        review_status = "auto"
        auto_reason = None
        if source == "suggested":
            review_status = "pending"
            auto_reason = f"方法名含 {next((f.split(':')[1] for f in factors if f.startswith('name_pattern:')), name)}"
        elif not testable:
            review_status = "exempt"     # 不可测试：豁免复核

        entry = {
            # schema 必需字段（与 inventory-schema.md 对齐）
            "qualified_name": qn,
            "name": name,
            "class_qn": class_qn,
            "file_path": file_path,
            "access": method.get("access", "public"),
            "level": level,
            "score": score,
            "factors": factors,
            "source": source,
            "testable": testable,
            "usecase_count": 0,  # 模式一不扫描测试文件，默认 0
            # 实现扩展字段（schema 未定义，供编辑器/调试用，见 schema "扩展字段" 小节）
            "signature": signature[:200] if signature else "",
            "exempt_reason": exempt_reason,
            "review_status": review_status,
            "node_type": method.get("_node_type", "Method"),  # Method or Function
        }
        if auto_reason:
            entry["auto_reason"] = auto_reason
        inventory_methods.append(entry)

        if review_status == "pending":
            review_queue.append({
                "qualified_name": qn,
                "name": name,
                "class_qn": class_qn,
                "suggested_level": "mid",
                "reason": auto_reason or f"方法名含 {name}",
                "review_status": "pending",
            })

        # 统计
        if testable:
            stats["testable"] += 1
            if level:
                stats[level] += 1
        else:
            stats["non_testable"] += 1

    stats["review_pending"] = len(review_queue)
    stats["usecase_not_covered"] = stats["testable"]  # 全部 0

    inventory = {
        "version": 1,
        "project": project_name,
        "project_root": project_root,
        "base_sha": base_sha,
        "qt_version": qt_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_stats": stats,
        "gate_thresholds": gate_thresholds if gate_thresholds is not None else DEFAULT_GATE_THRESHOLDS,
        "scope_rules": SCOPE_RULES,
        "file_overrides": [],
        # 类级画像：只列 GUI 类，不在列表中的类 is_gui=false（Mode 2 直接读，不再查图谱）
        # 注意：qualified_name 是图谱全限定名，name 是短名；methods[].class_qn 用短名，匹配时用 name
        "classes": [
            {
                "qualified_name": c.get("qualified_name", ""),
                "name": c.get("name", ""),
                "file_path": c.get("file_path", ""),
                "is_gui": True,
            }
            for c in gui_classes
        ],
        "methods": inventory_methods,
        "review_queue": review_queue
    }

    return inventory


def generate_summary(inventory: dict) -> str:
    """Generate human-readable Markdown summary."""
    stats = inventory["scan_stats"]
    methods = inventory["methods"]

    # 按类分组
    class_stats = {}
    for m in methods:
        if not m["testable"]:
            continue
        cls = m["class_qn"] or "(free functions)"
        if cls not in class_stats:
            class_stats[cls] = {"high": 0, "mid": 0, "low": 0, "total": 0, "file": m["file_path"]}
        level = m["level"] or "low"
        class_stats[cls][level] += 1
        class_stats[cls]["total"] += 1

    lines = [
        f"# 函数重要性探测报告",
        f"",
        f"项目: `{inventory['project']}`",
        f"生成时间: {inventory['generated_at']}",
        f"",
        f"## 统计概览",
        f"",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 总节点数 | {stats['total_nodes']} |",
        f"| 过滤后方法数 | {stats['filtered_methods']} |",
        f"| 可测试 | {stats['testable']} |",
        f"| 不可测试 | {stats['non_testable']} |",
        f"| high | {stats['high']} |",
        f"| mid | {stats['mid']} |",
        f"| low | {stats['low']} |",
        f"| 待复核 | {stats['review_pending']} |",
        f"",
        f"## 按类分布",
        f"",
        f"| 类名 | 文件 | high | mid | low | 合计 |",
        f"|------|------|------|-----|-----|------|",
    ]

    for cls, cs in sorted(class_stats.items(), key=lambda x: -x[1]["high"] - x[1]["mid"]):
        lines.append(f"| {cls} | {cs['file']} | {cs['high']} | {cs['mid']} | {cs['low']} | {cs['total']} |")

    # high 方法详情
    high_methods = [m for m in methods if m["testable"] and m["level"] == "high"]
    if high_methods:
        lines.append(f"")
        lines.append(f"## high 方法详情 ({len(high_methods)})")
        lines.append(f"")
        lines.append(f"| 方法 | 类 | 因子 |")
        lines.append(f"|------|-----|------|")
        for m in high_methods[:50]:  # 最多显示 50
            lines.append(f"| {m['name']} | {m['class_qn'] or '-'} | {', '.join(m['factors'])} |")

    # review queue
    rq = inventory.get("review_queue", [])
    if rq:
        lines.append(f"")
        lines.append(f"## 待复核条目 ({len(rq)})")
        lines.append(f"")
        for item in rq[:30]:
            lines.append(f"- `{item.get('name', '?')}` ({item.get('class_qn') or '-'}): "
                         f"{item.get('reason', '')} → 建议 high，默认 mid")

    return "\n".join(lines)


# ── MCP 数据采集适配器 ──
# 由于 Agent 无法直接调用 MCP，这里提供两种模式:
# 1. --mcp-dump: 从预采集的 JSON 文件读取
# 2. --inline: Agent 通过 spawn_subagent 调用 MCP 后将结果传入


# ── MCP 数据采集适配器 ──
# 由于 Agent 无法直接调用 MCP，这里提供两种模式:
# 1. --mcp-dump: 从预采集的 JSON 文件读取
# 2. --inline: Agent 通过 spawn_subagent 调用 MCP 后将结果传入

def fetch_mcp_data_via_subagent(project_name: str) -> dict:
    """
    Agent 调用此函数会触发 MCP 查询。
    实际由 Agent 在对话中手动执行 MCP 调用，结果传入 build_inventory。
    """
    raise NotImplementedError(
        "MCP 调用需由 Agent 在对话中执行。请使用 --mcp-dump 模式或手动采集数据。"
    )


# ════════════════════════════════════════════════════════════════════════════
# === MCP 采集逻辑 (from fetch-mcp-data.py) ===
# ════════════════════════════════════════════════════════════════════════════

# ── MCP HTTP 客户端 ──

# search_graph fields: build_inventory 评分所需的所有节点属性。
# 本地 MCP 静默丢弃不支持的 field（如 docstring/return_type），
# 因此只列已验证支持的 12 个；lines/in/out 为核心列，不在此请求。
SEARCH_GRAPH_FIELDS = [
    "complexity", "cognitive", "loop_count", "loop_depth",
    "alloc_in_loop", "recursive", "transitive_loop_depth",
    "linear_scan_in_loop", "param_count", "signature",
    "parent_class", "is_test",
]


def _lines_range_to_int(lines_val):
    """Convert search_graph `lines` core column (range "start-end") to count.

    search_graph 返回 "251-334"（行范围）；build_inventory 评分（>=50, >=150）
    需要整数行数。count = end - start + 1（251-334 → 84，与 query_graph
    m.lines 真值一致）。裸整数（已是计数）原样返回。
    """
    if isinstance(lines_val, int):
        return lines_val
    if isinstance(lines_val, str):
        s = lines_val.strip()
        if not s or s == "-":
            return 0
        # 行范围 "start-end"（排除负数）
        if "-" in s and not s.startswith("-"):
            parts = s.split("-", 1)
            try:
                return int(parts[1]) - int(parts[0]) + 1
            except ValueError:
                pass
        try:
            return int(s)
        except ValueError:
            return 0
    return 0


def _tokenize_text_row(line):
    """Tokenize a query_graph text data row.

    含特殊字符（空格/方括号/引号）的值是 JSON 引号串；裸值不含空格。
    裸 `-` 表示 null → None。
    """
    tokens = []
    i = 0
    n = len(line)
    while i < n:
        while i < n and line[i] in " \t":
            i += 1
        if i >= n:
            break
        if line[i] == '"':
            j = i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == '"':
                    break
                j += 1
            tok = line[i:j + 1]
            try:
                tokens.append(json.loads(tok))
            except json.JSONDecodeError:
                tokens.append(tok)
            i = j + 1
        else:
            j = i
            while j < n and line[j] not in " \t":
                j += 1
            bare = line[i:j]
            tokens.append(None if bare == "-" else bare)
            i = j
    return tokens


def _parse_query_graph_text(text):
    """Parse query_graph plain-text response into {rows, total, cols}.

    Format::
        rows: N  (cols: c.name c.qualified_name ...)
          <val1> <val2> ...
          ...
        total: N
    """
    rows = []
    total = 0
    cols = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^rows:\s+(\d+)\s+\(cols:\s*(.*?)\)\s*$", line)
        if m:
            cols = m.group(2).split()
            continue
        m = re.match(r"^total:\s+(\d+)", line)
        if m:
            total = int(m.group(1))
            continue
        # 数据行有缩进；header/total/hint("Query returned no results")顶格
        if raw[:1] in (" ", "\t"):
            toks = _tokenize_text_row(line)
            if toks:
                rows.append(toks)
    return {"rows": rows, "total": total, "cols": cols}


def _flatten_search_graph(result):
    """Flatten search_graph format=json tree model into {results: [...]}.

    Input:  {total, cols, groups[{qn_prefix, file, rows[[...]]}], has_more}
    Output: {results: [{qualified_name, name, label, file_path, lines,
            in_degree, out, <fields...>}], total, has_more}

    qualified_name = qn_prefix + "." + name（无 prefix 时取 name）。
    `lines` 行范围串 → 整数计数；`in` → `in_degree`。
    """
    cols = result.get("cols", [])
    groups = result.get("groups", [])
    results = []
    for grp in groups:
        qn_prefix = grp.get("qn_prefix", "")
        file_path = grp.get("file", "")
        for row in grp.get("rows", []):
            d = dict(zip(cols, row))
            name = d.get("name", "")
            d["qualified_name"] = (qn_prefix + "." + name) if qn_prefix else name
            d["file_path"] = file_path
            if "lines" in d:
                d["lines"] = _lines_range_to_int(d["lines"])
            if "in" in d:
                d["in_degree"] = d["in"]
            results.append(d)
    return {
        "results": results,
        "total": result.get("total", len(results)),
        "has_more": result.get("has_more", False),
    }


def _normalize_search_code(result):
    """Normalize search_code full-mode {cols, rows} into {results: [{...}]}.

    列数组 → dict；`qn`→`qualified_name`、`file`→`file_path`。
    files 模式（有 `files` 无 `rows`）不进入此函数。
    """
    cols = result.get("cols", [])
    rows = result.get("rows", [])
    results = []
    for row in rows:
        d = dict(zip(cols, row))
        if "qn" in d and "qualified_name" not in d:
            d["qualified_name"] = d["qn"]
        if "file" in d and "file_path" not in d:
            d["file_path"] = d["file"]
        results.append(d)
    out = dict(result)
    out["results"] = results
    return out


def _normalize_mcp_response(tool_name, result):
    """归一化本地 codebase-memory-mcp 响应形状至 mcp-scan 下游期望。

    对远端 MCP 安全（形状已匹配时原样返回）：
    - search_graph format=json → tree model {groups}；展平为 {results:[...]}
    - query_graph → 纯文本 "rows: N (cols: ...)"；解析为 {rows:[[...]]}
    - search_code full → {cols,rows}；映射为 {results:[{...}]}
    """
    if isinstance(result, str):
        if tool_name == "query_graph":
            return _parse_query_graph_text(result)
        return result
    if isinstance(result, dict):
        if tool_name == "search_graph" and "groups" in result and "results" not in result:
            return _flatten_search_graph(result)
        if tool_name == "search_code" and "rows" in result and "results" not in result:
            return _normalize_search_code(result)
        return result
    return result


class MCPClient:
    """Minimal MCP HTTP JSON-RPC 2.0 client."""

    def __init__(self, url=MCP_URL, timeout=120, extra_headers=None):
        self.url = url
        self.timeout = timeout
        self.session_id = None
        self._id = 0
        self.extra_headers = extra_headers if extra_headers is not None else MCP_EXTRA_HEADERS

    def _next_id(self):
        self._id += 1
        return self._id

    def _parse_body(self, body):
        """Parse MCP HTTP response body: plain JSON or SSE (text/event-stream).

        mcp-proxy stateless mode returns text/event-stream with `data:` lines.
        """
        body = body.strip() if body else ""
        if not body:
            return {}
        # SSE: contains `data:` / `event:` lines (text/event-stream)
        if body.startswith("data:") or body.startswith("event:") or "\ndata:" in body:
            data_parts = []
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data_parts.append(line[len("data:"):].strip())
            if data_parts:
                # 单个 event：直接拼接解析
                try:
                    return json.loads("".join(data_parts))
                except json.JSONDecodeError:
                    # 多个 event：逐个解析，取 JSON-RPC response
                    for part in data_parts:
                        try:
                            obj = json.loads(part)
                            if isinstance(obj, dict) and "jsonrpc" in obj:
                                return obj
                        except json.JSONDecodeError:
                            continue
            return {}
        # 纯 JSON
        return json.loads(body)

    def initialize(self):
        """Initialize MCP session and capture session ID."""
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.extra_headers}
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
        result = self._parse_body(body)
        if "error" in result:
            raise RuntimeError(f"Initialize error: {result['error']}")
        # Send initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        notif_headers = {**headers}
        if self.session_id:
            notif_headers["Mcp-Session-Id"] = self.session_id
        req2 = urllib.request.Request(
            self.url, data=json.dumps(notif).encode(),
            headers=notif_headers)
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
                    "Accept": "application/json, text/event-stream",
                    **self.extra_headers,
                }
                if self.session_id:
                    headers["Mcp-Session-Id"] = self.session_id
                req = urllib.request.Request(
                    self.url, data=json.dumps(payload).encode(), headers=headers)
                resp = urllib.request.urlopen(req, timeout=self.timeout)
                result = self._parse_body(resp.read().decode())
                if "error" in result:
                    raise RuntimeError(
                        f"RPC error: {json.dumps(result['error'], ensure_ascii=False)[:300]}")
                # MCP returns content as text blocks
                content = result.get("result", {}).get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        try:
                            parsed = json.loads(block["text"])
                        except json.JSONDecodeError:
                            parsed = block["text"]
                        return _normalize_mcp_response(name, parsed)
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


# ── 数据采集步骤 ──

def collect_methods(client, project, file_patterns=None, limit=2000):
    """Step 1: Paginated search_graph to collect all Method + Function nodes.

    file_patterns 支持多个目录（项目含多个源码目录 / 插件目录时使用）：
      - 传入 list 时，逐个 pattern 分页拉取后按 qualified_name 去重合并
      - None / 空 list 时全量查询（不过滤）
    Function nodes include free C/C++ functions (main, helpers, etc.).
    Some Function entries are noise (macros, using-declarations, misclassified
    constructors) — filtered later by scan-inventory.py.
    """
    # 兼容单个字符串传入
    if isinstance(file_patterns, str):
        file_patterns = [file_patterns]
    # None / 空 → 单轮全量查询
    patterns = file_patterns or [None]

    all_methods = []
    all_functions = []
    seen_method_qns = set()
    seen_function_qns = set()

    print(f"\n📊 [1/5] Collecting Method + Function nodes...")
    if any(patterns):
        print(f"   file_patterns: {[p for p in patterns if p]}")

    for pattern in patterns:
        args_base = {
            "project": project, "limit": limit, "offset": 0,
            "format": "json", "fields": SEARCH_GRAPH_FIELDS,
        }
        if pattern:
            args_base["file_pattern"] = pattern
            print(f"   ── pattern: {pattern}")

        # 1A. Method nodes (class methods)
        offset = 0
        page = 0
        args = {**args_base, "label": "Method"}
        while True:
            page += 1
            args["offset"] = offset
            data = client.call_tool("search_graph", args)
            results = data.get("results", [])
            total = data.get("total", 0)
            has_more = data.get("has_more", False)

            new = 0
            for r in results:
                qn = r.get("qualified_name")
                # 无 qn 的噪声节点直接保留；有 qn 的按 qn 去重
                if qn and qn in seen_method_qns:
                    continue
                if qn:
                    seen_method_qns.add(qn)
                all_methods.append(r)
                new += 1

            if page == 1:
                print(f"   Method: total={total}, +{new} new")

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

            new = 0
            for r in results:
                qn = r.get("qualified_name")
                if qn and qn in seen_function_qns:
                    continue
                if qn:
                    seen_function_qns.add(qn)
                all_functions.append(r)
                new += 1

            if page == 1:
                print(f"   Function: total={total}, +{new} new")

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
    """Deduplicate classes by qualified_name.

    缺 qualified_name 的节点（空字符串/None）不参与去重，全部保留——
    它们是各自独立的噪声节点，塌缩成一个会丢失数据。
    """
    seen = set()
    result = []
    for cls in classes:
        qn = cls.get("qualified_name") or ""
        if not qn:  # 无 qn → 不去重，直接保留
            result.append(cls)
            continue
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


def resolve_base_sha(client, project, explicit=None):
    """解析 base_sha：显式传入优先，否则从图谱 index_status 取 git.head_sha。

    base_sha 语义为「本次 inventory 数据所基于的图谱版本」，应与图谱索引时
    记录的 git HEAD 一致，而非本地工作区 HEAD（后者可能比图谱更新）。
    用图谱版本才能让 reconcile 正确检测「图谱落后于代码」的情形。
    """
    if explicit:
        return explicit
    try:
        data = client.call_tool("index_status", {"project": project})
        git = data.get("git", {}) if isinstance(data, dict) else {}
        sha = git.get("head_sha") or git.get("base_sha")
        if sha:
            print(f"   base_sha (from graph): {sha}")
            return sha
        print(f"   ⚠️  index_status 未返回 git.head_sha，base_sha 回退 unknown")
    except Exception as e:
        print(f"   ⚠️  获取 index_status 失败，base_sha 回退 unknown: {e}")
    return "unknown"


# ── 主流程 ──


# ── 主流程 ──

def build_mcp_dump(project, methods, functions, dbus_adaptor, dbus_interface,
                   concurrent, gui, dbus_slots, q_invokables, q_plugins, p75):
    """Assemble mcp_dump dict for build_inventory()."""
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


# ── 增量更新：人工标记 overlay ──────────────────────────────────────────────
#
# 思路：以图谱最新数据全量重建 methods，旧 inventory 中只有"人工标记"需要同步回写。
#   - qn 对得上 → 同步人工字段（level/source/review_status/usecase_count）
#   - qn 对不上（方法已删）→ 直接丢弃，不留墓碑
#   - 不做改名软匹配：qn 是唯一主键，qn 变了即视为新方法，人工标记自然丢失
#
# 视为"人工标记"需要同步的字段：
#   source == "manual"           → 人工设定了 level，同步 source + level
#   review_status == "confirmed" → 人工确认过，同步 review_status
#   usecase_count > 0            → Mode 2 写入的用例数，同步 usecase_count
#
# 顶层配置：file_overrides 整体保留；review_queue 中 confirmed 条目保留（方法仍存在时）。
# scope_rules / gate_thresholds 跟随 build_inventory 重新生成（与 testable 计算保持一致）。


# ── 增量更新：人工标记 overlay ──────────────────────────────────────────────
#
# 思路：以图谱最新数据全量重建 methods，旧 inventory 中只有"人工标记"需要同步回写。
#   - qn 对得上 → 同步人工字段（level/source/review_status/usecase_count）
#   - qn 对不上（方法已删）→ 直接丢弃，不留墓碑
#   - 不做改名软匹配：qn 是唯一主键，qn 变了即视为新方法，人工标记自然丢失
#
# 视为"人工标记"需要同步的字段：
#   source == "manual"           → 人工设定了 level，同步 source + level
#   review_status == "confirmed" → 人工确认过，同步 review_status
#   usecase_count > 0            → Mode 2 写入的用例数，同步 usecase_count
#
# 顶层配置：file_overrides 整体保留；review_queue 中 confirmed 条目保留（方法仍存在时）。
# scope_rules / gate_thresholds 跟随 build_inventory 重新生成（与 testable 计算保持一致）。

def extract_human_overlay(old_inventory):
    """从旧 inventory 提取人工标记。

    返回 {qualified_name: {field: value}}，只含人工标记字段。
    """
    overlay = {}
    for m in old_inventory.get("methods", []):
        qn = m.get("qualified_name", "")
        if not qn:
            continue
        human = {}
        if m.get("source") == "manual" and m.get("level"):
            human["source"] = "manual"
            human["level"] = m["level"]
        if m.get("review_status") == "confirmed":
            human["review_status"] = "confirmed"
        if m.get("usecase_count", 0) > 0:
            human["usecase_count"] = m.get("usecase_count", 0)
        if m.get("test_cover_count", 0) > 0:
            human["test_cover_count"] = m["test_cover_count"]
        if m.get("test_files"):
            human["test_files"] = m["test_files"]
        if m.get("test_cases"):
            human["test_cases"] = m["test_cases"]
        if m.get("test_source"):
            human["test_source"] = m["test_source"]
        if human:
            overlay[qn] = human
    return overlay


def apply_overlay_to_methods(new_methods, overlay):
    """把人工标记回写到新 methods。

    返回 (applied_count, lost_list):
      applied_count — 成功回写的方法数
      lost_list     — 旧 inventory 中有人工标记、但新 methods 里已不存在（被删除）的方法
    """
    new_qns = {m.get("qualified_name") for m in new_methods}
    applied = 0
    for m in new_methods:
        qn = m.get("qualified_name", "")
        if qn in overlay:
            m.update(overlay[qn])
            applied += 1
    lost = [{"qualified_name": qn, "fields": v}
            for qn, v in overlay.items() if qn not in new_qns]
    return applied, lost


def merge_review_queue(new_rq, old_rq, new_method_qns, new_methods_by_qn=None):
    """合并 review_queue。

    - 旧 confirmed 且方法仍存在 → 保留（review_status=confirmed），补全缺失字段
    - 旧 confirmed 但方法已删   → 丢弃（与"不存在的都去掉"一致）
    - 新 pending 且未被旧 confirmed 覆盖 → 追加
    - 新 pending 但该方法已 confirmed → 抑制（避免重复 pending）
    """
    confirmed_qns = set()
    kept_confirmed = []
    for r in old_rq or []:
        if (r.get("review_status") == "confirmed"
                and r.get("qualified_name") in new_method_qns):
            # 从新 methods 补全旧 confirmed 条目可能缺的字段（class_qn 等）
            entry = dict(r)  # 浅拷贝
            if new_methods_by_qn and r.get("qualified_name") in new_methods_by_qn:
                nm = new_methods_by_qn[r["qualified_name"]]
                entry.setdefault("class_qn", nm.get("class_qn"))
                entry.setdefault("name", nm.get("name"))
            kept_confirmed.append(entry)
            confirmed_qns.add(r.get("qualified_name"))
    new_pending = [r for r in new_rq or []
                   if r.get("qualified_name") not in confirmed_qns]
    return kept_confirmed + new_pending


def compute_diff(old_inventory, new_inventory, applied_count, lost_list):
    """对比新旧 inventory，返回 diff dict 供报告渲染。"""
    old_by_qn = {m.get("qualified_name"): m for m in old_inventory.get("methods", [])}
    new_by_qn = {m.get("qualified_name"): m for m in new_inventory.get("methods", [])}

    added, removed, sig_changed, level_changed = [], [], [], []

    for qn, m in new_by_qn.items():
        if qn not in old_by_qn:
            added.append(m)
            continue
        old_m = old_by_qn[qn]
        # 签名变更（qn 相同）
        if (m.get("signature") or "") != (old_m.get("signature") or ""):
            sig_changed.append({"qn": qn,
                                "old": old_m.get("signature", ""),
                                "new": m.get("signature", "")})
        # level 变化：只看非 manual（manual 的 level 被 overlay 保留，必然不变）
        if m.get("source") != "manual" and m.get("level") != old_m.get("level"):
            level_changed.append({
                "qn": qn, "old": old_m.get("level"), "new": m.get("level"),
                "factors": m.get("factors", []),
            })

    for qn, m in old_by_qn.items():
        if qn not in new_by_qn:
            removed.append(m)

    return {
        "added": added, "removed": removed,
        "sig_changed": sig_changed, "level_changed": level_changed,
        "preserved": applied_count, "lost": lost_list,
    }


def _md_table(headers, rows):
    """渲染一个 Markdown 表格。"""
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return out


def render_diff_report(diff, project, old_sha, new_sha):
    """渲染增量更新 Markdown 报告。"""
    lines = [
        "# Inventory 增量更新报告",
        "",
        f"- 项目: `{project}`",
        f"- 旧 base_sha: `{old_sha}`",
        f"- 新 base_sha: `{new_sha}`",
        "",
        "## 概览",
        "",
        "| 类别 | 数量 |",
        "|------|------|",
        f"| 新增方法 | {len(diff['added'])} |",
        f"| 删除方法（已清理） | {len(diff['removed'])} |",
        f"| 签名变更 | {len(diff['sig_changed'])} |",
        f"| level 变化（auto 方法） | {len(diff['level_changed'])} |",
        f"| 人工标记保留 | {diff['preserved']} |",
        f"| 人工标记丢失（方法已删） | {len(diff['lost'])} |",
        "",
    ]

    if diff["added"]:
        lines += ["## 新增方法", ""]
        lines += _md_table(
            ["qualified_name", "类", "level", "factors"],
            [[m.get("qualified_name"), m.get("class_qn") or "-",
              m.get("level"), ", ".join(m.get("factors", []))] for m in diff["added"]])
        lines.append("")

    if diff["removed"]:
        lines += ["## 删除方法（已从 inventory 清理）", ""]
        lines += _md_table(
            ["qualified_name", "原 level", "含人工标记"],
            [[m.get("qualified_name"), m.get("level"),
              "是" if (m.get("source") == "manual"
                       or m.get("review_status") == "confirmed") else "否"]
             for m in diff["removed"]])
        lines.append("")

    if diff["sig_changed"]:
        lines += ["## 签名变更", "",
                  "> qn 相同但 signature 变化：对应测试可能需要重新生成。", ""]
        lines += _md_table(
            ["qualified_name", "旧 signature", "新 signature"],
            [[r["qn"], (r["old"] or "")[:80], (r["new"] or "")[:80]]
             for r in diff["sig_changed"]])
        lines.append("")

    if diff["level_changed"]:
        lines += ["## level 变化（仅 auto 方法，人工 manual 不受影响）", ""]
        lines += _md_table(
            ["qualified_name", "旧 level", "新 level", "factors"],
            [[r["qn"], r["old"], r["new"], ", ".join(r["factors"])]
             for r in diff["level_changed"]])
        lines.append("")

    if diff["lost"]:
        lines += ["## ⚠️ 人工标记丢失（方法已从代码库删除）", "",
                  "以下方法曾有人工标记，但图谱中已不存在，已被清理。",
                  "如需保留请从 git 历史恢复旧 inventory。", ""]
        lines += _md_table(
            ["qualified_name", "丢失字段"],
            [[r["qualified_name"], ", ".join(r["fields"].keys())] for r in diff["lost"]])
        lines.append("")

    if diff["preserved"]:
        lines += [f"## 人工标记保留（{diff['preserved']} 个方法已回写）", "",
                  "详见 .ut-inventory.json 中 source=manual / "
                  "review_status=confirmed 的条目。", ""]

    return "\n".join(lines)


# ── extract-branches 子命令（self-checker §2c 分支清单交叉验证） ──────
#
# 把 self-checker.md §2c「分支清单交叉验证」从"模型+MCP 手工步骤"固化为脚本：
#   1. 从 inventory 取目标类的 testable 方法（qn + name + complexity）
#   2. 对每个方法调 MCP get_code_snippet 拉真实方法体（不 read 源文件，Iron Law #12）
#   3. extract_branches() 用正则数真实分支（if/else if/switch case/for/while/throw/early return/三元）
#   4. parse_declared_branches() 解析测试文件顶部声明的分支清单（// B1: ... 格式）
#   5. cross_check_branches() 做差集 → MISSING_BRANCH_LIST / BRANCH_NOT_MAPPED
# 模型只消费违规清单决定补什么用例，不自己回读源码数分支。
#
# 用法:
#   python3 fetch-mcp-data.py extract-branches \
#     --project <name> --test-file autotests/core/test_foo.cpp \
#     --inventory autotests/.ut-inventory.json [--class Foo] [--json] [-o out.json]

# 分支类型正则（在 strip_cpp_comments_and_strings 之后匹配，避免误数注释/字符串里的关键字）
BRANCH_PATTERNS = [
    ("if",      re.compile(r'\bif\s*\(')),
    ("case",    re.compile(r'\bcase\b\s+[^:]+:')),   # switch case
    ("default", re.compile(r'\bdefault\s*:')),            # switch default（test-types §4.2 要求覆盖）
    ("for",     re.compile(r'\bfor\s*\(')),
    ("while",   re.compile(r'\bwhile\s*\(')),
    ("throw",   re.compile(r'\bthrow\b')),
    ("return",  re.compile(r'\breturn\b')),            # 早退 return（末尾 return 在 extract_branches 里剔除）
    ("ternary", re.compile(r'\w\s*\?\s*\w[^:]*:')),   # 三元 cond ? a : b
]

# 测试文件顶部声明的分支清单标记：// B1: cond → outcome
DECLARED_BRANCH_RE = re.compile(r'//\s*B\d+\s*:', re.MULTILINE)
# 分支清单段落标题：// 分支清单（来源：...MethodName...）
BRANCH_SECTION_RE = re.compile(
    r'//\s*分支清单[（(]([^）)]*)[）)]')


# ── extract-branches 子命令（self-checker §2c 分支清单交叉验证） ──────
#
# 把 self-checker.md §2c「分支清单交叉验证」从"模型+MCP 手工步骤"固化为脚本：
#   1. 从 inventory 取目标类的 testable 方法（qn + name + complexity）
#   2. 对每个方法调 MCP get_code_snippet 拉真实方法体（不 read 源文件，Iron Law #12）
#   3. extract_branches() 用正则数真实分支（if/else if/switch case/for/while/throw/early return/三元）
#   4. parse_declared_branches() 解析测试文件顶部声明的分支清单（// B1: ... 格式）
#   5. cross_check_branches() 做差集 → MISSING_BRANCH_LIST / BRANCH_NOT_MAPPED
# 模型只消费违规清单决定补什么用例，不自己回读源码数分支。
#
# 用法:
#   python3 mcp-scan.py extract-branches \
#     --project <name> --test-file autotests/core/test_foo.cpp \
#     --inventory autotests/.ut-inventory.json [--class Foo] [--json] [-o out.json]

# 分支类型正则（在 strip_cpp_comments_and_strings 之后匹配，避免误数注释/字符串里的关键字）
BRANCH_PATTERNS = [
    ("if",      re.compile(r'\bif\s*\(')),
    ("case",    re.compile(r'\bcase\b\s+[^:]+:')),   # switch case
    ("default", re.compile(r'\bdefault\s*:')),            # switch default（test-types §4.2 要求覆盖）
    ("for",     re.compile(r'\bfor\s*\(')),
    ("while",   re.compile(r'\bwhile\s*\(')),
    ("throw",   re.compile(r'\bthrow\b')),
    ("return",  re.compile(r'\breturn\b')),            # 早退 return（末尾 return 在 extract_branches 里剔除）
    ("ternary", re.compile(r'\w\s*\?\s*\w[^:]*:')),   # 三元 cond ? a : b
]

# 测试文件顶部声明的分支清单标记：// B1: cond → outcome
DECLARED_BRANCH_RE = re.compile(r'//\s*B\d+\s*:', re.MULTILINE)
# 分支清单段落标题：// 分支清单（来源：...MethodName...）
BRANCH_SECTION_RE = re.compile(
    r'//\s*分支清单[（(]([^）)]*)[）)]')



def strip_cpp_comments_and_strings(code):
    """去掉 C++ 注释和字符串/字符字面量，避免误数分支。

    注释里的 if、字符串里的 case 不应计入真实分支。
    保留代码结构与换行，只清空字面量内容。
    """
    # 块注释 /* ... */（跨行）
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
    # 行注释 //...
    code = re.sub(r'//[^\n]*', '', code)
    # 字符串 "..." （含转义）→ ""
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    # 字符 '...' → ''
    code = re.sub(r"'(?:\\.|[^'\\])*'", "''", code)
    return code


def extract_branches(body):
    """从方法体提取真实分支计数。

    返回 dict: {branch_type: count} + {"total": N}。
    early return = return 总数 - 1（末尾 return 不算分支），最少 0。
    body 为空字符串时返回全 0。

    已知漏计（heuristic 限制，需 test-writer.md 编排流程人工补）：
    - for/while 内的 break/continue（§4.2 要求覆盖，但 break 在 switch 里与 case 重复，难区分）
    - 短路求值 && / || 的左右分支（§4.2 要求）
    """
    counts = {btype: 0 for btype, _ in BRANCH_PATTERNS}
    counts["total"] = 0
    if not body:
        return counts
    cleaned = strip_cpp_comments_and_strings(body)
    for btype, pat in BRANCH_PATTERNS:
        counts[btype] = len(pat.findall(cleaned))
    # early return：末尾 return 不算分支
    if counts["return"] > 0:
        counts["return"] = max(0, counts["return"] - 1)
    counts["total"] = sum(counts.values())
    return counts


def parse_declared_branches(content, method_name=""):
    r"""解析测试文件中为某方法声明的分支清单（// B1: ... 格式）。

    定位 "// 分支清单（来源：...method_name...）" 段落，数该段落内的 B\d+ 行。
    method_name 为空时数全文件声明分支（兜底）。
    返回声明分支计数（int）。
    """
    if not method_name:
        return len(DECLARED_BRANCH_RE.findall(content))
    # 定位含 method_name 的分支清单段落（词边界匹配，避免短名子串误匹配）
    # 如方法 "a" 不应误命中 "来源：Foo::parse" 段落
    section = None
    for m in BRANCH_SECTION_RE.finditer(content):
        if re.search(r'\b' + re.escape(method_name) + r'\b', m.group(1)):
            start = m.start()
            # 段落到下一个分支清单标题或文件末结束
            nxt = BRANCH_SECTION_RE.search(content, m.end())
            end = nxt.start() if nxt else len(content)
            section = content[start:end]
            break
    if section is None:
        return 0  # 该方法无声明段落
    return len(DECLARED_BRANCH_RE.findall(section))


def cross_check_branches(real_total, declared_count, is_complex):
    """差集判定分支清单是否覆盖真实分支。

    - is_complex 且 declared==0 → MISSING_BRANCH_LIST
    - declared>0 且 declared < real_total → BRANCH_NOT_MAPPED (declared=X actual=Y)
    - 简单方法 declared==0 → pass（§4.1 允许简单方法省略清单）
    返回 (rule, severity, message) 或 None。
    """
    if is_complex and declared_count == 0:
        return ("MISSING_BRANCH_LIST", "error", "复杂方法无分支清单注释")
    # 仅当声明了清单（declared>0）但不完整时判 NOT_MAPPED；
    # 简单方法无清单（declared==0）允许跳过（§4.1）
    if declared_count > 0 and declared_count < real_total:
        return ("BRANCH_NOT_MAPPED", "error",
                f"declared={declared_count} actual={real_total}")
    return None


def fetch_method_bodies(client, project, methods):
    """对每个方法调 get_code_snippet 拉方法体。

    methods: [{"qualified_name":..., "name":..., "complexity":...}, ...]
    返回 {qn: {"body":..., "name":..., "complexity":..., "error"?:...}}。
    get_code_snippet 返回结构字段名不确定（body/code/source/snippet/implementation），
    逐一尝试 + 纯字符串兜底。失败的方法 body 为空 + error 记录。
    """
    result = {}
    for m in methods:
        qn = m.get("qualified_name")
        if not qn:
            continue
        entry = {"body": "", "name": m.get("name", ""),
                 "complexity": m.get("complexity", 0) or 0}
        try:
            snippet = client.call_tool(
                "get_code_snippet",
                {"project": project, "qualified_name": qn})
            body = ""
            if isinstance(snippet, dict):
                # 尝试常见字段名（远端/本地提供方可能不同）
                for key in ("body", "code", "source", "snippet",
                            "implementation", "text"):
                    val = snippet.get(key)
                    if isinstance(val, str) and val:
                        body = val
                        break
            elif isinstance(snippet, str):
                body = snippet
            entry["body"] = body
        except Exception as e:
            entry["error"] = str(e)
        result[qn] = entry
    return result


def select_class_methods(inventory, classname):
    """从 inventory 取目标类的 testable 方法。

    匹配规则：method.class_qn 末段 == classname，或 class_qn 含 classname（命名空间场景）。
    返回 [{qualified_name, name, complexity, class_qn}, ...]。
    """
    methods = []
    for m in inventory.get("methods", []):
        if not m.get("testable", True):
            continue
        cqn = m.get("class_qn") or ""
        # class_qn 形如 "ns.Class"；取末段与 classname 比对，也允许整体包含
        if cqn == classname or cqn.endswith("." + classname) or classname in cqn.split("."):
            methods.append(m)
    return methods


def run_extract_branches():
    """extract-branches 子命令入口（独立 argparse，向后兼容）。

    保留原 fetch-mcp-data.py 的独立入口签名，内部委托给 cmd_extract_branches。
    """
    parser = argparse.ArgumentParser(
        description="self-checker §2c 分支清单交叉验证："
                    "MCP get_code_snippet 拉真实源码分支 → 与测试文件声明分支做差集")
    parser.add_argument("--project", required=True, help="MCP 项目名")
    parser.add_argument("--test-file", required=True, help="测试文件路径")
    parser.add_argument("--inventory", required=True,
                        help=".ut-inventory.json 路径")
    parser.add_argument("--class", dest="classname", default=None,
                        help="限定类名；未指定时从 test_<class>.cpp 文件名推断")
    parser.add_argument("--mcp-url", default=MCP_URL, help="MCP HTTP 端点")
    parser.add_argument("--json", action="store_true", help="额外打印完整 JSON")
    parser.add_argument("-o", "--output", default=None, help="写 JSON 到文件")
    args = parser.parse_args()
    sys.exit(cmd_extract_branches(args))



# ════════════════════════════════════════════════════════════════════════════
# === 子命令入口 ===
# ════════════════════════════════════════════════════════════════════════════

def cmd_scan(args):
    """scan 子命令：从预采集的 MCP 数据 JSON 评分建表（原 scan-inventory.py main）。"""
    if args.mcp_dump:
        mcp_data = load_mcp_dump(args.mcp_dump)
    else:
        print("错误: 需要 --mcp-dump 参数指定预采集数据文件", file=sys.stderr)
        print("请先用 Agent 采集 MCP 数据并保存为 JSON", file=sys.stderr)
        sys.exit(1)

    inventory = build_inventory(mcp_data, args.project, args.base_sha,
                                project_root=str(Path(args.output).resolve().parent.parent)
                                if Path(args.output).resolve().parent.name == 'autotests'
                                else str(Path(args.output).resolve().parent),
                                qt_version=args.qt_version)

    # 写 JSON
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(inventory, f, indent=2, ensure_ascii=False)
    print(f"✅ 写入 {args.output} ({len(inventory['methods'])} 方法)")

    # 写摘要
    if args.summary:
        summary_path = args.output.replace('.json', '-summary.md')
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(generate_summary(inventory))
        print(f"✅ 写入 {summary_path}")

    # 打印概要
    stats = inventory["scan_stats"]
    print(f"\n{'='*50}")
    print(f"项目: {args.project}")
    print(f"可测试: {stats['testable']}  不可测试: {stats['non_testable']}")
    print(f"high: {stats['high']}  mid: {stats['mid']}  low: {stats['low']}")
    print(f"待复核: {stats['review_pending']}")

    return 0


def cmd_fetch(args):
    """fetch 子命令：端到端 MCP 采集 + 评分（原 fetch-mcp-data.py main）。"""
    # 增量模式前置校验：--existing 必须存在，避免浪费 MCP 采集
    if args.incremental:
        if not args.existing:
            print("❌ --incremental 需配合 --existing 指向旧 .ut-inventory.json",
                  file=sys.stderr)
            sys.exit(1)
        if not os.path.isfile(args.existing):
            print(f"❌ --existing 文件不存在: {args.existing}", file=sys.stderr)
            sys.exit(1)

    # 连接 MCP
    client = MCPClient(url=args.mcp_url)
    print(f"🔗 Connecting to {args.mcp_url}...")
    client.initialize()
    _sid = client.session_id or "stateless"
    print(f"✅ Session: {_sid[:12]}...")
    print(f"📋 Project: {args.project}")

    # 解析 base_sha：显式传入优先，否则从图谱 index_status 取 git.head_sha
    base_sha = resolve_base_sha(client, args.project, args.base_sha)

    # 规整 --file-pattern：action="append" 产出 list，再拆逗号分隔、去空、去重
    file_patterns = None
    if args.file_pattern:
        file_patterns = []
        for fp in args.file_pattern:
            for p in fp.split(","):
                p = p.strip()
                if p and p not in file_patterns:
                    file_patterns.append(p)

    # Step 1-5: 采集数据
    methods, functions = collect_methods(client, args.project, file_patterns, args.limit)
    dbus_adaptor, dbus_interface, concurrent, gui = collect_inheritance(client, args.project)
    dbus_slots = collect_dbus_slots(client, args.project, dbus_adaptor)
    q_invokables, q_plugins = collect_qt_macros(client, args.project)
    p75 = compute_p75_nonzero(methods + functions)

    # 构建 mcp_dump
    mcp_dump = build_mcp_dump(
        args.project, methods, functions, dbus_adaptor, dbus_interface,
        concurrent, gui, dbus_slots, q_invokables, q_plugins, p75)

    # 提前创建输出目录，避免 --keep-dump / summary / diff 写入时目录不存在
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    if args.keep_dump:
        dump_path = os.path.join(
            os.path.dirname(args.output) or ".",
            os.path.basename(args.output).replace(".json", "") + "_mcp_dump.json")
        with open(dump_path, "w") as f:
            json.dump(mcp_dump, f, ensure_ascii=False)
        print(f"\n💾 mcp_dump saved to {dump_path}")

    # 调用 build_inventory 评分（直接调用，不再动态导入 scan-inventory.py）
    print(f"\n🔧 Scoring methods via build_inventory()...")

    # gate_thresholds：增量模式从旧 inventory 读取（保留外部设定），首次建表用默认值
    existing_gates = None
    if args.incremental and args.existing:
        try:
            with open(args.existing, "r", encoding="utf-8") as f:
                old_inv = json.load(f)
            existing_gates = old_inv.get("gate_thresholds")
            if existing_gates:
                print(f"   gate_thresholds: 从旧 inventory 读取（保留外部设定）")
        except Exception:
            pass  # 读取失败不影响主流程，后续正式 load 会再校验

    # 推导项目根目录：inventory 文件通常在 <project>/autotests/.ut-inventory.json
    # 取 output 父目录的父目录；若路径在 tests/ 或 autotests/ 下则取父目录
    output_dir = Path(args.output).resolve().parent
    output_parent = output_dir.parent
    dir_name = output_dir.name
    project_root = str(output_parent) if dir_name in ("autotests", "tests") else str(output_dir)
    print(f"📁 推导项目根目录: {project_root}  (from --output {args.output})")

    inventory = build_inventory(mcp_dump, args.project, base_sha,
                                gate_thresholds=existing_gates,
                                project_root=project_root)

    # ── test_* 覆盖回写（非增量默认采集；增量模式靠 overlay 保留，不重采） ──
    # 首次建表时 fetch 应产出带 test_* 的完整 inventory，而非半成品。
    # 增量模式下旧 test_* 已由 extract_human_overlay 提取并 apply_overlay_to_methods
    # 贴回（见下方增量分支），无需重新采集（省一整轮 CALLS 查询）。
    if not args.incremental and not args.skip_test_mapping:
        print(f"\n📊 test_* 采集：CALLS 边 → 被测函数...")
        try:
            test_modules = discover_test_modules(client, args.project)
            if test_modules:
                source_to_tests = collect_all_calls(client, args.project, test_modules)
                file_to_cases = fetch_test_cases(client, args.project, test_modules)
                mapping = build_test_mapping(source_to_tests, file_to_cases)
                updated, unmatched, _ = update_inventory_test_mapping(
                    inventory, mapping)
                print(f"   test_* 已回写：{updated} 方法覆盖，{unmatched} 未匹配")

                # 回写后重算 usecase_* 统计（与增量分支保持一致），避免首次建表
                # 出现 usecase_covered=0 而 methods 已带 test_cover_count 的矛盾
                stats = inventory["scan_stats"]
                covered = sum(1 for m in inventory["methods"]
                              if m.get("testable", True) and m.get("usecase_count", 0) > 0)
                stats["usecase_covered"] = covered
                stats["usecase_not_covered"] = stats["testable"] - covered
            else:
                print(f"   无 ut_* 测试模块，跳过 test_* 采集")
        except Exception as e:
            # test_* 采集失败不阻断主流程（inventory 仍可用，仅缺 test_* 字段）
            print(f"   ⚠️  test_* 采集失败（不阻断）: {e}")

    # ── 增量模式：同步旧 inventory 的人工标记 ──
    old_sha_for_report = "unknown"
    diff = None
    if args.incremental:
        try:
            with open(args.existing, "r", encoding="utf-8") as f:
                old_inventory = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ --existing JSON 损坏: {args.existing}: {e}", file=sys.stderr)
            sys.exit(1)
        old_sha_for_report = old_inventory.get("base_sha", "unknown")
        print(f"\n🔀 增量模式：从 {args.existing} 同步人工标记...")

        overlay = extract_human_overlay(old_inventory)
        print(f"   提取人工标记: {len(overlay)} 个方法")

        applied, lost = apply_overlay_to_methods(inventory["methods"], overlay)
        print(f"   回写: {applied} 个，丢失（方法已删）: {len(lost)} 个")

        new_method_qns = {m.get("qualified_name") for m in inventory["methods"]}
        new_methods_by_qn = {m.get("qualified_name"): m for m in inventory["methods"]}
        inventory["review_queue"] = merge_review_queue(
            inventory.get("review_queue", []),
            old_inventory.get("review_queue", []),
            new_method_qns,
            new_methods_by_qn,
        )

        # file_overrides 整体保留
        if "file_overrides" in old_inventory:
            inventory["file_overrides"] = old_inventory["file_overrides"]

        # gate_thresholds 保留旧 inventory 的值（已在 build_inventory 调用时传入，此处确认一致性）
        if "gate_thresholds" in old_inventory:
            inventory["gate_thresholds"] = old_inventory["gate_thresholds"]

        # 重算 scan_stats 中受 overlay 影响的统计
        stats = inventory["scan_stats"]
        stats["review_pending"] = sum(
            1 for r in inventory["review_queue"]
            if r.get("review_status") == "pending")
        # usecase_covered 只统计 testable 方法（non-testable 方法有 usecase 也不计）
        covered = sum(1 for m in inventory["methods"]
                      if m.get("testable", True) and m.get("usecase_count", 0) > 0)
        stats["usecase_covered"] = covered
        stats["usecase_not_covered"] = stats["testable"] - covered
        # overlay 可能改变 level 分布（manual 覆盖 auto），需重算
        level_counts = {"high": 0, "mid": 0, "low": 0}
        for m in inventory["methods"]:
            if m.get("testable") and m.get("level"):
                level_counts[m["level"]] = level_counts.get(m["level"], 0) + 1
        stats["high"] = level_counts["high"]
        stats["mid"] = level_counts["mid"]
        stats["low"] = level_counts["low"]

        # 计算 diff 供报告
        diff = compute_diff(old_inventory, inventory, applied, lost)

    # 增量覆盖原文件时自动备份（inventory 纳入 git，.bak 不入）
    if args.incremental and os.path.isfile(args.output) \
            and os.path.abspath(args.output) == os.path.abspath(args.existing):
        bak = args.output + ".bak"
        shutil.copyfile(args.output, bak)
        print(f"💾 已备份原文件到 {bak}")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2, ensure_ascii=False)
    print(f"✅ 写入 {args.output} ({len(inventory['methods'])} 方法)")

    # 写摘要
    if args.summary:
        summary_path = args.output.replace(".json", "-summary.md")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(generate_summary(inventory))
        print(f"✅ 写入 {summary_path}")

    # 增量模式：写 diff 报告
    if diff is not None:
        report_path = args.output.replace(".json", "-diff.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(render_diff_report(diff, args.project,
                                       old_sha_for_report, base_sha))
        print(f"✅ 写入增量报告 {report_path}")

    # 打印概要
    stats = inventory["scan_stats"]
    print(f"\n{'=' * 60}")
    print(f"项目: {args.project}")
    print(f"base_sha: {base_sha}")
    print(f"可测试: {stats['testable']}  不可测试: {stats['non_testable']}")
    print(f"high: {stats['high']}  mid: {stats['mid']}  low: {stats['low']}")
    print(f"待复核: {stats['review_pending']}")
    print(f"{'=' * 60}")

    return 0


def cmd_extract_branches(args):
    """extract-branches 子命令：MCP 真实源码分支 × 测试文件声明分支 差集校验。

    原 fetch-mcp-data.py run_extract_branches 的逻辑体，
    参数由 build_parser 的 extract-branches 子解析器预解析后传入。
    """
    # 推断 classname（PascalCase 惯例：test_calculator.cpp → Calculator）
    classname = args.classname
    if not classname:
        basename = os.path.basename(args.test_file)
        m = re.match(r'test_(\w+)\.cpp$', basename)
        if not m:
            print(f"❌ 无法从文件名推断类名: {basename}，请用 --class 指定",
                  file=sys.stderr)
            sys.exit(2)
        classname = m.group(1)
        classname = classname[0].upper() + classname[1:]

    # 读测试文件
    if not os.path.isfile(args.test_file):
        print(f"❌ 测试文件不存在: {args.test_file}", file=sys.stderr)
        sys.exit(2)
    with open(args.test_file, "r", encoding="utf-8") as f:
        test_content = f.read()

    # 读 inventory，取该类 testable 方法
    if not os.path.isfile(args.inventory):
        print(f"❌ inventory 不存在: {args.inventory}", file=sys.stderr)
        sys.exit(2)
    with open(args.inventory, "r", encoding="utf-8") as f:
        inventory = json.load(f)
    methods = select_class_methods(inventory, classname)

    if not methods:
        print(f"[BRANCH] {os.path.basename(args.test_file)} | "
              f"class:{classname} methods:0 | 无可测方法，跳过")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump({"file": args.test_file, "class": classname,
                           "methods": 0, "checked": 0, "violations": []},
                          f, ensure_ascii=False, indent=2)
        return 0

    # 连 MCP 拉方法体
    client = MCPClient(url=args.mcp_url)
    print(f"🔗 Connecting to {args.mcp_url}...")
    client.initialize()
    print(f"📋 Project: {args.project}  Class: {classname}  Methods: {len(methods)}")
    bodies = fetch_method_bodies(client, args.project, methods)

    # 交叉验证
    violations = []
    checked = 0
    for qn, info in bodies.items():
        body = info["body"]
        name = info["name"]
        complexity = info.get("complexity", 0)
        if not body:
            violations.append({
                "check": "branch", "severity": "warning",
                "rule": "SNIPPET_FETCH_FAILED", "method": name,
                "message": f"get_code_snippet 返回空 body"
                           + (f": {info.get('error', '')}" if info.get('error') else ""),
            })
            continue
        real = extract_branches(body)
        real_total = real["total"]
        declared = parse_declared_branches(test_content, name)
        is_complex = complexity >= 10 or real_total >= 3
        res = cross_check_branches(real_total, declared, is_complex)
        checked += 1
        if res:
            rule, sev, msg = res
            violations.append({
                "check": "branch", "severity": sev, "rule": rule,
                "method": name, "message": msg,
                "declared": declared, "actual": real_total,
            })

    errors = sum(1 for v in violations if v["severity"] == "error")
    warnings = sum(1 for v in violations if v["severity"] == "warning")
    print(f"[BRANCH] {os.path.basename(args.test_file)} | "
          f"class:{classname} methods:{len(methods)} checked:{checked} | "
          f"{errors} errors, {warnings} warnings")
    for i, v in enumerate(violations, 1):
        tag = "E" if v["severity"] == "error" else "W"
        print(f"  {tag}{i} branch | {v['rule']} | {v['method']} | {v['message']}")

    out = {"file": args.test_file, "class": classname,
           "methods": len(methods), "checked": checked,
           "violations": violations}
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"✅ JSON written to {args.output}")
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))

    return 1 if errors else 0


# ── test-mapping 子命令（原 fetch-test-mapping.py：MCP CALLS → test_* 回写）──

UT_FILE_PATTERN = re.compile(r'(?:^|/)ut_\w+\.(?:cpp|h)$')
TEST_DIR_MARKERS = ("tests/", "test/")


def discover_test_modules(client, project):
    """发现项目中的所有单元测试模块文件（ut_*.cpp/h）。

    search_graph(label=Module, file_pattern=tests/**) → 过滤 ut_* 文件。
    返回 [{name, file_path, out_degree}, ...]，out_degree>0 表示有 CALLS 关系。
    """
    print(f"\n📊 [1/4] 发现测试模块...")
    data = client.call_tool("search_graph", {
        "project": project,
        "label": "Module",
        "file_pattern": "tests/**",
        "limit": 200,
        "format": "json",
    })
    results = data.get("results", [])
    total = data.get("total", 0)

    test_modules = []
    for r in results:
        file_path = r.get("file_path", "")
        name = r.get("name", "")
        # out_degree 兼容：远端列名可能是 out_degree 或 out
        out_degree = r.get("out_degree") or r.get("out") or 0
        if UT_FILE_PATTERN.search(file_path) or UT_FILE_PATTERN.search(name):
            test_modules.append({
                "name": name,
                "file_path": file_path,
                "out_degree": out_degree,
            })

    test_modules.sort(key=lambda x: x["out_degree"], reverse=True)

    with_calls = [m for m in test_modules if m["out_degree"] > 0]
    without_calls = [m for m in test_modules if m["out_degree"] == 0]

    print(f"   tests/ Module 总数: {total}")
    print(f"   ut_* 单元测试文件: {len(test_modules)}")
    print(f"   有 CALLS 关系: {len(with_calls)}")
    print(f"   无 CALLS 关系: {len(without_calls)}")

    return test_modules


def collect_calls_for_module(client, project, module_name, module_file=None,
                              skip_module_query=False):
    """查询单个测试模块的 CALLS 关系目标。

    返回 [(target_name, target_qn, target_file, target_labels), ...]。

    图谱 schema 兼容：
    - v0.10.0：CALLS 边挂在 Module 节点（name=文件路径），按模块名查询。
    - v0.10.8+：Module 无 CALLS 出边（挂在 File/Function/Method 上），
      回退按测试文件路径匹配任意源节点。
    """
    rows = []
    if not skip_module_query:
        query = (
            f"MATCH (m:Module {{name:'{module_name}'}})-[:CALLS]->(target) "
            f"RETURN target.name, target.qualified_name, target.file_path, labels(target)"
        )
        data = client.call_tool("query_graph", {
            "project": project,
            "query": query,
        })
        rows = data.get("rows", []) if isinstance(data, dict) else []
    if not rows and module_file:
        query = (
            f"MATCH (src)-[:CALLS]->(target) "
            f"WHERE src.file_path = '{module_file}' "
            f"RETURN DISTINCT target.name, target.qualified_name, "
            f"target.file_path, labels(target)"
        )
        data = client.call_tool("query_graph", {
            "project": project,
            "query": query,
        })
        rows = data.get("rows", []) if isinstance(data, dict) else []
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

    过滤规则：去 Field 节点、去 tests/ 目录目标（stub）、只留 Method/Function。
    返回 {source_qn: set(test_files)} 映射。
    """
    print(f"\n📊 [2/4] 收集 CALLS 关系...")

    source_to_tests = defaultdict(set)
    modules_with_calls = [m for m in test_modules if m["out_degree"] > 0]

    # 图谱 schema 探测：v0.10.8+ Module 节点无 CALLS 出边，且其 out_degree
    # 反映 DEFINES 而非 CALLS，不能用 out_degree>0 预筛（会漏掉有 CALLS 的
    # 测试文件）。用首个 out_degree>0 模块探测，新 schema 下改为全量按
    # file_path 查询。
    skip_module_query = False
    if modules_with_calls:
        probe = modules_with_calls[0]
        try:
            data = client.call_tool("query_graph", {
                "project": project,
                "query": (f"MATCH (m:Module {{name:'{probe['name']}'}})"
                          f"-[:CALLS]->() RETURN count(m) AS n"),
            })
            prows = data.get("rows", []) if isinstance(data, dict) else []
            n = int(str(prows[0][0]).strip('"'))
        except Exception:
            n = -1
        if n == 0:
            skip_module_query = True
            modules_with_calls = test_modules
            print("   ℹ️  检测到 v0.10.8+ 图谱（Module 无 CALLS 出边），"
                  "改用 file_path 匹配全量测试模块")

    total_modules = len(modules_with_calls)

    for idx, module in enumerate(modules_with_calls, 1):
        module_name = module["name"]
        module_file = module["file_path"]

        if idx % 5 == 0 or idx == total_modules:
            print(f"   [{idx}/{total_modules}] {module_name} "
                  f"(out_degree={module['out_degree']})")

        try:
            targets = collect_calls_for_module(
                client, project, module_name, module_file,
                skip_module_query=skip_module_query)
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

    search_graph(label=Function, name_pattern=TEST_F, file_pattern=tests/**)
    signature 格式 (test_class, test_case_name)。
    返回 {test_file_path: [test_case_name, ...]} 映射。
    """
    print(f"\n📊 [3/4] 采集 TEST_F 用例名...")
    data = client.call_tool("search_graph", {
        "project": project,
        "label": "Function",
        "name_pattern": "TEST_F",
        "file_pattern": "tests/**",
        "limit": 500,
        "format": "json",
        "fields": ["signature", "docstring"],
    })
    results = data.get("results", [])
    total = data.get("total", 0)

    file_to_cases = defaultdict(list)
    for r in results:
        sig = r.get("signature", "")
        file_path = r.get("file_path", "")
        if not sig or not file_path:
            continue
        match = re.match(r'\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', sig)
        if match:
            test_class = match.group(1).strip()
            test_case = match.group(2).strip()
            full_name = f"{test_class}.{test_case}"
            doc = r.get("docstring", "")
            comment = doc.lstrip('/ ').strip() if doc else ""
            if comment:
                full_name = f"{full_name}  // {comment}"
            file_to_cases[file_path].append(full_name)

    total_cases = sum(len(v) for v in file_to_cases.values())
    files_with_cases = len(file_to_cases)
    print(f"   TEST_F 节点总数: {total}")
    print(f"   含用例名的文件: {files_with_cases}")
    print(f"   用例名总数: {total_cases}")

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


def _tm_normalize_qn(qn):
    """去掉项目前缀段，得到源码路径+类+方法的归一化 qn。

    通用方法：项目根名带 '-'（如 home-uos-service-codebase-repos-deepin-reader、
    home-zhy-debug-deepin-reader），源码路径段不带 '-'（reader/document/browser/sheet/
    application/3rdparty 等）。所以剥掉所有含 '-' 的前导段即可，不需要硬编码仓库名。

    对 'home-uos-service-codebase-repos-deepin-reader.reader.A.foo':
      split('.') → ['home-uos-service-...', 'reader', 'A', 'foo']
      strip leading '-' segments → ['reader', 'A', 'foo']
      → 'reader.A.foo'
    """
    if not qn:
        return qn
    parts = qn.split(".")
    # 去掉所有含 '-' 的前导段（项目根路径段）
    while parts and "-" in parts[0]:
        parts.pop(0)
    return ".".join(parts) if parts else qn


def build_test_mapping(source_to_tests, file_to_cases=None):
    """构建归一化 qn → {test_cover_count, test_files, test_cases} 映射。"""
    mapping = {}
    for qn, test_files in source_to_tests.items():
        nqn = _tm_normalize_qn(qn)
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


def update_inventory_test_mapping(inventory, mapping):
    """将测试覆盖映射回写到 inventory（内存对象，不落盘）。

    匹配：inventory.methods[].qualified_name 归一化后与 mapping key 匹配。
    回写：test_cover_count>0 → 写 test_cover_count/test_files/test_cases/test_source；
         usecase_count 取 max(原值, test_cover_count)；未匹配 → 保留原值。
    返回 (updated_count, unmatched_count, updated_methods_list)。
    """
    updated = 0
    unmatched = 0
    updated_methods = []

    for method in inventory.get("methods", []):
        qn = method.get("qualified_name", "")
        nqn = _tm_normalize_qn(qn)
        if nqn in mapping:
            new_cover = mapping[nqn]["test_cover_count"]
            old_cover = method.get("test_cover_count", 0)
            old_uc = method.get("usecase_count", 0)
            if new_cover > 0:
                method["test_cover_count"] = new_cover
                method["test_files"] = mapping[nqn]["test_files"]
                method["test_cases"] = mapping[nqn].get("test_cases", [])
                method["test_source"] = "mcp_calls"
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


def render_test_mapping_report(updated_methods, unmatched, project, test_summary):
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


def load_test_mapping_from_file(path):
    """从 JSON 文件加载已保存的测试映射（兼容新/旧/原始三种格式）。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    mapping = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and "test_cover_count" in value:
                nqn = _tm_normalize_qn(key)
                mapping[nqn] = value
            elif isinstance(value, dict) and "usecase_count" in value:
                nqn = _tm_normalize_qn(key)
                mapping[nqn] = {
                    "test_cover_count": value.get("usecase_count", 0),
                    "test_files": value.get("test_files", []),
                }
            elif isinstance(value, (list, set)):
                nqn = _tm_normalize_qn(key)
                mapping[nqn] = {
                    "test_cover_count": len(value),
                    "test_files": sorted(value),
                }
    return mapping


def cmd_test_mapping(args):
    """test-mapping 子命令：MCP CALLS → test_* 字段回写 .ut-inventory.json。"""
    if not args.mapping_in and not args.project:
        print("❌ 必须指定 --project 或 --mapping-in（至少一项）", file=sys.stderr)
        return 1
    if not os.path.isfile(args.inventory):
        print(f"❌ inventory 不存在: {args.inventory}", file=sys.stderr)
        return 1

    with open(args.inventory, "r", encoding="utf-8") as f:
        try:
            inventory = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ inventory JSON 损坏: {e}", file=sys.stderr)
            return 1

    project_name = args.project or inventory.get("project", "")

    if args.mapping_in:
        print(f"\n📂 从文件加载映射: {args.mapping_in}")
        mapping = load_test_mapping_from_file(args.mapping_in)
        test_summary = {
            "total_modules": 0,
            "with_calls": 0,
            "covered_sources": len(mapping),
            "total_calls": sum(m["test_cover_count"] for m in mapping.values()),
        }
    else:
        client = MCPClient(url=args.mcp_url)
        print(f"🔗 Connecting to {args.mcp_url}...")
        client.initialize()
        print(f"✅ Session: {(client.session_id or 'stateless')[:12]}...")
        print(f"📋 Project: {project_name}")

        test_modules = discover_test_modules(client, project_name)
        source_to_tests = collect_all_calls(client, project_name, test_modules)
        file_to_cases = fetch_test_cases(client, project_name, test_modules)

        print(f"\n📊 [4/4] 构建函数↔测试映射...")
        mapping = build_test_mapping(source_to_tests, file_to_cases)

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

    print(f"\n🔧 回写 inventory...")
    print(f"   映射中源码节点: {len(mapping)}")
    print(f"   inventory 方法数: {len(inventory.get('methods', []))}")

    updated, unmatched, updated_methods = update_inventory_test_mapping(inventory, mapping)

    print(f"   已更新: {updated}")
    print(f"   未匹配: {unmatched}")

    if updated_methods:
        top = sorted(updated_methods,
                      key=lambda x: x["new_cover"], reverse=True)[:10]
        print(f"\n   Top {min(10, len(top))} 覆盖最多的方法:")
        for m in top:
            files = ", ".join(os.path.basename(f) for f in m["test_files"])
            print(f"     {m['name']}: {m['new_cover']} test_files, "
                  f"usecase_count {m['old_uc']}→{m['new_uc']} ({files})")

    zero_cover = [m for m in inventory.get("methods", [])
                  if m.get("testable", True) and m.get("test_cover_count", 0) == 0
                  and m.get("level") in ("high", "mid")]
    if zero_cover:
        print(f"\n   ⚠️  {len(zero_cover)} 个 high/mid 可测方法无测试覆盖:")
        for m in zero_cover[:15]:
            print(f"     {m.get('name')} ({m.get('level')}) in {m.get('file_path','?')}")
        if len(zero_cover) > 15:
            print(f"     ... 还有 {len(zero_cover) - 15} 个")

    if args.mapping_out:
        os.makedirs(os.path.dirname(args.mapping_out) or ".", exist_ok=True)
        with open(args.mapping_out, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        print(f"\n💾 映射已保存到 {args.mapping_out}")

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

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        report = render_test_mapping_report(updated_methods, unmatched,
                                            project_name, test_summary)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"✅ 报告已写入 {args.report}")

    print(f"\n{'=' * 60}")
    print(f"项目: {project_name}")
    print(f"测试模块: {test_summary['total_modules']}")
    print(f"有 CALLS 关系: {test_summary['with_calls']}")
    print(f"被测源码节点: {test_summary['covered_sources']}")
    print(f"总 CALLS 边: {test_summary['total_calls']}")
    print(f"inventory 更新: {updated} / 未匹配: {unmatched}")
    print(f"{'=' * 60}")
    return 0


def build_parser():
    """构建顶层 argparse，含 scan / fetch / extract-branches / test-mapping 四个子命令。"""
    parser = argparse.ArgumentParser(
        description="MCP 知识图谱 → .ut-inventory.json 端到端探测")

    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # ── scan 子命令（原 scan-inventory.py） ──
    sp_scan = subparsers.add_parser(
        "scan", help="评分建表（原 scan-inventory.py）")
    sp_scan.add_argument("--project", required=True, help="知识图谱中的项目名")
    sp_scan.add_argument("--output", required=True, help="输出 JSON 路径")
    sp_scan.add_argument("--mcp-dump", help="预采集的 MCP 数据 JSON 文件")
    sp_scan.add_argument("--base-sha", default="unknown", help="Git base SHA")
    sp_scan.add_argument("--qt-version", default=None, help="Qt 目标版本 (5/6/6.8)")
    sp_scan.add_argument("--summary", action="store_true", help="同时输出 Markdown 摘要")

    # ── fetch 子命令（原 fetch-mcp-data.py） ──
    sp_fetch = subparsers.add_parser(
        "fetch", help="端到端 MCP 采集 + 评分（原 fetch-mcp-data.py）")
    sp_fetch.add_argument("--project", required=True, help="MCP 项目名")
    sp_fetch.add_argument("--file-pattern", action="append", default=None,
                          help="Glob 过滤源码目录，可多次指定或用逗号分隔 "
                               "(e.g. --file-pattern 'src/**' --file-pattern 'plugins/**', "
                               "或 --file-pattern 'src/**,plugins/**')")
    sp_fetch.add_argument("--output", "-o", required=True,
                          help="输出 .ut-inventory.json 路径")
    sp_fetch.add_argument("--mcp-url", default=MCP_URL, help="MCP HTTP 端点")
    sp_fetch.add_argument("--limit", type=int, default=2000,
                          help="search_graph 分页大小 (default: 2000)")
    sp_fetch.add_argument("--base-sha", default=None,
                          help="Git base SHA；未指定时自动从图谱 index_status 的 "
                               "git.head_sha 获取（推荐，与图谱版本一致）")
    sp_fetch.add_argument("--summary", action="store_true",
                          help="同时输出 Markdown 摘要")
    sp_fetch.add_argument("--keep-dump", action="store_true",
                          help="保留中间 mcp_dump.json 文件")
    sp_fetch.add_argument("--incremental", action="store_true",
                          help="增量模式：全量重建 + 同步旧 inventory 的人工标记"
                               "（level/source/review_status/usecase_count）。"
                               "需配合 --existing 指向旧 .ut-inventory.json")
    sp_fetch.add_argument("--existing", default=None,
                          help="旧 .ut-inventory.json 路径（--incremental 时必需，"
                               "从中提取人工标记回写到新输出；file_overrides 整体保留）")
    sp_fetch.add_argument("--skip-test-mapping", action="store_true",
                          help="跳过 test_* 覆盖回写（默认 fetch 会采集 CALLS 边"
                               "回写 test_cover_count/test_files 等；增量模式本就"
                               "从 overlay 保留 test_*，无需此步；首次建表如"
                               "无 tests/ 目录可跳过省时）")

    # ── extract-branches 子命令（原 fetch-mcp-data.py 子命令） ──
    sp_eb = subparsers.add_parser(
        "extract-branches",
        help="分支清单交叉验证（self-checker §2c）")
    sp_eb.add_argument("--project", required=True, help="MCP 项目名")
    sp_eb.add_argument("--test-file", required=True, help="测试文件路径")
    sp_eb.add_argument("--inventory", required=True,
                       help=".ut-inventory.json 路径")
    sp_eb.add_argument("--class", dest="classname", default=None,
                       help="限定类名；未指定时从 test_<class>.cpp 文件名推断")
    sp_eb.add_argument("--mcp-url", default=MCP_URL, help="MCP HTTP 端点")
    sp_eb.add_argument("--json", action="store_true", help="额外打印完整 JSON")
    sp_eb.add_argument("-o", "--output", default=None, help="写 JSON 到文件")

    # ── test-mapping 子命令（原 fetch-test-mapping.py） ──
    sp_tm = subparsers.add_parser(
        "test-mapping",
        help="仅回写 test_* 字段（原 fetch-test-mapping.py）")
    sp_tm.add_argument("--project", required=False,
                       help="MCP 项目名（与 fetch --project 一致）；"
                            "使用 --mapping-in 时可省略")
    sp_tm.add_argument("--inventory", "-i", required=True,
                       help=".ut-inventory.json 路径")
    sp_tm.add_argument("--mapping-in", default=None,
                       help="已保存的映射 JSON（跳过 MCP 查询，直接回写）")
    sp_tm.add_argument("--mapping-out", default=None,
                       help="保存映射到 JSON 文件（供后续 --mapping-in 使用）")
    sp_tm.add_argument("--report", default=None,
                       help="输出 Markdown 测试覆盖报告路径")
    sp_tm.add_argument("--mcp-url", default=MCP_URL, help="MCP HTTP 端点")
    sp_tm.add_argument("--dry-run", action="store_true",
                       help="只打印映射结果不写回 inventory")
    sp_tm.add_argument("--verbose", "-v", action="store_true",
                       help="打印每个测试模块的详细 CALLS 目标")

    return parser


def main_no_exit(argv=None):
    """统一入口，供测试调用。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {"scan": cmd_scan, "fetch": cmd_fetch,
                "extract-branches": cmd_extract_branches,
                "test-mapping": cmd_test_mapping}
    result = dispatch[args.cmd](args)
    return result if result is not None else 0


def main():
    sys.exit(main_no_exit())


if __name__ == "__main__":
    main()
