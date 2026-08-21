#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 Uniontech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
self-check-structural.py — Mode 2 self-checker 结构性检查固化脚本

固化 self-checker.md §2/§2b/§3/§4/§5/§5b 的纯文件正则检查（无图谱依赖）。
模型只消费违规清单决定改什么，不再回读自己的文件做正则——这是 Mode 2
逐类闭环中"模型回读自己刚写的文件"这一机械步骤的最大开销点。

模型保留职责（本脚本不做）：
  - 语义检查（断言名实相符、AAA 结构合理性、期望值正确性）
  - 方法名差集的图谱侧（拉全量方法需 MCP；本脚本只提供 extract_tested_names）
  - 副作用/返回值断言缺失的源码侧判断（需 trace_path 图谱查询）
  - 修复决策（违规 → 改哪里）

检查项（全部纯文件正则，对应 self-checker.md 章节）：
  spdx       — SPDX 头存在（§3）
  naming     — 命名规范 {Feature}_{Scenario}_{ExpectedResult} + 禁止轮数批次号（§2）
  assertion  — 断言强度：EMPTY/SOLE_NO_FATAL/SOLE_GMOCK/LOW_ASSERT/SOLE_BOOL（§2b）
  structure  — 继承 ::testing::Test、SetUp/TearDown 存在（§5）
  stub       — stub.clear() 存在且在 TearDown（§4/§5b）
  env        — 环境隔离：硬编码路径/env未还原/真实外部资源（§5b）

用法:
  python3 self-check-structural.py --file autotests/core/test_calculator.cpp
  python3 self-check-structural.py --file test_x.cpp --json     # 额外打印完整 JSON
  python3 self-check-structural.py --file test_x.cpp -o out.json # 写 JSON 到文件

输出摘要（stdout，退出码 0=全通过 / 1=有违规）:
  [CHECK] test_calculator.cpp | spdx:pass naming:pass assertion:fail structure:pass stub:pass env:pass | 15 errors, 3 warnings
    E1 assertion | LOW_ASSERT | Add_PositiveNumbers_ReturnsCorrectSum | line 26
    E2 assertion | LOW_ASSERT | Subtract_PositiveNumbers_ReturnsCorrectDifference | line 38
    W1 assertion | SOLE_BOOL_ASSERT | IsEmpty_InitialState_ReturnsTrue | line 118
"""

import argparse
import json
import os
import re
import sys

# ── 正则规则（self-checker.md 原样映射） ──────────────────────────────

SPDX_COPYRIGHT = re.compile(r'SPDX-FileCopyrightText:\s*\d{4}\s+UnionTech')
SPDX_LICENSE = re.compile(r'SPDX-License-Identifier:\s*GPL-3\.0-or-later')

ROUND_BATCH = re.compile(r'(R\d+|Round\d+|Batch\d+)', re.IGNORECASE)
MEANINGLESS_NAME = re.compile(r'^Test\d+$')

EXPECT_CALL_RE = re.compile(r'EXPECT_CALL')
NO_FATAL_RE = re.compile(r'EXPECT_NO_FATAL_FAILURE')
NO_THROW_RE = re.compile(r'EXPECT_NO_THROW')
EXPECT_BOOL_RE = re.compile(r'EXPECT_TRUE\(|EXPECT_FALSE\(')
EXPECT_ANY_RE = re.compile(r'EXPECT_')

INHERIT_TEST_RE = re.compile(r'class\s+\w+\s*:\s*public\s+::testing::Test')
SETUP_RE = re.compile(r'void\s+SetUp\s*\(\s*\)')
TEARDOWN_RE = re.compile(r'void\s+TearDown\s*\(\s*\)')

SET_LAMDA_RE = re.compile(r'stub\.set_lamda\s*\(')
STUB_CLEAR_RE = re.compile(r'stub\.clear\s*\(\)')

HARDCODED_PATH_RE = re.compile(r'"\/(?:home|tmp|usr|var|opt|etc|root)\/')
TEMP_PATH_OK_RE = re.compile(r'QTemporaryDir|QTemporaryFile|tempPath|QDir::temp')
QPUTENV_RE = re.compile(r'qputenv\s*\(')
QUNSETENV_RE = re.compile(r'qunsetenv\s*\(')
EXTERNAL_CALL_RE = re.compile(
    r'QProcess::start|::system\s*\(|popen\s*\(|'
    r'QNetworkAccessManager::(?:get|post)|QTcpSocket::connectToHost|\.connectToHost\s*\(|'
    r'QDateTime::currentDateTime|QTime::currentTime|'
    r'QRandomGenerator::system|\bsrand\s*\(|\bqsrand\s*\(')
STUB_HINT_RE = re.compile(r'stub\.set_lamda|__DBG_STUB_INVOKE__')

HOME_PATH_RE = re.compile(r'QDir::homePath\s*\(|QStandardPaths::writableLocation\s*\(')


# ── TEST_F/TEST_P 块切分（按大括号深度，等价 self-checker.md 的 awk） ──

def split_test_blocks(content):
    """切分 TEST_F/TEST_P(...) { ... } 块。

    返回 [{"fixture","case","body","start_line"}]。
    按大括号深度判定块边界；同一行的 { } 抵消（与 awk gsub 行为一致）。

    局限（与 awk 版相同）：字符串字面量内的 { } 会干扰深度计数，
    实际测试文件中罕见；异常时模型回读文件即可。
    """
    blocks = []
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        m = re.match(r'^\s*TEST_[FP]\s*\(\s*(\w+)\s*,\s*([^)\s]+)\s*\)', lines[i])
        if not m:
            i += 1
            continue
        fixture, case = m.group(1), m.group(2)
        start_line = i + 1
        body_lines = []
        depth = 0
        opened = False
        j = i
        while j < len(lines):
            for ch in lines[j]:
                if ch == '{':
                    depth += 1
                    opened = True
                elif ch == '}':
                    depth -= 1
            body_lines.append(lines[j])
            if opened and depth <= 0:
                break
            j += 1
        blocks.append({
            "fixture": fixture, "case": case,
            "body": '\n'.join(body_lines), "start_line": start_line,
        })
        i = j + 1
    return blocks


def extract_tested_names(content):
    """提取测试文件中已测方法名（用例名首段 PascalCase）。

    供方法名差集检查（§1a）：模型拿此集合与图谱全量方法做差集。
    本脚本不调图谱（MCP 依赖留给运行时），只做纯文件提取。
    """
    return {b["case"].split('_')[0] for b in split_test_blocks(content)}


# ── 各检查函数（返回 violation dict 列表） ────────────────────────────

def _v(check, severity, message, case=None, line=0, rule=None):
    d = {"check": check, "severity": severity, "message": message, "line": line}
    if case:
        d["case"] = case
    if rule:
        d["rule"] = rule
    return d


def check_spdx(content):
    """§3 SPDX 头：前 5 行必须有 Copyright + License 两行。"""
    head = '\n'.join(content.split('\n')[:5])
    v = []
    if not SPDX_COPYRIGHT.search(head):
        v.append(_v("spdx", "error", "SPDX-FileCopyrightText 头缺失", line=1))
    if not SPDX_LICENSE.search(head):
        v.append(_v("spdx", "error", "SPDX-License-Identifier 头缺失或非 GPL-3.0-or-later", line=1))
    return v


def check_naming(blocks):
    """§2 命名规范：≥2 下划线分段（Feature_Scenario_ExpectedResult）+ 禁轮数批次号。"""
    v = []
    for b in blocks:
        case = b["case"]
        sl = b["start_line"]
        if ROUND_BATCH.search(case):
            v.append(_v("naming", "error", f"用例名含轮数/批次号: {case}",
                        case=case, line=sl, rule="ROUND_BATCH"))
            continue
        if MEANINGLESS_NAME.search(case):
            v.append(_v("naming", "error", f"无意义用例名: {case}",
                        case=case, line=sl, rule="MEANINGLESS"))
            continue
        if len(case.split('_')) < 3:
            v.append(_v("naming", "error",
                        f"用例名分段不足（需 Feature_Scenario_ExpectedResult）: {case}",
                        case=case, line=sl, rule="TOO_FEW_SEGMENTS"))
    return v


def _classify_assert_line(line):
    """返回行内断言计数增量： (expect, nofatal, gmock, bool_only, other)。"""
    expect = nofatal = gmock = bool_only = other = 0
    if EXPECT_CALL_RE.search(line):
        gmock += 1
    if NO_FATAL_RE.search(line):
        nofatal += 1
    # 有效断言 = EXPECT_* 但排除 CALL/NO_FATAL/NO_THROW
    if EXPECT_ANY_RE.search(line) and not EXPECT_CALL_RE.search(line) \
            and not NO_FATAL_RE.search(line) and not NO_THROW_RE.search(line):
        expect += 1
        if EXPECT_BOOL_RE.search(line):
            bool_only += 1
        else:
            other += 1
    return expect, nofatal, gmock, bool_only, other


def check_assertion(blocks):
    """§2b 断言强度：每用例 ≥2 有效 EXPECT_*，排除 NO_FATAL/NO_THROW/EXPECT_CALL。"""
    v = []
    for b in blocks:
        body = b["body"]
        case = b["case"]
        sl = b["start_line"]
        expect = nofatal = gmock = bool_only = other = 0
        for line in body.split('\n'):
            e, nf, gm, bo, ot = _classify_assert_line(line)
            expect += e; nofatal += nf; gmock += gm; bool_only += bo; other += ot

        if expect == 0 and nofatal == 0 and gmock == 0:
            v.append(_v("assertion", "error", "空断言（无任何 EXPECT_*）",
                        case=case, line=sl, rule="EMPTY_ASSERT"))
        elif expect == 0 and nofatal > 0:
            v.append(_v("assertion", "error", "唯一断言为 EXPECT_NO_FATAL_FAILURE",
                        case=case, line=sl, rule="SOLE_NO_FATAL"))
        elif expect == 0 and gmock > 0:
            v.append(_v("assertion", "error", "纯 gMock 期望无传统断言",
                        case=case, line=sl, rule="SOLE_GMOCK_EXPECT"))
        elif expect < 2:
            v.append(_v("assertion", "error", f"有效断言不足（{expect}<2）",
                        case=case, line=sl, rule="LOW_ASSERT"))
        # SOLE_BOOL：唯一有效断言为布尔期望（可疑，warning）
        if bool_only > 0 and other == 0 and expect > 0:
            v.append(_v("assertion", "warning", "唯一有效断言为布尔期望（复核源码分支）",
                        case=case, line=sl, rule="SOLE_BOOL_ASSERT"))
    return v


def check_structure(content):
    """§5 结构：继承 ::testing::Test、SetUp/TearDown 存在。"""
    v = []
    if not INHERIT_TEST_RE.search(content):
        v.append(_v("structure", "error", "测试类未继承 ::testing::Test", line=1))
    if not SETUP_RE.search(content):
        v.append(_v("structure", "error", "缺少 SetUp() 定义", line=1))
    if not TEARDOWN_RE.search(content):
        v.append(_v("structure", "error", "缺少 TearDown() 定义", line=1))
    return v


def _teardown_body(content):
    """提取 TearDown() {...} 块体文本。无则返回 None。"""
    m = re.search(r'void\s+TearDown\s*\(\s*\)\s*(?:override\s*)?\{', content)
    if not m:
        return None
    depth = 1
    i = m.end()
    while i < len(content) and depth > 0:
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
        i += 1
    return content[m.end():i - 1] if depth == 0 else None


def check_stub(content):
    """§4/§5b stub：set_lamda 出现则必须有 clear；clear 应在 TearDown。"""
    v = []
    has_set = bool(SET_LAMDA_RE.search(content))
    has_clear = bool(STUB_CLEAR_RE.search(content))
    if has_set and not has_clear:
        v.append(_v("stub", "error", "stub.set_lamda 出现但无 stub.clear()",
                    line=1, rule="STUB_NOT_CLEARED"))
    if has_clear:
        td = _teardown_body(content)
        if td is None or not STUB_CLEAR_RE.search(td):
            v.append(_v("stub", "warning", "stub.clear() 不在 TearDown() 中",
                        line=1, rule="STUB_CLEAR_NOT_IN_TEARDOWN"))
    return v


def check_env(content):
    """§5b 环境隔离：硬编码绝对路径/env未还原/真实外部资源调用。"""
    v = []
    for i, line in enumerate(content.split('\n'), 1):
        if HARDCODED_PATH_RE.search(line) and not TEMP_PATH_OK_RE.search(line):
            v.append(_v("env", "error", f"硬编码绝对路径: {line.strip()[:60]}",
                        line=i, rule="HARDCODED_PATH"))
    put = len(QPUTENV_RE.findall(content))
    unset = len(QUNSETENV_RE.findall(content))
    if put != unset:
        v.append(_v("env", "error", f"qputenv/qunsetenv 不平衡（put={put}, unset={unset}）",
                    line=1, rule="ENV_UNBALANCED"))
    for i, line in enumerate(content.split('\n'), 1):
        if EXTERNAL_CALL_RE.search(line) and not STUB_HINT_RE.search(line):
            v.append(_v("env", "error", f"真实外部资源调用: {line.strip()[:60]}",
                        line=i, rule="REAL_EXTERNAL_CALL"))
    # §5b 用户目录访问（QDir::homePath / QStandardPaths）——仅标 warning
    for i, line in enumerate(content.split('\n'), 1):
        if HOME_PATH_RE.search(line):
            v.append(_v("env", "warning",
                        f"用户目录访问（确认已重定向到临时目录）: {line.strip()[:60]}",
                        line=i, rule="HOME_PATH_ACCESS"))
    return v


# ── AAA 结构检查正则 ────────────────────────────────────────────────

ARRIVE_RE = re.compile(r'//\s*Arrange')
ACT_RE = re.compile(r'//\s*Act')
ASSERT_COMMENT_RE = re.compile(r'//\s*Assert')

# ── 用例计数声明正则 ────────────────────────────────────────────────

# 匹配 "| method | level | factors | min | actual |" 表格行
DECL_ROW_RE = re.compile(
    r'\|\s*\S+\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|')


def check_aaa(blocks):
    """§4.1 AAA 结构：每个 TEST_F 必须包含 // Arrange / // Act / // Assert 三段注释。

    self-checker 可验证的结构性规则：缺少任一段注释即 MISSING_AAA 违规；
    空段（注释后无实质代码）报 EMPTY_AAA warning。
    """
    v = []
    for b in blocks:
        body = b["body"]
        case = b["case"]
        sl = b["start_line"]
        lines = body.split('\n')

        has_arrange = any(ARRIVE_RE.search(l) for l in lines)
        has_act = any(ACT_RE.search(l) for l in lines)
        has_assert = any(ASSERT_COMMENT_RE.search(l) for l in lines)

        missing = []
        if not has_arrange:
            missing.append("Arrange")
        if not has_act:
            missing.append("Act")
        if not has_assert:
            missing.append("Assert")

        if missing:
            v.append(_v("aaa", "error",
                        f"缺少 AAA 注释段: {', '.join(missing)}",
                        case=case, line=sl, rule="MISSING_AAA"))
        else:
            # 检查空段：// Arrange 后到 // Act 之间是否有实质代码行
            segs = _split_aaa_segments(lines)
            for seg_name, seg_lines in [("Arrange", segs[0]), ("Act", segs[1]), ("Assert", segs[2])]:
                # 实质行 = 非空、非纯注释、非仅大括号
                substantive = [l for l in seg_lines
                               if l.strip() and l.strip() not in ('{', '}')
                               and not l.strip().startswith('//')]
                if not substantive:
                    v.append(_v("aaa", "warning",
                                f"{seg_name} 段为空（无实质代码）",
                                case=case, line=sl, rule="EMPTY_AAA"))
    return v


def _split_aaa_segments(lines):
    """将用例体按 // Arrange / // Act / // Assert 分为三段。返回 [arrange_lines, act_lines, assert_lines]。"""
    arrange = act = assert_ = []
    current = 0  # 0=before arrange, 1=arrange, 2=act, 3=assert
    segs = [[], [], []]
    for line in lines:
        if ARRIVE_RE.search(line):
            current = 1
            continue
        if ACT_RE.search(line):
            current = 2
            continue
        if ASSERT_COMMENT_RE.search(line):
            current = 3
            continue
        if current >= 1:
            segs[current - 1].append(line)
    return segs


def check_usecase_decl(content):
    """§4 用例计数声明：模板要求文件顶部有 min/actual 表格，actual < min 报违规。

    这是纯文件检查：读取模板生成的声明表格，比较 min 和 actual 列。
    如果文件无声明表格（旧格式），仅报 MISSING_DECL warning。
    """
    v = []
    rows = DECL_ROW_RE.findall(content)
    if not rows:
        # 无声明表格——可能是旧格式文件，标 warning 提醒
        v.append(_v("assertion", "warning", "缺少用例计数声明表格（模板已内置）",
                    line=1, rule="MISSING_DECL"))
        return v
    for level, factors, min_s, actual_s in rows:
        min_n = int(min_s)
        actual_n = int(actual_s)
        method_name = "?"  # 正则未提取方法名，从违规消息看不出具体方法
        if actual_n < min_n:
            v.append(_v("assertion", "error",
                        f"用例数不足下限（actual={actual_n} < min={min_n}, level={level}）",
                        line=1, rule="BELOW_MIN_CASES"))
    return v


CHECK_NAMES = ["spdx", "naming", "assertion", "aaa", "structure", "stub", "env"]


def run_all_checks(content):
    """跑全部 7 类检查，返回 (violations, summary, blocks)。"""
    blocks = split_test_blocks(content)
    results = {
        "spdx": check_spdx(content),
        "naming": check_naming(blocks),
        "assertion": check_assertion(blocks) + check_usecase_decl(content),
        "aaa": check_aaa(blocks),
        "structure": check_structure(content),
        "stub": check_stub(content),
        "env": check_env(content),
    }
    all_v = []
    summary = {}
    for name in CHECK_NAMES:
        vs = results[name]
        all_v.extend(vs)
        has_err = any(v["severity"] == "error" for v in vs)
        has_warn = any(v["severity"] == "warning" for v in vs)
        summary[name] = "fail" if has_err else ("warn" if has_warn else "pass")
    return all_v, summary, blocks


def summarize(file_path, violations, summary):
    """stdout 摘要（人类可读）。"""
    parts = [f"{n}:{summary[n]}" for n in CHECK_NAMES]
    err = sum(1 for v in violations if v["severity"] == "error")
    warn = sum(1 for v in violations if v["severity"] == "warning")
    lines = [f"[CHECK] {os.path.basename(file_path)} | {' '.join(parts)} "
             f"| {err} errors, {warn} warnings"]
    ei = wi = 0
    for v in violations:
        tag = f"E{ei + 1}" if v["severity"] == "error" else f"W{wi + 1}"
        if v["severity"] == "error":
            ei += 1
        else:
            wi += 1
        rule = v.get("rule", v["check"])
        case = f" | {v['case']}" if v.get("case") else ""
        lines.append(f"  {tag} {v['check']} | {rule}{case} | line {v['line']}")
    return "\n".join(lines)


def main_no_exit(argv=None):
    ap = argparse.ArgumentParser(
        description="Mode 2 self-checker 结构性检查固化（纯正则，无图谱依赖）")
    ap.add_argument("--file", "-f", required=True, help="测试文件路径")
    ap.add_argument("--json", action="store_true", help="额外打印完整 JSON 到 stdout")
    ap.add_argument("--output", "-o", default=None, help="写 JSON 到文件")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.file):
        print(f"[CHECK] error: file not found: {args.file}")
        return 2

    with open(args.file, encoding="utf-8") as f:
        content = f.read()

    violations, summary, blocks = run_all_checks(content)

    report = {
        "file": os.path.abspath(args.file),
        "test_case_count": len(blocks),
        "tested_names": sorted(extract_tested_names(content)),
        "summary": summary,
        "violations": violations,
    }

    print(summarize(args.file, violations, summary))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    out = args.output
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[CHECK] report written: {out}")

    return 0 if not any(v["severity"] == "error" for v in violations) else 1


def main():
    sys.exit(main_no_exit())


if __name__ == "__main__":
    main()
