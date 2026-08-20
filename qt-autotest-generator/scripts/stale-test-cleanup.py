#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
stale-test-cleanup.py — 过时测试主动清理

reconcile 对账后，若 diff 报告含 removed 方法，立即清理引用已删方法的测试用例，
不等编译报错。

用法:
  # 从 diff 报告 JSON 清理
  python3 stale-test-cleanup.py \
    --diff-json /path/to/ut-inventory-diff.json \
    --test-dir autotests \
    --inventory autotests/.ut-inventory.json \
    --dry-run

  # 直接指定已删方法列表
  python3 stale-test-cleanup.py \
    --removed-methods "subtract,multiply,findMax" \
    --class-name Calculator \
    --test-dir autotests \
    --inventory autotests/.ut-inventory.json

  # 分支切换 stale 标记
  python3 stale-test-cleanup.py \
    --stale-classes "OldView,LegacyHandler" \
    --test-dir autotests \
    --cmakelists autotests/CMakeLists.txt \
    --branch feature/new-ui
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# ── 测试用例块提取 ──

def extract_test_blocks(content: str) -> list[dict]:
    """从测试文件提取所有 TEST_F / TEST_P 用例块。

    返回 [{"type": "TEST_F"|"TEST_P", "fixture": str, "case": str,
           "start": int, "end": int, "text": str, "method_hint": str}]
    """
    blocks = []
    # 匹配 TEST_F( Fixture, CaseName ) 或 TEST_P( Fixture, CaseName )
    pattern = re.compile(
        r'(TEST_(?P<type>[FP])\s*\(\s*(?P<fixture>\w+)\s*,\s*(?P<case>\w+)\s*\))'
    )

    for m in pattern.finditer(content):
        test_type = "TEST_F" if m.group("type") == "F" else "TEST_P"
        fixture = m.group("fixture")
        case_name = m.group("case")

        # 从用例名提取方法提示: Add_PositiveNumbers_ReturnsCorrectSum → add
        # 规则: 取第一个 _ 前的段，转小写
        method_hint = case_name.split("_")[0].lower() if "_" in case_name else case_name.lower()

        # 找到用例体: 从 TEST_F 行后找第一个 {，按大括号深度匹配到 }
        start = m.start()
        # 从 TEST_F 行往后找块的开始 {
        rest = content[m.end():]
        depth = 0
        body_start = None
        body_end = None
        pos = m.end()

        for i, ch in enumerate(rest):
            if ch == '{':
                if depth == 0:
                    body_start = pos + i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    body_end = pos + i + 1
                    break

        if body_start is not None and body_end is not None:
            blocks.append({
                "type": test_type,
                "fixture": fixture,
                "case": case_name,
                "method_hint": method_hint,
                "start": start,
                "end": body_end,
                "text": content[start:body_end],
            })

    return blocks


def extract_instantiate_blocks(content: str) -> list[dict]:
    """提取 INSTANTIATE_TEST_SUITE_P 块。"""
    blocks = []
    pattern = re.compile(
        r'INSTANTIATE_TEST_SUITE_P\s*\(\s*(?P<prefix>\w+)\s*,\s*(?P<suite>\w+)\s*,[^;]*;?\s*',
        re.DOTALL
    )
    for m in pattern.finditer(content):
        blocks.append({
            "prefix": m.group("prefix"),
            "suite": m.group("suite"),
            "start": m.start(),
            "end": m.end(),
            "text": m.group(0),
        })
    return blocks


# ── 方法名匹配 ──

def method_matches_case(method_name: str, case_name: str) -> bool:
    """判断用例名是否引用了指定方法。

    匹配规则:
      - 用例名以方法名开头（大小写不敏感，方法名转 PascalCase 后前缀匹配）
      - 例: add → Add_*, subtract → Subtract_*
      - 也支持完全匹配: add → Add (方法名 == 用例名)
    """
    # 方法名转 PascalCase: add → Add, findMax → FindMax
    method_pascal = method_name[0].upper() + method_name[1:] if method_name else ""
    return case_name.startswith(method_pascal) or case_name.lower().startswith(method_name.lower())


# ── 注释用例块 ──

def comment_out_block(text: str, reason: str) -> str:
    """将整个 TEST_F/TEST_P 块注释掉，加原因说明。"""
    lines = text.split("\n")
    commented = [f"// {reason}"]
    for line in lines:
        commented.append(f"// {line}")
    return "\n".join(commented)


# ── 用例计数 ──

def count_active_cases_by_method(content: str, class_short: str) -> dict[str, int]:
    """统计未注释的 TEST_F 用例数，按方法名分组。

    返回 {method_name_lower: count}
    """
    counts = {}
    fixture_pattern = f"{class_short}Test"

    # 只统计未注释行（行首不含 //）
    active_lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        active_lines.append(line)
    active_content = "\n".join(active_lines)

    pattern = re.compile(
        rf'TEST_[FP]\s*\(\s*{re.escape(fixture_pattern)}\s*,\s*(\w+)\s*\)'
    )
    for m in pattern.finditer(active_content):
        case_name = m.group(1)
        method_hint = case_name.split("_")[0].lower() if "_" in case_name else case_name.lower()
        counts[method_hint] = counts.get(method_hint, 0) + 1

    return counts


# ── 核心清理逻辑 ──

def cleanup_removed_methods(
    test_dir: str,
    inventory_path: str,
    removed_methods: list[dict],
    dry_run: bool = False,
) -> dict:
    """主清理逻辑：已删方法 → 注释测试用例 → 更新 inventory。

    removed_methods: [{"name": str, "class_qn": str, ...}]
    返回清理报告 dict。
    """
    report = {
        "removed_methods": [],
        "cleaned_files": [],
        "cleaned_cases": 0,
        "cleaned_instantiates": 0,
        "updated_inventory": False,
    }

    # 按 class_qn 分组
    removed_by_class: dict[str, list[dict]] = {}
    for m in removed_methods:
        cls = m.get("class_qn") or "(free_functions)"
        # 取短名
        cls_short = cls.rsplit(".", 1)[-1] if "." in cls else cls
        removed_by_class.setdefault(cls_short, []).append(m)

    # 加载 inventory
    inventory = None
    if os.path.isfile(inventory_path):
        with open(inventory_path, "r", encoding="utf-8") as f:
            inventory = json.load(f)

    for cls_short, methods in removed_by_class.items():
        method_names = {m["name"] for m in methods}
        report["removed_methods"].extend(methods)

        # 找测试文件: test_{cls_short}.cpp 或 {cls_short}test.cpp
        test_file = None
        for candidate in [
            os.path.join(test_dir, f"test_{cls_short.lower()}.cpp"),
            os.path.join(test_dir, "core", f"test_{cls_short.lower()}.cpp"),
            os.path.join(test_dir, f"{cls_short.lower()}test.cpp"),
            os.path.join(test_dir, f"{cls_short}Test.cpp"),
        ]:
            if os.path.isfile(candidate):
                test_file = candidate
                break

        # 也 glob 搜索
        if test_file is None:
            for root, dirs, files in os.walk(test_dir):
                for fname in files:
                    if fname.startswith("test_") and fname.endswith(".cpp"):
                        base = fname[5:-4].replace("_", "").lower()
                        if base == cls_short.lower():
                            test_file = os.path.join(root, fname)
                            break
                if test_file:
                    break

        if test_file is None:
            print(f"  ⏭️  {cls_short}: 无测试文件，跳过")
            continue

        print(f"  📄 {cls_short}: 测试文件 {test_file}")

        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 提取所有用例块
        all_blocks = extract_test_blocks(content)
        blocks_to_remove = []

        for block in all_blocks:
            for method_name in method_names:
                if method_matches_case(method_name, block["case"]):
                    blocks_to_remove.append((block, method_name))
                    break

        if not blocks_to_remove:
            print(f"     无匹配用例")
            continue

        # 按位置倒序排列，避免替换时偏移
        blocks_to_remove.sort(key=lambda x: x[0]["start"], reverse=True)

        new_content = content
        cleaned = 0
        cleaned_cases_detail = []

        for block, method_name in blocks_to_remove:
            commented = comment_out_block(
                block["text"],
                f"Removed: method '{method_name}' deleted from source"
            )
            new_content = new_content[:block["start"]] + commented + new_content[block["end"]:]
            cleaned += 1
            cleaned_cases_detail.append({
                "method": method_name,
                "case": block["case"],
                "type": block["type"],
                "line": content[:block["start"]].count("\n") + 1,
            })

        # 清理 INSTANTIATE_TEST_SUITE_P（仅 TEST_P 用例被删时）
        inst_blocks = extract_instantiate_blocks(new_content)
        cleaned_inst = 0
        for inst in inst_blocks:
            # 检查该 suite 是否仍有活跃的 TEST_P 引用
            suite_name = inst["suite"]
            has_active_test_p = any(
                b["type"] == "TEST_P" and b["fixture"] == suite_name
                for b in extract_test_blocks(new_content)
                if not new_content[b["start"]:b["end"]].strip().startswith("//")
            )
            if not has_active_test_p:
                # 整个 suite 无活跃 TEST_P，注释掉 INSTANTIATE
                commented_inst = f"// Removed: parameterized suite for deleted method\n// {inst['text']}"
                new_content = new_content[:inst["start"]] + commented_inst + new_content[inst["end"]:]
                cleaned_inst += 1

        # 更新 usecase_count
        if inventory is not None:
            new_counts = count_active_cases_by_method(new_content, cls_short)
            for m in inventory.get("methods", []):
                cls_qn = m.get("class_qn") or ""
                cls_qn_short = cls_qn.rsplit(".", 1)[-1] if "." in cls_qn else cls_qn
                if cls_qn_short == cls_short and m.get("testable"):
                    method_lower = m["name"].lower()
                    m["usecase_count"] = new_counts.get(method_lower, 0)

        # 写回
        if not dry_run:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write(new_content)
            if inventory is not None:
                with open(inventory_path, "w", encoding="utf-8") as f:
                    json.dump(inventory, f, indent=2, ensure_ascii=False)
                report["updated_inventory"] = True

        report["cleaned_files"].append(test_file)
        report["cleaned_cases"] += cleaned
        report["cleaned_instantiates"] += cleaned_inst

        for detail in cleaned_cases_detail:
            print(f"     ❌ {detail['type']}({detail['case']}) — 引用已删方法 '{detail['method']}' (行 {detail['line']})")
        if cleaned_inst:
            print(f"     ❌ {cleaned_inst} 个 INSTANTIATE_TEST_SUITE_P 已清理")

    return report


def mark_stale_tests(
    test_dir: str,
    stale_classes: list[str],
    cmakelists_path: str | None,
    branch: str,
    dry_run: bool = False,
) -> dict:
    """分支切换：标记 stale 测试（不删用例，只移除 CMake + 加文件头标记）。"""
    report = {"stale_files": [], "cmake_removed": []}

    for cls_short in stale_classes:
        # 找测试文件
        test_file = None
        for root, dirs, files in os.walk(test_dir):
            for fname in files:
                if fname.startswith("test_") and fname.endswith(".cpp"):
                    base = fname[5:-4].replace("_", "").lower()
                    if base == cls_short.lower():
                        test_file = os.path.join(root, fname)
                        break
            if test_file:
                break

        if test_file is None:
            continue

        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 加 stale 标记到文件头部（SPDX 之后）
        stale_marker = (
            f"\n// ⚠️ STALE: class source file not found in branch '{branch}'\n"
            f"// This test file is excluded from compilation.\n"
            f"// It will be restored when switching back to the original branch.\n"
        )
        # 插到 SPDX 头之后
        spdx_end = content.find("\n\n", content.find("SPDX-License-Identifier"))
        if spdx_end > 0:
            new_content = content[:spdx_end + 2] + stale_marker + content[spdx_end + 2:]
        else:
            new_content = stale_marker + content

        if not dry_run:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write(new_content)

        report["stale_files"].append(test_file)
        print(f"  🏷️  {cls_short}: 标记 stale (branch={branch})")

        # 移除 CMake add_subdirectory
        if cmakelists_path and os.path.isfile(cmakelists_path):
            with open(cmakelists_path, "r", encoding="utf-8") as f:
                cmake_content = f.read()

            # 匹配 add_subdirectory(类名相关目录)
            pattern = re.compile(
                rf'add_subdirectory\s*\(\s*{re.escape(cls_short.lower())}[^)]*\)\s*\n?',
                re.IGNORECASE
            )
            new_cmake = pattern.sub(
                f"# STALE: removed add_subdirectory for {cls_short} (branch={branch})\n",
                cmake_content,
            )
            if new_cmake != cmake_content:
                if not dry_run:
                    with open(cmakelists_path, "w", encoding="utf-8") as f:
                        f.write(new_cmake)
                report["cmake_removed"].append(cls_short)
                print(f"     CMake: 已移除 add_subdirectory({cls_short.lower()})")

    return report


def render_report(report: dict) -> str:
    """渲染清理报告为 Markdown。"""
    lines = [
        "# 过时测试清理报告",
        "",
        "## 概览",
        "",
        "| 指标 | 值 |",
        "|------|----|",
        f"| 已删方法数 | {len(report['removed_methods'])} |",
        f"| 清理用例数 | {report['cleaned_cases']} |",
        f"| 清理 INSTANTIATE 数 | {report['cleaned_instantiates']} |",
        f"| 清理测试文件数 | {len(report['cleaned_files'])} |",
        f"| 更新 inventory | {'✅' if report['updated_inventory'] else '❌'} |",
        "",
    ]

    if report["removed_methods"]:
        lines += ["## 已删方法", ""]
        lines += ["| 方法名 | 类 | 原 level |", "|--------|-----|----------|"]
        for m in report["removed_methods"]:
            lines.append(f"| {m.get('name', '?')} | {m.get('class_qn', '?')} | {m.get('level', '?')} |")
        lines.append("")

    if report["cleaned_files"]:
        lines += ["## 清理的测试文件", ""]
        for f in report["cleaned_files"]:
            lines.append(f"- `{f}`")
        lines.append("")

    if report.get("stale_files"):
        lines += ["## Stale 标记（分支切换）", ""]
        for f in report["stale_files"]:
            lines.append(f"- `{f}`")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="过时测试主动清理：reconcile 对账后立即清理引用已删方法的测试用例")
    parser.add_argument("--test-dir", required=True,
                        help="测试目录 (如 autotests/)")
    parser.add_argument("--inventory", required=True,
                        help=".ut-inventory.json 路径")
    parser.add_argument("--diff-json", default=None,
                        help="增量 diff JSON 路径（由 fetch-mcp-data.py 产出）")
    parser.add_argument("--removed-methods", default=None,
                        help="直接指定已删方法名（逗号分隔），配合 --class-name 使用")
    parser.add_argument("--class-name", default=None,
                        help="配合 --removed-methods 指定所属类名")
    parser.add_argument("--class-qn", default=None,
                        help="配合 --removed-methods 指定所属类全限定名")
    parser.add_argument("--stale-classes", default=None,
                        help="stale 类名（逗号分隔），分支切换场景")
    parser.add_argument("--cmakelists", default=None,
                        help="根 CMakeLists.txt 路径（stale 场景移除 add_subdirectory）")
    parser.add_argument("--branch", default=None,
                        help="当前分支名（stale 场景标记用）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览不修改文件")
    parser.add_argument("--report", default=None,
                        help="清理报告输出路径（Markdown）")
    args = parser.parse_args()

    # 构建 removed_methods 列表
    removed_methods = []

    if args.diff_json:
        # 从 diff JSON 读取
        with open(args.diff_json, "r", encoding="utf-8") as f:
            diff = json.load(f)
        removed_methods = diff.get("removed", [])
    elif args.removed_methods:
        # 从命令行直接指定
        class_qn = args.class_qn or args.class_name or "Unknown"
        for name in args.removed_methods.split(","):
            removed_methods.append({
                "name": name.strip(),
                "class_qn": class_qn,
                "level": "?",
            })

    if not removed_methods and not args.stale_classes:
        print("⚠️  无已删方法也无 stale 类，无需清理")
        return

    print("🧹 过时测试清理")
    print(f"   test_dir: {args.test_dir}")
    print(f"   inventory: {args.inventory}")
    if args.dry_run:
        print(f"   🔍 DRY RUN 模式（不修改文件）")
    print()

    # 已删方法清理
    report = {"removed_methods": [], "cleaned_files": [], "cleaned_cases": 0,
              "cleaned_instantiates": 0, "updated_inventory": False, "stale_files": [],
              "cmake_removed": []}

    if removed_methods:
        print("📋 步骤 1: 清理已删方法的测试用例")
        cleanup_report = cleanup_removed_methods(
            args.test_dir, args.inventory, removed_methods, args.dry_run)
        report.update(cleanup_report)
        print()

    # Stale 标记
    if args.stale_classes:
        print("📋 步骤 2: 标记 stale 测试")
        stale_report = mark_stale_tests(
            args.test_dir,
            [c.strip() for c in args.stale_classes.split(",")],
            args.cmakelists,
            args.branch or "unknown",
            args.dry_run,
        )
        report["stale_files"] = stale_report["stale_files"]
        report["cmake_removed"] = stale_report["cmake_removed"]
        print()

    # 输出摘要
    print("=" * 60)
    print(f"✅ 清理完成")
    print(f"   已删方法: {len(report['removed_methods'])}")
    print(f"   清理用例: {report['cleaned_cases']}")
    print(f"   清理 INSTANTIATE: {report['cleaned_instantiates']}")
    print(f"   更新 inventory: {'是' if report['updated_inventory'] else '否'}")
    print("=" * 60)

    # 写报告
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(render_report(report))
        print(f"📄 报告已写入 {args.report}")


if __name__ == "__main__":
    main()
