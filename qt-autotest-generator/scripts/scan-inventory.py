#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
scan-inventory.py — 模式一：函数重要性探测

用法:
  python3 scan-inventory.py --project <project_name> --output <path> [--test-dir <dir>]

示例:
  python3 scan-inventory.py \
    --project home-uos-service-codebase-repos-deepin-calculator \
    --output /tmp/inventory-calculator.json

  python3 scan-inventory.py \
    --project home-uos-service-codebase-repos-dde-file-manager \
    --output /tmp/inventory-filemanager.json

本脚本通过 MCP 工具扫描知识图谱，为每个方法/函数评分并生成 .ut-inventory.json。
由于 MCP 工具只能由 Agent 调用，本脚本作为"模拟实现"供验证设计，
实际生产中由 Agent 按 importance_inventory.md 阶段文档执行。
"""

import json
import re
import sys
import time
import argparse
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

def load_mcp_dump(path: str) -> dict:
    """Load pre-fetched MCP data from JSON file."""
    with open(path, 'r') as f:
        return json.load(f)


# ── 主流程 ──

def build_inventory(mcp_data: dict, project_name: str, base_sha: str,
                    gate_thresholds: dict | None = None) -> dict:
    """Build .ut-inventory.json from pre-fetched MCP data.

    Args:
        gate_thresholds: 外部门禁阈值；为 None 时使用 DEFAULT_GATE_THRESHOLDS。
            增量模式应传入旧 inventory 的 gate_thresholds，避免覆盖外部设定。
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
        "base_sha": base_sha,
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

def fetch_mcp_data_via_subagent(project_name: str) -> dict:
    """
    Agent 调用此函数会触发 MCP 查询。
    实际由 Agent 在对话中手动执行 MCP 调用，结果传入 build_inventory。
    """
    raise NotImplementedError(
        "MCP 调用需由 Agent 在对话中执行。请使用 --mcp-dump 模式或手动采集数据。"
    )


def main():
    parser = argparse.ArgumentParser(description="函数重要性探测 - 生成 .ut-inventory.json")
    parser.add_argument("--project", required=True, help="知识图谱中的项目名")
    parser.add_argument("--output", required=True, help="输出 JSON 路径")
    parser.add_argument("--mcp-dump", help="预采集的 MCP 数据 JSON 文件")
    parser.add_argument("--base-sha", default="unknown", help="Git base SHA")
    parser.add_argument("--summary", action="store_true", help="同时输出 Markdown 摘要")
    args = parser.parse_args()

    if args.mcp_dump:
        mcp_data = load_mcp_dump(args.mcp_dump)
    else:
        print("错误: 需要 --mcp-dump 参数指定预采集数据文件", file=sys.stderr)
        print("请先用 Agent 采集 MCP 数据并保存为 JSON", file=sys.stderr)
        sys.exit(1)

    inventory = build_inventory(mcp_data, args.project, args.base_sha)

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


if __name__ == "__main__":
    main()
