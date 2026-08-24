#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
export-defects.py — Mode 5 源码缺陷导出与统计

三个子命令:
  upsert     标红时增量写入缺陷记录
  mark-fixed 用例通过时标记修复
  export     批量导出 defects.json + defects-summary.md

数据文件: .ut-defects.json（version=1, defects[], stats{}, history{}）

用法:
  # 增量写入
  python3 scripts/export-defects.py upsert --defects .ut-defects.json \
      --defect-id "pkg.Class.method#Fixture.Case" \
      --method-qn "pkg.Class.method" --method-name "method" \
      --class-qn "pkg.Class" --class-name "Class" \
      --module core --file-path src/core/foo.cpp --file-line 42 \
      --test-fixture "Fixture" --test-case-name "Case" \
      --test-file autotests/test_foo.cpp \
      --type source_defect_runtime --type-category runtime \
      --detected-at-stage compile --evidence "segfault" \
      --suggestion "加空指针检查" --root-cause-snippet "void f() { x->y; }" \
      --method-level high --batch 3

  # 标记修复
  python3 scripts/export-defects.py mark-fixed --defects .ut-defects.json \
      --defect-id "pkg.Class.method#Fixture.Case" --fixed-in-sha "def5678"

  # 批量标记修复（按类）
  python3 scripts/export-defects.py mark-fixed --defects .ut-defects.json \
      --class "MyClass" --fixed-in-sha "def5678"

  # 导出
  python3 scripts/export-defects.py export --defects .ut-defects.json \
      --report-dir build-ut [--inventory autotests/.ut-inventory.json]
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


# ───────────────────────── helpers ─────────────────────────

def run(cmd, **kw):
    """Run subprocess, raise on failure."""
    print(f"  $ {cmd}" if isinstance(cmd, str) else f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, shell=isinstance(cmd, str), **kw)


def run_capture(cmd, **kw):
    """Run subprocess, capture output, don't raise."""
    return subprocess.run(cmd, capture_output=True, text=True, shell=isinstance(cmd, str), **kw)


# ───────────────────────── I/O ─────────────────────────

def load_defects(path):
    """Load .ut-defects.json; create skeleton if missing."""
    p = Path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        # version 兼容
        if "version" not in data:
            data["version"] = 1
        if "defects" not in data:
            data["defects"] = []
        if "stats" not in data:
            data["stats"] = {}
        if "history" not in data:
            data["history"] = {}
        return data
    return {
        "version": 1,
        "base_sha": None,
        "project": None,
        "defects": [],
        "stats": {},
        "history": {},
    }


def save_defects(path, data):
    """Write .ut-defects.json atomically."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(p)


# ───────────────────────── severity 计算 ─────────────────────────

_SEVERITY_MAP = {
    "source_defect_runtime": "high",
    "source_defect_compile": "high",
    "source_defect_logic": "mid",
    "needs_manual": "mid",
}


def _calc_severity(defect_type, method_level):
    """根据 type 和 method_level 计算 severity."""
    sev = _SEVERITY_MAP.get(defect_type, "low")
    if method_level == "high" and sev == "mid":
        sev = "high"
    return sev


# ───────────────────────── 统计 ─────────────────────────

def _recalc_stats(data):
    """重算 stats 对象，直接修改 data["stats"]."""
    defects = data.get("defects", [])
    now = datetime.now(timezone.utc).isoformat()
    stats = {
        "last_updated": now,
        "total": len(defects),
        "by_status": {"open": 0, "fixed": 0, "reopened": 0, "wontfix": 0},
        "by_severity": {"high": 0, "mid": 0, "low": 0},
        "by_type_category": {"compile": 0, "runtime": 0, "logic": 0, "manual": 0},
        "affected_methods": 0,
        "affected_classes": 0,
    }
    open_methods = set()
    open_classes = set()
    for d in defects:
        st = d.get("status", "open")
        stats["by_status"][st] = stats["by_status"].get(st, 0) + 1
        sev = d.get("severity", "low")
        stats["by_severity"][sev] = stats["by_severity"].get(sev, 0) + 1
        cat = d.get("type_category", "manual")
        stats["by_type_category"][cat] = stats["by_type_category"].get(cat, 0) + 1
        if st in ("open", "reopened"):
            open_methods.add(d.get("method_qn", ""))
            open_classes.add(d.get("class_qn", ""))
    stats["affected_methods"] = len(open_methods)
    stats["affected_classes"] = len(open_classes)
    data["stats"] = stats
    return stats


# ───────────────────────── SHA 变更归档 ─────────────────────────

def _archive_on_sha_change(data, new_base_sha):
    """若 base_sha 变更，归档旧数据到 history，清空 defects."""
    old_sha = data.get("base_sha")
    if not new_base_sha or new_base_sha == old_sha:
        return False
    if old_sha:
        data.setdefault("history", {})[old_sha] = {
            "defects": data.get("defects", []),
            "stats": data.get("stats", {}),
            "archived_at": datetime.now(timezone.utc).isoformat(),
        }
    data["defects"] = []
    data["base_sha"] = new_base_sha
    _recalc_stats(data)
    return True


# ───────────────────────── 子命令: upsert ─────────────────────────

def cmd_upsert(args):
    """增量写入或更新一条缺陷记录."""
    data = load_defects(args.defects)

    # SHA 归档
    if args.base_sha:
        archived = _archive_on_sha_change(data, args.base_sha)
        if archived:
            print(f"  📦 base_sha 变更，旧缺陷已归档到 history")

    now = datetime.now(timezone.utc).isoformat()
    defect_id = args.defect_id
    defect_type = args.type
    method_level = args.method_level
    severity = _calc_severity(defect_type, method_level)

    # 查找已有条目
    found = None
    for d in data["defects"]:
        if d["defect_id"] == defect_id:
            found = d
            break

    if found:
        status = found.get("status", "open")
        if status == "wontfix":
            print(f"  ⏭  {defect_id} 状态为 wontfix，跳过更新")
            save_defects(args.defects, data)
            return
        if status == "fixed":
            found["status"] = "reopened"
            found["fixed_at"] = None
            found["fixed_in_sha"] = None
            print(f"  🔄  {defect_id} fixed → reopened")
        # open 或 reopened: 更新字段
        found["last_updated"] = now
        found["evidence"] = args.evidence
        found["repair_attempts"] = args.repair_attempts
        found["iteration_count"] = args.iteration_count
        found["root_cause_snippet"] = args.root_cause_snippet
        found["severity"] = severity
        found["type"] = defect_type
        found["type_category"] = args.type_category
        found["file_line"] = args.file_line
        found["detected_at_stage"] = args.detected_at_stage
        print(f"  ✏️  {defect_id} 已更新 (status={found['status']})")
    else:
        # 新建条目
        entry = {
            "defect_id": defect_id,
            "method_qn": args.method_qn,
            "method_name": args.method_name,
            "class_qn": args.class_qn,
            "class_name": args.class_name,
            "module": args.module,
            "file_path": args.file_path,
            "file_line": args.file_line,
            "test_fixture": args.test_fixture,
            "test_case_name": args.test_case_name,
            "test_file": args.test_file,
            "type": defect_type,
            "type_category": args.type_category,
            "detected_at_stage": args.detected_at_stage,
            "severity": severity,
            "method_level": method_level,
            "evidence": args.evidence,
            "suggestion": args.suggestion,
            "root_cause_snippet": args.root_cause_snippet,
            "status": "open",
            "created_at": now,
            "last_updated": now,
            "repair_attempts": args.repair_attempts,
            "iteration_count": args.iteration_count,
            "batch": args.batch,
            "fixed_at": None,
            "fixed_in_sha": None,
        }
        data["defects"].append(entry)
        print(f"  ➕  {defect_id} 新增 (severity={severity})")

    # 元信息
    if args.project:
        data["project"] = args.project

    _recalc_stats(data)
    save_defects(args.defects, data)
    s = data["stats"]
    print(f"  📊 当前: {s['total']} 缺陷 (open={s['by_status']['open']} fixed={s['by_status']['fixed']} "
          f"reopened={s['by_status']['reopened']})")


# ───────────────────────── 子命令: mark-fixed ─────────────────────────

def cmd_mark_fixed(args):
    """标记缺陷为已修复."""
    data = load_defects(args.defects)
    now = datetime.now(timezone.utc).isoformat()
    fixed_sha = args.fixed_in_sha
    count = 0

    if args.defect_id:
        # 单条标记
        for d in data["defects"]:
            if d["defect_id"] == args.defect_id:
                if d.get("status") in ("open", "reopened"):
                    d["status"] = "fixed"
                    d["fixed_at"] = now
                    d["fixed_in_sha"] = fixed_sha
                    d["last_updated"] = now
                    count += 1
                    print(f"  ✅ {args.defect_id} → fixed")
                else:
                    print(f"  ⏭  {args.defect_id} 状态为 {d.get('status')}，跳过")
                break
        else:
            print(f"  ⚠ 未找到缺陷: {args.defect_id}")
    elif args.cls:
        # 按类批量标记
        for d in data["defects"]:
            if d.get("class_name") == args.cls and d.get("status") in ("open", "reopened"):
                d["status"] = "fixed"
                d["fixed_at"] = now
                d["fixed_in_sha"] = fixed_sha
                d["last_updated"] = now
                count += 1
                print(f"  ✅ {d['defect_id']} → fixed")
        if count == 0:
            print(f"  ⚠ 类 {args.cls} 下无 open/reopened 缺陷")
    else:
        print("  ⚠ 请指定 --defect-id 或 --class", file=sys.stderr)
        sys.exit(1)

    if count > 0:
        _recalc_stats(data)
        save_defects(args.defects, data)
        s = data["stats"]
        print(f"  📊 当前: {s['total']} 缺陷 (open={s['by_status']['open']} fixed={s['by_status']['fixed']} "
              f"reopened={s['by_status']['reopened']})")


# ───────────────────────── 子命令: export ─────────────────────────


def _render_md_table(defects_list, report_dir_rel="."):
    """渲染缺陷列表为 Markdown 表格行."""
    lines = []
    for d in defects_list:
        cls = d.get("class_name", "")
        method = d.get("method_name", "")
        case = d.get("test_case_name", "")
        test_file = d.get("test_file", "")
        fpath = d.get("file_path", "")
        fline = d.get("file_line", "")
        cat = d.get("type_category", "")
        evidence = d.get("evidence", "")
        suggestion = d.get("suggestion", "")

        # 用例链接到测试文件（从报告目录回溯到项目根）
        if test_file:
            case_cell = f"[{case}]({report_dir_rel}{test_file})"
        else:
            case_cell = case

        # 文件:行 链接到源码
        if fpath and fline:
            file_cell = f"[{fpath}:{fline}]({report_dir_rel}{fpath}#L{fline})"
        elif fpath:
            file_cell = f"[{fpath}]({report_dir_rel}{fpath})"
        else:
            file_cell = fpath

        lines.append(f"| {cls} | {method} | {case_cell} | {file_cell} | {cat} | {evidence} | {suggestion} |")
    return "\n".join(lines)


def _render_fixed_table(defects_list):
    """渲染已修复列表为 Markdown 表格行."""
    lines = []
    for d in defects_list:
        cls = d.get("class_name", "")
        method = d.get("method_name", "")
        case = d.get("test_case_name", "")
        sha = d.get("fixed_in_sha", "")[:8]
        fixed_at = (d.get("fixed_at", "") or "")[:19].replace("T", " ")
        lines.append(f"| {cls} | {method} | {case} | {sha} @ {fixed_at} |")
    return "\n".join(lines)


def cmd_export(args):
    """批量导出 defects.json + defects-summary.md."""
    data = load_defects(args.defects)
    _recalc_stats(data)
    save_defects(args.defects, data)

    stats = data["stats"]
    defects = data.get("defects", [])
    project = data.get("project", "(unknown)")
    base_sha = data.get("base_sha", "(unknown)")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. defects.json（纯数组） ──
    defects_json_path = report_dir / "defects.json"
    with open(defects_json_path, "w", encoding="utf-8") as f:
        json.dump(defects, f, indent=2, ensure_ascii=False)
    print(f"  📄 defects.json → {defects_json_path}")

    # ── 2. 分类 ──
    high_open = [d for d in defects if d.get("severity") == "high" and d.get("status") in ("open", "reopened")]
    mid_open = [d for d in defects if d.get("severity") == "mid" and d.get("status") in ("open", "reopened")]
    low_open = [d for d in defects if d.get("severity") == "low" and d.get("status") in ("open", "reopened")]
    fixed_list = [d for d in defects if d.get("status") == "fixed"]

    by_status = stats["by_status"]
    by_severity = stats["by_severity"]
    by_cat = stats["by_type_category"]

    # 已修复用例按类汇总（下一步建议）
    manual_open = [d for d in defects if d.get("type") == "needs_manual" and d.get("status") in ("open", "reopened")]
    high_open_count = len(high_open)
    manual_open_count = len(manual_open)
    fixed_count = len(fixed_list)

    # ── 3. 生成 Markdown ──
    # 项目名：取知识图谱节点路径最后一段
    if project and project.startswith("home-uos-service-codebase-repos-"):
        project_display = project[len("home-uos-service-codebase-repos-"):]
    elif project:
        project_display = Path(project).name if "/" in project else project
    else:
        project_display = "(unknown)"

    # 报告目录相对项目根的深度，用于修正链接
    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path(".").resolve()
    try:
        rel = report_dir.resolve().relative_to(project_dir)
        depth = len(rel.parts)  # e.g. build-autotests → depth=1
        report_dir_rel = "../" * depth
    except ValueError:
        report_dir_rel = ""

    md_lines = []
    md_lines.append(f"# 源码缺陷清单 · {project_display}")
    md_lines.append("")
    md_lines.append(f"> 基线: {base_sha} · 生成: {timestamp}")
    md_lines.append("")

    # 统计摘要
    md_lines.append("## 统计摘要")
    md_lines.append("")
    md_lines.append(f"- 共 **{stats['total']}** 个缺陷"
                    f"（open {by_status.get('open', 0)} / "
                    f"fixed {by_status.get('fixed', 0)} / "
                    f"reopened {by_status.get('reopened', 0)}）")
    md_lines.append(f"- 严重度: 高 {by_severity.get('high', 0)} / "
                    f"中 {by_severity.get('mid', 0)} / "
                    f"低 {by_severity.get('low', 0)}")
    md_lines.append(f"- 类型: 编译 {by_cat.get('compile', 0)} / "
                    f"运行 {by_cat.get('runtime', 0)} / "
                    f"逻辑 {by_cat.get('logic', 0)} / "
                    f"待排查 {by_cat.get('manual', 0)}")
    md_lines.append(f"- 影响: {stats['affected_methods']} 个方法 / {stats['affected_classes']} 个类")
    md_lines.append("")

    # 高危
    md_lines.append(f"## 🔴 高危 ({len(high_open)})")
    md_lines.append("")
    if high_open:
        md_lines.append("| 类 | 方法 | 用例 | 文件:行 | 类型 | 证据 | 建议 |")
        md_lines.append("|----|------|------|---------|------|------|------|")
        md_lines.append(_render_md_table(high_open, report_dir_rel))
    else:
        md_lines.append("无")
    md_lines.append("")

    # 中危
    md_lines.append(f"## 🟡 中危 ({len(mid_open)})")
    md_lines.append("")
    if mid_open:
        md_lines.append("| 类 | 方法 | 用例 | 文件:行 | 类型 | 证据 | 建议 |")
        md_lines.append("|----|------|------|---------|------|------|------|")
        md_lines.append(_render_md_table(mid_open, report_dir_rel))
    else:
        md_lines.append("无")
    md_lines.append("")

    # 低危
    md_lines.append(f"## 🟢 低危 ({len(low_open)})")
    md_lines.append("")
    if low_open:
        md_lines.append("| 类 | 方法 | 用例 | 文件:行 | 类型 | 证据 | 建议 |")
        md_lines.append("|----|------|------|---------|------|------|------|")
        md_lines.append(_render_md_table(low_open, report_dir_rel))
    else:
        md_lines.append("无")
    md_lines.append("")

    # 已修复
    md_lines.append(f"## ✅ 已修复 ({fixed_count})")
    md_lines.append("")
    if fixed_list:
        md_lines.append("| 类 | 方法 | 用例 | 修复于 |")
        md_lines.append("|----|------|------|--------|")
        md_lines.append(_render_fixed_table(fixed_list))
    else:
        md_lines.append("无")
    md_lines.append("")

    # 下一步建议
    md_lines.append("## 下一步建议")
    md_lines.append("")
    md_lines.append(f"- 优先处理 {high_open_count} 个高危运行/编译缺陷（阻塞测试推进）")
    md_lines.append(f"- {manual_open_count} 个 needs_manual 需人工排查根因")
    md_lines.append(f"- {fixed_count} 个已修复记录保留，关注回归")
    md_lines.append("")

    md_content = "\n".join(md_lines)
    md_path = report_dir / "defects-summary.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"  📄 defects-summary.md → {md_path}")

    # ── 4. 打印统计到 stdout ──
    print(f"\n{'='*60}")
    print(f"Mode 5 · 源码缺陷导出")
    print(f"{'='*60}")
    print(f"  项目:     {project}")
    print(f"  基线:     {base_sha}")
    print(f"  总计:     {stats['total']} 个缺陷")
    print(f"  状态:     open={by_status.get('open',0)} fixed={by_status.get('fixed',0)} "
          f"reopened={by_status.get('reopened',0)} wontfix={by_status.get('wontfix',0)}")
    print(f"  严重度:   high={by_severity.get('high',0)} mid={by_severity.get('mid',0)} low={by_severity.get('low',0)}")
    print(f"  类型:     compile={by_cat.get('compile',0)} runtime={by_cat.get('runtime',0)} "
          f"logic={by_cat.get('logic',0)} manual={by_cat.get('manual',0)}")
    print(f"  影响:     {stats['affected_methods']} 方法 / {stats['affected_classes']} 类")
    print(f"{'='*60}")


# ───────────────────────── main ─────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Mode 5: 源码缺陷导出与统计（upsert / mark-fixed / export）"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # ── upsert ──
    p_up = sub.add_parser("upsert", help="标红时增量写入缺陷记录")
    p_up.add_argument("--defects", required=True, help=".ut-defects.json 路径")
    p_up.add_argument("--defect-id", required=True, help="唯一缺陷 ID")
    p_up.add_argument("--method-qn", required=True, help="方法全限定名")
    p_up.add_argument("--method-name", required=True, help="方法名")
    p_up.add_argument("--class-qn", required=True, help="类全限定名")
    p_up.add_argument("--class-name", required=True, help="类名")
    p_up.add_argument("--module", required=True, help="所属模块")
    p_up.add_argument("--file-path", required=True, help="源文件路径")
    p_up.add_argument("--file-line", required=True, type=int, help="行号")
    p_up.add_argument("--test-fixture", required=True, help="测试夹具名")
    p_up.add_argument("--test-case-name", required=True, help="测试用例名")
    p_up.add_argument("--test-file", required=True, help="测试文件路径")
    p_up.add_argument("--type", required=True,
                      choices=["source_defect_runtime", "source_defect_compile",
                               "source_defect_logic", "needs_manual"],
                      help="缺陷类型")
    p_up.add_argument("--type-category", required=True,
                      choices=["compile", "runtime", "logic", "manual"],
                      help="类型分类")
    p_up.add_argument("--detected-at-stage", required=True, help="检测阶段 (compile/runtime)" )
    p_up.add_argument("--evidence", required=True, help="证据")
    p_up.add_argument("--suggestion", required=True, help="修复建议")
    p_up.add_argument("--root-cause-snippet", required=True, help="根因代码片段")
    p_up.add_argument("--method-level", required=True,
                      choices=["high", "mid", "low"],
                      help="方法重要等级")
    p_up.add_argument("--batch", required=True, type=int, help="批次号")
    p_up.add_argument("--base-sha", default=None, help="基线 commit SHA")
    p_up.add_argument("--project", default=None, help="项目名")
    p_up.add_argument("--repair-attempts", default=0, type=int, help="修复尝试次数")
    p_up.add_argument("--iteration-count", default=1, type=int, help="迭代次数")

    # ── mark-fixed ──
    p_fix = sub.add_parser("mark-fixed", help="用例通过时标记修复")
    p_fix.add_argument("--defects", required=True, help=".ut-defects.json 路径")
    p_fix.add_argument("--defect-id", default=None, help="缺陷 ID（单条标记）")
    p_fix.add_argument("--class", dest="cls", default=None, help="类名（批量标记该类下所有缺陷）")
    p_fix.add_argument("--fixed-in-sha", required=True, help="修复所在 commit SHA")

    # ── export ──
    p_ex = sub.add_parser("export", help="批量导出 defects.json + defects-summary.md")
    p_ex.add_argument("--project-dir", default=None, help="项目根目录（用于解析相对路径）")
    p_ex.add_argument("--defects", "--defects-file", required=True, help=".ut-defects.json 路径（--defects-file 为向后兼容别名）")
    p_ex.add_argument("--report-dir", "--output-dir", required=True, help="报告输出目录（--output-dir 为向后兼容别名）")
    p_ex.add_argument("--inventory", default=None, help=".ut-inventory.json 路径（预留）")

    args = ap.parse_args()

    if args.command == "upsert":
        cmd_upsert(args)
    elif args.command == "mark-fixed":
        cmd_mark_fixed(args)
    elif args.command == "export":
        cmd_export(args)


if __name__ == "__main__":
    main()
