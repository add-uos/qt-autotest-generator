#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""mode2-ops.py — Mode 2 固化操作集合

合并自 plan-test-classes.py + update-usecase-count.py + compose-commit.py。
三个子命令：
  plan    — 确定待测类列表（原 plan-test-classes.py）
  usecase — 用例计数回写 inventory（原 update-usecase-count.py）
  commit  — 提交信息拼装（原 compose-commit.py）

各子命令的详细说明见对应章节注释。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter

# === 模块级常量 ===

# 来自 plan-test-classes.py
LEVEL_RANK = {"high": 3, "mid": 2, "low": 1}

# 来自 update-usecase-count.py
TEST_CASE_RE = re.compile(r'TEST_[FP]\s*\(\s*\w+\s*,\s*(\w+)\s*\)')


# === 共享工具 ===
# _field 和 _class_short_name 在 plan-test-classes.py 与 update-usecase-count.py
# 中重复定义，此处合并为唯一一份。

def _field(entry, *names, default=None):
    """防御式取字段：返回第一个非空值（兼容 schema 与存量字段名）。"""
    for n in names:
        v = entry.get(n)
        if v not in (None, ""):
            return v
    return default


def _class_short_name(class_qn):
    """class_qn（短名或全名）→ 类短名：取最后一段。"""
    return class_qn.rsplit(".", 1)[-1] if "." in class_qn else class_qn


# ============================================================================
# === plan 逻辑 (from plan-test-classes.py) ===
# ============================================================================
# plan-test-classes.py — Mode 2 test-writer §4「确定待测类列表」固化脚本
#
# 固化 test-writer.md §4 的纯数据变换：按 class_qn 分组 testable 方法 →
# 类 level 取方法最高级 → level_rank 排序（high → mid → low）→ is_gui 匹配 →
# 自由函数按 file_path 归组。模型只消费输出，不再在上下文里执行分组排序。
#
# 模型保留职责（本脚本不做）：
#   - 逐类闭环调度（依赖追踪/生成/验证/自检）
#   - stub 策略与用例设计
#   - 同名类合并到同一测试文件的例外判断（强耦合自由函数归并到某类，由 Agent 定）
#
# 字段兼容（实测存量数据与 schema 文档不一致，必须双兼容）：
#   - 全限定名：`qualified_name`（schema）或 `qn`（存量，如 deepin-image-viewer）
#   - 文件路径：`file_path`（schema）或 `file`（存量）
#   - `class_qn`：scan-inventory.py 产出**短名**（如 `ApplicationAdaptor`），
#     手写/旧数据可能是全名（如 `project.src.Calculator`）——统一取最后一段作类名
#
# 用法:
#   python3 mode2-ops.py plan --inventory autotests/.ut-inventory.json
#   python3 mode2-ops.py plan -i autotests/.ut-inventory.json --stdout   # 不落盘，只打印
#
# 输出:
#   {test_dir}/.reports/testable-classes.json — 模型消费的类清单
#   stdout 摘要（人类可读统计）
# ============================================================================


def _level(m):
    """方法 level；testable 但 level 缺失时按最低档处理（防御）。"""
    lv = _field(m, "level", default="low")
    return lv if lv in LEVEL_RANK else "low"


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


def plan_summarize(classes, free_groups):
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


def plan_main_no_exit(argv=None):
    """plan 子命令入口（原 plan-test-classes.py 的 main_no_exit）：接受 argv 列表，返回退出码而不调 sys.exit。"""
    ap = argparse.ArgumentParser(
        prog="mode2-ops.py plan",
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
        print(plan_summarize(classes, free_groups))
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    out = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.inventory)),
        ".reports", "testable-classes.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(plan_summarize(classes, free_groups))
    print(f"[PLAN] plan written: {out}")
    return 0


# ============================================================================
# === usecase 逻辑 (from update-usecase-count.py) ===
# ============================================================================
# update-usecase-count.py — Mode 2 test-writer §6「usecase_count 回写」固化脚本
#
# 固化 test-writer.md §6 的纯机械操作：每类编译通过后，扫描测试文件统计 TEST_F 用例数，
# 按方法名匹配（用例名首段 PascalCase vs 方法名 camelCase 小写归一化）增量写回
# inventory 的 usecase_count。模型不再在上下文里数 TEST_F、改 JSON。
#
# 模型保留职责（本脚本不做）：
#   - 决定何时回写（编译+自检通过后才回写，调度语义在模型侧）
#   - 覆盖率门禁判定（usecase_count 只是事实，门禁在 self-checker）
#
# 匹配规则（test-writer.md §6）：
#   - TEST_F(ClassNameTest, {MethodName_PascalCase}_{Scenario}_{Expected}) 用例名首段
#     转 lower 与 method.name lower 比对
#   - 只更新 testable=true 且 class_qn 匹配当前类的方法
#   - 匹配不到的方法保持原 usecase_count 不动（失败安全）
#   - 增量操作：只改当前类方法，不覆盖其他类数据
#
# 字段兼容（与 plan-test-classes.py 一致）：qualified_name|qn、file_path|file、
# class_qn 短名或全名（统一取最后一段作类名匹配）。
#
# 用法:
#   python3 mode2-ops.py usecase --test-file autotests/core/test_calculator.cpp \
#       --inventory autotests/.ut-inventory.json --class Calculator
#   python3 mode2-ops.py usecase ... --class-qn proj.src.Calculator  # 精确匹配（同名歧义时用）
#   python3 mode2-ops.py usecase ... --dry-run   # 只打印不写回
#
# 输出:
#   [COUNT] Calculator | test_calculator.cpp | 15 cases | 9 methods updated
#     add: 3 | subtract: 1 | multiply: 1 | divide: 2 | ...
#   inventory: autotests/.ut-inventory.json (written)
# ============================================================================


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


def usecase_summarize(class_short, test_file, total_cases, updated):
    """usecase 回写摘要（人类可读）。"""
    parts = [f"[COUNT] {class_short} | {os.path.basename(test_file)} | "
             f"{total_cases} cases | {len(updated)} methods updated"]
    if updated:
        detail = " | ".join(f"{u['name']}: {u['usecase_count']}" for u in updated)
        parts.append("  " + detail)
    return "\n".join(parts)


def usecase_main_no_exit(argv=None):
    """usecase 子命令入口（原 update-usecase-count.py 的 main_no_exit）：接受 argv 列表，返回退出码而不调 sys.exit。"""
    ap = argparse.ArgumentParser(
        prog="mode2-ops.py usecase",
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

    print(usecase_summarize(class_short, args.test_file, total_cases, updated))

    if args.dry_run:
        print(f"[COUNT] dry-run: inventory not written ({args.inventory})")
    else:
        with open(args.inventory, "w", encoding="utf-8") as f:
            json.dump(inventory, f, ensure_ascii=False, indent=2)
        print(f"inventory: {args.inventory} (written)")
    return 0


# ============================================================================
# === commit 逻辑 (from compose-commit.py) ===
# ============================================================================
# compose-commit.py — Mode 2 code-committer §5「提交信息拼装」固化脚本
#
# 固化 code-committer.md §5 的纯模板渲染：从 classes_status 统计本批次/累计数据，
# 按 git-commit-workflow 的 test 类型 Log/Influence 格式生成提交信息到 stdout。
# 模型只负责最终 `git commit -F`，不再在上下文里数类、填模板。
#
# 模型保留职责（本脚本不做）：
#   - 精确暂存（git add 哪些文件，code-committer §4）
#   - staged diff 二次复核（§6）
#   - 执行提交 + 后续流程（§7-§9）
#   - 跳过 git-commit-workflow 人工确认的既定规则不变
#
# 输入：一个 JSON 文件（模型把内存变量 dump 出来），结构：
# {
#   "classes_status": [
#     {"name": "Calculator", "status": "done", "methods_total": 9, "methods_tested": 9},
#     {"name": "FileView", "status": "failed", "methods_total": 10, "methods_tested": 3},
#     ...
#   ],
#   "batch_classes": ["Calculator"],            // 本批次目标类名列表
#   "batch": 1,                                 // 批次号
#   "baseline_commit": "abc123def",             // 基线 sha（可选，缺省占位）
#   "branch_name": "main",                      // 分支名（可选）
#   "project_name": "deepin-image-viewer",      // 项目名（可选；缺省时从 project_path 取 basename）
#   "project_path": "/path/to/deepin-image-viewer",  // 项目路径（可选，project_name 缺省时用它推导）
#   "test_dir": "autotests",                    // 测试目录名
#   "pms_no": null,                             // 可选
#   "issue_no": null                            // 可选
# }
#
# 用法:
#   python3 mode2-ops.py commit --status-file status.json
#   python3 mode2-ops.py commit --status-file status.json --git-dir /path/to/project
#   python3 mode2-ops.py commit --status-file status.json -o commit-msg.txt
#
# 输出: 提交信息到 stdout（模型用 git commit -F <file> 或 -m "$(...)"）
#       退出码 0=有 done 类可提交 / 2=无可提交类（跳过 commit）
# ============================================================================


def _short_sha(sha):
    """sha 前 8 位截断，空值返回占位。"""
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


def commit_main_no_exit(argv=None):
    """commit 子命令入口（原 compose-commit.py 的 main_no_exit）：接受 argv 列表，返回退出码而不调 sys.exit。"""
    ap = argparse.ArgumentParser(
        prog="mode2-ops.py commit",
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


# ============================================================================
# === 统一入口 ===
# ============================================================================

_SUBCOMMANDS = {
    "plan": plan_main_no_exit,
    "usecase": usecase_main_no_exit,
    "commit": commit_main_no_exit,
}


def main_no_exit(argv=None):
    """统一入口（子命令分发）。

    用法:
        mode2-ops.py plan    --inventory ...
        mode2-ops.py usecase --test-file ... --inventory ... --class ...
        mode2-ops.py commit  --status-file ...
    """
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: mode2-ops.py {plan|usecase|commit} ...\n")
        print("subcommands:")
        print("  plan    — 确定待测类列表（原 plan-test-classes.py）")
        print("  usecase — 用例计数回写 inventory（原 update-usecase-count.py）")
        print("  commit  — 提交信息拼装（原 compose-commit.py）")
        print("\nRun 'mode2-ops.py <subcommand> --help' for subcommand-specific options.")
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    handler = _SUBCOMMANDS.get(cmd)
    if handler is None:
        print(f"error: unknown subcommand '{cmd}'")
        print("usage: mode2-ops.py {plan|usecase|commit} ...")
        return 2
    return handler(rest)


def main():
    sys.exit(main_no_exit())


if __name__ == "__main__":
    main()
