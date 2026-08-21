#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
plan-test-classes.py — Mode 2 test-writer §4「确定待测类列表」固化脚本

固化 test-writer.md §4 的纯数据变换：按 class_qn 分组 testable 方法 →
类 level 取方法最高级 → level_rank 排序（high → mid → low）→ is_gui 匹配 →
自由函数按 file_path 归组。模型只消费输出，不再在上下文里执行分组排序。

模型保留职责（本脚本不做）：
  - 逐类闭环调度（依赖追踪/生成/验证/自检）
  - stub 策略与用例设计
  - 同名类合并到同一测试文件的例外判断（强耦合自由函数归并到某类，由 Agent 定）

字段兼容（实测存量数据与 schema 文档不一致，必须双兼容）：
  - 全限定名：`qualified_name`（schema）或 `qn`（存量，如 deepin-image-viewer）
  - 文件路径：`file_path`（schema）或 `file`（存量）
  - `class_qn`：scan-inventory.py 产出**短名**（如 `ApplicationAdaptor`），
    手写/旧数据可能是全名（如 `project.src.Calculator`）——统一取最后一段作类名

用法:
  python3 plan-test-classes.py --inventory autotests/.ut-inventory.json
  python3 plan-test-classes.py -i autotests/.ut-inventory.json --stdout   # 不落盘，只打印

输出:
  {test_dir}/.reports/testable-classes.json — 模型消费的类清单
  stdout 摘要（人类可读统计）
"""

import argparse
import json
import os
import sys
from collections import Counter

LEVEL_RANK = {"high": 3, "mid": 2, "low": 1}


def _field(entry, *names, default=None):
    """防御式取字段：返回第一个非空值（兼容 schema 与存量字段名）。"""
    for n in names:
        v = entry.get(n)
        if v not in (None, ""):
            return v
    return default


def _level(m):
    """方法 level；testable 但 level 缺失时按最低档处理（防御）。"""
    lv = _field(m, "level", default="low")
    return lv if lv in LEVEL_RANK else "low"


def _class_short_name(class_qn):
    """class_qn（短名或全名）→ 类短名：取最后一段。"""
    return class_qn.rsplit(".", 1)[-1] if "." in class_qn else class_qn


def _class_full_name(class_qn, method_qn, name):
    """推导类全限定名：class_qn 已是全名直接用；短名则从方法 qn 剥最后一节。"""
    if "." in class_qn:
        return class_qn
    if method_qn and method_qn.endswith("." + name):
        return method_qn[: -len(name) - 1]
    return class_qn


def _module_of(file_path, default="common"):
    """模块名：文件路径最后一段目录（test-code-gen §3 的 source_dirs 最后一段语义）。

    src/lib/ui/fileview.cpp → ui；src/utils.cpp → common（无目录段）。
    """
    if not file_path:
        return default
    parts = [p for p in str(file_path).replace("\\", "/").split("/") if p]
    # parts[-1] 是文件名；有目录段则取最后一段目录，否则 default
    return parts[-2] if len(parts) >= 2 else default


def build_plan(inventory):
    """inventory dict → (classes 列表, 自由函数组列表)。已排序，可直接序列化。"""
    methods = inventory.get("methods", []) or []
    gui_names = {_class_short_name(_field(c, "name", "qualified_name", default=""))
                 for c in (inventory.get("classes", []) or [])
                 if c.get("is_gui")}

    grouped = {}       # group_key → {"class_qn", "short_name", "methods": [...], "file": ..., "qn": ..., "name": ...}
    order = []         # 保持 inventory 出现顺序（稳定排序的兜底序）
    free_by_file = {}  # file_path → [methods]

    for m in methods:
        if not m.get("testable"):
            continue
        class_qn = _field(m, "class_qn", default=None)
        if not class_qn:
            # 自由函数（node_type=Function 或缺失 class_qn 的条目）按文件归组
            fp = _field(m, "file_path", "file", default="(unknown)")
            free_by_file.setdefault(fp, []).append(m)
            continue
        # 分组键：class_qn 本身；当 qn 相同但文件不同时追加 file_path 消歧
        # （同名类不同模块不合并，test-code-gen §3）
        key = class_qn
        fp = _field(m, "file_path", "file", default="")
        if key in grouped and grouped[key]["file"] != fp:
            key = f"{class_qn}@{fp}"
        if key not in grouped:
            grouped[key] = {
                "class_qn": class_qn,
                "short_name": _class_short_name(class_qn),
                "file": fp,
                "qn": _field(m, "qualified_name", "qn", default=""),
                "name": _field(m, "name", default=""),  # 推导类全名用
                "methods": [],
            }
            order.append(key)
        grouped[key]["methods"].append(m)

    classes = []
    for key in order:
        g = grouped[key]
        gmethods = sorted(g["methods"], key=lambda x: -LEVEL_RANK[_level(x)])
        cls_level = _level(gmethods[0])  # 类 level = 方法最高级（排序后首个即最高）
        classes.append({
            "name": g["short_name"],
            "qualified_name": _class_full_name(g["class_qn"], g["qn"], g["name"]),
            "level": cls_level,
            "is_gui": g["short_name"] in gui_names,
            "file_path": g["file"],
            "module": _module_of(g["file"]),
            "method_count": len(gmethods),
            "methods": gmethods,
        })

    # 类间排序：level 降序（high → mid → low）；同级保持 inventory 出现顺序（稳定）
    classes.sort(key=lambda c: -LEVEL_RANK[c["level"]])

    free_groups = []
    for fp in sorted(free_by_file):
        fns = sorted(free_by_file[fp], key=lambda x: -LEVEL_RANK[_level(x)])
        free_groups.append({
            "file_path": fp,
            "module": _module_of(fp),
            "function_count": len(fns),
            "functions": fns,
        })

    return classes, free_groups


def summarize(classes, free_groups):
    """stdout 摘要（人类可读）。"""
    by_level = Counter(c["level"] for c in classes)
    total_methods = sum(c["method_count"] for c in classes)
    total_free = sum(g["function_count"] for g in free_groups)
    lines = [
        f"[PLAN] classes: {len(classes)} "
        f"(high={by_level.get('high', 0)}, mid={by_level.get('mid', 0)}, "
        f"low={by_level.get('low', 0)}) | class methods: {total_methods} "
        f"| free-function groups: {len(free_groups)} ({total_free} funcs)",
    ]
    for c in classes:
        lv = Counter(_level(m) for m in c["methods"])
        gui = " [GUI]" if c["is_gui"] else ""
        lines.append(
            f"  {c['level']:>4}  {c['name']} ({c['method_count']} methods: "
            f"h={lv.get('high', 0)},m={lv.get('mid', 0)},l={lv.get('low', 0)})"
            f"{gui}  <- {c['file_path']}")
    for g in free_groups:
        lines.append(
            f"  free  {g['module']} ({g['function_count']} funcs)  <- {g['file_path']}")
    return "\n".join(lines)


def main_no_exit(argv=None):
    """main 的可测试形态：接受 argv 列表，返回退出码而不调 sys.exit。"""
    ap = argparse.ArgumentParser(
        description="Mode 2 test-writer §4 固化：inventory → 排序后的待测类清单")
    ap.add_argument("--inventory", "-i", required=True,
                    help=".ut-inventory.json 路径")
    ap.add_argument("--output", "-o", default=None,
                    help="输出 JSON 路径，默认 {inventory 所在目录}/.reports/testable-classes.json")
    ap.add_argument("--stdout", action="store_true",
                    help="只打印摘要与 JSON，不落盘")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.inventory):
        print(f"[PLAN] error: inventory not found: {args.inventory}")
        return 2

    with open(args.inventory, encoding="utf-8") as f:
        try:
            inventory = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[PLAN] error: invalid JSON: {e}")
            return 2

    classes, free_groups = build_plan(inventory)

    plan = {
        "inventory": os.path.abspath(args.inventory),
        "class_count": len(classes),
        "classes": classes,
        "free_function_groups": free_groups,
    }

    if args.stdout:
        print(summarize(classes, free_groups))
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    out = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.inventory)),
        ".reports", "testable-classes.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(summarize(classes, free_groups))
    print(f"[PLAN] plan written: {out}")
    return 0


def main():
    sys.exit(main_no_exit())


if __name__ == "__main__":
    main()
