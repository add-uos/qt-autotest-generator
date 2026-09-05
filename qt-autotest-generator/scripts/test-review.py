#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 Uniontech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test-review.py — Mode 6 测试质量审查（Test Review）

对给定输入集合中的 GTest 测试做**只读质量审查**，产出 MD 审查报告 + 机读 JSON。
不生成/不修改测试代码、不改源码、不编译、不跑测试（独立只读审查模式）。

两种输入场景：
  A. commit 审查   --commit <sha> | <shaA>..<shaB>
     git diff-tree 找出变更测试文件 → git show 提取该版本内容到审查工作区
     （不 checkout、不动工作区），对快照审查；D（删除）文件跳过并记录。
  B. 未缓存测试    --uncached | --files f1 f2 ...
     扫描 test_dir 下 test_*.cpp 与 .ut-inventory.json 已登记集合做归一化差集
     （basename 命中即算已登记）；inventory 不存在时全部视为未缓存；
     或显式指定文件列表。

复用既有固化脚本（不重复造轮子），本脚本只做三件事：输入解析、编排、裁决+报告：
  1. 规范检查   self-check-structural.py（纯正则，无图谱依赖）→ structural JSON
  2. 分支白盒   mcp-scan.py extract-branches（需 MCP + inventory + 类已入册）
                不可用时该维度降级并在报告标注原因
  3. 数值评分   qt-autotest-scorer score.py（可选依赖）
                发现顺序：--scorer-path > $QTAG_SCORER_PATH > 仓库兄弟目录探测；
                找不到则降级为纯规则裁决

裁决模型：
  FAIL   任一 critical 规则（EMPTY_ASSERT / TRIVIAL_ASSERT / SOLE_NO_FATAL /
         SOLE_GMOCK_EXPECT / BRANCH_NOT_MAPPED / FN_COVERAGE_LT_100）
  WARN   无 critical 但有 error 级违规
  PASS   仅 warning 或干净
  ERROR  structural 检查本身失败（工具错误，不计入质量结论）

用法:
  # 场景 A：审查一次 commit（或区间）
  python3 test-review.py review --commit HEAD --repo /path/to/project -o .reports/

  # 场景 B：审查未入册测试
  python3 test-review.py review --uncached --repo /path/to/project
  python3 test-review.py review --files autotests/test_x.cpp autotests/test_y.cpp

  # 只解析输入、不跑审查（供人工确认目标清单）
  python3 test-review.py resolve --commit <sha> -o targets.json

  # 先 resolve 后 review（两步走）
  python3 test-review.py review --targets targets.json

退出码：默认恒 0（审查完成 ≠ 测试失败）；--strict 时存在 FAIL/ERROR 退出 1（可挂 CI 门禁）；
硬错误（仓库/commit/test_dir 无效、无审查目标）退出 2。
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime

VERSION = "1.0.0"
SKILL = "qt-autotest-generator"
MODE = "Mode 6 · 测试质量审查"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SELF_CHECK_SCRIPT = os.path.join(SCRIPT_DIR, "self-check-structural.py")
MCP_SCAN_SCRIPT = os.path.join(SCRIPT_DIR, "mcp-scan.py")

# 测试文件 basename 模式（本技能约定 test_<class>.cpp；兼容 GTest 通用 <class>_test.cpp）
# 测试文件 basename 匹配：test_*.cpp / ut_*.cpp / *_test.cpp（deepin 生态常用 ut_ 前缀）
TEST_FILE_RE = re.compile(r'(?:^|/)(?:(?:test|ut)_[^/]+\.cpp|[^/]+_test\.cpp)$')

# critical 规则（触发即 FAIL）。SOLE_GMOCK 兼容 scorer 的别名写法。
CRITICAL_RULES = {
    "EMPTY_ASSERT",          # 空断言：等于没测
    "TRIVIAL_ASSERT",        # 唯一断言为字面量布尔（EXPECT_TRUE(true)）：占位断言
    "SOLE_NO_FATAL",         # 唯一断言为 NO_FATAL_FAILURE：逻辑全错也过
    "SOLE_GMOCK_EXPECT",     # 纯 gMock 期望无传统断言：未验证 SUT 自身行为
    "SOLE_GMOCK",            # 同上（别名兼容）
    "BRANCH_NOT_MAPPED",     # 声明分支 < 真实分支：漏测
    "FN_COVERAGE_LT_100",    # 函数覆盖率 < 100%（Iron Law #3 hard gate）
}

# 规则 → (参考文档, 修复动作)，供改进建议路由到规范文档对应小节
RULE_ROUTES = {
    "EMPTY_ASSERT":            ("self-checker.md §2b", "补有效断言（返回值/状态/副作用），空断言等于没测"),
    "TRIVIAL_ASSERT":          ("self-checker.md §2b", "字面量布尔改为真实值断言（如 EXPECT_EQ(model.rowCount(), 1)）"),
    "SOLE_NO_FATAL":           ("self-checker.md §2b", "NO_FATAL_FAILURE 外补传统断言验证 SUT 行为"),
    "SOLE_GMOCK_EXPECT":       ("self-checker.md §2b", "gMock 期望之外补 EXPECT/ASSERT 验证 SUT 自身行为"),
    "SOLE_GMOCK":              ("self-checker.md §2b", "gMock 期望之外补 EXPECT/ASSERT 验证 SUT 自身行为"),
    "LOW_ASSERT":              ("self-checker.md §2b", "每用例 ≥2 条有效断言，多维度验证"),
    "SOLE_BOOL_ASSERT":        ("self-checker.md §2b", "布尔断言改为具体值断言（EXPECT_EQ）"),
    "TOO_FEW_SEGMENTS":        ("test-code-gen.md §用例命名", "按 {Feature}_{Scenario}_{ExpectedResult} 重命名"),
    "ROUND_BATCH":             ("test-code-gen.md §用例命名", "去掉用例名中的轮数/批次号"),
    "MEANINGLESS":             ("test-code-gen.md §用例命名", "用语义化命名替换 Test1/无意义名"),
    "MISSING_DECL":            ("test-code-gen.md §用例计数声明", "文件顶部补用例计数声明表格"),
    "BELOW_MIN_CASES":         ("test-code-gen.md §用例计数声明", "声明数与实际用例数对齐"),
    "MISSING_AAA":             ("self-checker.md §2", "补 // Arrange / // Act / // Assert 注释框架"),
    "EMPTY_AAA":               ("self-checker.md §2", "AAA 段不能为空，Arrange/Assert 段需有实际内容"),
    "STUB_NOT_CLEARED":        ("self-checker.md §4", "补 stub.clear()，避免桩状态泄漏到后续用例"),
    "STUB_CLEAR_NOT_IN_TEARDOWN": ("self-checker.md §4", "把 stub.clear() 移到 TearDown"),
    "HARDCODED_PATH":          ("self-checker.md §5b", "硬编码绝对路径改 QTemporaryDir/QDir::temp"),
    "ENV_UNBALANCED":          ("self-checker.md §5b", "qputenv 后必须在 TearDown 里 qunsetenv 还原"),
    "REAL_EXTERNAL_CALL":      ("self-checker.md §5b", "真实外部资源调用改 stub_ext 拦截"),
    "HOME_PATH_ACCESS":        ("self-checker.md §5b", "homePath/writableLocation 改临时目录隔离"),
    "MISSING_BRANCH_LIST":     ("self-checker.md §2c", "复杂方法补 // B1: cond → outcome 分支清单"),
    "BRANCH_NOT_MAPPED":       ("self-checker.md §2c", "声明分支 < 真实分支：用 MCP 反查补漏测分支用例"),
    "FN_COVERAGE_LT_100":      ("build-verifier.md", "函数覆盖率 < 100%：补未覆盖函数的用例（Iron Law #3）"),
}

# 检查类别 → (参考文档, 通用动作)，未知规则时按 check 字段兜底
CHECK_ROUTES = {
    "spdx":      ("self-checker.md §3", "补 SPDX 版权与许可证头"),
    "naming":    ("test-code-gen.md §用例命名", "用例命名改为 Feature_Scenario_ExpectedResult"),
    "assertion": ("self-checker.md §2b", "补强断言"),
    "aaa":       ("self-checker.md §2", "补 AAA 注释框架"),
    "structure": ("self-checker.md §5", "补 ::testing::Test 继承与 SetUp/TearDown"),
    "stub":      ("self-checker.md §4", "补 stub 清理"),
    "env":       ("self-checker.md §5b", "环境隔离整改"),
    "branch":    ("self-checker.md §2c", "分支白盒补测"),
}

VERDICT_EMOJI = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "ERROR": "💥"}


class ReviewError(Exception):
    """硬错误：仓库/commit/test_dir 无效、无审查目标等，退出码 2。"""


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ── 纯逻辑：文件识别与类名推断 ────────────────────────────────────────

def is_test_file(path):
    """basename 匹配 test_*.cpp / *_test.cpp（GTest 测试文件）。"""
    return bool(TEST_FILE_RE.search(path.replace("\\", "/")))


def class_hint_from_path(path):
    """test_calculator.cpp → Calculator；ut_bitbutton.cpp → Bitbutton；foo_test.cpp → Foo。

    去 test_/ut_ 前缀与 _test 后缀后按 _ 分段 capitalize（与 scorer 风格一致），
    仅作提示用，实际类名以文件内 class XTest / inventory 为准。
    """
    base = os.path.basename(path)
    stem = base[:-4] if base.endswith(".cpp") else base
    if stem.startswith("test_"):
        stem = stem[5:]
    elif stem.startswith("ut_"):
        stem = stem[3:]
    if stem.endswith("_test"):
        stem = stem[:-5]
    parts = [p for p in stem.split("_") if p]
    return "".join(p.capitalize() for p in parts) if parts else stem


# ── 纯逻辑：git 输出解析 ─────────────────────────────────────────────

def parse_commit_spec(spec):
    """解析 commit 规格：'abc' → (None,'abc')；'a..b'/'a...b' → ('a','b')。

    区间支持两点（差异）与三点（merge-base 差异），原样透传 git；
    非法输入（空、a..、..b、a..b..c）抛 ReviewError。
    """
    spec = (spec or "").strip()
    if not spec or spec.startswith(".") or spec.endswith(".") or " " in spec:
        raise ReviewError(f"非法 commit 规格: {spec!r}")
    m = re.match(r'^(.+?)\.{2,3}(.+)$', spec)
    if m:
        a, b = m.group(1), m.group(2)
        if ".." in a or ".." in b:
            raise ReviewError(f"非法 commit 区间: {spec!r}（应为 <shaA>..<shaB>）")
        return a, b
    return None, spec


def parse_name_status_z(raw):
    """解析 git diff-tree/diff -z --name-status 输出（NUL 分隔）。

    条目格式：'M\\0path'；'R100\\0old\\0new'（R/C 双路径，取最后一个为目标路径）。
    返回 [{"status": "M", "path": "p", "from": "old"|None}, ...]。
    """
    tokens = [t for t in raw.split("\0") if t != ""]
    out, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        m = re.match(r'^([ARMDCUTX])(\d*)$', tok)
        if not m:
            raise ReviewError(f"无法解析 name-status token: {tok!r}")
        status = m.group(1)
        n = 2 if status in ("R", "C") else 1
        if i + n > len(tokens):
            raise ReviewError(f"name-status 条目不完整: {status}（缺路径）")
        paths = tokens[i:i + n]
        i += n
        entry = {"status": status, "path": paths[-1], "from": paths[0] if n == 2 else None}
        out.append(entry)
    return out


def filter_test_changes(changes):
    """从 name-status 条目中筛出可审查的测试文件。

    返回 (review, skipped_deleted, n_non_test)：
      review          —— 非 D 且是测试文件的条目
      skipped_deleted —— D 状态的测试文件（内容不可取，跳过并记录）
      n_non_test      —— 非测试文件变更数（仅计数入元信息）
    """
    review, skipped, non_test = [], [], 0
    for c in changes:
        if is_test_file(c["path"]):
            if c["status"] == "D":
                skipped.append({"path": c["path"], "reason": "deleted（该 commit 删除了测试文件，无内容可审）"})
            else:
                review.append(c)
        else:
            non_test += 1
    return review, skipped, non_test


# ── git 边界（subprocess，仅 CLI 编排层调用）──────────────────────────

def run_git(repo, args):
    """跑 git 命令返回 stdout；失败抛 ReviewError（带 stderr 摘要）。"""
    cmd = ["git", "-C", repo] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReviewError(f"git 执行失败: {' '.join(args)}: {e}") from e
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ReviewError(f"git {' '.join(args)} 失败: {stderr[-1] if stderr else proc.returncode}")
    return proc.stdout


def git_name_status(repo, spec):
    """取 commit（或区间）的 name-status（-z NUL 分隔文本）。

    强制 --no-renames：rename 检测会把 D+A 折叠成 R，隐藏删除信号；
    审查场景需要显式 A/D/M 状态。
    """
    a, b = parse_commit_spec(spec)
    if a is None:
        return run_git(repo, ["diff-tree", "--no-commit-id", "--name-status",
                              "-r", "--root", "-m", "-z", "--no-renames", b]).decode("utf-8", "replace")
    return run_git(repo, ["diff", "--name-status", "-r", "-z", "--no-renames", a, b]).decode("utf-8", "replace")


def git_commit_meta(repo, sha):
    """单 commit 元信息：{sha, short, author, date, subject}。"""
    raw = run_git(repo, ["show", "-s", "--date=iso",
                         "--format=%H%x1f%an%x1f%ad%x1f%s", sha]).decode("utf-8", "replace").strip()
    parts = raw.split("\x1f")
    if len(parts) != 4:
        raise ReviewError(f"无法解析 commit 元信息: {sha}")
    return {"sha": parts[0], "short": parts[0][:8], "author": parts[1],
            "date": parts[2], "subject": parts[3]}


def git_range_meta(repo, a, b):
    """区间元信息：{range, commit_count}。"""
    raw = run_git(repo, ["rev-list", "--count", f"{a}..{b}"]).decode("utf-8", "replace").strip()
    return {"range": f"{a}..{b}", "commit_count": int(raw) if raw.isdigit() else None}


def git_show(repo, sha, path):
    """git show <sha>:<path> → bytes；路径不存在抛 ReviewError。"""
    return run_git(repo, ["show", f"{sha}:{path}"])


def extract_commit_files(repo, sha, paths, dest_dir):
    """把 commit 中的文件内容提取到 dest_dir（保持相对路径结构）。

    返回 (copied, errors)：copied = {path: 本地绝对路径}；errors = [{path, error}]。
    """
    copied, errors = {}, []
    for p in paths:
        norm_raw = p.replace("\\", "/")
        if norm_raw.startswith("/") or ".." in norm_raw.split("/"):
            errors.append({"path": p, "error": "路径非法，拒绝提取"})
            continue
        norm = norm_raw.lstrip("./")
        local = os.path.join(dest_dir, norm)
        try:
            content = git_show(repo, sha, p)
        except ReviewError as e:
            errors.append({"path": p, "error": str(e)})
            continue
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as f:
            f.write(content)
        copied[p] = os.path.abspath(local)
    return copied, errors


# ── 纯逻辑：uncached 解析 ────────────────────────────────────────────

def load_managed_files(inventory):
    """inventory methods[].test_files 的归一化登记集合。

    登记格式可能为仓库相对路径 / test_dir 相对路径 / 纯 basename，
    统一收入 basename 集合（宽松判定：basename 命中即算已登记，宁可漏报不误报）。
    """
    managed = set()
    for m in (inventory or {}).get("methods", []):
        for tf in m.get("test_files") or []:
            norm = tf.replace("\\", "/")
            managed.add(os.path.basename(norm))
            managed.add(norm)
    return managed


def collect_uncached(test_dir, managed):
    """扫描 test_dir 下的测试文件，返回未登记的相对路径列表（排序）。

    managed 传 basename 集合（见 load_managed_files）；None/空集 = 全部未登记。
    """
    uncached = []
    for root, _dirs, files in os.walk(test_dir):
        for fn in files:
            if not is_test_file(fn):
                continue
            if fn in (managed or set()):
                continue
            uncached.append(os.path.relpath(os.path.join(root, fn), test_dir).replace(os.sep, "/"))
    return sorted(uncached)


def find_test_dir(repo):
    """探测测试目录：优先 autotests/，其次 tests/（含测试文件才算命中）。"""
    for cand in ("autotests", "tests"):
        d = os.path.join(repo, cand)
        if os.path.isdir(d) and any(
                is_test_file(fn) for _r, _ds, fs in os.walk(d) for fn in fs):
            return cand
    return None


def discover_inventory(repo, test_dir):
    """探测 .ut-inventory.json：显式 test_dir 优先，其次 autotests/、tests/。"""
    candidates = []
    if test_dir:
        candidates.append(os.path.join(repo, test_dir, ".ut-inventory.json"))
    for cand in ("autotests", "tests"):
        candidates.append(os.path.join(repo, cand, ".ut-inventory.json"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# ── 目标清单构建（resolve 核心）──────────────────────────────────────

def _make_target(source_path, review_path, managed, git_status=None):
    return {
        "source_path": source_path,
        "review_path": os.path.abspath(review_path),
        "git_status": git_status,
        "managed": bool(managed),
        "class_hint": class_hint_from_path(source_path),
    }


def build_targets_commit(repo, spec, inventory=None, workspace=None):
    """场景 A：commit → 审查目标清单。返回 (targets_doc, label)。"""
    if not os.path.isdir(repo):
        raise ReviewError(f"仓库路径不存在: {repo}")
    a, b = parse_commit_spec(spec)
    meta = git_commit_meta(repo, b) if a is None else None
    range_meta = git_range_meta(repo, a, b) if a is not None else None
    changes = parse_name_status_z(git_name_status(repo, spec))
    review, skipped_deleted, non_test = filter_test_changes(changes)
    if not review:
        raise ReviewError(f"commit {spec} 中未发现可审查的测试文件"
                          f"（非测试文件变更 {non_test} 个，删除 {len(skipped_deleted)} 个）")

    label = meta["short"] if meta else f"{a[:8]}..{b[:8]}"
    ws = None
    if workspace:
        ws = os.path.join(workspace, label)
    copied, extract_errors = [], []
    if ws:
        copied_map, extract_errors = extract_commit_files(
            repo, b if a is None else b, [c["path"] for c in review], ws)
    else:
        copied_map = {}

    managed_set = load_managed_files(inventory) if inventory else set()
    targets = []
    for c in review:
        local = copied_map.get(c["path"])
        if local is None and ws:
            continue  # 提取失败的进 skipped
        targets.append(_make_target(c["path"], local or c["path"],
                                    os.path.basename(c["path"]) in managed_set,
                                    git_status=c["status"]))
    skipped = skipped_deleted + [{"path": e["path"], "reason": e["error"]} for e in extract_errors]
    doc = {
        "scenario": "commit", "generated_at": now_iso(), "repo": os.path.abspath(repo),
        "commit_spec": spec, "commit": meta, "range": range_meta,
        "test_dir": None, "inventory": None, "workspace": ws,
        "targets": targets, "skipped": skipped, "non_test_changes": non_test,
    }
    return doc, label


def build_targets_uncached(repo, test_dir=None, inventory=None, files=None):
    """场景 B：未缓存/显式文件 → 审查目标清单。返回 (targets_doc, label)。"""
    inv_path = inventory
    inv_data = None
    if inv_path and os.path.isfile(inv_path):
        with open(inv_path, encoding="utf-8") as f:
            inv_data = json.load(f)

    if files:
        targets, missing = [], []
        for f in files:
            ap = os.path.abspath(f)
            if not os.path.isfile(ap):
                missing.append(f)
                continue
            managed = os.path.basename(f) in (load_managed_files(inv_data) if inv_data else set())
            targets.append(_make_target(f, ap, managed))
        if missing:
            raise ReviewError(f"以下文件不存在: {', '.join(missing)}")
        if not targets:
            raise ReviewError("未提供有效的审查文件")
        doc = {"scenario": "files", "generated_at": now_iso(), "repo": os.path.abspath(repo),
               "commit_spec": None, "commit": None, "range": None, "test_dir": test_dir,
               "inventory": inv_path, "workspace": None, "targets": targets,
               "skipped": [], "non_test_changes": 0}
        return doc, "files"

    td = test_dir or find_test_dir(repo)
    if not td:
        raise ReviewError(f"未找到测试目录（探测过 {repo}/autotests、{repo}/tests）；"
                          f"可用 --test-dir 显式指定")
    td_abs = os.path.join(repo, td) if not os.path.isabs(td) else td
    if not os.path.isdir(td_abs):
        raise ReviewError(f"测试目录不存在: {td_abs}")
    managed = load_managed_files(inv_data) if inv_data else set()
    uncached = collect_uncached(td_abs, managed)
    if not uncached:
        raise ReviewError(f"{td_abs} 下未发现未登记的测试文件（全部已入册或有 none 匹配）")
    targets = [_make_target(os.path.join(td, rel), os.path.join(td_abs, rel),
                            os.path.basename(rel) in managed)
               for rel in uncached]
    doc = {"scenario": "uncached", "generated_at": now_iso(), "repo": os.path.abspath(repo),
           "commit_spec": None, "commit": None, "range": None, "test_dir": td,
           "inventory": inv_path, "workspace": None, "targets": targets,
           "skipped": [], "non_test_changes": 0}
    return doc, "uncached"


# ── 裁决与建议（纯逻辑）──────────────────────────────────────────────

def derive_verdict(structural, branch):
    """从 structural/branch JSON 推导裁决。

    返回 (verdict, critical_rules)：FAIL 有 critical；WARN 有 error；PASS 其余。
    structural 为 None（工具失败）时返回 (ERROR, [])，由调用方处理。
    """
    if structural is None:
        return "ERROR", []
    violations = list((structural or {}).get("violations") or [])
    violations += list((branch or {}).get("violations") or [])
    critical, errors = [], []
    for v in violations:
        if v.get("rule") in CRITICAL_RULES and v.get("severity") == "error":
            critical.append(v["rule"])
        elif v.get("severity") == "error":
            errors.append(v)
    if critical:
        return "FAIL", sorted(set(critical))
    if errors:
        return "WARN", []
    return "PASS", []


def merge_recommendations(structural, branch):
    """违规清单 → 去重排序的改进建议（路由到规范文档对应小节）。"""
    violations = list((structural or {}).get("violations") or [])
    violations += list((branch or {}).get("violations") or [])
    recs, seen = [], set()
    prio = {"P0": 0, "P1": 1, "P2": 2}

    def _rec(priority, rule, reference, action, case=None):
        key = (rule, case)
        if key in seen:
            return
        seen.add(key)
        recs.append({"priority": priority, "rule": rule,
                     "case": case, "reference": reference, "action": action})

    for v in violations:
        rule = v.get("rule") or v.get("check") or "UNKNOWN"
        check = v.get("check", "")
        if rule in CRITICAL_RULES and v.get("severity") == "error":
            priority = "P0"
        elif v.get("severity") == "error":
            priority = "P1"
        else:
            priority = "P2"
        reference, action = RULE_ROUTES.get(rule) or CHECK_ROUTES.get(
            check, ("self-checker.md", f"修复违规 {rule}"))
        _rec(priority, rule, reference, action, case=v.get("case"))
    recs.sort(key=lambda r: (prio.get(r["priority"], 9), r["rule"], r.get("case") or ""))
    return recs


# ── 工具编排边界（subprocess）────────────────────────────────────────

def find_scorer(explicit=None):
    """定位 qt-autotest-scorer 的 score.py（可选依赖）。

    顺序：--scorer-path > $QTAG_SCORER_PATH > 仓库兄弟目录探测。
    返回绝对路径或 None。
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("QTAG_SCORER_PATH")
    if env:
        candidates.append(env)
    candidates += [
        os.path.join(SCRIPT_DIR, "..", "..", ".pi", "skills", "qt-autotest-scorer",
                     "scripts", "score.py"),
        os.path.join(SCRIPT_DIR, "..", "qt-autotest-scorer", "scripts", "score.py"),
        os.path.join(SCRIPT_DIR, "score.py"),
    ]
    for c in candidates:
        c = os.path.abspath(c)
        if os.path.isfile(c):
            return c
    return None


def run_structural(review_path, out_json):
    """跑 self-check-structural.py。返回 (data|None, error|None)。"""
    cmd = [sys.executable, SELF_CHECK_SCRIPT, "-f", review_path, "-o", out_json]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"self-check-structural 执行失败: {e}"
    if not os.path.isfile(out_json):
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, f"self-check-structural 未产出 JSON (rc={proc.returncode}): " + \
                     (tail[-1] if tail else "")
    with open(out_json, encoding="utf-8") as f:
        return json.load(f), None


def run_branch(review_path, inventory_path, project, mcp_url, out_json,
               repo_root=None):
    """跑 mcp-scan.py extract-branches（MCP 分支白盒反查）。

    返回 (status, data|None, reason)：status ∈ ok | skipped | failed。
    """
    if not inventory_path or not os.path.isfile(inventory_path):
        return "skipped", None, "无 .ut-inventory.json（分支白盒需 inventory 提供方法清单）"
    if not project:
        return "skipped", None, "未指定 --project 且 inventory 无 project 字段"
    cmd = [sys.executable, MCP_SCAN_SCRIPT, "extract-branches",
           "--project", project, "--test-file", review_path,
           "--inventory", inventory_path, "-o", out_json]
    if mcp_url:
        cmd += ["--mcp-url", mcp_url]
    if repo_root:
        cmd += ["--repo-root", repo_root]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "failed", None, f"extract-branches 执行失败: {e}"
    if os.path.isfile(out_json):
        with open(out_json, encoding="utf-8") as f:
            return "ok", json.load(f), None
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    # rc∈{0,1} 但无 JSON：多为类不在 inventory（未缓存文件的预期情形）
    return "failed", None, (f"extract-branches 未产出 JSON (rc={proc.returncode}): "
                            f"{tail[-1] if tail else '无输出'}")


def run_score(review_path, structural_json, branch_json, inventory_path,
              coverage, mutation, score_dir, scorer):
    """跑 qt-autotest-scorer score.py。返回 (status, data|None, reason)。

    status ∈ ok | disabled | not_found | failed。
    """
    if scorer is None:
        return "not_found", None, "未找到 score.py（scorer 为可选依赖；可 --scorer-path 指定）"
    cmd = [sys.executable, scorer, "-f", review_path, "-s", structural_json,
           "-o", score_dir]
    if branch_json and os.path.isfile(branch_json):
        cmd += ["-b", branch_json]
    if inventory_path and os.path.isfile(inventory_path):
        cmd += ["-i", inventory_path]
    if coverage:
        cmd += ["-c", coverage]
    if mutation:
        cmd += ["-m", mutation]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "failed", None, f"score.py 执行失败: {e}"
    candidates = sorted(glob.glob(os.path.join(score_dir, "scorecard-*.json")),
                        key=os.path.getmtime)
    if candidates:
        with open(candidates[-1], encoding="utf-8") as f:
            return "ok", json.load(f), None
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return "failed", None, (f"score.py 未产出 scorecard JSON (rc={proc.returncode}): "
                            f"{tail[-1] if tail else '无输出'}")


def summarize_score(score):
    """scorer scorecard JSON → 报告展示摘要（宽松摄取，字段缺失不炸）。"""
    if not isinstance(score, dict):
        return None
    dims = [{"name": d.get("name"), "score": d.get("score"),
             "weight": d.get("weight"), "status": d.get("status")}
            for d in (score.get("dimensions") or []) if isinstance(d, dict)]
    return {
        "grade": score.get("grade"), "score": score.get("score"),
        "pass": score.get("pass"), "raw_score": score.get("raw_score"),
        "capped_by": score.get("capped_by"),
        "triggered_hardgates": score.get("triggered_hardgates") or [],
        "min_pass": score.get("min_pass"), "dimensions": dims,
        "recommendations": score.get("recommendations") or [],
        "inputs_used": score.get("inputs_used") or {},
    }


# ── 聚合与报告渲染（纯逻辑）──────────────────────────────────────────

def review_targets(targets_doc, args_like):
    """编排：逐目标跑 structural → branch → score，聚合为报告 dict。

    args_like 需要属性：project/mcp_url/no_branch/no_scorer/scorer_path/
    coverage/mutation/outdir/inventory。纯编排层，I/O 只经 run_* 边界。
    """
    outdir = os.path.abspath(args_like.outdir)
    label = targets_doc.get("_label", targets_doc["scenario"])
    art_dir = os.path.join(outdir, "review-artifacts", label)
    os.makedirs(art_dir, exist_ok=True)

    inventory = targets_doc.get("inventory") or args_like.inventory
    project = getattr(args_like, "project", None)
    if not project and inventory and os.path.isfile(inventory):
        try:
            with open(inventory, encoding="utf-8") as f:
                project = (json.load(f) or {}).get("project")
        except (OSError, json.JSONDecodeError):
            project = None

    scorer = None if args_like.no_scorer else find_scorer(getattr(args_like, "scorer_path", None))
    degraded = []
    if args_like.no_branch:
        degraded.append("分支白盒：--no-branch 显式禁用")
    if args_like.no_scorer:
        degraded.append("数值评分：--no-scorer 显式禁用")
    elif scorer is None:
        degraded.append("数值评分：未找到 qt-autotest-scorer（可选依赖），降级为纯规则裁决")

    files = []
    for i, t in enumerate(targets_doc["targets"]):
        base = f"{i:02d}_{os.path.basename(t['review_path'])}"
        structural, struct_err = run_structural(
            t["review_path"], os.path.join(art_dir, base + ".structural.json"))
        branch, branch_data = None, None
        if structural is not None and not args_like.no_branch:
            bjson = os.path.join(art_dir, base + ".branch.json")
            bstatus, branch_data, breason = run_branch(
                t["review_path"], inventory, project, args_like.mcp_url, bjson,
                repo_root=getattr(args_like, "repo_root", None))
            branch = {"status": bstatus, "data": branch_data, "reason": breason}
            if bstatus != "ok":
                degraded.append(f"分支白盒[{t['source_path']}]: {breason}")
        score, score_data = None, None
        if structural is not None and not args_like.no_scorer:
            sjson = os.path.join(art_dir, base)
            sstatus, raw_score, sreason = run_score(
                t["review_path"], os.path.join(art_dir, base + ".structural.json"),
                os.path.join(art_dir, base + ".branch.json")
                if branch and branch["status"] == "ok" else None,
                inventory, args_like.coverage, args_like.mutation, sjson, scorer)
            score = {"status": sstatus, "reason": sreason}
            if sstatus == "ok":
                score_data = summarize_score(raw_score)
                score["summary"] = score_data
            else:
                degraded.append(f"数值评分[{t['source_path']}]: {sreason}")

        verdict, critical = derive_verdict(structural, branch_data)
        violations = list((structural or {}).get("violations") or [])
        violations += list((branch_data or {}).get("violations") or [])
        n_err = sum(1 for v in violations if v.get("severity") == "error"
                    and v.get("rule") not in critical)
        n_warn = sum(1 for v in violations if v.get("severity") == "warning")
        files.append({
            "source_path": t["source_path"], "review_path": t["review_path"],
            "git_status": t.get("git_status"), "managed": t.get("managed"),
            "class_hint": t.get("class_hint"),
            "test_case_count": (structural or {}).get("test_case_count"),
            "verdict": verdict, "critical": critical,
            "error_count": n_err, "warning_count": n_warn,
            "structural": structural, "structural_error": struct_err,
            "branch": branch, "score": score,
            "recommendations": merge_recommendations(structural, branch_data),
        })

    summary = {
        "total": len(files),
        "pass": sum(1 for f in files if f["verdict"] == "PASS"),
        "warn": sum(1 for f in files if f["verdict"] == "WARN"),
        "fail": sum(1 for f in files if f["verdict"] == "FAIL"),
        "error": sum(1 for f in files if f["verdict"] == "ERROR"),
        "scored": sum(1 for f in files if f["score"] and f["score"]["status"] == "ok"),
        "branch_ok": sum(1 for f in files if f["branch"] and f["branch"]["status"] == "ok"),
    }
    report = {
        "tool": f"{SKILL} {MODE}", "version": VERSION,
        "generated_at": now_iso(),
        "meta": {
            "scenario": targets_doc["scenario"], "repo": targets_doc["repo"],
            "commit_spec": targets_doc.get("commit_spec"),
            "commit": targets_doc.get("commit"), "range": targets_doc.get("range"),
            "test_dir": targets_doc.get("test_dir"), "inventory": inventory,
            "workspace": targets_doc.get("workspace"),
            "project": project, "scorer": scorer, "readonly": True,
            "caveats": [
                "分支白盒使用图谱当前状态的源码分支，与 commit 时刻可能存在漂移（未 push 场景必然）",
            ] if targets_doc["scenario"] == "commit" else [],
        },
        "summary": summary, "degraded": sorted(set(degraded)),
        "skipped": targets_doc.get("skipped") or [],
        "non_test_changes": targets_doc.get("non_test_changes", 0),
        "files": files,
    }
    return report


def render_report_md(report):
    """报告 dict → Markdown 审查报告（纯函数，供单测断言）。"""
    meta, summary = report["meta"], report["summary"]
    label = meta.get("commit_spec") or meta.get("scenario")
    lines = [
        f"# 单元测试质量审查报告（{MODE}）",
        "",
        f"> 生成时间：{report['generated_at']} ｜ 工具：{report['tool']} v{report['version']}",
        f"> 场景：**{meta['scenario']}** ｜ 审查对象：`{label}` ｜ 仓库：`{meta['repo']}`",
        f"> 模式：**只读审查**（未修改/生成任何测试或源码，未编译未运行）",
    ]
    if meta.get("commit"):
        c = meta["commit"]
        lines.append(f"> Commit：`{c['short']}` {c['subject']}（{c['author']}，{c['date']}）")
    if meta.get("range"):
        lines.append(f"> 区间：`{meta['range']['range']}`（{meta['range']['commit_count']} commits）")

    lines += ["", "## 1. 总览", ""]
    if summary["total"] == 0:
        lines.append("无可审查目标。")
    else:
        lines += [
            "| 测试文件 | 状态 | 用例数 | 错误 | 警告 | 规则裁决 | 评分（等级） |",
            "|---|---|---|---|---|---|---|",
        ]
        for f in report["files"]:
            emoji = VERDICT_EMOJI.get(f["verdict"], "")
            score_txt = "—"
            s = (f.get("score") or {}).get("summary")
            if s and s.get("grade"):
                flag = "" if s.get("pass") else "（不合格）"
                score_txt = f"{s['score']}（{s['grade']}）{flag}"
            lines.append(
                f"| `{f['source_path']}` | {f.get('git_status') or '-'} "
                f"| {f.get('test_case_count') if f.get('test_case_count') is not None else '-'} "
                f"| {f['error_count']} | {f['warning_count']} "
                f"| {emoji} {f['verdict']} | {score_txt} |")
        lines.append("")
        lines.append(f"**裁决分布**：PASS {summary['pass']} · WARN {summary['warn']} · "
                     f"FAIL {summary['fail']} · ERROR {summary['error']}（共 {summary['total']}）"
                     f" ｜ 已评分 {summary['scored']} ｜ 分支白盒可用 {summary['branch_ok']}")

    lines += ["", "## 2. 逐文件明细", ""]
    for i, f in enumerate(report["files"], 1):
        emoji = VERDICT_EMOJI.get(f["verdict"], "")
        lines.append(f"### 2.{i} `{f['source_path']}` — {emoji} {f['verdict']}")
        lines.append("")
        info = [f"类：{f.get('class_hint') or '-'}",
                f"用例数：{f.get('test_case_count') if f.get('test_case_count') is not None else '-'}",
                f"入册：{'是' if f.get('managed') else '否'}"]
        if f.get("git_status"):
            info.append(f"git 状态：{f['git_status']}")
        lines.append("- " + " ｜ ".join(info))
        if f["verdict"] == "ERROR":
            lines.append(f"- structural 检查失败：{f.get('structural_error')}")
        if f["critical"]:
            lines.append(f"- **critical 规则**：{', '.join(f['critical'])}")

        violations = list((f.get("structural") or {}).get("violations") or [])
        violations += list(((f.get("branch") or {}).get("data") or {}).get("violations") or [])
        if violations:
            lines += ["", "| 级别 | 规则 | 用例 | 行 | 说明 |", "|---|---|---|---|---|"]
            for v in violations:
                sev = v.get("severity", "")
                lines.append(f"| {sev} | {v.get('rule') or v.get('check')} "
                             f"| {v.get('case') or '-'} | {v.get('line') or '-'} "
                             f"| {v.get('message', '')} |")
        else:
            lines.append("- 违规：无")

        br = f.get("branch")
        if br:
            if br["status"] == "ok":
                n_checked = (br["data"] or {}).get("checked")
                lines.append(f"- 分支白盒：完成（核对方法数 {n_checked}）")
            else:
                lines.append(f"- 分支白盒：{br['status']}（{br['reason']}）")

        sc = f.get("score")
        if sc:
            if sc["status"] == "ok" and sc.get("summary"):
                s = sc["summary"]
                cap = f"，封顶规则 {s['capped_by']}" if s.get("capped_by") else ""
                hg = f"，硬门禁 {'/'.join(s['triggered_hardgates'])}" if s.get("triggered_hardgates") else ""
                lines.append(f"- 数值评分：**{s['score']}（{s['grade']}）**"
                             f"{'，合格' if s.get('pass') else '，**不合格**'}"
                             f"（合格线 {s.get('min_pass')}{cap}{hg}）")
                if s.get("recommendations"):
                    lines.append("  - 评分卡建议：")
                    for r in s["recommendations"]:
                        lines.append(f"    - [{r.get('priority')}] {r.get('dimension')}: "
                                     f"{r.get('action')}（路由 {r.get('route') or '-'}）")
            else:
                lines.append(f"- 数值评分：未接入（{sc.get('reason')}）")

        if f["recommendations"]:
            lines += ["", "改进建议：", ""]
            lines += ["| 优先级 | 规则 | 建议 | 路由 |", "|---|---|---|---|"]
            for r in f["recommendations"]:
                lines.append(f"| {r['priority']} | {r['rule']} | {r['action']} "
                             f"| `{r['reference']}` |")
        lines.append("")

    all_recs = []
    for f in report["files"]:
        all_recs += f["recommendations"]
    if all_recs:
        lines += ["## 3. 改进路由汇总", "",
                  "按优先级修复测试代码后，重跑本审查验证裁决变化：", ""]
        for p in ("P0", "P1", "P2"):
            group = [r for r in all_recs if r["priority"] == p]
            if group:
                rules = sorted({r['rule'] for r in group})
                refs = sorted({r['reference'] for r in group})
                lines.append(f"- **{p}**：{'、'.join(rules)} → {'、'.join('`'+x+'`' for x in refs)}")
        lines.append("")

    lines += ["## 附录", ""]
    if report["skipped"]:
        lines += ["**跳过清单**：", ""]
        lines += [f"- `{s['path']}`：{s['reason']}" for s in report["skipped"]]
        lines.append("")
    if report["degraded"]:
        lines += ["**降级说明**（数据源缺失，相应维度未参与裁决）：", ""]
        lines += [f"- {d}" for d in report["degraded"]]
        lines.append("")
    if report["non_test_changes"]:
        lines.append(f"非测试文件变更 {report['non_test_changes']} 个（未审查）。")
    if meta.get("caveats"):
        lines += ["**Caveats**："] + [f"- {c}" for c in meta["caveats"]]
    lines.append("")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────

def _add_input_args(p):
    p.add_argument("--repo", default=".", help="项目仓库根目录（默认当前目录）")
    p.add_argument("--test-dir", default=None,
                   help="测试目录名（默认探测 autotests/ → tests/）")
    p.add_argument("--inventory", default=None,
                   help=".ut-inventory.json 路径（默认自动探测）")
    p.add_argument("--commit", default=None, help="commit SHA 或 <shaA>..<shaB>（场景 A）")
    p.add_argument("--uncached", action="store_true", help="扫描未入册测试文件（场景 B）")
    p.add_argument("--files", nargs="+", default=None, help="显式指定测试文件（场景 B）")


def _resolve_inputs(args):
    """按 CLI 参数解析场景 → (targets_doc, label, inventory_path)。"""
    repo = os.path.abspath(args.repo)
    inventory = args.inventory
    if not inventory:
        td = args.test_dir or find_test_dir(repo)
        inventory = discover_inventory(repo, td)
    inv_data = None
    if inventory and os.path.isfile(inventory):
        with open(inventory, encoding="utf-8") as f:
            inv_data = json.load(f)
    else:
        inventory = None

    if args.targets:
        with open(args.targets, encoding="utf-8") as f:
            doc = json.load(f)
        label = doc.get("_label") or doc["scenario"]
        return doc, label, doc.get("inventory") or inventory
    if args.commit:
        doc, label = build_targets_commit(repo, args.commit, inv_data,
                                          workspace=getattr(args, "workspace", None))
    elif args.uncached or (args.files and not args.commit):
        doc, label = build_targets_uncached(repo, args.test_dir, inventory, args.files)
    else:
        raise ReviewError("未指定输入：需 --commit / --uncached / --files / --targets 之一")
    doc["inventory"] = doc.get("inventory") or inventory
    return doc, label, doc.get("inventory")


def cmd_resolve(args):
    doc, label, _inv = _resolve_inputs(args)
    doc["_label"] = label
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
        print(f"[RESOLVE] 场景={doc['scenario']} label={label} "
              f"目标 {len(doc['targets'])} 个，跳过 {len(doc['skipped'])} 个 → {args.out}")
    else:
        print(payload)
    return 0


def cmd_review(args):
    doc, label, inventory = _resolve_inputs(args)
    doc["_label"] = label
    doc["inventory"] = inventory
    args.outdir = os.path.abspath(args.outdir)
    if doc["scenario"] == "commit" and not doc.get("workspace"):
        # commit 场景需要工作区快照；两步走时 resolve 已提取，此处兜底
        doc["workspace"] = os.path.join(args.outdir, "review-workspace")
        repo, spec = doc["repo"], doc["commit_spec"]
        _a, _b = parse_commit_spec(spec)
        ws = os.path.join(doc["workspace"], label)
        copied, errors = extract_commit_files(repo, _b, [t["source_path"] for t in doc["targets"]], ws)
        for t in doc["targets"]:
            if t["source_path"] in copied:
                t["review_path"] = copied[t["source_path"]]
        if errors:
            failed_paths = {e["path"] for e in errors}
            doc["skipped"] = (doc.get("skipped") or []) + [
                {"path": e["path"], "reason": e["error"]} for e in errors]
            doc["targets"] = [t for t in doc["targets"]
                               if t["source_path"] not in failed_paths]
            if not doc["targets"]:
                raise ReviewError("commit 测试文件快照全部提取失败，无剩余可审查目标")
        doc["workspace"] = ws
    args.inventory = inventory
    report = review_targets(doc, args)
    report["meta"]["_label"] = label

    safe_label = label.replace(os.sep, "_").replace(" ", "_")
    md_path = os.path.join(args.outdir, f"test-review-{safe_label}.md")
    json_path = os.path.join(args.outdir, f"test-review-{safe_label}.json")
    os.makedirs(args.outdir, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_report_md(report))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    s = report["summary"]
    print(f"[REVIEW] 场景={report['meta']['scenario']} label={label} | "
          f"目标 {s['total']} · PASS {s['pass']} · WARN {s['warn']} · "
          f"FAIL {s['fail']} · ERROR {s['error']}")
    print(f"[REVIEW] 报告: {md_path}")
    print(f"[REVIEW] JSON : {json_path}")
    if args.strict and (s["fail"] or s["error"]):
        print("[REVIEW] --strict：存在 FAIL/ERROR，退出码 1", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="test-review.py", description=f"{MODE}（只读，不改测试/源码，不编译不运行）")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_res = sub.add_parser("resolve", help="输入解析 → 审查目标清单 JSON")
    _add_input_args(p_res)
    p_res.add_argument("--targets", default=None, help="（review 用）已解析的 targets.json")
    p_res.add_argument("--out", "-o", default=None, help="targets JSON 输出路径（缺省打印 stdout）")
    p_res.set_defaults(func=cmd_resolve)

    p_rev = sub.add_parser("review", help="编排审查 → MD + JSON 报告")
    _add_input_args(p_rev)
    p_rev.add_argument("--targets", default=None, help="resolve 产出的 targets.json（可选）")
    p_rev.add_argument("--outdir", "-o", default=".reports", help="报告输出目录（默认 .reports/）")
    p_rev.add_argument("--project", default=None, help="MCP 项目名（分支白盒；缺省取 inventory.project）")
    p_rev.add_argument("--mcp-url", default=None, help="MCP HTTP 端点（透传 extract-branches）")
    p_rev.add_argument("--repo-root", default=None,
                       help="仓库本地路径（透传 extract-branches；"
                            "缺省从测试文件 git 顶层推导）")
    p_rev.add_argument("--scorer-path", default=None, help="qt-autotest-scorer score.py 路径")
    p_rev.add_argument("--no-branch", action="store_true", help="跳过分支白盒维度")
    p_rev.add_argument("--no-scorer", action="store_true", help="跳过数值评分维度")
    p_rev.add_argument("--coverage", default=None, help="coverage_by_level.json（喂给 score.py，可选）")
    p_rev.add_argument("--mutation", default=None, help=".ut-mutation.json（喂给 score.py，可选）")
    p_rev.add_argument("--strict", action="store_true", help="存在 FAIL/ERROR 时退出码 1（CI 门禁）")
    p_rev.set_defaults(func=cmd_review)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except ReviewError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
