#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 Uniontech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
update-usecase-count.py — Mode 2 test-writer §6「usecase_count 回写」固化脚本

固化 test-writer.md §6 的纯机械操作：每类编译通过后，扫描测试文件统计 TEST_F 用例数，
按方法名匹配（用例名首段 PascalCase vs 方法名 camelCase 小写归一化）增量写回
inventory 的 usecase_count。模型不再在上下文里数 TEST_F、改 JSON。

模型保留职责（本脚本不做）：
  - 决定何时回写（编译+自检通过后才回写，调度语义在模型侧）
  - 覆盖率门禁判定（usecase_count 只是事实，门禁在 self-checker）

匹配规则（test-writer.md §6）：
  - TEST_F(ClassNameTest, {MethodName_PascalCase}_{Scenario}_{Expected}) 用例名首段
    转 lower 与 method.name lower 比对
  - 只更新 testable=true 且 class_qn 匹配当前类的方法
  - 匹配不到的方法保持原 usecase_count 不动（失败安全）
  - 增量操作：只改当前类方法，不覆盖其他类数据

字段兼容（与 plan-test-classes.py 一致）：qualified_name|qn、file_path|file、
class_qn 短名或全名（统一取最后一段作类名匹配）。

用法:
  python3 update-usecase-count.py --test-file autotests/core/test_calculator.cpp \\
      --inventory autotests/.ut-inventory.json --class Calculator
  python3 update-usecase-count.py ... --class-qn proj.src.Calculator  # 精确匹配（同名歧义时用）
  python3 update-usecase-count.py ... --dry-run   # 只打印不写回

输出:
  [COUNT] Calculator | test_calculator.cpp | 15 cases | 9 methods updated
    add: 3 | subtract: 1 | multiply: 1 | divide: 2 | ...
  inventory: autotests/.ut-inventory.json (written)
"""

import argparse
import json
import os
import re
import sys

TEST_CASE_RE = re.compile(r'TEST_[FP]\s*\(\s*\w+\s*,\s*(\w+)\s*\)')


def _field(entry, *names, default=None):
    """防御式取字段：返回第一个非空值（兼容 schema 与存量字段名）。"""
    for n in names:
        v = entry.get(n)
        if v not in (None, ""):
            return v
    return default


def _class_short_name(class_qn):
    return class_qn.rsplit(".", 1)[-1] if "." in class_qn else class_qn


def count_cases_for_method(content, method_name_lower):
    """统计测试文件中用例名首段（PascalCase）小写 == method_name_lower 的用例数。

    test-writer.md §6 的匹配规则：用例名首段 PascalCase，方法名 camelCase，
    小写归一化后比对。
    """
    count = 0
    for m in TEST_CASE_RE.finditer(content):
        case_name = m.group(1)
        first_segment = case_name.split('_')[0].lower()
        if first_segment == method_name_lower:
            count += 1
    return count


def count_total_cases(content):
    """测试文件中 TEST_F/TEST_P 总数。"""
    return len(TEST_CASE_RE.findall(content))


def _method_matches(method, class_short, class_qn_exact):
    """判断方法是否属于目标类。"""
    if not method.get("testable"):
        return False
    cq = _field(method, "class_qn", default=None)
    if not cq:
        return False
    if class_qn_exact:
        return cq == class_qn_exact
    return _class_short_name(cq) == class_short


def detect_ambiguous_class(inventory, class_short):
    """检测短名匹配是否命中多个不同 class_qn（同名类歧义）。

    返回匹配到的 distinct class_qn 集合。长度 >1 时调用方应提示用 --class-qn。
    """
    qns = set()
    for method in inventory.get("methods", []):
        if not method.get("testable"):
            continue
        cq = _field(method, "class_qn", default=None)
        if cq and _class_short_name(cq) == class_short:
            qns.add(cq)
    return qns


def update_inventory(inventory, content, class_short, class_qn_exact=None):
    """增量更新 inventory：只改匹配方法的 usecase_count，其余不动。

    返回 [{"name","usecase_count"}] 已更新方法列表。
    """
    updated = []
    for method in inventory.get("methods", []):
        if not _method_matches(method, class_short, class_qn_exact):
            continue
        name = _field(method, "name", default="")
        cases = count_cases_for_method(content, name.lower())
        method["usecase_count"] = cases
        updated.append({"name": name, "usecase_count": cases})
    return updated


def summarize(class_short, test_file, total_cases, updated):
    parts = [f"[COUNT] {class_short} | {os.path.basename(test_file)} | "
             f"{total_cases} cases | {len(updated)} methods updated"]
    if updated:
        detail = " | ".join(f"{u['name']}: {u['usecase_count']}" for u in updated)
        parts.append("  " + detail)
    return "\n".join(parts)


def main_no_exit(argv=None):
    ap = argparse.ArgumentParser(
        description="Mode 2 test-writer §6 固化：TEST_F 计数 + usecase_count 回写")
    ap.add_argument("--test-file", "-t", required=True, help="测试文件路径")
    ap.add_argument("--inventory", "-i", required=True, help=".ut-inventory.json 路径")
    ap.add_argument("--class", dest="class_short", default=None,
                    help="类短名（如 Calculator），匹配 class_qn 短名")
    ap.add_argument("--class-qn", dest="class_qn_exact", default=None,
                    help="精确 class_qn（如 proj.src.Calculator），同名歧义时用，优先于 --class")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写回 inventory")
    args = ap.parse_args(argv)

    if not args.class_short and not args.class_qn_exact:
        print("[COUNT] error: 必须指定 --class 或 --class-qn")
        return 2
    if not os.path.isfile(args.test_file):
        print(f"[COUNT] error: test file not found: {args.test_file}")
        return 2
    if not os.path.isfile(args.inventory):
        print(f"[COUNT] error: inventory not found: {args.inventory}")
        return 2

    with open(args.test_file, encoding="utf-8") as f:
        content = f.read()
    with open(args.inventory, encoding="utf-8") as f:
        try:
            inventory = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[COUNT] error: invalid inventory JSON: {e}")
            return 2

    class_short = args.class_short or _class_short_name(args.class_qn_exact)
    total_cases = count_total_cases(content)

    # 同名类歧义检测：--class 短名命中多个 class_qn 时警告（不中断，但提示用 --class-qn）
    if args.class_short and not args.class_qn_exact:
        matched_qns = detect_ambiguous_class(inventory, class_short)
        if len(matched_qns) > 1:
            print(f"[COUNT] WARNING: 短名 '{class_short}' 命中 {len(matched_qns)} 个不同 class_qn：")
            for q in sorted(matched_qns):
                print(f"    - {q}")
            print("  建议用 --class-qn <全限定名> 精确匹配，否则会串改同名类数据")

    updated = update_inventory(inventory, content, class_short, args.class_qn_exact)

    print(summarize(class_short, args.test_file, total_cases, updated))

    if args.dry_run:
        print(f"[COUNT] dry-run: inventory not written ({args.inventory})")
    else:
        with open(args.inventory, "w", encoding="utf-8") as f:
            json.dump(inventory, f, ensure_ascii=False, indent=2)
        print(f"inventory: {args.inventory} (written)")
    return 0


def main():
    sys.exit(main_no_exit())


if __name__ == "__main__":
    main()
