#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""mcp-scan.py — GitNexus 代码图谱 MCP → .ut-inventory.json 端到端探测

合并自 scan-inventory.py + fetch-mcp-data.py + fetch-test-mapping.py。四个子命令：
  scan             — 评分建表（原 scan-inventory.py）
  fetch            — 端到端 MCP 采集 + 评分 + test_* 覆盖回写（原 fetch-mcp-data.py）
  extract-branches — 分支清单交叉验证（原 fetch-mcp-data.py 子命令）
  test-mapping     — 仅回写 test_* 字段（原 fetch-test-mapping.py；inventory 已存在时增量刷）

数据源（GitNexus，双源架构，详见 doc/gitnexus-适配分析.md）：
  - 图谱（cypher/context/list_repos）：符号定位、CALLS/EXTENDS/HAS_METHOD 关系。
    属性 camelCase（filePath/startLine/endLine），无 parent_class/base_classes/
    annotations 属性，content 在 5016 字符截断（均实测）。
  - 本地仓库（--repo-root）：方法体行切片、复杂度指标、Q_INVOKABLE/
    Q_PLUGIN_METADATA、签名、TEST_F 用例。

fetch 的 test_* 采集：build_inventory 之后、写文件之前，复用 GitNexusAdapter 采集
CALLS 边（测试文件 → 被测函数），回写 test_cover_count/test_files/test_cases/
test_source。增量模式不重采（overlay 已保留旧 test_*）。

用法:
  # scan: 从预采集的 MCP 数据 JSON 评分建表
  python3 mcp-scan.py scan \
    --project dde-file-manager \
    --output /tmp/inventory-calculator.json \
    --mcp-dump /tmp/mcp_dump.json

  # fetch: 端到端 MCP 采集 + 评分
  python3 mcp-scan.py fetch \
    --project dde-file-manager \
    --repo-root ~/debug/dde-file-manager \
    --file-pattern "src/**" \
    --output .ut-inventory.json

  # extract-branches: 分支清单交叉验证
  python3 mcp-scan.py extract-branches \
    --project <name> --test-file autotests/core/test_foo.cpp \
    --inventory autotests/.ut-inventory.json [--class Foo] [--json] [-o out.json]

示例:
  # dde-file-manager（排除 3rdparty）
  python3 mcp-scan.py fetch \
    --project dde-file-manager \
    --repo-root ~/debug/dde-file-manager \
    --file-pattern "src/**" \
    --output /tmp/dde-file-manager/.ut-inventory.json

  # 多目录项目（源码 + 插件，分别指定或逗号分隔，结果自动去重合并）
  python3 mcp-scan.py fetch \
    --project xxx --repo-root ~/debug/xxx \
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
import subprocess
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
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
# - QTAG_MCP_URL: GitNexus MCP HTTP 端点（覆盖默认值）

MCP_URL = os.environ.get("QTAG_MCP_URL", "https://codegraph.uniontech.com/api/mcp")

# GitNexus 默认认证头（Basic，见 doc/new_代码图谱MCP_使用文档.md）。
#   QTAG_MCP_HEADERS: JSON 字符串，设置后整体替换默认头，如 '{"Authorization": "Basic xxx"}'
#   QTAG_MCP_API_KEY: 单独 apiKey → 自动构造 {"X-API-Key": "<key>"}
MCP_EXTRA_HEADERS = {"Authorization": "Basic Z2l0bmV4dXM6Z2l0bmV4dXMuMTEyMg=="}
_json_headers = os.environ.get("QTAG_MCP_HEADERS")
if _json_headers:
    try:
        MCP_EXTRA_HEADERS = json.loads(_json_headers) or {}
    except Exception:
        pass
if os.environ.get("QTAG_MCP_API_KEY"):
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

# ── GitNexus 响应编解码（ResponseCodec）──────────────────────────────────────
#
# GitNexus 实测响应形态（见 doc/gitnexus-适配分析.md）：
#   1. 工具结果统一为字符串化 JSON（list_repos 大分页时尾部可能多段拼接）
#   2. cypher 查询结果被包裹为 {"markdown": "| 列名 |\\n| --- |...", "row_count": N}
#      markdown 表单元格为 JSON 转义字符串，多行单元格内含真实换行
#   3. 错误为 {"error": "Prepare failed: ..."} 或 "Error: LadybugDB unavailable..."
#      （后者是索引重建锁，可重试）

class GraphQueryError(RuntimeError):
    """GitNexus 查询失败。retryable=True 时上层可重试（如 LadybugDB 索引重建锁）。"""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


_RETRYABLE_ERROR_RE = re.compile(
    r"LadybugDB unavailable|rebuilding the index|shadow pages", re.I)


def _split_md_cells(line):
    """按管道符切分 markdown 表行（引号感知）。

    返回 (已完整切出的 cells, 行尾残留文本, 行尾是否仍在引号内)。
    多行单元格（内含真实换行的 JSON 字符串）由调用方拼接后续行。
    """
    cells, cur = [], []
    in_q = False
    esc = False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\" and in_q:
            cur.append(ch)
            esc = True
        elif ch == '"':
            in_q = not in_q
            cur.append(ch)
        elif ch == "|" and not in_q:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    return cells, "".join(cur).strip(), in_q


def _decode_md_cell(cell):
    """markdown 单元格 → python 值：JSON 引号串还原，'-' 视为 null，其余原样。

    多行 content 单元格内含真实换行（JSON 控制字符非法）导致 json.loads
    失败时，降级为手工去首尾引号（保留内文，含换行）。
    """
    if cell == "-" or cell == "":
        return None
    if len(cell) >= 2 and cell[0] == '"' and cell[-1] == '"':
        try:
            return json.loads(cell)
        except json.JSONDecodeError:
            return cell[1:-1]
    return cell


def markdown_rows(md):
    """解析 GitNexus cypher 的 markdown 表 → {cols, rows}。

    多行单元格：行尾仍在引号内时继续拼接下一物理行，直到引号闭合。
    空/无表头输入返回 ({"cols": [], "rows": []})。
    """
    if not md:
        return {"cols": [], "rows": []}
    cols = []
    rows = []
    buf = ""       # 跨物理行累积中的行文本
    buf_q = False  # 缓冲行是否仍在引号内
    pending = False
    for line in md.split("\n"):
        s = line.rstrip("\r")
        if pending:
            buf += "\n" + s
            _, _, buf_q = _split_md_cells(buf)
            if buf_q:
                continue
            row_text, pending = buf, False
        elif s.startswith("|"):
            buf = s
            _, _, buf_q = _split_md_cells(buf)
            if buf_q:
                pending = True
                continue
            row_text, pending = buf, False
        else:
            continue
        # 处理完整行：先按管道切分，再去掉首尾管道产生的结构性空格，最后解码
        cells, trailing, _ = _split_md_cells(row_text)
        if trailing:
            cells = cells + [trailing]  # 行尾无竖线（异常容错）
        if cells and cells[0] == "":
            cells = cells[1:]           # 首管道前的空段
        if row_text.rstrip().endswith("|") and cells and cells[-1] == "":
            cells = cells[:-1]          # 尾管道后的空段
        cells = [_decode_md_cell(c) for c in cells]
        if not cols:
            # 第一行是表头（列名）；分隔行 | --- | 跳过
            if all(isinstance(c, str) and set(c) <= set("-: ") and c for c in cells):
                continue  # 分隔行且尚未建立表头（异常输入）
            cols = cells
            continue
        if all(isinstance(c, str) and set(c) <= set("-: ") and c for c in cells):
            continue  # 分隔行
        rows.append(cells)
    return {"cols": cols, "rows": rows}


def parse_tool_result(raw):
    """MCP text block 内容 → 干净 dict/list。

    字符串化 JSON（可能多段拼接）→ raw_decode 取首段；
    markdown 表包裹 → 展开为 {cols, rows, total}；错误 → GraphQueryError。
    非 JSON 非 markdown 的纯文本 → 原样透传（旧路径宽容语义）。
    """
    if isinstance(raw, dict):
        if "error" in raw:
            msg = str(raw["error"])
            raise GraphQueryError(msg[:300],
                                  retryable=bool(_RETRYABLE_ERROR_RE.search(msg)))
        md = raw.get("markdown")
        if isinstance(md, str) and md.lstrip().startswith("|"):
            parsed = markdown_rows(md)
            out = dict(raw)
            out["cols"] = parsed["cols"]
            out["rows"] = parsed["rows"]
            out["total"] = raw.get("row_count", len(parsed["rows"]))
            return out
        return raw
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if not s:
        return {"cols": [], "rows": [], "total": 0}
    if s.startswith(("Error:", "error:")):
        raise GraphQueryError(s[:300], retryable=bool(_RETRYABLE_ERROR_RE.search(s)))
    try:
        obj, _ = json.JSONDecoder().raw_decode(s)
    except json.JSONDecodeError:
        return s  # 纯文本透传（如 list_repos 尾部非 JSON 片段）
    if isinstance(obj, str):
        return parse_tool_result(obj)  # 双重包装
    if isinstance(obj, dict) and "error" in obj:
        msg = str(obj["error"])
        raise GraphQueryError(msg[:300], retryable=bool(_RETRYABLE_ERROR_RE.search(msg)))
    if isinstance(obj, dict):
        md = obj.get("markdown")
        if isinstance(md, str) and md.lstrip().startswith("|"):
            parsed = markdown_rows(md)
            out = dict(obj)
            out["cols"] = parsed["cols"]
            out["rows"] = parsed["rows"]
            out["total"] = obj.get("row_count", len(parsed["rows"]))
            return out
    return obj


# ── 本地源码分析（GitNexus 图谱不提供的指标，全部由本地仓库推导）───────────────
#
# 实测 GitNexus Method/Function 节点仅含 name/filePath/startLine/endLine/content，
# 无 complexity/cognitive/annotations/returnType 等属性（Binder exception 验证）。

def _signature_from_body(body):
    """从方法体头几行提取签名字符串（到 '{' 为止），供 inventory 展示。

    无 '(' 的首行不是签名（如纯文本/截断内容）→ 空串。
    """
    if not body:
        return ""
    head = body.split("{", 1)[0]
    if "(" not in head:
        return ""
    sig = " ".join(head.split())
    return sig.rstrip(":").strip()[:300]


def _param_count_from_signature(sig):
    """签名括号内参数个数（顶层逗号 + 1；空括号 0）。用于重载消歧。"""
    m = re.search(r"\(([^)]*)\)", sig or "")
    if not m:
        return 0
    inner = m.group(1).strip()
    if not inner:
        return 0
    depth = commas = 0
    for ch in inner:
        if ch in "(<[":
            depth += 1
        elif ch in ")>]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            commas += 1
    return commas + 1


def compute_body_metrics(body, method_name=""):
    """本地估算复杂度指标（替代旧 indexer 提供的 complexity 等）。

    - complexity: 圈复杂度 = 1 + 分支关键字/短路/三目计数（与 extract_branches
      同源的 strip_cpp_comments_and_strings 先清洗）
    - cognitive: 嵌套加权分支数（简化实现：控制关键字 1+depth，逻辑运算符按
      连续段计 1）
    - loop_count / loop_depth / alloc_in_loop / recursive
    返回 dict；body 为空时全 0（recursive False, param_count 0）。
    """
    empty = {"complexity": 0, "cognitive": 0, "loop_count": 0, "loop_depth": 0,
             "alloc_in_loop": 0, "recursive": False, "param_count": 0,
             "signature": ""}
    if not body:
        return empty
    cleaned = strip_cpp_comments_and_strings(body)
    sig = _signature_from_body(body)
    signature_present = bool(sig) and ("(" in sig)
    lines = cleaned.split("\n")

    complexity = 1
    cognitive = 0
    loop_count = 0
    loop_depth = 0
    alloc_in_loop = 0
    depth = 0        # 大括号深度（相对方法体）
    loop_depths = [] # 各循环所在深度
    in_loop = 0      # 当前所处循环层数
    ctrl_re = re.compile(r"\b(if|else\s+if|for|while|case|catch)\b")
    loop_re = re.compile(r"\b(for|while)\s*\(")
    logic_re = re.compile(r"&&|\|")
    tern_re = re.compile(r"\?\s*[^:?]+\s*:")
    alloc_re = re.compile(r"\bnew\s+\w|\bmalloc\s*\(|\bcalloc\s*\(")
    prev_logic = False

    for ln in lines:
        opens = ln.count("{")
        closes = ln.count("}")
        n_ctrl = len(ctrl_re.findall(ln))
        has_logic = bool(logic_re.search(ln))
        has_tern = bool(tern_re.search(ln))
        complexity += n_ctrl + (1 if has_logic else 0) + (1 if has_tern else 0)
        # cognitive：控制关键字按 1+depth，同连续段逻辑运算符只 +1
        cognitive += n_ctrl * (1 + depth)
        if has_logic and not prev_logic:
            cognitive += 1 + depth
        prev_logic = has_logic
        if has_tern:
            cognitive += 1 + depth
        if loop_re.search(ln):
            loop_count += 1
            loop_depths.append(depth)
            in_loop += 1
        if alloc_re.search(ln) and in_loop > 0:
            alloc_in_loop += 1
        depth += opens - closes
        # 循环退出检测：深度回落到该循环起始深度以下
        while loop_depths and depth <= loop_depths[-1] and closes:
            loop_depths.pop()
            in_loop = max(0, in_loop - 1)
        if loop_depths:
            loop_depth = max(loop_depth, len(loop_depths))

    name_matches = len(re.findall(r"\b" + re.escape(method_name) + r"\s*\(", cleaned)) if method_name else 0
    recursive = name_matches >= 2 if signature_present else name_matches >= 1

    return {"complexity": complexity, "cognitive": cognitive,
            "loop_count": loop_count, "loop_depth": loop_depth,
            "alloc_in_loop": alloc_in_loop, "recursive": recursive,
            "param_count": _param_count_from_signature(sig),
            "signature": sig}


# Q_INVOKABLE 方法声明（方法名提取，与旧 search_code 正则同源）
_Q_INVOKABLE_RE = re.compile(
    r"Q_INVOKABLE\s+(?:virtual\s+)?[\w:<>&*\s,]+?\s+(\w+)\s*\(")
_CLASS_DECL_RE = re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?class\s+(\w+)")


def scan_qt_macros_in_file(text, class_name_from_stem=None):
    """单文件本地扫描 Q_INVOKABLE / Q_PLUGIN_METADATA。

    返回 (invokables {class: [method]}, has_plugin_metadata bool)。
    class 上下文：文件内最近一次 `class X` 声明（嵌套类以后者为准）。
    """
    inv = {}
    has_plugin = False
    current = None
    for line in text.splitlines():
        m = _CLASS_DECL_RE.match(line)
        if m:
            current = m.group(1)
        if "Q_PLUGIN_METADATA" in line:
            has_plugin = True
        m = _Q_INVOKABLE_RE.search(line)
        if m and current:
            inv.setdefault(current, []).append(m.group(1))
    return inv, has_plugin


# ── cypher 查询构造助手 ────────────────────────────────────────────────────

def _cypher_str(s):
    """cypher 字符串字面量转义（单引号）。"""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _file_where(alias, patterns):
    """file_patterns（glob 简化）→ cypher WHERE 子句（OR 连接，None 表示不过滤）。

    支持：'src/**' → STARTS WITH 'src/'；'**/plugins/**' → CONTAINS '/plugins/'；
    'src/foo' → STARTS WITH 'src/foo/'。
    """
    clauses = []
    for p in patterns or []:
        p = (p or "").strip().strip("/")
        if not p:
            continue
        if "**/" in p:
            seg = p.split("**/")[-1].strip("/").split("/**")[0]
            if seg:
                clauses.append(f"{alias}.filePath CONTAINS {_cypher_str('/' + seg + '/')}")
        else:
            prefix = p.split("/**")[0]
            if prefix:
                clauses.append(f"{alias}.filePath STARTS WITH {_cypher_str(prefix + '/')}")
    return " OR ".join(clauses) if clauses else None


# GitNexus（LadybugDB）分页语法：SKIP 必须在 LIMIT 之前（实测 LIMIT n SKIP m 报语法错）
PAGE_SIZE = 500
MAX_CYPHER_ROWS = 20000
LIST_REPOS_PAGE = 200    # list_repos 单页条数（实测 cap 200）：768 仓全量 = 4 页 ≈ 2.5min


def _list_repos_workers():
    """并发页数（env QTAG_LIST_REPOS_WORKERS，默认 1=顺序）。

    实测服务端单页 ~40s 固定开销且对并发会话敏感（压测后曾整体不可用），
    默认顺序翻页；确需提速可显式 QTAG_LIST_REPOS_WORKERS=4。
    """
    try:
        return max(1, int(os.environ.get("QTAG_LIST_REPOS_WORKERS", "1")))
    except ValueError:
        return 1
_REPO_MISS = object()    # find_repo「已搜索未命中」哨兵（缓存搜索结论，避免重复遍历）


def paginate_cypher(client, match_clause, return_cols, order_cols,
                    page_size=PAGE_SIZE, max_rows=MAX_CYPHER_ROWS, repo=None):
    """SKIP-before-LIMIT 分页执行 cypher，汇总 {cols, rows, total}。

    排序保证翻页确定性；单页不足 page_size 即止。
    repo：GitNexus 多仓库索引时必传（否则服务端报 Multiple repositories）。
    """
    cols, rows = [], []
    skip = 0
    while True:
        stmt = (f"{match_clause} RETURN {return_cols} "
                f"ORDER BY {order_cols} SKIP {skip} LIMIT {page_size}")
        args = {"statement": stmt}
        if repo:
            args["repo"] = repo
        data = client.call_tool("cypher", args)
        if not cols:
            cols = data.get("cols", []) if isinstance(data, dict) else []
        batch = data.get("rows", []) if isinstance(data, dict) else []
        rows.extend(batch)
        if len(batch) < page_size or len(rows) >= max_rows:
            break
        skip += page_size
    return {"cols": cols, "rows": rows, "total": len(rows)}


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
        """Call an MCP tool with retry on transport errors and index-rebuild locks.

        响应经 parse_tool_result 解码（字符串化 JSON / markdown 表）；
        GraphQueryError.retryable（LadybugDB 重建锁）重试时加倍退避；
        语法/绑定类错误（Prepare failed）立即抛出不重试。
        """
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
                    raise GraphQueryError(
                        f"RPC error: {json.dumps(result['error'], ensure_ascii=False)[:300]}")
                # MCP returns content as text blocks
                content = result.get("result", {}).get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        return parse_tool_result(block["text"])
                if content:
                    return content
                # 空 content：视作空表（cypher 无结果）
                return {"cols": [], "rows": [], "total": 0}
            except GraphQueryError as e:
                if e.retryable and attempt < retries - 1:
                    wait = 4 * (attempt + 1) * 2  # 索引重建锁耗时较长，加倍退避
                    print(f"   ⚠️  {name} index busy (retry {attempt + 1}/{retries}, wait {wait}s): {e}")
                    time.sleep(wait)
                    try:
                        self.initialize()
                    except Exception:
                        pass
                    continue
                raise
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


# ── GitNexus 数据适配层（GitNexusAdapter）────────────────────────────────────────────


def _derive_return_keys(return_cols):
    """RETURN 子句 → 列键名列表（取 AS 别名；无别名时用表达式的最后一段）。"""
    keys = []
    depth = 0
    cur = []
    for ch in return_cols:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            keys.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        keys.append("".join(cur))
    out = []
    for k in keys:
        k = k.strip()
        if " AS " in k.upper():
            # 取 AS 后的别名（大小写不敏感分割）
            idx = k.upper().rindex(" AS ")
            out.append(k[idx + 4:].strip().strip("`"))
        else:
            out.append(k.split(".")[-1].strip().strip("`"))
    return out


class GitNexusAdapter:
    """GitNexus 图谱 + 本地仓库双源适配层。

    职责划分（详见 doc/gitnexus-适配分析.md）：
      - 图谱（cypher/context/list_repos）：符号定位、CALLS/EXTENDS/HAS_METHOD
        关系、File 枚举。属性 camelCase；无 parent_class/base_classes/annotations
        属性（实测 Binder exception）；content 截断 5016 字符（实测）。
      - 本地仓库（repo_root）：方法体行切片、复杂度指标估算、Q_INVOKABLE/
        Q_PLUGIN_METADATA、签名、TEST_F 用例。
    产出的数据结构与旧 codebase-memory-mcp 路径完全对齐（下游零改动）。
    """

    # 测试文件路径前缀（is_test 判定 + 测试模块发现）
    TEST_PATH_PREFIXES = ("tests/", "test/", "autotests/")
    SOURCE_EXTS = (".h", ".hpp", ".hh", ".cpp", ".cc", ".cxx")
    TEST_F_RE = re.compile(r"\bTEST_F\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)")

    def __init__(self, client, project, repo_root=None):
        self.client = client
        self.project = project
        self.repo_root = repo_root or ""
        self._parent_cache = None
        self._indegree_cache = None
        self._repo_cache = None  # find_repo 结果缓存（check_drift/resolve_base_sha 复用）

    # ── 基础查询 ──

    def cypher_rows(self, match_clause, return_cols, order_cols,
                    max_rows=MAX_CYPHER_ROWS, page_size=PAGE_SIZE):
        """分页 cypher 查询 → [{别名: 值}, ...]（键来自 RETURN 子句显式别名）。"""
        keys = _derive_return_keys(return_cols)
        paginated = paginate_cypher(self.client, match_clause, return_cols,
                                    order_cols, page_size=page_size, max_rows=max_rows,
                                    repo=self.project)
        rows = []
        for raw in paginated["rows"]:
            # markdown 单元格按列位置对齐；行短于列时补 None
            vals = list(raw) + [None] * (len(keys) - len(raw))
            rows.append(dict(zip(keys, vals)))
        return rows

    def find_repo(self):
        """list_repos 顺序翻页（早退）→ 本项目元信息；未索引返回 None。

        结果缓存：fetch 流程内 check_drift / resolve_base_sha 多次调用
        只发起一轮 list_repos（真机单页 ~40s，缓存后减半）。
        """
        if self._repo_cache is None:
            offset = 0
            while True:
                data = self.client.call_tool(
                    "list_repos", {"limit": LIST_REPOS_PAGE, "offset": offset})
                repos = (data.get("repositories", [])
                         if isinstance(data, dict) else [])
                hit = next((r for r in repos
                            if r.get("name") == self.project), None)
                if hit is not None:
                    self._repo_cache = hit
                    break
                if len(repos) < LIST_REPOS_PAGE:
                    self._repo_cache = _REPO_MISS
                    break
                offset += LIST_REPOS_PAGE
        return None if self._repo_cache is _REPO_MISS else self._repo_cache

    def repo_head_sha(self):
        """图谱侧提交哈希（list_repos.lastCommit，替代旧 index_status.git.head_sha）。"""
        info = self.find_repo()
        return (info or {}).get("lastCommit", "")

    def check_drift(self):
        """本地 HEAD vs 图谱 lastCommit 漂移检查。

        返回 (local_sha, graph_sha)；无法取本地 HEAD 时 local_sha 为空串。
        """
        graph_sha = self.repo_head_sha()
        local_sha = ""
        if self.repo_root and os.path.isdir(os.path.join(self.repo_root, ".git")):
            try:
                proc = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=self.repo_root,
                    capture_output=True, text=True, timeout=10)
                if proc.returncode == 0:
                    local_sha = proc.stdout.strip()
            except Exception:
                local_sha = ""
        return local_sha, graph_sha
    # ── 本地仓库访问 ──

    def read_local_lines(self, file_path):
        """读本地仓库文件 → 行列表（UTF-8 容错）；不可得返回 None。"""
        if not self.repo_root or not file_path:
            return None
        fp = os.path.join(self.repo_root, file_path)
        if not os.path.isfile(fp):
            return None
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                return f.readlines()
        except OSError:
            return None

    def slice_body(self, file_path, start_line, end_line):
        """按图谱行范围 [startLine, endLine]（1-based 闭区间）本地切片。

        实测 GitNexus 行范围精确；切片免疫图谱 content 的 5016 字符截断。
        """
        lines = self.read_local_lines(file_path)
        if lines is None or not start_line:
            return ""
        s = max(1, int(start_line))
        e = int(end_line) if end_line else s
        e = min(e, s + 5000, len(lines))
        if e < s:
            return ""
        return "".join(lines[s - 1:e])

    @classmethod
    def is_test_path(cls, file_path):
        """路径含 /tests/ /test/ /autotests/ 段 → 测试文件。"""
        norm = "/" + (file_path or "").replace("\\", "/").strip("/")
        return any(seg in norm for seg in ("/tests/", "/test/", "/autotests/"))

    # ── 图谱关系缓存 ──

    def parent_class_map(self):
        """(filePath, name, startLine) → 所属类名（HAS_METHOD 全量扫描一遍）。

        GitNexus 无 parent_class 属性（实测 Binder exception）；File 源的
        HAS_METHOD（不属于类）不当作父类。
        """
        if self._parent_cache is None:
            self._parent_cache = {}
            rows = self.cypher_rows(
                "MATCH (c)-[r:CodeRelation]->(m:Method) WHERE r.type = 'HAS_METHOD'",
                "c.name AS parent, labels(c) AS clabels, m.filePath AS filePath, "
                "m.name AS name, m.startLine AS sline",
                "filePath, sline, name")
            for r in rows:
                clabels = r.get("clabels") or []
                if isinstance(clabels, str):
                    try:
                        clabels = json.loads(clabels)
                    except json.JSONDecodeError:
                        clabels = [clabels]
                if "File" in clabels:
                    continue
                self._parent_cache[(r["filePath"], r["name"], int(r["sline"] or 0))] = r["parent"]
        return self._parent_cache

    def in_degree_map(self):
        """(filePath, name, startLine) → 被 CALLS 次数（全仓聚合，替代旧 `in` 字段）。"""
        if self._indegree_cache is None:
            self._indegree_cache = defaultdict(int)
            rows = self.cypher_rows(
                "MATCH (src)-[r:CodeRelation]->(t) "
                "WHERE r.type = 'CALLS'",
                "t.filePath AS filePath, t.name AS name, t.startLine AS sline",
                "filePath, sline, name")
            for r in rows:
                self._indegree_cache[(r["filePath"], r["name"], int(r["sline"] or 0))] += 1
        return self._indegree_cache

    # ── Step 1: Method/Function 采集 ──

    def collect_methods(self, file_patterns=None, limit=2000):
        """Step 1: 枚举 Method + Function 节点，补齐下游评分所需字段。

        图谱仅提供 name/filePath/startLine/endLine（实测）；其余字段来源：
          parent_class ← HAS_METHOD；in_degree ← CALLS 聚合；
          complexity/cognitive/loop_*/recursive/param_count/signature ← 本地源码估算。
        产出字段与旧 search_graph 路径对齐（qualified_name/file_path/lines/...）。
        Function 节点含自由函数（main/helpers），噪声由 scan 评分层过滤。
        """
        if isinstance(file_patterns, str):
            file_patterns = [file_patterns]
        patterns = file_patterns or [None]

        print(f"\n📊 [1/5] Collecting Method + Function nodes...")
        if any(patterns):
            print(f"   file_patterns: {[p for p in patterns if p]}")

        all_methods = []
        all_functions = []
        seen = set()  # (label, filePath, name, startLine) 跨 pattern 去重
        parent_map = self.parent_class_map()
        indeg = self.in_degree_map()

        for pattern in patterns:
            where_m = _file_where("m", [pattern] if pattern else None)
            where_f = _file_where("f", [pattern] if pattern else None)
            if pattern:
                print(f"   ── pattern: {pattern}")

            m_rows = self.cypher_rows(
                "MATCH (m:Method)" + (f" WHERE {where_m}" if where_m else ""),
                "m.name AS name, m.filePath AS filePath, "
                "m.startLine AS startLine, m.endLine AS endLine",
                "filePath, startLine, name", max_rows=limit)
            f_rows = self.cypher_rows(
                "MATCH (f:Function)" + (f" WHERE {where_f}" if where_f else ""),
                "f.name AS name, f.filePath AS filePath, "
                "f.startLine AS startLine, f.endLine AS endLine",
                "filePath, startLine, name", max_rows=limit)
            print(f"   Method: {len(m_rows)}, Function: {len(f_rows)}")

            for r in m_rows:
                key = ("Method", r["filePath"], r["name"], int(r["startLine"] or 0))
                if key in seen:
                    continue
                seen.add(key)
                all_methods.append(self._method_row(r, "Method", parent_map, indeg))
            for r in f_rows:
                key = ("Function", r["filePath"], r["name"], int(r["startLine"] or 0))
                if key in seen:
                    continue
                seen.add(key)
                all_functions.append(self._method_row(r, "Function", parent_map, indeg))

        # qn 在全量收集后统一分配（Method/Function 各自一个命名空间）
        self._assign_qualified_names(all_methods)
        self._assign_qualified_names(all_functions)
        print(f"   ✅ {len(all_methods)} methods + {len(all_functions)} functions collected")
        return all_methods, all_functions

    def _method_row(self, r, label, parent_map, indeg):
        """图谱行 → mcp_dump 方法行（字段与旧 search_graph 路径对齐）。"""
        fp = r["filePath"] or ""
        name = r["name"] or ""
        start = int(r["startLine"] or 0)
        end = int(r["endLine"] or 0)
        parent = parent_map.get((fp, name, start), "")
        body = self.slice_body(fp, start, end)
        metrics = compute_body_metrics(body, name)
        return {
            "qualified_name": "",  # _assign_qualified_names 统一分配
            "name": name,
            "label": label,
            "file_path": fp,
            "startLine": start,
            "lines": max(0, end - start + 1) if end else 0,
            "in_degree": indeg.get((fp, name, start), 0),
            "out": 0,
            "complexity": metrics["complexity"],
            "cognitive": metrics["cognitive"],
            "loop_count": metrics["loop_count"],
            "loop_depth": metrics["loop_depth"],
            "alloc_in_loop": metrics["alloc_in_loop"],
            "recursive": metrics["recursive"],
            "transitive_loop_depth": 0,   # GitNexus 无跨层循环传播数据（保守 0）
            "linear_scan_in_loop": 0,
            "param_count": metrics["param_count"],
            "signature": metrics["signature"],
            "parent_class": parent,
            "is_test": self.is_test_path(fp),
            "docstring": "",
        }

    @staticmethod
    def _assign_qualified_names(rows):
        """分配 qualified_name：Method='Class.name'，Function=裸名；撞名追加 '@文件名'。

        旧图谱 qn 带命名空间前缀；GitNexus 无命名空间属性，采用类内归属 +
        文件名消歧，同文件重载再追加行号。_tm_normalize_qn（剥 '-' 前导段）
        对新格式天然兼容。
        """
        base_counts = defaultdict(int)
        bases = []
        for r in rows:
            base = f"{r['parent_class']}.{r['name']}" if r["parent_class"] else r["name"]
            bases.append(base)
            base_counts[base] += 1
        used = set()
        for r, base in zip(rows, bases):
            qn = base
            if base_counts[base] > 1:
                stem = os.path.splitext(os.path.basename(r["file_path"] or ""))[0]
                cand = f"{base}@{stem}" if stem else base
                if cand in used:
                    cand = f"{cand}@{int(r['startLine'] or 0)}"
                qn = cand
            if qn in used:
                qn = f"{qn}@{int(r['startLine'] or 0)}"
            used.add(qn)
            r["qualified_name"] = qn


    # ── Step 2-4: 继承 / DBus slots / Qt 宏 ──

    def list_source_files(self, file_patterns=None, max_files=8000):
        """图谱 File 节点 → 本地存在的 C/C++ 源文件路径列表（宏扫描范围）。"""
        where = _file_where("f", file_patterns)
        rows = self.cypher_rows(
            "MATCH (f:File)" + (f" WHERE {where}" if where else ""),
            "f.filePath AS filePath", "filePath", max_rows=max_files)
        out = []
        for r in rows:
            fp = r["filePath"] or ""
            if not fp.lower().endswith(self.SOURCE_EXTS):
                continue
            if not self.repo_root or os.path.isfile(os.path.join(self.repo_root, fp)):
                out.append(fp)
        return out

    def collect_inheritance(self):
        """Step 2: EXTENDS/IMPLEMENTS 边 → DBus / 并发 / GUI 基类清单。

        GitNexus 无 base_classes 属性（实测 CONTAINS 直译报 Binder exception），
        改查 CodeRelation 关系边 + 基类名白名单过滤。
        Returns (dbus_adaptor, dbus_interface, concurrent, gui)，
        每项 [{name, qualified_name, file_path, base_classes}]。
        """
        print(f"\n📊 [2/5] Detecting inheritance chains...")
        buckets = [
            ("DBus Adaptor (server)", DBUS_ADAPTOR_BASES),
            ("DBus Interface (client)", DBUS_INTERFACE_BASES),
            ("Concurrent", CONCURRENT_BASES),
            ("GUI", GUI_BASES),
        ]
        all_bases = sorted({b for _, bases in buckets for b in bases})
        base_clause = ",".join(_cypher_str(b) for b in all_bases)
        rows = self.cypher_rows(
            "MATCH (c)-[r:CodeRelation]->(b) "
            f"WHERE r.type IN ['EXTENDS', 'IMPLEMENTS'] AND b.name IN [{base_clause}]",
            "c.name AS name, c.filePath AS filePath, b.name AS base",
            "name, filePath")
        per_class = {}
        for r in rows:
            if self.is_test_path(r["filePath"]):
                continue
            per_class.setdefault((r["name"], r["filePath"] or ""), set()).add(r["base"])
        results = []
        for label, bases_list in buckets:
            bset = set(bases_list)
            out = []
            for (cname, fp), bases in per_class.items():
                hit = bases & bset
                if hit:
                    out.append({
                        "name": cname,
                        "qualified_name": cname,
                        "file_path": fp,
                        "base_classes": sorted(hit),
                    })
            out.sort(key=lambda c: c["name"])
            results.append(out)
            print(f"   {label}: {len(out)}")
        return results[0], results[1], results[2], results[3]

    def collect_dbus_slots(self, dbus_adaptor_classes):
        """Step 3: HAS_METHOD → 每个 DBus Adaptor 类的方法 → dbus_slots。

        仅 QDBusAbstractAdaptor（服务端）slots 是契约级测试目标；
        过滤构造/析构/emit* 信号（与旧一致）。
        """
        print(f"\n📊 [3/5] Collecting DBus Adaptor slots...")
        dbus_slots = {}
        for cls in dbus_adaptor_classes:
            cls_name = cls["name"]
            rows = self.cypher_rows(
                f"MATCH (c)-[r:CodeRelation]->(m:Method) WHERE r.type = 'HAS_METHOD' "
                f"AND c.name = {_cypher_str(cls_name)}",
                "m.name AS name", "name")
            slots = []
            for r in rows:
                method_name = r["name"] or ""
                if method_name == cls_name:      # 构造函数
                    continue
                if method_name.startswith("~"):  # 析构函数
                    continue
                if method_name.startswith("emit"):  # 可能是 Q_SIGNALS
                    continue
                slots.append(method_name)
            if slots:
                dbus_slots[cls["qualified_name"]] = sorted(set(slots))
                print(f"   {cls_name}: {len(slots)} slots")
        print(f"   ✅ {sum(len(v) for v in dbus_slots.values())} DBus slots total")
        return dbus_slots

    def collect_qt_macros(self, file_patterns=None):
        """Step 4: Q_INVOKABLE / Q_PLUGIN_METADATA 本地扫描。

        GitNexus 图谱无 annotations 属性、Macro 标签不可查（实测）→
        图谱 File 枚举 + 本地逐文件正则（同旧 search_code 正则）。
        Returns (q_invokables, q_plugins)。
        """
        print(f"\n📊 [4/5] Detecting Q_INVOKABLE / Q_PLUGIN_METADATA...")
        q_invokables = {}
        q_plugins = {}
        if not self.repo_root:
            print("   ⚠️  未提供 --repo-root，跳过本地宏扫描")
            return q_invokables, q_plugins
        files = self.list_source_files(file_patterns)
        scanned = 0
        for fp in files:
            lines = self.read_local_lines(fp)
            if lines is None:
                continue
            inv, has_plugin = scan_qt_macros_in_file("".join(lines))
            scanned += 1
            for cls_name, method_names in inv.items():
                bucket = q_invokables.setdefault(cls_name, [])
                for mname in method_names:
                    if mname not in bucket:
                        bucket.append(mname)
            if has_plugin:
                # 与旧 search_code files 模式同构：按文件名主干归plugin类
                class_name = os.path.splitext(os.path.basename(fp))[0]
                q_plugins[class_name] = True
        print(f"   scanned {scanned}/{len(files)} source files")
        print(f"   Q_INVOKABLE: {sum(len(v) for v in q_invokables.values())} methods "
              f"in {len(q_invokables)} classes")
        print(f"   Q_PLUGIN_METADATA: {len(q_plugins)} plugin classes")
        return q_invokables, q_plugins

    # ── 方法体 / 测试模块 / CALLS 采集 ──

    def _context_content(self, name, file_path):
        """context 工具降级取 content（5016 字符截断，实测）。"""
        try:
            data = self.client.call_tool("context", {
                "name": name, "file_path": file_path,
                "kind": "Method", "include_content": True,
                "repo": self.project,
            })
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        if data.get("status") == "found":
            return (data.get("symbol") or {}).get("content", "") or ""
        if data.get("status") == "ambiguous":
            for cand in data.get("candidates") or []:
                if (cand.get("filePath") or cand.get("file_path")) == file_path:
                    return cand.get("content", "") or ""
        return ""

    def fetch_method_bodies(self, classname, methods):
        """图谱定位 + 本地行切片获取方法体（extract-branches 用）。

        重载消歧：HAS_METHOD 候选按（类名匹配， 参数个数差）排序取最优；
        参数个数来自本地签名字段解析（inventory 方法 signature）。
        本地文件缺失时降级 context content（5016 截断，标 truncated=True）；
        规则名 SNIPPET_FETCH_FAILED 及输出结构保持不变（下游兼容）。
        Returns {qualified_name: {body, name, complexity, error?, truncated?}}。
        """
        if not methods:
            return {}
        names = sorted({m.get("name") for m in methods if m.get("name")})
        rows = self.cypher_rows(
            "MATCH (c)-[r:CodeRelation]->(m:Method) WHERE r.type = 'HAS_METHOD' "
            f"AND m.name IN [{','.join(_cypher_str(n) for n in names)}]",
            "c.name AS parent, m.name AS name, m.filePath AS filePath, "
            "m.startLine AS startLine, m.endLine AS endLine",
            "filePath, startLine, name")
        by_name = defaultdict(list)
        for r in rows:
            by_name[r["name"]].append(r)

        result = {}
        for m in methods:
            qn = m.get("qualified_name") or m.get("name") or ""
            entry = {
                "body": "",
                "name": m.get("name", ""),
                "complexity": m.get("complexity", 0) or 0,
            }
            cands = by_name.get(m.get("name"), [])
            if cands:
                want_pc = _param_count_from_signature(m.get("signature", "") or "")

                def cand_key(r):
                    body = self.slice_body(r["filePath"], r["startLine"], r["endLine"])
                    pc = _param_count_from_signature(_signature_from_body(body))
                    return (0 if r["parent"] == classname else 1,
                            abs(pc - want_pc) if want_pc else 0)

                pick = min(cands, key=cand_key)
                body = self.slice_body(pick["filePath"], pick["startLine"], pick["endLine"])
                if body:
                    entry["body"] = body
                else:
                    content = self._context_content(pick["name"], pick["filePath"])
                    if content:
                        entry["body"] = content
                        entry["truncated"] = True
                    else:
                        entry["error"] = (
                            f"方法体不可得：本地文件缺失且图谱 content 为空 "
                            f"({pick['filePath']}:{pick['startLine']}-{pick['endLine']})")
            else:
                entry["error"] = f"图谱未定位到方法 {m.get('name')}（类 {classname}）"
            result[qn] = entry
        return result

    def discover_test_modules(self):
        """测试目录 File 节点 → ut_*/test_* 测试模块 [{name, file_path, out_degree}]。

        兼容两种命名惯例：新代码 ut_*.cpp（deepin 现行），旧快照 test_*.cpp
        （如 dde-file-manager 图谱基线）。非 gTest 文件无 TEST_* 宏，在
        fetch_test_cases 里自然产出空用例，不引入噪声。
        """
        clauses = " OR ".join(
            f"f.filePath STARTS WITH {_cypher_str(p)}" for p in self.TEST_PATH_PREFIXES)
        rows = self.cypher_rows(
            f"MATCH (f:File) WHERE {clauses}",
            "f.filePath AS filePath", "filePath")
        modules = []
        for r in rows:
            fp = r["filePath"] or ""
            if UT_FILE_PATTERN.search(fp):
                modules.append({"name": os.path.basename(fp), "file_path": fp, "out_degree": 0})
        return modules

    def collect_all_calls(self, test_modules):
        """测试模块 CALLS 目标采集（图谱关系边）→ {源 qn: {test_file}}。

        与旧逻辑对齐：仅统计测试模块文件发出的 CALLS；目标在测试目录 →
        视为 stub 剔除。源 qn 用 parent_class_map 归一（Class.name 或裸名），
        与 inventory qualified_name 同构。
        """
        module_files = {m["file_path"] for m in test_modules}
        if not module_files:
            return {}
        src_clause = ",".join(_cypher_str(f) for f in sorted(module_files))
        rows = self.cypher_rows(
            f"MATCH (src)-[r:CodeRelation]->(t) WHERE r.type = 'CALLS' "
            f"AND src.filePath IN [{src_clause}]",
            "src.filePath AS srcFile, t.name AS name, t.filePath AS filePath, "
            "t.startLine AS startLine",
            "srcFile, name, filePath")
        parent_map = self.parent_class_map()
        source_to_tests = defaultdict(set)
        test_hit = set()
        for r in rows:
            test_hit.add(r["srcFile"])
            tpath = r["filePath"] or ""
            if self.is_test_path(tpath):
                continue  # 测试目录内定义的被“调用”目标 → stub
            name = r["name"]
            if not name:
                continue
            parent = parent_map.get((tpath, name, int(r["startLine"] or 0)), "")
            qn = f"{parent}.{name}" if parent else name
            source_to_tests[qn].add(r["srcFile"])
        # 回填测试模块 out_degree（有 CALLS 出边记 1，与旧逻辑一致）
        for m in test_modules:
            m["out_degree"] = 1 if m["file_path"] in test_hit else 0
        return dict(source_to_tests)

    def fetch_test_cases(self, test_modules):
        """本地解析 TEST_F(Suite, Case) → {file: ["Suite.Case", ...]}。

        GitNexus 无 docstring 属性 → 用例注释暂缺（旧值来自图谱 docstring）。
        """
        file_to_cases = defaultdict(list)
        for m in test_modules:
            lines = self.read_local_lines(m["file_path"])
            if lines is None:
                continue
            text = "".join(lines)
            for mm in self.TEST_F_RE.finditer(text):
                file_to_cases[m["file_path"]].append(f"{mm.group(1)}.{mm.group(2)}")
        return dict(file_to_cases)


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


# ── 连接与解析辅助 ──


def _list_repos_parallel(client, total, page_size, first_repos):
    """并发拉取 list_repos 剩余页（每页独立 MCPClient 会话）。

    前提：首页响应携带 pagination.total（真机实测）。页间无依赖，
    ex.map 保持 offset 顺序拼接。
    """
    offsets = range(page_size, total, page_size)
    if not offsets:
        return first_repos
    url = getattr(client, "url", None) or MCP_URL
    timeout = getattr(client, "timeout", None) or 120
    headers = getattr(client, "extra_headers", None)

    def _page(off):
        c = MCPClient(url=url, timeout=timeout, extra_headers=headers)
        c.initialize()
        d = c.call_tool("list_repos", {"limit": page_size, "offset": off})
        return d.get("repositories", []) if isinstance(d, dict) else []

    rest = []
    workers = min(_list_repos_workers(), len(offsets))
    if workers <= 1:
        for off in offsets:
            rest.extend(_page(off))
        return first_repos + rest
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for page in ex.map(_page, offsets):
            rest.extend(page)
    return first_repos + rest


def list_repos_all(client, page_size=LIST_REPOS_PAGE):
    """list_repos 全量仓库列表：有 pagination.total 时并发拉取，否则顺序翻页。

    返回 [{name, lastCommit, branch, stats, ...}, ...]。顺序兜底路径兼容
    无 pagination 元数据的桩/旧响应；异常传播给调用方。
    """
    first = client.call_tool("list_repos", {"limit": page_size, "offset": 0})
    d = first if isinstance(first, dict) else {}
    repos = list(d.get("repositories", []))
    pag = d.get("pagination") or {}
    total = int(pag.get("total") or 0)
    if total > len(repos):
        return _list_repos_parallel(client, total, page_size, repos)
    if not pag:
        # 无元数据：顺序翻页兜底（单页满页才继续）
        offset = page_size
        while True:
            d2 = client.call_tool("list_repos", {"limit": page_size, "offset": offset})
            page = d2.get("repositories", []) if isinstance(d2, dict) else []
            repos.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
    return repos


def open_adapter(project, mcp_url=None, repo_root=None):
    """连接 GitNexus 并校验仓库已索引 → GitNexusAdapter。

    仓库未索引 → SystemExit(2)（一等失败，不再静默拉空数据）。
    """
    client = MCPClient(url=mcp_url or MCP_URL)
    print(f"🔗 Connecting to {client.url} ...")
    client.initialize()
    print(f"✅ MCP session: {(client.session_id or 'stateless')[:12]}...")
    adapter = GitNexusAdapter(client, project=project, repo_root=repo_root)
    info = adapter.find_repo()
    if info is None:
        print(f"❌ GitNexus 未索引仓库 {project!r}（list_repos 无匹配）", file=sys.stderr)
        raise SystemExit(2)
    print(f"📋 Repo: {info.get('name')}  branch: {info.get('branch', '?')}  "
          f"indexedAt: {info.get('indexedAt', '?')}")
    return adapter


def resolve_base_sha(client_or_adapter, project, explicit=None):
    """解析 base_sha：显式传入优先，否则取 GitNexus list_repos.lastCommit。

    base_sha 语义为「本次 inventory 数据所基于的图谱版本」（继承旧语义，
    对应旧 index_status.git.head_sha），而非本地工作区 HEAD（后者可能比图谱
    更新）。用图谱版本才能让 reconcile 正确检测「图谱落后于代码」的情形。
    """
    if explicit:
        return explicit
    try:
        if isinstance(client_or_adapter, GitNexusAdapter):
            info = client_or_adapter.find_repo()
        else:
            info = GitNexusAdapter(client_or_adapter, project=project).find_repo()
        sha = (info or {}).get("lastCommit", "")
        if sha:
            print(f"   base_sha (from graph): {sha}")
            return sha
        print("   ⚠️  list_repos 未返回 lastCommit，base_sha 回退 unknown")
    except Exception as e:
        print(f"   ⚠️  获取图谱提交哈希失败，base_sha 回退 unknown: {e}")
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
                    "GitNexus 图谱定位 + 本地切片拉真实源码分支 → 与测试文件声明分支做差集")
    parser.add_argument("--project", required=True, help="GitNexus 仓库名")
    parser.add_argument("--test-file", required=True, help="测试文件路径")
    parser.add_argument("--inventory", required=True,
                        help=".ut-inventory.json 路径")
    parser.add_argument("--class", dest="classname", default=None,
                        help="限定类名；未指定时从 test_<class>.cpp / ut_<class>.cpp 文件名推断")
    parser.add_argument("--mcp-url", default=MCP_URL, help="GitNexus MCP HTTP 端点")
    parser.add_argument("--repo-root", default=None,
                        help="GitNexus 仓库内的本地检出路径（方法体切片来源；"
                             "默认从测试文件 git 顶层推导）")
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

    # repo_root：显式传入 > 从 --output 推导（inventory 通常在 <repo>/autotests/ 下）
    repo_root = getattr(args, "repo_root", None)
    if not repo_root:
        output_dir = Path(args.output).resolve().parent
        if output_dir.name in ("autotests", "tests", "test"):
            repo_root = str(output_dir.parent)
        elif os.path.isdir(os.path.join(str(output_dir), ".git")):
            repo_root = str(output_dir)
    if repo_root:
        print(f"📁 repo_root: {repo_root}")
    else:
        print("⚠️  未指定 --repo-root 且无法从 --output 推导："
              "方法体/指标/宏扫描将不可用，仅依赖图谱数据")

    # 连接 GitNexus
    adapter = open_adapter(args.project, args.mcp_url, repo_root)

    # 本地 HEAD vs 图谱 lastCommit 漂移告警（继承旧 base_sha 语义的 Iron Law：
    # 数据应与图谱版本一致；本地较新时方法体切片可能与图谱行号错位）
    local_sha, graph_sha = adapter.check_drift()
    if local_sha and graph_sha and local_sha != graph_sha:
        print(f"⚠️  本地 HEAD ({local_sha[:8]}) ≠ 图谱 lastCommit ({graph_sha[:8]})："
              "本地切片基于较新代码，建议同步图谱索引或检出图谱对应版本")

    # 解析 base_sha：显式传入优先，否则从图谱 list_repos.lastCommit 取
    base_sha = resolve_base_sha(adapter, args.project, args.base_sha)

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
    methods, functions = adapter.collect_methods(file_patterns, args.limit)
    dbus_adaptor, dbus_interface, concurrent, gui = adapter.collect_inheritance()
    dbus_slots = adapter.collect_dbus_slots(dbus_adaptor)
    q_invokables, q_plugins = adapter.collect_qt_macros(file_patterns)
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
            test_modules = adapter.discover_test_modules()
            if test_modules:
                source_to_tests = adapter.collect_all_calls(test_modules)
                file_to_cases = adapter.fetch_test_cases(test_modules)
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
    """extract-branches 子命令：真实源码分支 × 测试文件声明分支 差集校验。

    原 fetch-mcp-data.py run_extract_branches 的逻辑体，
    参数由 build_parser 的 extract-branches 子解析器预解析后传入。
    方法体来源：GitNexus 图谱定位 + 本地仓库行切片（adapter）。
    """
    # 推断 classname（PascalCase 惯例：test_calculator.cpp / ut_calculator.cpp → Calculator）
    classname = args.classname
    if not classname:
        basename = os.path.basename(args.test_file)
        m = re.match(r'(?:test|ut)_(\w+)\.cpp$', basename)
        if not m:
            print(f"❌ 无法从文件名推断类名: {basename}，请用 --class 指定",
                  file=sys.stderr)
            sys.exit(2)
        classname = m.group(1)
        classname = classname[0].upper() + classname[1:]

    # repo_root：显式传入 > 测试文件所在 git 仓库顶层
    repo_root = getattr(args, "repo_root", None)
    if not repo_root:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=os.path.dirname(os.path.abspath(args.test_file)) or ".",
                capture_output=True, text=True, timeout=10)
            if proc.returncode == 0 and proc.stdout.strip():
                repo_root = proc.stdout.strip()
        except Exception:
            repo_root = None
        if repo_root:
            print(f"📁 repo_root (auto): {repo_root}")
        else:
            print("⚠️  未找到本地仓库（--repo-root 未传且测试文件不在 git 仓内）；"
                  "方法体将降级图谱 content（5016 字符截断）")

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

    # 连 GitNexus，图谱定位 + 本地切片拉方法体
    adapter = open_adapter(args.project, args.mcp_url, repo_root)
    print(f"📋 Project: {args.project}  Class: {classname}  Methods: {len(methods)}")
    bodies = adapter.fetch_method_bodies(classname, methods)

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
                "message": f"方法体不可得（本地切片与图谱 content 均为空）"
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

UT_FILE_PATTERN = re.compile(r'(?:^|/)(?:ut|test)_\w+\.(?:cpp|h)$')


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
        # repo_root：显式传入 > 从 inventory 路径推导（<repo>/autotests/ → <repo>）
        repo_root = getattr(args, "repo_root", None)
        if not repo_root:
            inv_dir = Path(args.inventory).resolve().parent
            if inv_dir.name in ("autotests", "tests", "test"):
                repo_root = str(inv_dir.parent)
            elif os.path.isdir(os.path.join(str(inv_dir), ".git")):
                repo_root = str(inv_dir)
        adapter = open_adapter(project_name, args.mcp_url, repo_root)
        print(f"📋 Project: {project_name}")

        print(f"\n📊 [1/4] 发现测试模块...")
        test_modules = adapter.discover_test_modules()
        with_calls = sum(1 for m in test_modules if m["out_degree"] > 0)
        print(f"   ut_* 单元测试文件: {len(test_modules)}")
        print(f"   有 CALLS 关系: {with_calls}")

        print(f"\n📊 [2/4] 收集 CALLS 关系...")
        source_to_tests = adapter.collect_all_calls(test_modules)
        print(f"   被测源码节点: {len(source_to_tests)}")
        print(f"   总 CALLS 边: {sum(len(v) for v in source_to_tests.values())}")

        print(f"\n📊 [3/4] 采集 TEST_F 用例名...")
        file_to_cases = adapter.fetch_test_cases(test_modules)
        print(f"   含用例名的文件: {len(file_to_cases)}")
        print(f"   用例名总数: {sum(len(v) for v in file_to_cases.values())}")

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
        description="GitNexus 代码图谱 MCP → .ut-inventory.json 端到端探测")

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
    sp_fetch.add_argument("--project", required=True, help="GitNexus 仓库名")
    sp_fetch.add_argument("--file-pattern", action="append", default=None,
                          help="Glob 过滤源码目录，可多次指定或用逗号分隔 "
                               "(e.g. --file-pattern 'src/**' --file-pattern 'plugins/**', "
                               "或 --file-pattern 'src/**,plugins/**')")
    sp_fetch.add_argument("--output", "-o", required=True,
                          help="输出 .ut-inventory.json 路径")
    sp_fetch.add_argument("--mcp-url", default=MCP_URL, help="GitNexus MCP HTTP 端点")
    sp_fetch.add_argument("--repo-root", default=None,
                          help="GitNexus 仓库内的本地检出路径（方法体切片/复杂度估算/"
                               "宏扫描来源；默认从 --output 路径推导）")
    sp_fetch.add_argument("--limit", type=int, default=2000,
                          help="Method/Function 采集上限 (default: 2000)")
    sp_fetch.add_argument("--base-sha", default=None,
                          help="Git base SHA；未指定时自动从 GitNexus list_repos 的 "
                               "lastCommit 获取（推荐，与图谱版本一致）")
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
    sp_eb.add_argument("--project", required=True, help="GitNexus 仓库名")
    sp_eb.add_argument("--test-file", required=True, help="测试文件路径")
    sp_eb.add_argument("--inventory", required=True,
                       help=".ut-inventory.json 路径")
    sp_eb.add_argument("--class", dest="classname", default=None,
                       help="限定类名；未指定时从 test_<class>.cpp / ut_<class>.cpp 文件名推断")
    sp_eb.add_argument("--mcp-url", default=MCP_URL, help="GitNexus MCP HTTP 端点")
    sp_eb.add_argument("--repo-root", default=None,
                       help="GitNexus 仓库内的本地检出路径（方法体切片来源；"
                            "默认从测试文件 git 顶层推导）")
    sp_eb.add_argument("--json", action="store_true", help="额外打印完整 JSON")
    sp_eb.add_argument("-o", "--output", default=None, help="写 JSON 到文件")

    # ── test-mapping 子命令（原 fetch-test-mapping.py） ──
    sp_tm = subparsers.add_parser(
        "test-mapping",
        help="仅回写 test_* 字段（原 fetch-test-mapping.py）")
    sp_tm.add_argument("--project", required=False,
                       help="GitNexus 仓库名（与 fetch --project 一致）；"
                            "使用 --mapping-in 时可省略")
    sp_tm.add_argument("--inventory", "-i", required=True,
                       help=".ut-inventory.json 路径")
    sp_tm.add_argument("--mapping-in", default=None,
                       help="已保存的映射 JSON（跳过 MCP 查询，直接回写）")
    sp_tm.add_argument("--mapping-out", default=None,
                       help="保存映射到 JSON 文件（供后续 --mapping-in 使用）")
    sp_tm.add_argument("--report", default=None,
                       help="输出 Markdown 测试覆盖报告路径")
    sp_tm.add_argument("--mcp-url", default=MCP_URL, help="GitNexus MCP HTTP 端点")
    sp_tm.add_argument("--repo-root", default=None,
                       help="GitNexus 仓库内的本地检出路径（TEST_F 解析来源；"
                            "默认从 --inventory 路径推导）")
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
