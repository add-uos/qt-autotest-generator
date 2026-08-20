#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
coverage-by-level.py — 按 .ut-inventory.json 的 level(high/mid/low) 统计【函数覆盖率】与【行覆盖率】。

按 inventory 分级统计【函数级 + 行级】覆盖率：
解析 FN/FNDA/DA，c++filt demangle 后关联 inventory 分级，产出按 high/mid/low 的覆盖率。

数据源:
  - lcov tracefile (coverage.info) : FN/FNDA(函数调用) + DA(行命中)
  - .ut-inventory.json             : method 的 level / name / class_qn / file + gate_thresholds

匹配链:
  file 后缀匹配 SF -> c++filt 批量 demangle FN -> 'class_qn::name(' 子串定位 method
  （处理重载：一个 method 可匹配多个 mangled FN，全部并入）
行覆盖率:
  按 FN 起始行 + 下一个 FN 起始行划分函数体区间，统计区间内 DA 行命中(>0 即覆盖)。
  注: gcov/lcov 不直接给单函数行覆盖，区间法为业界通用近似(含 lambda/内联会有偏差)。

用法:
  # 全量分级汇总（批次收尾 / 报告）
  python3 scripts/coverage-by-level.py \\
      -i autotests/.ut-inventory.json \\
      -c build-autotests/coverage/filtered.info

  # per-class 分级覆盖率（逐类闭环，build_verifier/self_checker 用）
  python3 scripts/coverage-by-level.py \\
      -i autotests/.ut-inventory.json \\
      -c build-autotests/coverage/filtered.info \\
      --class IconButton --json

  # 每函数明细（定位缺口）
  python3 scripts/coverage-by-level.py -i ... -c ... --detail

依赖: c++filt (binutils 自带，gcc 安装即有)
"""

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict

LEVELS = ["high", "mid", "low"]

# inventory 字段兼容（schema 文档用 file_path/qualified_name，实际产出用 file/qn）
def m_file(m):
    return m.get("file_path") or m.get("file") or ""

def m_class(m):
    return m.get("class_qn") or ""

def m_name(m):
    return m.get("name") or ""


def parse_lcov(path):
    """解析 lcov tracefile -> {sf_path: {fns:[(line,mangled)], fnda:{mangled:count}, da:{line:hits}}}"""
    files = {}
    cur = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip()
            if line.startswith("SF:"):
                cur = {"fns": [], "fnda": {}, "da": {}}
                files[line[3:]] = cur
            elif cur is None:
                continue
            elif line.startswith("FN:"):
                lnum, mangled = line[3:].split(",", 1)
                cur["fns"].append((int(lnum), mangled))
            elif line.startswith("FNDA:"):
                cnt, mangled = line[5:].split(",", 1)
                cur["fnda"][mangled] = int(cnt)
            elif line.startswith("DA:"):
                lnum, hits = line[3:].split(",", 1)
                cur["da"][int(lnum)] = int(hits)
            elif line == "end_of_record":
                cur = None
    return files


def demangle_batch(mangled_list):
    if not mangled_list:
        return {}
    p = subprocess.run(
        ["c++filt"], input="\n".join(mangled_list),
        capture_output=True, text=True, check=True,
    )
    out = p.stdout.split("\n")
    return {m: (out[i] if i < len(out) else m) for i, m in enumerate(mangled_list)}


def build_ranges(rec):
    """返回 [(start, end, mangled)]，end 为下一个 FN 起始行或文件最后 DA 行+1"""
    fns = sorted(set(rec["fns"]), key=lambda x: x[0])
    da_lines = sorted(rec["da"].keys())
    max_line = da_lines[-1] if da_lines else 0
    return [
        (ln, fns[i + 1][0] if i + 1 < len(fns) else max_line + 1, mg)
        for i, (ln, mg) in enumerate(fns)
    ]


def class_matches(m, kw):
    """--class 关键字匹配: class_qn 末尾段 == kw，或 class_qn == kw"""
    cls = m_class(m)
    if not cls:
        return False
    return cls == kw or cls.endswith("." + kw) or cls.split(".")[-1] == kw


def main():
    ap = argparse.ArgumentParser(
        description="按 inventory level 统计函数/行覆盖率（函数级+行级）"
    )
    ap.add_argument("-i", "--inventory", required=True, help=".ut-inventory.json 路径")
    ap.add_argument("-c", "--coverage", required=True, help="lcov coverage.info / filtered.info 路径")
    ap.add_argument("--class", dest="class_name", help="只统计指定类（per-class，逐类闭环用）")
    ap.add_argument("--detail", action="store_true", help="打印每个 high/mid 函数的明细")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON（供 self_checker 门禁判定）")
    ap.add_argument("-o", "--output", help="JSON 写入路径（默认 stdout，与 --json 配合）")
    args = ap.parse_args()

    inv = json.load(open(args.inventory, encoding="utf-8"))
    methods = [m for m in inv["methods"]
               if m.get("testable") and m.get("level") in LEVELS]
    if args.class_name:
        methods = [m for m in methods if class_matches(m, args.class_name)]

    gates = inv.get("gate_thresholds", {})

    files = parse_lcov(args.coverage)

    # 批量 demangle
    all_mangled = {mg for rec in files.values() for _, mg in rec["fns"]}
    dem = demangle_batch(list(all_mangled))

    file_fns = {
        sf: [(ln, mg, dem.get(mg, mg)) for ln, mg in sorted(set(rec["fns"]), key=lambda x: x[0])]
        for sf, rec in files.items()
    }
    file_ranges = {sf: build_ranges(files[sf]) for sf in files}
    sf_list = list(files.keys())

    def find_sf(inv_file):
        for sf in sf_list:
            if sf.endswith(inv_file):
                return sf
        return None

    def fn_lines(sf, mangled):
        for s, e, mg in file_ranges[sf]:
            if mg == mangled:
                return [ln for ln in files[sf]["da"] if s <= ln < e]
        return []

    stat = {lv: {"methods": 0, "fn_cov": 0, "line_set": set(), "cov_set": set()}
            for lv in LEVELS + ["total"]}
    detail = defaultdict(list)
    uncovered = []
    unmatched = []

    for m in methods:
        lv = m["level"]
        sf = find_sf(m_file(m))
        if not sf:
            unmatched.append(m)
            continue
        name, cls = m_name(m), m_class(m)
        key = f"{cls}::{name}(" if cls else f"{name}("
        hits = [(ln, mg, dm) for ln, mg, dm in file_fns[sf] if key in dm]
        if not hits:
            unmatched.append(m)
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
        for _, mg, _ in hits:
            for ln in fn_lines(sf, mg):
                line_pairs.add((sf, ln))
        for pair in line_pairs:
            stat[lv]["line_set"].add(pair)
            stat["total"]["line_set"].add(pair)
            if files[pair[0]]["da"][pair[1]] > 0:
                stat[lv]["cov_set"].add(pair)
                stat["total"]["cov_set"].add(pair)

        if args.detail and lv in ("high", "mid"):
            ln_total = len(line_pairs)
            ln_cov = sum(1 for p in line_pairs if files[p[0]]["da"][p[1]] > 0)
            detail[lv].append({
                "name": f"{cls}::{name}" if cls else name,
                "file": m_file(m),
                "covered": covered,
                "calls": [files[sf]["fnda"].get(mg, 0) for _, mg, _ in hits],
                "lines": ln_total,
                "lines_cov": ln_cov,
                "line_rate": (ln_cov / ln_total * 100) if ln_total else 0.0,
            })

    def rate(a, b):
        return round(a / b * 100, 1) if b else 0.0

    def gate_pass(lv):
        g = gates.get(lv, {})
        fn_ok = rate(stat[lv]["fn_cov"], stat[lv]["methods"]) >= 100 if g.get("function") else True
        line_th = g.get("line", 0) or 0
        line_ok = rate(len(stat[lv]["cov_set"]), len(stat[lv]["line_set"])) >= line_th
        return fn_ok and line_ok

    by_level = {}
    for lv in LEVELS:
        by_level[lv] = {
            "methods": stat[lv]["methods"],
            "function_coverage": rate(stat[lv]["fn_cov"], stat[lv]["methods"]),
            "lines": len(stat[lv]["line_set"]),
            "line_coverage": rate(len(stat[lv]["cov_set"]), len(stat[lv]["line_set"])),
            "gate": gates.get(lv, {}),
            "pass": gate_pass(lv),
        }
    total = {
        "methods": stat["total"]["methods"],
        "function_coverage": rate(stat["total"]["fn_cov"], stat["total"]["methods"]),
        "lines": len(stat["total"]["line_set"]),
        "line_coverage": rate(len(stat["total"]["cov_set"]), len(stat["total"]["line_set"])),
        "pass": all(by_level[lv]["pass"] for lv in LEVELS),
    }

    result = {
        "class": args.class_name,
        "by_level": by_level,
        "total": total,
        "unmatched": len(unmatched),
        "uncovered_functions": uncovered,
    }

    if args.json:
        out = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            open(args.output, "w", encoding="utf-8").write(out)
            print(f"已写入: {args.output}", file=sys.stderr)
        else:
            print(out)
        return

    # 人类可读汇总
    print(f"\n项目: {inv.get('project','?')}"
          + (f"  | 类: {args.class_name}" if args.class_name else ""))
    print(f"coverage: {args.coverage}")
    print(f"inventory: {args.inventory}")
    print(f"testable methods: {len(methods)}  匹配: {len(methods)-len(unmatched)}  未匹配: {len(unmatched)}")
    print()
    hdr = f"{'level':<8}{'methods':>9}{'fn_cov':>9}{'fn_rate':>10}{'lines':>9}{'line_cov':>10}{'line_rate':>10}{'gate':>6}"
    print(hdr)
    print("-" * len(hdr))
    for lv in LEVELS + ["total"]:
        s = stat[lv] if lv != "total" else stat["total"]
        fn_rate = rate(s["fn_cov"], s["methods"])
        line_rate = rate(len(s["cov_set"]), len(s["line_set"]))
        gate = "✓" if (by_level.get(lv, total)["pass"]) else "✗"
        print(f"{lv:<8}{s['methods']:>9}{s['fn_cov']:>9}{fn_rate:>9.1f}%"
              f"{len(s['line_set']):>9}{len(s['cov_set']):>10}{line_rate:>9.1f}%{gate:>6}")

    if unmatched:
        print(f"\n⚠ 未匹配到 coverage.info 的方法: {len(unmatched)} 个")
        print("  按文件:", dict(Counter(m_file(m) for m in unmatched).most_common(8)))

    if args.detail:
        for lv in ("high", "mid"):
            rows = sorted(detail[lv], key=lambda r: (r["covered"], -r["line_rate"]))
            print(f"\n=== {lv} 函数明细 ({len(rows)}) ===")
            print(f"{'covered':>8}  {'line_rate':>9}  {'calls':>10}  function")
            for r in rows:
                tag = "✅" if r["covered"] else "❌"
                print(f"{tag:>6}  {r['line_rate']:>8.1f}%  {str(r['calls']):>10}  {r['name']}  [{r['file']}]")

    if args.output:
        json.dump(result, open(args.output, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"\n汇总 JSON 已写入: {args.output}")


if __name__ == "__main__":
    main()
