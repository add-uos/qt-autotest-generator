#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 Uniontech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
compose-commit.py — Mode 2 code-committer §5「提交信息拼装」固化脚本

固化 code-committer.md §5 的纯模板渲染：从 classes_status 统计本批次/累计数据，
按 git-commit-workflow 的 test 类型 Log/Influence 格式生成提交信息到 stdout。
模型只负责最终 `git commit -F`，不再在上下文里数类、填模板。

模型保留职责（本脚本不做）：
  - 精确暂存（git add 哪些文件，code-committer §4）
  - staged diff 二次复核（§6）
  - 执行提交 + 后续流程（§7-§9）
  - 跳过 git-commit-workflow 人工确认的既定规则不变

输入：一个 JSON 文件（模型把内存变量 dump 出来），结构：
{
  "classes_status": [
    {"name": "Calculator", "status": "done", "methods_total": 9, "methods_tested": 9},
    {"name": "FileView", "status": "failed", "methods_total": 10, "methods_tested": 3},
    ...
  ],
  "batch_classes": ["Calculator"],            // 本批次目标类名列表
  "batch": 1,                                 // 批次号
  "baseline_commit": "abc123def",             // 基线 sha（可选，缺省占位）
  "branch_name": "main",                      // 分支名（可选）
  "project_name": "deepin-image-viewer",      // 项目名（可选；缺省时从 project_path 取 basename）
  "project_path": "/path/to/deepin-image-viewer",  // 项目路径（可选，project_name 缺省时用它推导）
  "test_dir": "autotests",                    // 测试目录名
  "pms_no": null,                             // 可选
  "issue_no": null                            // 可选
}

用法:
  python3 compose-commit.py --status-file status.json
  python3 compose-commit.py --status-file status.json --git-dir /path/to/project
  python3 compose-commit.py --status-file status.json -o commit-msg.txt

输出: 提交信息到 stdout（模型用 git commit -F <file> 或 -m "$(...)"）
      退出码 0=有 done 类可提交 / 2=无可提交类（跳过 commit）
"""

import argparse
import json
import os
import subprocess
import sys


def _short_sha(sha):
    return sha[:8] if sha else "unknown"


def _git_baseline_info(git_dir, sha):
    """用 git log 查 baseline 的 title 和 date。无 git/无 sha 时返回占位。"""
    if not sha:
        return "unknown", "unknown", "unknown"
    if not git_dir or not os.path.isdir(git_dir):
        # 无 git 访问时 title 用占位提示（模型应传 --git-dir 取真实 title）
        return _short_sha(sha), "(no --git-dir)", "unknown"
    try:
        title = subprocess.check_output(
            ["git", "-C", git_dir, "log", "-1", "--format=%s", sha],
            stderr=subprocess.DEVNULL).decode().strip()
        date = subprocess.check_output(
            ["git", "-C", git_dir, "log", "-1", "--format=%ad", "--date=short", sha],
            stderr=subprocess.DEVNULL).decode().strip()
        return _short_sha(sha), title, date
    except (subprocess.CalledProcessError, FileNotFoundError):
        return _short_sha(sha), "(git log failed)", "unknown"


def compute_stats(status_data):
    """从 classes_status + batch_classes 计算本批次/累计统计。"""
    classes_status = status_data.get("classes_status", [])
    batch_set = set(status_data.get("batch_classes", []))

    batch_done = [c for c in classes_status
                  if c.get("name") in batch_set and c.get("status") == "done"]
    batch_total = sum(1 for c in classes_status if c.get("name") in batch_set)
    batch_methods = sum(c.get("methods_total", 0) for c in batch_done)
    batch_tested = sum(c.get("methods_tested", 0) for c in batch_done)
    batch_classes_str = ", ".join(c.get("name", "?") for c in batch_done)

    all_done = [c for c in classes_status if c.get("status") == "done"]
    cumulative_classes = len(all_done)
    cumulative_total = len(classes_status)
    cumulative_methods = sum(c.get("methods_total", 0) for c in all_done)
    cumulative_tested = sum(c.get("methods_tested", 0) for c in all_done)

    return {
        "batch_done": batch_done,
        "batch_done_count": len(batch_done),
        "batch_total": batch_total,
        "batch_methods": batch_methods,
        "batch_tested": batch_tested,
        "batch_classes_str": batch_classes_str,
        "cumulative_classes": cumulative_classes,
        "cumulative_total": cumulative_total,
        "cumulative_methods": cumulative_methods,
        "cumulative_tested": cumulative_tested,
    }


def _derive_project_name(status_data):
    """项目名：优先 project_name；否则从 project_path 取 basename（对齐 code-committer §5）。"""
    name = status_data.get("project_name")
    if name:
        return name
    pp = status_data.get("project_path", "")
    if pp:
        return pp.rstrip('/').split('/')[-1]
    return "project"


def compose_message(status_data, stats, git_dir):
    """渲染提交信息（code-committer.md §5 模板）。"""
    project = _derive_project_name(status_data)
    test_dir = status_data.get("test_dir", "autotests")
    branch = status_data.get("branch_name", "unknown")
    baseline = status_data.get("baseline_commit", "")
    batch = status_data.get("batch", 1)

    short, title, date = _git_baseline_info(git_dir, baseline)

    # 标题行 ≤ 80 字符
    title_line = (f"test: add {test_dir} for {project} batch {batch} "
                  f"({stats['cumulative_classes']}/{stats['cumulative_total']} classes)")
    if len(title_line) > 80:
        title_line = title_line[:77] + "..."

    lines = [
        title_line,
        "",
        "Generated by qt-autotest-generator skill.",
        f"Batch {batch}: {stats['batch_classes_str']}",
        (f"Cumulative: {stats['cumulative_classes']}/{stats['cumulative_total']} classes, "
         f"{stats['cumulative_tested']}/{stats['cumulative_methods']} methods tested"),
        f'Baseline: {branch} @ {short} "{title}" ({date})',
        "",
        f"Log: 新增 {project} 单元测试",
        (f"Influence: 新增 {stats['batch_done_count']} 个类的单元测试，"
         f"本批次覆盖率 {stats['batch_tested']}/{stats['batch_methods']}，"
         f"累计覆盖率 {stats['cumulative_tested']}/{stats['cumulative_methods']}"),
    ]

    pms = status_data.get("pms_no")
    issue = status_data.get("issue_no")
    if pms:
        lines.append(f"PMS: {pms}")
    if issue:
        lines.append(f"Issue: {issue}")

    # body 行 ≤ 80 字符（截断超长行）
    result = [lines[0]]
    for line in lines[1:]:
        result.append(line if len(line) <= 80 else line[:77] + "...")
    return "\n".join(result)


def main_no_exit(argv=None):
    ap = argparse.ArgumentParser(
        description="Mode 2 code-committer §5 固化：提交信息拼装")
    ap.add_argument("--status-file", "-s", required=True,
                    help="classes_status JSON 文件路径")
    ap.add_argument("--git-dir", default=None,
                    help="git 仓库路径，提供时用 git log 查 baseline title/date")
    ap.add_argument("--output", "-o", default=None,
                    help="写提交信息到文件（默认只 stdout）")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.status_file):
        print(f"[COMMIT] error: status file not found: {args.status_file}")
        return 2
    try:
        with open(args.status_file, encoding="utf-8") as f:
            status_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[COMMIT] error: invalid JSON: {e}")
        return 2

    stats = compute_stats(status_data)

    if stats["batch_done_count"] == 0:
        print(f'[COMMIT] no done classes in batch {status_data.get("batch", "?")} '
              f'(skip commit)')
        return 2

    msg = compose_message(status_data, stats, args.git_dir)
    print(msg)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(msg + "\n")
        print(f"\n[COMMIT] message written: {args.output}", file=sys.stderr)

    return 0


def main():
    sys.exit(main_no_exit())


if __name__ == "__main__":
    main()
