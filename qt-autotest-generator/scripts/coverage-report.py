#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""coverage-report.py — Mode 3 覆盖率采集与分级统计

合并自 collect-coverage-report.py + coverage-by-level.py。

默认模式 — 一条命令完成：编译 → 运行测试 → lcov 采集 → genhtml → 分级覆盖率 → 汇总 JSON。
--level-only — 只跑分级统计（原 coverage-by-level.py 独立模式）。

产出目录结构（${REPORT_DIR}，默认模式）:
  ├── report/                   # gtest XML（运行前自动清理上次残留）
  │   └── report_<target>.xml
  ├── html/                     # lcov genhtml（每次重建，不残留陈旧文件）
  │   └── index.html            # genhtml 默认入口（稳定引用名）
  ├── coverage_by_level.json   # 分级覆盖率（来自分级统计逻辑）
  └── ut-summary.json          # 汇总 JSON（测试结果 + 总覆盖率 + 分级覆盖率 + warnings）

用法:
  # 全流程（自动探测 test target / build 目录）
  python3 scripts/coverage-report.py /path/to/project

  # 指定更多参数
  python3 scripts/coverage-report.py /path/to/project \\
      --build-dir build-ut --test-target my-test-binary \\
      --report-dir build-ut --inventory autotests/.ut-inventory.json

  # 跳过编译（仅重新采集覆盖率）
  python3 scripts/coverage-report.py /path/to/project --skip-build

  # 项目 CMake 已自行开启插桩时，关闭自动注入 --coverage
  python3 scripts/coverage-report.py /path/to/project --coverage-flags ''

  # 额外传 cmake 参数（可多次 --cmake-extra=...）
  python3 scripts/coverage-report.py /path/to/project --cmake-extra=-DBUILD_TESTS=ON

  # 源码不在 src/ 下的项目，自定义 lcov 提取模式
  python3 scripts/coverage-report.py /path/to/project --extract '*/lib/*,*/app/*'

  # 门禁失败时以非零退出码结束（CI 用）
  python3 scripts/coverage-report.py /path/to/project --fail-on-gate

  # 只跑分级统计（原 coverage-by-level.py）
  python3 scripts/coverage-report.py --level-only \\
      -i autotests/.ut-inventory.json \\
      -c build-autotests/coverage/filtered.info

  # per-class 分级覆盖率（逐类闭环，build-verifier/self-checker 用）
  python3 scripts/coverage-report.py --level-only \\
      -i autotests/.ut-inventory.json \\
      -c build-autotests/coverage/filtered.info \\
      --class IconButton --json

  # 每函数明细（定位缺口）
  python3 scripts/coverage-report.py --level-only -i ... -c ... --detail

分级统计说明:
  按 inventory 分级统计【函数级 + 行级 + 分支级】覆盖率：
  解析 FN/FNDA/DA/BRDA，c++filt demangle 后关联 inventory 分级，产出按 high/mid/low 的覆盖率。

  数据源:
    - lcov tracefile (coverage.info) : FN/FNDA(函数调用) + DA(行命中) + BRDA(分支命中)
    - .ut-inventory.json             : method 的 level / name / class_qn / file + gate_thresholds

  匹配链:
    file 后缀匹配 SF（多副本歧义时取最长路径并计数告警）
    -> 后缀失败时折叠路径中的连续重复段（如 src/src/ → src/）重试
    -> c++filt 批量 demangle FN -> 'class_qn::name(' 子串定位 method
    （处理重载：一个 method 可匹配多个 mangled FN，全部并入）
  行/分支覆盖率:
    按 FN 起始行 + 下一个 FN 起始行划分函数体区间，统计区间内 DA 行命中(>0 即覆盖)
    与 BRDA 分支命中(taken>0 即覆盖)。
    注: gcov/lcov 不直接给单函数行覆盖，区间法为业界通用近似(含 lambda/内联会有偏差；
    同一起始行的多个 FN 共享同一区间)。

退出码:
  0  成功（门禁未开或通过）
  1  测试失败 / 目标超时 / 过滤后覆盖率为空
  2  参数或输入文件错误 / 编译失败 / 未检测到覆盖率插桩产物
  3  --fail-on-gate 且门禁未通过（含 0 匹配）

依赖: cmake, lcov, genhtml, c++filt（缺失时友好报错；c++filt 解码失败时降级并告警）
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

SCRIPT_DIR = Path(__file__).resolve().parent

LEVELS = ["high", "mid", "low"]


# ═══════════════════════════════════════════════════════════════
# === 通用 helpers ===
# ═══════════════════════════════════════════════════════════════

def _fail(msg, code=2):
    """友好报错（不打 traceback），并以指定退出码结束。"""
    print(f"错误: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def _check_tool(tool):
    """启动前检查依赖工具是否存在。"""
    if shutil.which(tool) is None:
        _fail(f"缺少依赖工具: {tool}，请先安装（apt install ... / pip install ...）后重试")


def _check_coverage_instrumentation(build_dir):
    """编译后/运行前检查是否生成了 gcov 插桩产物（.gcno）。

    未找到任何 .gcno → 覆盖率采集必然为空（SF=0），在运行测试前直接失败，
    避免浪费一整轮 build + test。
    """
    gcnos = list(Path(build_dir).rglob("*.gcno"))
    if gcnos:
        print(f"  ✓ 检测到 {len(gcnos)} 个 .gcno 插桩产物，继续", flush=True)
        return
    _fail(
        f"未在 {build_dir} 下找到 .gcno 插桩产物，覆盖率采集将为空（SF=0）。\n"
        "      请确认: 1) 编译时是否传了 --coverage-flags；2) 项目 CMake 是否覆盖了 CMAKE_CXX_FLAGS；"
        "3) 项目已自行开启插桩时可用 --coverage-flags '' 关闭自动注入后重试。"
    )


def count_sf(info_path):
    """统计 lcov tracefile 中的 SF 记录数。"""
    n = 0
    with open(info_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("SF:"):
                n += 1
    return n


def run(cmd, **kw):
    """Run subprocess；失败时友好报错（不打 traceback）。"""
    sys.stdout.flush()
    sys.stderr.flush()
    display = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"  $ {display}", flush=True)
    try:
        return subprocess.run(cmd, check=True, shell=isinstance(cmd, str), **kw)
    except subprocess.CalledProcessError as exc:
        _fail(f"命令执行失败（退出码 {exc.returncode}）: {display}")


def run_capture(cmd, **kw):
    """Run subprocess, capture output, don't raise.（flush 保证输出顺序）"""
    sys.stdout.flush()
    sys.stderr.flush()
    return subprocess.run(cmd, capture_output=True, text=True, shell=isinstance(cmd, str), **kw)


def run_stream(cmd, **kw):
    """Run subprocess, 实时透传 stdout/stderr（不做捕获，适合长任务看进度）。"""
    sys.stdout.flush()
    sys.stderr.flush()
    display = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"  $ {display}", flush=True)
    return subprocess.run(cmd, shell=isinstance(cmd, str), **kw)


# ═══════════════════════════════════════════════════════════════
# === 分级覆盖率统计 (from coverage-by-level.py) ===
# ═══════════════════════════════════════════════════════════════

# inventory 字段兼容（schema 文档用 file_path/qualified_name，实际产出用 file/qn）
def m_file(m):
    return m.get("file_path") or m.get("file") or ""

def m_class(m):
    return m.get("class_qn") or ""

def m_name(m):
    return m.get("name") or ""


def _friendly_project_name(inv, inventory_path):
    """inventory 的 project 字段常是路径变体（如 home-uos-...-deepin-picker），
    优先从 inventory 所在目录推导（<proj>/autotests/.ut-inventory.json → <proj>）。"""
    p = Path(inventory_path).resolve()
    if p.parent.name in ("autotests", "tests"):
        return p.parent.parent.name
    return inv.get("project") or "?"


def parse_lcov(path):
    """解析 lcov tracefile -> {sf_path: {fns, fnda, da, brda}}

    - FN:  [(line, mangled)]
    - FNDA: {mangled: count}
    - DA:  {line: hits}，兼容带 checksum 的三段式 DA:line,hits[,checksum]
    - BRDA: {(line, block, branch): taken}，taken 为 int 或 None('-')；
      同一分支多次出现（合并场景）取最大命中数。

    无法解析的行跳过并汇总告警。
    """
    files = {}
    cur = None
    bad_lines = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip()
            if line.startswith("SF:"):
                cur = {"fns": [], "fnda": {}, "da": {}, "brda": {}}
                files[line[3:]] = cur
            elif cur is None:
                continue
            elif line.startswith("FN:"):
                try:
                    lnum, mangled = line[3:].split(",", 1)
                    cur["fns"].append((int(lnum), mangled))
                except ValueError:
                    bad_lines += 1
            elif line.startswith("FNDA:"):
                try:
                    cnt, mangled = line[5:].split(",", 1)
                    cur["fnda"][mangled] = int(cnt)
                except ValueError:
                    bad_lines += 1
            elif line.startswith("DA:"):
                parts = line[3:].split(",")
                try:
                    cur["da"][int(parts[0])] = int(parts[1])
                except (ValueError, IndexError):
                    bad_lines += 1
            elif line.startswith("BRDA:"):
                parts = line[5:].split(",")
                if len(parts) == 4:
                    try:
                        key = (int(parts[0]), parts[1], parts[2])
                        taken = None if parts[3] in ("-", "") else int(parts[3])
                        old = cur["brda"].get(key)
                        if old is None or (taken is not None and taken > (old or 0)):
                            cur["brda"][key] = taken
                    except ValueError:
                        bad_lines += 1
            elif line == "end_of_record":
                cur = None
    if bad_lines:
        print(f"Warning: {path} 有 {bad_lines} 行无法解析（已跳过）", file=sys.stderr, flush=True)
    return files


def demangle_batch(mangled_list):
    """批量 demangle；c++filt 缺失时友好报错，解码失败时降级为原样返回并告警。"""
    if not mangled_list:
        return {}
    try:
        p = subprocess.run(
            ["c++filt"], input="\n".join(mangled_list),
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        _fail("缺少依赖工具: c++filt（binutils），请安装后重试")
    if p.returncode != 0:
        print(f"Warning: c++filt 退出码 {p.returncode}，按未 demangle 名称匹配，"
              f"命中率可能下降", file=sys.stderr, flush=True)
        return {m: m for m in mangled_list}
    out = p.stdout.split("\n")
    return {m: (out[i] or m) for i, m in enumerate(mangled_list)}


def build_ranges(rec):
    """返回 [(start, end, mangled)]，end 为下一个不同起始行或文件最大行+1。

    同一起始行的多个 FN（内联/lambda 等）共享同一区间，避免空区间错位。
    """
    fns = sorted(set(rec["fns"]), key=lambda x: x[0])
    start_lines = sorted({ln for ln, _ in fns})
    da_lines = sorted(rec["da"].keys())
    brda_lines = sorted({k[0] for k in rec["brda"]})
    max_line = max(
        da_lines[-1] if da_lines else 0,
        brda_lines[-1] if brda_lines else 0,
        start_lines[-1] if start_lines else 0,
    )
    nxt = {ln: (start_lines[i + 1] if i + 1 < len(start_lines) else max_line + 1)
           for i, ln in enumerate(start_lines)}
    return [(ln, nxt[ln], mg) for ln, mg in fns]


def class_matches(m, kw):
    """--class 关键字匹配: class_qn 末尾段 == kw，或 class_qn == kw"""
    cls = m_class(m)
    if not cls:
        return False
    return cls == kw or cls.endswith("." + kw) or cls.split(".")[-1] == kw


def rate(a, b):
    """百分比（a/b*100），分母为 0 返回 0.0。"""
    return round(a / b * 100, 1) if b else 0.0


def gate_pass(lv, stat, gates):
    """分级门禁判定。

    - 该级别 0 个方法 → 视为通过（空级别门禁不适用，避免 0 方法误判为失败）
    - 函数覆盖率须达 100%（当 gate 定义 function 时）
    - 行覆盖率达阈值
    - 分支覆盖率达阈值（未采集到分支数据时不判定该项，branch_coverage=None）
    """
    s = stat[lv]
    if s["methods"] == 0:
        return True
    g = gates.get(lv, {})
    fn_ok = rate(s["fn_cov"], s["methods"]) >= 100 if g.get("function") else True
    line_th = g.get("line", 0) or 0
    line_ok = rate(len(s["cov_set"]), len(s["line_set"])) >= line_th
    branch_th = g.get("branch", 0) or 0
    if branch_th and s["branch_set"]:
        branch_ok = rate(len(s["branch_cov_set"]), len(s["branch_set"])) >= branch_th
    else:
        if branch_th:
            print(f"Warning: {lv} 级配置了分支门禁(>={branch_th}%)但未采集到分支数据，该项按通过处理",
                  file=sys.stderr, flush=True)
        branch_ok = True
    return fn_ok and line_ok and branch_ok


def compute_tiered_coverage(inventory_path, coverage_info, output_json=None,
                            class_name=None, detail=False, json_output=False,
                            project_name=None):
    """核心分级覆盖率统计逻辑（提取自 coverage-by-level.py main()）。

    参数:
      inventory_path - .ut-inventory.json 路径
      coverage_info  - lcov coverage.info / filtered.info 路径
      output_json    - JSON 写入路径（对应 -o/--output）
      class_name     - 只统计指定类（对应 --class）
      detail         - 打印每个 high/mid 函数的明细（对应 --detail）
      json_output    - 输出结构化 JSON（对应 --json）
      project_name   - 项目名覆盖（全流程模式传入，覆盖 inventory 推导）

    返回: 结果 dict（project / testable_methods / matched / by_level / total /
          unmatched / unmatched_breakdown / unmatched_top_files / uncovered_functions 等）。
    """
    # ── 输入校验（友好报错而非 traceback）──
    if not os.path.exists(inventory_path):
        _fail(f"inventory 文件不存在: {inventory_path}")
    if not os.path.exists(coverage_info):
        _fail(f"coverage 文件不存在: {coverage_info}")
    try:
        with open(inventory_path, encoding="utf-8") as f:
            inv = json.load(f)
    except json.JSONDecodeError as exc:
        _fail(f"inventory 不是合法 JSON: {inventory_path}: {exc}")
    if not isinstance(inv.get("methods"), list):
        _fail(f"inventory 缺少 methods 列表（顶层字段: {sorted(inv.keys())}）: {inventory_path}")

    methods = [m for m in inv["methods"]
               if m.get("testable") and m.get("level") in LEVELS]
    if class_name:
        methods = [m for m in methods if class_matches(m, class_name)]

    gates = inv.get("gate_thresholds", {})

    files = parse_lcov(coverage_info)
    if not files:
        print(f"Warning: {coverage_info} 不含任何源文件记录(SF=0)，全部方法将无法匹配",
              file=sys.stderr, flush=True)

    # 批量 demangle（c++filt 缺失/失败时降级，见 demangle_batch）
    all_mangled = {mg for rec in files.values() for _, mg in rec["fns"]}
    dem = demangle_batch(list(all_mangled))

    file_fns = {
        sf: [(ln, mg, dem.get(mg, mg)) for ln, mg in sorted(set(rec["fns"]), key=lambda x: x[0])]
        for sf, rec in files.items()
    }
    file_ranges = {sf: build_ranges(files[sf]) for sf in files}
    sf_list = list(files.keys())

    # SF 匹配: 后缀匹配，多副本歧义取最长路径并计数；
    # 后缀失败时折叠连续重复路径段（如 src/src/ → src/）重试。
    sf_cache = {}
    ambiguous = [0]
    dedup_hits = [0]

    def find_sf(inv_file):
        if not inv_file:
            return None
        if inv_file in sf_cache:
            return sf_cache[inv_file]
        matches = [sf for sf in sf_list if sf == inv_file or sf.endswith("/" + inv_file)]
        if matches:
            if len(matches) > 1:
                ambiguous[0] += 1
            best = max(matches, key=len)
        else:
            parts = [p for p in inv_file.split("/") if p]
            collapsed = [p for i, p in enumerate(parts) if i == 0 or parts[i - 1] != p]
            if len(collapsed) != len(parts):
                collapsed_file = "/".join(collapsed)
                matches = [sf for sf in sf_list
                           if sf == collapsed_file or sf.endswith("/" + collapsed_file)]
                if matches:
                    best = max(matches, key=len)
                    dedup_hits[0] += 1
                else:
                    best = None
            else:
                best = None
        sf_cache[inv_file] = best
        return best

    def fn_range(sf, mangled):
        for s, e, mg in file_ranges[sf]:
            if mg == mangled:
                return s, e
        return None

    stat = {lv: {"methods": 0, "fn_cov": 0,
                 "line_set": set(), "cov_set": set(),
                 "branch_set": set(), "branch_cov_set": set()}
            for lv in LEVELS + ["total"]}
    detail_rows = defaultdict(list)
    uncovered = []
    unmatched_no_sf = []
    unmatched_no_fn = []

    for m in methods:
        lv = m["level"]
        sf = find_sf(m_file(m))
        if not sf:
            unmatched_no_sf.append(m)
            continue
        name, cls = m_name(m), m_class(m)
        key = f"{cls}::{name}(" if cls else f"{name}("
        hits = [(ln, mg, dm) for ln, mg, dm in file_fns[sf] if key in dm]
        if not hits:
            unmatched_no_fn.append(m)
            continue

        stat[lv]["methods"] += 1
        stat["total"]["methods"] += 1
        covered = any(files[sf]["fnda"].get(mg, 0) > 0 for _, mg, _ in hits)
        if covered:
            stat[lv]["fn_cov"] += 1
            stat["total"]["fn_cov"] += 1
        else:
            uncovered.append(f"{cls}::{name}" if cls else name)

        line_pairs = set()
        branch_pairs = set()   # {(sf, line, block, branch, taken)}
        for _, mg, _ in hits:
            rng = fn_range(sf, mg)
            if not rng:
                continue
            s, e = rng
            for ln in files[sf]["da"]:
                if s <= ln < e:
                    line_pairs.add((sf, ln))
            for (bl, bb, bbr), taken in files[sf]["brda"].items():
                if s <= bl < e:
                    branch_pairs.add((sf, bl, bb, bbr, taken))

        for pair in line_pairs:
            stat[lv]["line_set"].add(pair)
            stat["total"]["line_set"].add(pair)
            if files[pair[0]]["da"][pair[1]] > 0:
                stat[lv]["cov_set"].add(pair)
                stat["total"]["cov_set"].add(pair)
        for sf_, bl, bb, bbr, taken in branch_pairs:
            bp = (sf_, bl, bb, bbr)
            stat[lv]["branch_set"].add(bp)
            stat["total"]["branch_set"].add(bp)
            if taken is not None and taken > 0:
                stat[lv]["branch_cov_set"].add(bp)
                stat["total"]["branch_cov_set"].add(bp)

        if detail and lv in ("high", "mid"):
            ln_total = len(line_pairs)
            ln_cov = sum(1 for p in line_pairs if files[p[0]]["da"][p[1]] > 0)
            br_total = len(branch_pairs)
            br_cov = sum(1 for x in branch_pairs if x[4] is not None and x[4] > 0)
            detail_rows[lv].append({
                "name": f"{cls}::{name}" if cls else name,
                "file": m_file(m),
                "covered": covered,
                "calls": [files[sf]["fnda"].get(mg, 0) for _, mg, _ in hits],
                "lines": ln_total,
                "lines_cov": ln_cov,
                "line_rate": (ln_cov / ln_total * 100) if ln_total else 0.0,
                "branches": br_total,
                "branches_cov": br_cov,
                "branch_rate": (br_cov / br_total * 100) if br_total else 0.0,
            })

    unmatched = unmatched_no_sf + unmatched_no_fn

    by_level = {}
    for lv in LEVELS:
        s = stat[lv]
        by_level[lv] = {
            "methods": s["methods"],
            "function_coverage": rate(s["fn_cov"], s["methods"]),
            "lines": len(s["line_set"]),
            "line_coverage": rate(len(s["cov_set"]), len(s["line_set"])),
            "branches": len(s["branch_set"]),
            "branch_coverage": (rate(len(s["branch_cov_set"]), len(s["branch_set"]))
                                if s["branch_set"] else None),
            "gate": gates.get(lv, {}),
            "pass": gate_pass(lv, stat, gates),
        }
    # 有 testable 方法却 0 匹配 → 门禁视为不通过（避免静默假绿）
    total_pass = (all(by_level[lv]["pass"] for lv in LEVELS)
                  and (stat["total"]["methods"] > 0 or not methods))
    total = {
        "methods": stat["total"]["methods"],
        "function_coverage": rate(stat["total"]["fn_cov"], stat["total"]["methods"]),
        "lines": len(stat["total"]["line_set"]),
        "line_coverage": rate(len(stat["total"]["cov_set"]), len(stat["total"]["line_set"])),
        "branches": len(stat["total"]["branch_set"]),
        "branch_coverage": (rate(len(stat["total"]["branch_cov_set"]), len(stat["total"]["branch_set"]))
                            if stat["total"]["branch_set"] else None),
        "pass": total_pass,
    }

    result = {
        "class": class_name,
        "project": project_name or _friendly_project_name(inv, inventory_path),
        "testable_methods": len(methods),
        "matched": len(methods) - len(unmatched),
        "by_level": by_level,
        "total": total,
        "unmatched": len(unmatched),
        "unmatched_breakdown": {
            "no_source_file": len(unmatched_no_sf),
            "no_function_record": len(unmatched_no_fn),
        },
        "unmatched_top_files": {
            "no_source_file": dict(Counter(m_file(m) for m in unmatched_no_sf).most_common(8)),
            "no_function_record": dict(Counter(m_file(m) for m in unmatched_no_fn).most_common(8)),
        },
        "sf_ambiguous_matches": ambiguous[0],
        "matched_via_dedup_path": dedup_hits[0],
        "uncovered_functions": uncovered,
    }

    if json_output:
        out = json.dumps(result, indent=2, ensure_ascii=False)
        if output_json:
            with open(output_json, "w", encoding="utf-8") as f:
                f.write(out)
            print(f"已写入: {output_json}", file=sys.stderr)
        else:
            print(out)
        return result

    # 人类可读汇总
    print(f"\n项目: {result['project']}"
          + (f"  | 类: {class_name}" if class_name else ""), flush=True)
    print(f"coverage: {coverage_info}", flush=True)
    print(f"inventory: {inventory_path}", flush=True)
    print(f"testable methods: {len(methods)}  匹配: {result['matched']}  未匹配: {len(unmatched)}",
          flush=True)
    print(flush=True)
    hdr = (f"{'level':<8}{'methods':>9}{'fn_cov':>9}{'fn_rate':>10}"
           f"{'lines':>9}{'line_rate':>10}{'brs':>6}{'br_rate':>9}{'gate':>6}")
    print(hdr, flush=True)
    print("-" * len(hdr), flush=True)
    for lv in LEVELS + ["total"]:
        s = stat[lv] if lv != "total" else stat["total"]
        fn_rate = rate(s["fn_cov"], s["methods"])
        line_rate = rate(len(s["cov_set"]), len(s["line_set"]))
        br_n = len(s["branch_set"])
        br_rate = f"{rate(len(s['branch_cov_set']), br_n):.1f}%" if br_n else "-"
        gate = "✓" if (by_level.get(lv, total)["pass"]) else "✗"
        print(f"{lv:<8}{s['methods']:>9}{s['fn_cov']:>9}{fn_rate:>9.1f}%"
              f"{len(s['line_set']):>9}{line_rate:>9.1f}%{br_n:>6}{br_rate:>9}{gate:>6}",
              flush=True)

    if unmatched_no_sf:
        print(f"\n⚠ 未在 coverage 中找到源文件(SF): {len(unmatched_no_sf)} 个", flush=True)
        print("  按文件:",
              dict(Counter(m_file(m) for m in unmatched_no_sf).most_common(8)), flush=True)
        if files:
            print("  提示: coverage.info 可能未包含项目源码（未 extract 过滤？请检查采集来源/--extract 模式）",
                  flush=True)
    if unmatched_no_fn:
        print(f"\n⚠ 源文件已匹配但函数未命中(FN 记录): {len(unmatched_no_fn)} 个", flush=True)
        print("  按文件:",
              dict(Counter(m_file(m) for m in unmatched_no_fn).most_common(8)), flush=True)
    if ambiguous[0]:
        print(f"\n⚠ SF 后缀歧义匹配 {ambiguous[0]} 次（多副本路径取最长）", flush=True)
    if dedup_hits[0]:
        print(f"\n⚠ {dedup_hits[0]} 个方法经路径折叠匹配（inventory 路径疑似含重复段，如 src/src/）",
              flush=True)
    if methods and result["matched"] == 0:
        print("\n⚠⚠ 全部方法未匹配！请检查 coverage.info 是否包含项目源码、inventory 路径是否正确",
              flush=True)

    if detail:
        for lv in ("high", "mid"):
            rows = sorted(detail_rows[lv], key=lambda r: (r["covered"], -r["line_rate"]))
            print(f"\n=== {lv} 函数明细 ({len(rows)}) ===", flush=True)
            print(f"{'covered':>8}  {'line_rate':>9}  {'br_rate':>9}  {'calls':>10}  function",
                  flush=True)
            for r in rows:
                tag = "✅" if r["covered"] else "❌"
                br = f"{r['branch_rate']:.1f}%" if r["branches"] else "-"
                print(f"{tag:>6}  {r['line_rate']:>8.1f}%  {br:>8}  {str(r['calls']):>10}"
                      f"  {r['name']}  [{r['file']}]", flush=True)

    if output_json:
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n汇总 JSON 已写入: {output_json}", flush=True)

    return result


# ═══════════════════════════════════════════════════════════════
# === 采集与汇总 (from collect-coverage-report.py) ===
# ═══════════════════════════════════════════════════════════════

# 测试二进制命名约定（deepin 常用 ut-<name>/ut_<name>，gtest 通用 test_/test-，
# 以及 xxx-test/xxx_test/xxx-ut/xxx_ut 后缀）
TEST_NAME_RE = re.compile(
    r"^(ut[-_].+|test[-_].+|tst[-_].+|.+[-_](?:test|ut))$", re.IGNORECASE)
TEST_PATH_EXCLUDE = ("CMakeFiles", ".git", "__pycache__")


def _looks_like_gtest(path):
    """best-effort 探测: --gtest_list_tests 退出码 0 视为 gtest 二进制。

    探测超时 → 保留（运行期由 --timeout 兜底）；探测失败/不可执行 → 排除。
    """
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        p = subprocess.run([str(path), "--gtest_list_tests"],
                           capture_output=True, text=True, timeout=15, env=env)
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ⚠ {path.name} --gtest_list_tests 探测超时，仍保留（运行期由 --timeout 兜底）",
              file=sys.stderr, flush=True)
        return True
    except OSError:
        return False


def _scan_test_candidates(build_dir):
    """按命名约定 + 可执行位扫描候选（不跑 --gtest_list_tests 探测）。

    覆盖命名约定: ut-*/ut_*、test-*/test_*、tst-*/tst_* 前缀及 *-test/*_test/*-ut/*_ut 后缀
    （deepin 常用 ut-<name>，此前 ut-* 命名会被整体漏掉）。
    排除: CMakeFiles/.git 等目录、非可执行文件。
    """
    seen = set()
    out = []
    for p in sorted(Path(build_dir).rglob("*")):
        if not p.is_file() or not os.access(str(p), os.X_OK):
            continue
        if not TEST_NAME_RE.match(p.name):
            continue
        if any(part in TEST_PATH_EXCLUDE for part in p.parts):
            continue
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(rp)
    return out


def find_test_targets(build_dir):
    """Find ALL gtest executables under build_dir.

    先按命名约定+可执行位扫描候选，再用 --gtest_list_tests 探测确认。
    Returns a sorted list of absolute paths to executable test binaries.
    """
    confirmed = [str(p) for p in _scan_test_candidates(build_dir) if _looks_like_gtest(p)]
    return sorted(confirmed)


def find_test_target(build_dir):
    """Find the gtest binary under build_dir (common patterns)."""
    targets = find_test_targets(build_dir)
    return targets[0] if targets else None


def parse_gtest_xml(xml_path):
    """Parse gtest JUnit XML → (total, passed, failed, disabled)。

    disabled 用例计入 total 但既不算通过也不算失败，单独统计。
    """
    total = passed = failed = disabled = 0
    try:
        root = ET.parse(xml_path).getroot()
        t = int(root.get("tests", 0))
        f = int(root.get("failures", 0))
        e = int(root.get("errors", 0))
        d = int(root.get("disabled", 0))
        total += t
        failed += f + e
        disabled += d
        passed += t - f - e - d
    except Exception as exc:
        print(f"Warning: failed to parse {xml_path}: {exc}", file=sys.stderr)
    return total, passed, failed, disabled


def parse_gtest_xml_suites(xml_path):
    """Parse gtest XML → list of {suite, tests, failures, errors, disabled, time}."""
    suites = []
    try:
        root = ET.parse(xml_path).getroot()
        for ts in root.iter("testsuite"):
            suites.append({
                "suite": ts.get("name", ""),
                "tests": int(ts.get("tests", 0)),
                "failures": int(ts.get("failures", 0)),
                "errors": int(ts.get("errors", 0)),
                "disabled": int(ts.get("disabled", 0)),
                "time": float(ts.get("time", 0)),
            })
    except Exception:
        pass
    return suites


def parse_lcov_summary(coverage_info):
    """Parse lcov --summary → {line_coverage, function_coverage, branch_coverage}。"""
    result = {}
    if not os.path.exists(coverage_info):
        return result
    p = run_capture(
        ["lcov", "--summary", coverage_info, "--rc", "lcov_branch_coverage=1"],
    )
    txt = p.stdout + p.stderr
    for kind, key in (("lines", "line_coverage"),
                      ("functions", "function_coverage"),
                      ("branches", "branch_coverage")):
        m = re.search(rf"{kind}.*?:\s*([\d.]+)%\s*\((\d+)\s+of\s+(\d+)", txt)
        if m:
            pct, hit, tot = m.groups()
            result[key] = {
                "total": int(tot), "passed": int(hit),
                "failed": int(tot) - int(hit), "coverage": f"{float(pct):.2f}%",
            }
    if "line_coverage" not in result:
        print(f"Warning: lcov --summary 未解析到行覆盖率（数据为空？）: {coverage_info}",
              file=sys.stderr, flush=True)
    return result


def run_coverage_by_level(inventory, coverage_info, output_json, project_name=None):
    """计算分级覆盖率（直接调用 compute_tiered_coverage）并返回结果 dict。"""
    if not os.path.exists(coverage_info):
        print(f"Warning: coverage info 不存在 ({coverage_info})，跳过分级覆盖率", file=sys.stderr)
        return None
    return compute_tiered_coverage(
        inventory, coverage_info,
        output_json=output_json, json_output=True,
        project_name=project_name,
    )


def _write_timeout_xml(xml_path, t_name, timeout):
    """为超时目标合成失败用例 XML，使其计入汇总（失败 1 例）。"""
    name = escape(t_name)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuites tests="1" failures="1" errors="0" disabled="0" time="{timeout}">'
        f'<testsuite name="{name}" tests="1" failures="1" errors="0" disabled="0" time="{timeout}">'
        f'<testcase name="timeout" classname="{name}" status="run" time="{timeout}">'
        f'<failure message="测试超时（>{timeout}s），进程被终止" type="Timeout"/>'
        '</testcase></testsuite></testsuites>\n'
    )
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)


# ───────────────────────── main ─────────────────────────

def main_no_exit(argv=None):
    ap = argparse.ArgumentParser(
        description="Mode 3: 覆盖率采集与分级统计（编译 → 运行 → lcov → 分级 → 汇总 JSON），"
                    "或 --level-only 只跑分级统计"
    )

    # ── 模式选择 ──
    ap.add_argument("--level-only", action="store_true",
                    help="只跑分级统计（原 coverage-by-level.py 独立模式）")

    # ── 分级统计参数（--level-only 模式，部分与全流程共享）──
    ap.add_argument("-i", "--inventory", default=None,
                    help=".ut-inventory.json 路径，默认自动探测 autotests/.ut-inventory.json 或 tests/.ut-inventory.json")
    ap.add_argument("-c", "--coverage", default=None,
                    help="lcov coverage.info / filtered.info 路径（--level-only 模式必填）")
    ap.add_argument("--class", dest="class_name", help="只统计指定类（per-class，逐类闭环用）")
    ap.add_argument("--detail", action="store_true", help="打印每个 high/mid 函数的明细")
    ap.add_argument("--json", action="store_true",
                    help="输出结构化 JSON（--level-only 模式生效；全流程模式恒写 JSON 文件）")
    ap.add_argument("-o", "--output", help="JSON 写入路径（默认 stdout，与 --json 配合）")
    ap.add_argument("--fail-on-gate", action="store_true",
                    help="门禁未通过（或 0 匹配）时以退出码 3 结束（CI 门禁用）")

    # ── 全流程参数（默认模式）──
    ap.add_argument("project_dir", nargs="?", default=None,
                    help="项目根目录绝对路径（全流程模式必填）")
    ap.add_argument("--build-dir", default=None,
                    help="构建目录名（相对项目根），默认自动探测 build-ut / build-autotests / build")
    ap.add_argument("--test-target", default=None,
                    help="gtest 可执行文件路径（相对路径先按项目根、再按 build-dir 解析，或绝对路径）")
    ap.add_argument("--test-targets", default=None,
                    help="多个 gtest 可执行文件，逗号分隔（解析规则同 --test-target）")
    ap.add_argument("--timeout", type=int, default=300,
                    help="单个测试目标的超时秒数（默认 300，超时计入失败并继续其余目标）")
    ap.add_argument("--report-dir", default=None,
                    help="报告输出目录名（相对项目根），默认与 build-dir 相同")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过编译（假定已编译），直接运行测试采集覆盖率")
    ap.add_argument("--build-type", default="Debug",
                    help="CMAKE_BUILD_TYPE（默认 Debug）")
    ap.add_argument("--coverage-flags", default="--coverage", metavar="FLAGS",
                    help="覆盖率插桩 flag，写入 CMAKE_C_FLAGS/CMAKE_CXX_FLAGS/"
                         "CMAKE_EXE_LINKER_FLAGS/CMAKE_SHARED_LINKER_FLAGS（默认 '--coverage'）；"
                         "项目 CMake 已自行开启插桩时传空字符串 '' 关闭自动注入")
    ap.add_argument("--cmake-extra", action="append", default=[], metavar="ARG",
                    help="额外传给 cmake 的参数，可多次指定：--cmake-extra=-DBUILD_TESTS=ON")
    ap.add_argument("--extract", default=None, metavar="PAT[,PAT...]",
                    help="lcov --extract 模式，逗号分隔（默认 '*/src/*'）；"
                         "传空字符串 '' 表示跳过 extract，仅按 remove 规则过滤")

    args = ap.parse_args(argv)

    # ════════ --level-only 模式：只跑分级统计 ════════
    if args.level_only:
        if not args.inventory:
            ap.error("--level-only 模式需要 -i/--inventory")
        if not args.coverage:
            ap.error("--level-only 模式需要 -c/--coverage")
        result = compute_tiered_coverage(
            args.inventory, args.coverage,
            output_json=args.output,
            class_name=args.class_name,
            detail=args.detail,
            json_output=args.json,
        )
        if args.fail_on_gate and not result["total"]["pass"]:
            print("错误: 门禁未通过（--fail-on-gate）", file=sys.stderr, flush=True)
            return 3
        return 0

    # ════════ 默认模式：全流程（编译 → 运行 → lcov → 分级 → 汇总）════════
    if not args.project_dir:
        ap.error("全流程模式需要 project_dir（或使用 --level-only 只跑分级统计）")

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        ap.error(f"项目目录不存在: {project}")

    # ── 依赖预检（友好报错）──
    for tool in ("lcov", "genhtml"):
        _check_tool(tool)
    if not args.skip_build:
        _check_tool("cmake")

    # ── 自动探测 build-dir / report-dir / test-target / inventory ──
    # build-dir 探测：优先选真正含 gtest 目标的候选目录（避免选中只剩 html 残留的旧目录）
    build_dir_name = args.build_dir
    if not build_dir_name:
        tried = []
        for candidate in ("build-ut", "build-autotests", "build"):
            cand_dir = project / candidate
            if not cand_dir.is_dir():
                continue
            tried.append(candidate)
            name_hits = _scan_test_candidates(cand_dir)
            if not name_hits:
                continue
            confirmed = [str(p) for p in name_hits if _looks_like_gtest(p)]
            if confirmed:
                build_dir_name = candidate
                break
        if not build_dir_name:
            build_dir_name = tried[0] if tried else "build-ut"

    build_dir = project / build_dir_name
    report_dir_name = args.report_dir or build_dir_name
    report_dir = project / report_dir_name

    inventory_path = args.inventory
    if not inventory_path:
        for candidate in ("autotests/.ut-inventory.json", "tests/.ut-inventory.json"):
            if (project / candidate).exists():
                inventory_path = str(project / candidate)
                break

    # ── 确定测试目标列表 ──
    def _resolve_target(t):
        """相对路径先按项目根、再按 build-dir 解析（兼容两种语义）。"""
        p = Path(t)
        if p.is_absolute():
            cands = [p]
        else:
            cands = [project / t, build_dir / t]
        for c in cands:
            if c.is_file():
                return c.resolve()
        ap.error(f"测试目标不存在，尝试过: {', '.join(str(c) for c in cands)}")

    test_targets_abs = []  # list of (abs_path, name)

    if args.test_targets:
        for t in args.test_targets.split(","):
            t = t.strip()
            if not t:
                continue
            t_abs = _resolve_target(t)
            test_targets_abs.append((str(t_abs), Path(t_abs).name))
    elif args.test_target:
        t_abs = _resolve_target(args.test_target)
        test_targets_abs.append((str(t_abs), Path(t_abs).name))
    else:
        found = find_test_targets(str(build_dir))
        if not found:
            ap.error(f"未找到 gtest 可执行文件于 {build_dir}，请用 --test-target 或 --test-targets 指定")
        for f in found:
            test_targets_abs.append((str(Path(f).resolve()), Path(f).name))

    target_names = [n for _, n in test_targets_abs]
    is_multi = len(test_targets_abs) > 1

    print(f"\n{'='*60}", flush=True)
    print(f"Mode 3 · 覆盖率采集与汇总", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  项目:      {project}", flush=True)
    print(f"  构建目录:  {build_dir}", flush=True)
    print(f"  报告目录:  {report_dir}", flush=True)
    if is_multi:
        names = ", ".join(target_names)
        print(f"  测试目标:  {names}  ({len(test_targets_abs)} 个)", flush=True)
    else:
        print(f"  测试目标:  {test_targets_abs[0][0]}", flush=True)
    print(f"  inventory: {inventory_path or '(无)'}", flush=True)

    # ── Step 1: 编译 ──
    if not args.skip_build:
        print(f"\n── Step 1: 编译 ({args.build_type}, 覆盖率插桩) ──", flush=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        cmake_args = [
            "cmake", str(project),
            f"-DCMAKE_BUILD_TYPE={args.build_type}",
        ]
        if args.coverage_flags:
            cmake_args += [
                f"-DCMAKE_C_FLAGS={args.coverage_flags}",
                f"-DCMAKE_CXX_FLAGS={args.coverage_flags}",
                f"-DCMAKE_EXE_LINKER_FLAGS={args.coverage_flags}",
                f"-DCMAKE_SHARED_LINKER_FLAGS={args.coverage_flags}",
            ]
        cmake_args += args.cmake_extra
        run(cmake_args, cwd=str(build_dir))
        run(["cmake", "--build", str(build_dir), "-j", str(os.cpu_count() or 4)])
    else:
        print(f"\n── Step 1: 跳过编译 (--skip-build) ──", flush=True)

    # 编译后/运行前校验插桩产物，避免白跑一轮测试才发现 SF=0
    _check_coverage_instrumentation(build_dir)

    # ── Step 2: 运行测试 + gtest XML ──
    xml_dir = report_dir / "report"
    xml_dir.mkdir(parents=True, exist_ok=True)

    # 唯一化 XML 文件名（不同目录同名目标不互相覆盖）
    used_names = {}
    target_runs = []   # (t_abs, display_name, xml_path)
    for t_abs, t_name in test_targets_abs:
        k = used_names.get(t_name, 0)
        used_names[t_name] = k + 1
        unique = t_name if k == 0 else f"{t_name}__{k + 1}"
        target_runs.append((t_abs, t_name, xml_dir / f"report_{unique}.xml"))

    print(f"\n── Step 2: 运行测试 ({len(target_runs)} 个目标) ──", flush=True)
    # 清理上次运行残留的 XML，避免污染本次汇总
    stale = list(xml_dir.glob("report_*.xml"))
    for old in stale:
        old.unlink()
    if stale:
        print(f"  已清理上次运行残留 XML {len(stale)} 个", flush=True)

    env = os.environ.copy()
    env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "abort_on_error=0:detect_leaks=0")
    env["QT_QPA_PLATFORM"] = env.get("QT_QPA_PLATFORM", "offscreen")

    worst_exit_code = 0
    timed_out = []
    for t_abs, t_name, xml_path in target_runs:
        print(f"  运行 {t_name} ...", flush=True)
        try:
            p = run_stream(
                [t_abs, f"--gtest_output=xml:{xml_path}"],
                timeout=args.timeout, env=env,
            )
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = None
            timed_out.append(t_name)
            print(f"  ⚠ {t_name} 超时（>{args.timeout}s），已终止并计入失败，继续其余目标",
                  flush=True)
            _write_timeout_xml(xml_path, t_name, args.timeout)
        if rc is not None and rc != 0:
            print(f"  ⚠ {t_name} 退出码 {rc}（部分用例失败）", flush=True)
            worst_exit_code = max(worst_exit_code, rc)
        elif rc is None:
            worst_exit_code = max(worst_exit_code, 1)
    test_exit_code = worst_exit_code

    # ── Step 3: lcov 采集（含分支覆盖率）──
    print(f"\n── Step 3: lcov 采集 ──", flush=True)
    coverage_info = build_dir / "coverage.info"
    filtered_info = build_dir / "coverage" / "filtered.info"
    branch_rc = ["--rc", "lcov_branch_coverage=1"]

    run(["lcov", *branch_rc, "-d", str(build_dir), "-c", "-o", str(coverage_info)])

    extract_pats = []
    if args.extract != "":          # None → 默认 */src/*；'' → 跳过 extract
        pats = args.extract if args.extract is not None else "*/src/*"
        extract_pats = [p.strip() for p in pats.split(",") if p.strip()]

    sf_count = count_sf(str(coverage_info))
    coverage_empty = False
    if sf_count == 0:
        # 采集即为空（无 .gcda）：lcov --extract/--remove 会因无记录报错，直接跳过过滤
        coverage_empty = True
        empty_stage = "采集"
        print(f"\n  ⚠⚠ 采集后 coverage.info 无任何源文件记录（SF=0）！", flush=True)
        print(f"     可能原因: 编译未开启覆盖率插桩（--build-type Debug 不会自动加 --coverage）、"
              f"构建目录无 .gcda 文件", flush=True)
    else:
        if extract_pats:
            run(["lcov", *branch_rc, "--extract", str(coverage_info), *extract_pats,
                 "-o", str(coverage_info)])
        else:
            print("  跳过 extract（--extract ''）", flush=True)
        # extract 可能把 info 过滤成空（源码不在 src/ 下），此时 --remove 会报错，需先检测
        sf_count = count_sf(str(coverage_info))
        if sf_count == 0:
            coverage_empty = True
            empty_stage = "过滤"
            print(f"\n  ⚠⚠ 过滤后 coverage.info 无任何源文件记录（SF=0）！", flush=True)
            print(f"     当前 extract 模式: {extract_pats or '(无)'} —— 源码不在 src/ 下的项目请用 --extract 指定",
                  flush=True)
        else:
            run(["lcov", *branch_rc, "--remove", str(coverage_info),
                 "*/tests/*", "*/autotests/*", "*/3rdparty/*",
                 "-o", str(coverage_info)])
            sf_count = count_sf(str(coverage_info))
            if sf_count == 0:
                coverage_empty = True
                empty_stage = "过滤"
                print(f"\n  ⚠⚠ 过滤(remove)后 coverage.info 无任何源文件记录（SF=0）！", flush=True)
                print(f"     remove 规则: */tests/* */autotests/* */3rdparty/* —— 请检查是否把所有源码都过滤掉了",
                      flush=True)

    (build_dir / "coverage").mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(coverage_info), str(filtered_info))

    # ── Step 4: genhtml（每次重建输出目录，避免陈旧文件）──
    print(f"\n── Step 4: genhtml ──", flush=True)
    html_dir = report_dir / "html"
    cov_html = html_dir / "index.html"
    if coverage_empty:
        print("  跳过（覆盖率为空，清空旧 HTML 残留）", flush=True)
        if html_dir.exists():
            shutil.rmtree(html_dir)
        cov_html = None
    else:
        if html_dir.exists():
            shutil.rmtree(html_dir)
        run(["genhtml", "--rc", "genhtml_branch_coverage=1",
             "-o", str(html_dir), str(coverage_info)])
        if not cov_html.exists():
            cov_html = None

    # ── Step 5: 分级覆盖率 ──
    print(f"\n── Step 5: 分级覆盖率 ──", flush=True)
    tiered_json_path = report_dir / "coverage_by_level.json"
    tiered_data = None
    if inventory_path and os.path.exists(inventory_path):
        tiered_data = run_coverage_by_level(
            inventory_path, str(filtered_info), str(tiered_json_path),
            project_name=project.name,
        )
    else:
        print("  跳过（无 .ut-inventory.json）", flush=True)

    # ── Step 6: 汇总 JSON ──
    print(f"\n── Step 6: 生成 ut-summary.json ──", flush=True)

    total, passed, failed, disabled = 0, 0, 0, 0
    suites = []
    for xf in sorted(glob.glob(str(xml_dir / "*.xml"))):
        t, p_, f_, d_ = parse_gtest_xml(xf)
        total += t
        passed += p_
        failed += f_
        disabled += d_
        suites.extend(parse_gtest_xml_suites(xf))

    lcov_data = {} if coverage_empty else parse_lcov_summary(str(coverage_info))

    warnings = []
    if coverage_empty:
        warnings.append(f"{empty_stage}后 coverage.info 无源文件记录（SF=0），覆盖率/HTML 为空")
        test_exit_code = max(test_exit_code, 1)
    if timed_out:
        warnings.append(f"超时目标: {', '.join(timed_out)}（已计入失败）")
    if (tiered_data and tiered_data.get("matched") == 0
            and tiered_data.get("testable_methods", 0) > 0):
        warnings.append(
            f"分级统计 0 匹配（{tiered_data.get('testable_methods')} 个 testable 方法均未命中 coverage）")

    summary = {
        "project": project.name,
        "build_dir": build_dir_name,
        "test_target": target_names[0],  # backward compat alias
        "test_targets": target_names,
        "timeouts": timed_out,
        "test_cases": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "disabled": disabled,
        },
        "test_suites": suites,
    }
    summary.update(lcov_data)

    # 合并分级覆盖率（含 unmatched 诊断信息）
    if tiered_data:
        summary["tiered_coverage"] = {
            "by_level": tiered_data.get("by_level", {}),
            "total": tiered_data.get("total", {}),
            "uncovered_functions": tiered_data.get("uncovered_functions", []),
            "unmatched": tiered_data.get("unmatched", 0),
            "matched": tiered_data.get("matched", 0),
            "testable_methods": tiered_data.get("testable_methods", 0),
            "unmatched_breakdown": tiered_data.get("unmatched_breakdown", {}),
        }
    if warnings:
        summary["warnings"] = warnings

    summary_path = report_dir / "ut-summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    xml_files = sorted(glob.glob(str(xml_dir / "*.xml")))
    print(f"\n  gtest XML ({len(xml_files)} 文件): {xml_dir}", flush=True)
    if cov_html:
        print(f"  lcov HTML:     {cov_html}", flush=True)
    print(f"  汇总 JSON:     {summary_path}", flush=True)
    if tiered_data:
        print(f"  分级覆盖率:    {tiered_json_path}", flush=True)

    # Print summary
    tc = summary["test_cases"]
    if is_multi:
        print(f"\n  目标 ({len(test_targets_abs)}): {', '.join(target_names)}", flush=True)
    print(f"  用例: {tc['total']} 总 / {tc['passed']} 通过 / {tc['failed']} 失败"
          + (f" / {tc['disabled']} 禁用" if tc["disabled"] else ""), flush=True)
    if "line_coverage" in summary:
        print(f"  行覆盖率:     {summary['line_coverage']['coverage']}", flush=True)
    if "function_coverage" in summary:
        print(f"  函数覆盖率:    {summary['function_coverage']['coverage']}", flush=True)
    if "branch_coverage" in summary:
        print(f"  分支覆盖率:    {summary['branch_coverage']['coverage']}", flush=True)
    if tiered_data:
        t = tiered_data.get("total", {})
        br = t.get("branch_coverage")
        br_s = f"{br:.1f}%" if br is not None else "-"
        print(f"  分级(总):      函数 {t.get('function_coverage',0)}%  行 {t.get('line_coverage',0)}%"
              f"  分支 {br_s}", flush=True)
        for lv in ("high", "mid", "low"):
            bl = tiered_data.get("by_level", {}).get(lv, {})
            gate = "✓" if bl.get("pass") else "✗"
            b = bl.get("branch_coverage")
            b_s = f"{b:.1f}%" if b is not None else "-"
            print(f"    {lv:<5}: 函数 {bl.get('function_coverage',0):5.1f}%"
                  f"  行 {bl.get('line_coverage',0):5.1f}%"
                  f"  分支 {b_s:>5}  {gate}", flush=True)
        unmatched_n = tiered_data.get("unmatched", 0)
        if unmatched_n:
            ub = tiered_data.get("unmatched_breakdown", {})
            print(f"  分级未匹配:    {unmatched_n} 个（源文件缺失 {ub.get('no_source_file', 0)}"
                  f" / 函数未命中 {ub.get('no_function_record', 0)}）", flush=True)
    for w in warnings:
        print(f"  ⚠ {w}", flush=True)

    if args.fail_on_gate:
        if tiered_data is None:
            print("  ⚠ --fail-on-gate：无 inventory，门禁未评估", flush=True)
        elif not tiered_data["total"]["pass"]:
            print("  ✗ 门禁未通过（--fail-on-gate → 退出码 3）", flush=True)
            test_exit_code = max(test_exit_code, 3)

    return test_exit_code


def main():
    sys.exit(main_no_exit())


if __name__ == "__main__":
    main()
