#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 Uniontech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
utq.py — .ut-inventory.json 快速筛查工具（给 AI / 人补单元测试用）

一个项目目录（含 .ut-inventory.json + test-mapping.json）支持以下查询：

  stats                 总览：总量/可测/已测/未测，按 high/mid/low 分级统计
  todo                  ★ 未覆盖待写清单（--level/--file/--class/--kw 过滤）
  top [N]               按重要性分数排序的未测函数（默认 20）
  file PATTERN          某源文件的函数及覆盖状态（补某个模块时用）
  class NAME            某个类的全部函数及覆盖状态
  classes               按类聚合的未测数量排行
  search KW             模糊搜索函数名/类名/文件/签名
  info NAME             单个函数完整信息（factors/测试文件/用例名/签名）
  covered               已有测试的函数清单（避免重复写）
  weak                  弱覆盖：已有测试但用例少（复杂函数仅 1 例等）
  by-test-file FILE     反查某个测试文件覆盖了哪些源码函数
  files                 每个源文件的覆盖进度汇总（找薄弱模块）
  pending               待人工评审队列（review_queue）
  exempt                豁免/不可测清单及原因
  export                导出任务包 JSON（喂给子代理/AI 批量写测试）

字段语义:
  test_cover_count  调用该函数的测试文件数（MCP CALLS 静态分析，外部工具回写）
  usecase_count     GTest TEST_F 用例数（mode2-ops usecase 回写）
  覆盖判定         双信号：test_cover_count > 0 或 usecase_count > 0 任一成立即已覆盖
  score/factors     重要性打分与依据（complexity/cognitive/lines/in_degree...）
  level             high > mid > low（对应 gate_thresholds 的覆盖率门槛）

用法示例:
  python3 scripts/utq.py -P mcp-projects/deepin-image-viewer stats
  python3 scripts/utq.py todo --level high
  python3 scripts/utq.py file pathviewproxymodel
  python3 scripts/utq.py class ImageEditController
  python3 scripts/utq.py info asyncUpdateLoadInfo
  python3 scripts/utq.py export --level high --limit 10 > tasks.json
"""

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

LEVEL_ORDER = {"high": 0, "mid": 1, "low": 2}
LEVEL_BADGE = {"high": "H", "mid": "M", "low": "L", None: "-"}


# ---------- 基础工具 ----------

def disp_width(s):
    """East-Asian 宽度感知的显示宽度。"""
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s, width, right=True):
    s = str(s)
    gap = max(0, width - disp_width(s))
    return s + " " * gap if right else " " * gap + s


def table(rows, headers):
    """简单对齐表格，rows 为字符串列表的列表。"""
    if not rows:
        return ""
    widths = [disp_width(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], disp_width(cell))
    out = ["  ".join(pad(h, w) for h, w in zip(headers, widths)),
           "  ".join("-" * w for w in widths)]
    out += ["  ".join(pad(c, w) for c, w in zip(r, widths)) for r in rows]
    return "\n".join(out)


def fmt_list(items):
    return "\n".join("  - " + i for i in items)


# ---------- 数据加载 ----------

def find_project_dir(explicit):
    if explicit:
        p = Path(explicit)
        if (p / ".ut-inventory.json").exists():
            return p
        die(f"目录 {explicit} 下没有 .ut-inventory.json")
    d = Path.cwd()
    for cand in [d, *d.parents]:
        if (cand / ".ut-inventory.json").exists():
            return cand
    die("未找到 .ut-inventory.json（用 -P 指定项目目录，或在项目目录内运行）")


class Inv:
    def __init__(self, project_dir):
        self.dir = project_dir
        inv_path = project_dir / ".ut-inventory.json"
        try:
            with open(inv_path, encoding="utf-8") as f:
                self.data = json.load(f)
        except json.JSONDecodeError as e:
            die(f".ut-inventory.json 格式错误：{e}")
        except OSError as e:
            die(f"读取 .ut-inventory.json 失败：{e}")
        self.methods = self.data.get("methods", [])
        self.tm = {}
        tm_path = project_dir / "test-mapping.json"
        if tm_path.exists():
            with open(tm_path, encoding="utf-8") as f:
                self.tm = json.load(f)

    # --- 字段访问（全部带默认值，缺字段不炸） ---
    @staticmethod
    def owner(m):
        return m.get("class_qn") or "(自由函数)"

    def cover(self, m):
        return m.get("test_cover_count") or 0

    def usecase(self, m):
        return m.get("usecase_count") or 0

    def is_covered(self, m):
        # 双信号：MCP 静态分析计数或 TEST_F 用例数任一 > 0 即视为已有测试。
        # 只看 test_cover_count 会在 Mode 2 刚写完测试（usecase_count 已回写、
        # 外部 fetch-test-mapping 尚未跑）时误判为 todo，导致重复写测试。
        return self.cover(m) > 0 or self.usecase(m) > 0

    def is_todo(self, m):
        return bool(m.get("testable")) and not self.is_covered(m)

    def display_name(self, m):
        return f"{self.owner(m)}::{m.get('name')}"


# ---------- 过滤器 ----------

def apply_filters(inv, methods, args):
    out = methods
    if not getattr(args, "include_exempt", False):
        out = [m for m in out if m.get("testable")]
    if getattr(args, "level", None):
        out = [m for m in out if m.get("level") == args.level]
    if getattr(args, "file", None):
        out = [m for m in out if args.file.lower() in (m.get("file_path") or "").lower()]
    if getattr(args, "class_", None):
        out = [m for m in out
               if args.class_.lower() in (m.get("class_qn") or "").lower()]
    if getattr(args, "kw", None):
        kw = args.kw.lower()
        out = [m for m in out if kw in json.dumps(
            {k: m.get(k) for k in
             ("name", "class_qn", "file_path", "signature", "qualified_name")},
            ensure_ascii=False).lower()]
    return out


def sort_key(inv, m):
    return (LEVEL_ORDER.get(m.get("level"), 9), -(m.get("score") or 0),
            m.get("file_path") or "", inv.display_name(m))


def limit_rows(inv, rows, args, default=None):
    n = getattr(args, "limit", None)
    if n is None:
        n = default
    return rows[:n] if n else rows


# ---------- 子命令 ----------

def cmd_stats(inv, args):
    ms = inv.methods
    testable = [m for m in ms if m.get("testable")]
    exempt = [m for m in ms if not m.get("testable")]
    covered = [m for m in testable if inv.is_covered(m)]
    todo = [m for m in testable if not inv.is_covered(m)]
    d = inv.data

    print(f"项目: {d.get('project')}  base_sha: {(d.get('base_sha') or '')[:12]}"
          f"  生成: {d.get('generated_at','')[:19]}")
    print(f"函数总数 {len(ms)} | 可测 {len(testable)} | 豁免 {len(exempt)}"
          f" | 待评审 {len(d.get('review_queue') or [])}")
    print()
    rows = []
    for lv in ("high", "mid", "low"):
        g = [m for m in testable if m.get("level") == lv]
        c = [m for m in g if inv.is_covered(m)]
        pct = f"{len(c)*100//len(g)}%" if g else "-"
        gate = d.get("gate_thresholds", {}).get(lv, {})
        rows.append([lv.upper(), len(g), len(c), len(g) - len(c), pct,
                     f"line{gate.get('line','?')}/br{gate.get('branch','?')}"
                     f"/fn{gate.get('function','?')}"])
    rows.append(["合计", len(testable), len(covered), len(todo),
                 f"{len(covered)*100//max(1,len(testable))}%", ""])
    print(table(rows, ["级别", "可测", "已测", "未测", "覆盖", "门槛(line/br/fn)"]))
    if args.json:
        print(json.dumps({"total": len(ms), "testable": len(testable),
                          "covered": len(covered), "todo": len(todo),
                          "by_level": {r[0].lower(): dict(zip(
                              ["testable", "covered", "todo"], r[1:4])) for r in rows[:3]}},
                         ensure_ascii=False))


def method_row(inv, m):
    return [LEVEL_BADGE.get(m.get("level")),
            m.get("score", 0),
            inv.display_name(m),
            m.get("signature", ""),
            m.get("file_path", ""),
            inv.usecase(m), inv.cover(m)]


METHOD_HEADERS = ["级", "分", "函数", "签名", "源文件", "例", "测"]


def print_methods(inv, rows, extra=""):
    print(table([method_row(inv, m) for m in rows], METHOD_HEADERS))
    print(f"\n共 {len(rows)} 条{extra}   (列: 级=H/M/L 分=score 例=用例数 测=覆盖测试文件数)")
    if args_json_global:
        print(json.dumps([method_dict(inv, m) for m in rows],
                         ensure_ascii=False, indent=2))


def method_dict(inv, m):
    d = {k: m.get(k) for k in ("name", "qualified_name", "class_qn", "file_path",
                               "access", "level", "score", "factors", "signature",
                               "usecase_count", "test_cover_count", "test_files",
                               "test_cases", "node_type")}
    d["owner"] = inv.owner(m)
    d["covered"] = inv.is_covered(m)
    return d


def cmd_todo(inv, args):
    rows = [m for m in inv.methods if inv.is_todo(m)]
    rows = apply_filters(inv, rows, args)
    rows.sort(key=lambda m: sort_key(inv, m))
    rows = limit_rows(inv, rows, args)
    print_methods(inv, rows, "未覆盖")


def cmd_top(inv, args):
    n = args.n or 20
    rows = sorted([m for m in inv.methods if inv.is_todo(m)],
                  key=lambda m: (-(m.get("score") or 0),
                                 LEVEL_ORDER.get(m.get("level"), 9)))[:n]
    print_methods(inv, rows, f"Top{n} 重要未测")


def cmd_file(inv, args, label=None):
    rows = apply_filters(inv, inv.methods, args)
    rows.sort(key=lambda m: sort_key(inv, m))
    cov = sum(1 for m in rows if inv.is_covered(m))
    print_methods(inv, rows, label or f"（文件含 '{args.file}'）")
    print(f"覆盖 {cov}/{len(rows)}")


def cmd_class(inv, args):
    cmd_file(inv, args, label=f"（类含 '{args.class_}'）")


def cmd_search(inv, args):
    rows = apply_filters(inv, inv.methods, args)
    rows.sort(key=lambda m: (LEVEL_ORDER.get(m.get("level"), 9),
                             inv.display_name(m)))
    rows = limit_rows(inv, rows, args)
    print_methods(inv, rows, f"（关键字 '{args.kw}'）")


def cmd_info(inv, args):
    kw = args.name.lower()
    hits = [m for m in inv.methods
            if kw in (m.get("name") or "").lower()
            or kw in (m.get("qualified_name") or "").lower()]
    if not hits:
        die(f"没找到匹配 '{args.name}' 的函数")
    for m in hits:
        print(json.dumps(method_dict(inv, m), ensure_ascii=False, indent=2))
        print()


def cmd_covered(inv, args):
    rows = [m for m in inv.methods if inv.is_covered(m)]
    rows = apply_filters(inv, rows, args)
    rows.sort(key=lambda m: (LEVEL_ORDER.get(m.get("level"), 9), inv.display_name(m)))
    rows = limit_rows(inv, rows, args)
    out = []
    for m in rows:
        out.append(method_row(inv, m) + [",".join(m.get("test_files") or [])])
    print(table(out, METHOD_HEADERS + ["测试文件"]))
    print(f"\n共 {len(rows)} 条已覆盖")
    if args.show_cases:
        for m in rows:
            if m.get("test_cases"):
                print(f"\n{inv.display_name(m)}:")
                print(fmt_list(m["test_cases"]))


def cmd_weak(inv, args):
    """已覆盖但用例薄弱：复杂函数(score>=3)只有 1 个用例，或用例数 < 覆盖文件数。"""
    rows = [m for m in inv.methods
            if inv.is_covered(m) and m.get("testable")
            and ((m.get("score") or 0) >= 3 and inv.usecase(m) <= 1)]
    rows = apply_filters(inv, rows, args)
    rows.sort(key=lambda m: -(m.get("score") or 0))
    rows = limit_rows(inv, rows, args)
    print_methods(inv, rows, "弱覆盖（高复杂度但用例≤1）")


def cmd_by_test_file(inv, args):
    tf = args.test_file.lower()
    rows = [m for m in inv.methods
            if any(tf in (f or "").lower() for f in (m.get("test_files") or []))]
    rows.sort(key=lambda m: sort_key(inv, m))
    rows = limit_rows(inv, rows, args)
    out = [method_row(inv, m) + ["\n".join("  · " + c for c in (m.get("test_cases") or []))]
           for m in rows]
    print(table(out, METHOD_HEADERS + ["用例"]))
    print(f"\n测试文件 '{args.test_file}' 覆盖 {len(rows)} 个函数")


def cmd_files(inv, args):
    agg = {}
    for m in inv.methods:
        fp = m.get("file_path") or "?"
        a = agg.setdefault(fp, {"total": 0, "testable": 0, "cov": 0})
        a["total"] += 1
        if m.get("testable"):
            a["testable"] += 1
            if inv.is_covered(m):
                a["cov"] += 1
    rows = []
    for fp, a in agg.items():
        pct = a["cov"] * 100 // a["testable"] if a["testable"] else 100
        rows.append([fp, a["total"], a["testable"], a["cov"],
                     a["testable"] - a["cov"], f"{pct}%"])
    key = {"uncovered": lambda r: (-int(r[4]), r[0]),
           "pct": lambda r: (int(r[5][:-1]), -int(r[4])),
           "name": lambda r: r[0]}.get(args.sort, "uncovered")
    rows.sort(key=key)
    rows = limit_rows(inv, rows, args)
    print(table(rows, ["源文件", "全部", "可测", "已测", "未测", "覆盖率"]))
    print("\n(默认按未测数量排序；--sort pct 按覆盖率升序找最薄弱)")


def cmd_classes(inv, args):
    agg = {}
    for m in inv.methods:
        if not m.get("testable"):
            continue
        o = inv.owner(m)
        a = agg.setdefault(o, {"t": 0, "c": 0})
        a["t"] += 1
        if inv.is_covered(m):
            a["c"] += 1
    rows = [[o, a["t"], a["c"], a["t"] - a["c"]]
            for o, a in agg.items() if a["t"] - a["c"] > 0]
    rows.sort(key=lambda r: -r[3])
    rows = limit_rows(inv, rows, args)
    print(table(rows, ["类/命名空间", "可测", "已测", "未测"]))


def cmd_pending(inv, args):
    rq = inv.data.get("review_queue") or []
    rows = [[r.get("suggested_level", "?"), r.get("class_qn", ""),
             r.get("name", ""), r.get("reason", ""), r.get("review_status", "")]
            for r in rq
            if getattr(args, "all", False) or r.get("review_status") == "pending"]
    rows = limit_rows(inv, rows, args)
    print(table(rows, ["建议级", "类", "函数", "原因", "状态"]))
    print(f"\n共 {len(rows)} 条待评审 (--all 看全部)")


def cmd_exempt(inv, args):
    rows = [m for m in inv.methods if not m.get("testable")]
    rows = apply_filters(inv, rows, args)
    # apply_filters 默认隐藏豁免项，这里手动再取一次豁免集并做同样的文本过滤
    if not getattr(args, "include_exempt", False):
        rows = [m for m in inv.methods if not m.get("testable")]
        for attr in ("file", "kw", "class_"):
            v = getattr(args, attr, None)
            if v:
                vl = v.lower()
                rows = [m for m in rows if vl in json.dumps(
                    {k: m.get(k) for k in ("name", "class_qn", "file_path", "signature")},
                    ensure_ascii=False).lower()]
    rows = limit_rows(inv, rows, args)
    out = [[m.get("exempt_reason") or "?", m.get("file_path", ""),
            inv.display_name(m)] for m in rows]
    print(table(out, ["豁免原因", "源文件", "函数"]))
    print(f"\n共 {len(rows)} 条豁免")


def cmd_export(inv, args):
    rows = [m for m in inv.methods if inv.is_todo(m)]
    rows = apply_filters(inv, rows, args)
    rows.sort(key=lambda m: sort_key(inv, m))
    rows = limit_rows(inv, rows, args)
    payload = {
        "project": inv.data.get("project"),
        "base_sha": inv.data.get("base_sha"),
        "generated_at": inv.data.get("generated_at"),
        "count": len(rows),
        "tasks": [method_dict(inv, m) for m in rows],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------- CLI ----------

def die(msg, code=1):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(code)


args_json_global = False


def build_parser():
    p = argparse.ArgumentParser(prog="utq",
                                description=".ut-inventory.json 快速筛查（补单测用）")
    p.add_argument("-P", "--project", help="项目目录（含 .ut-inventory.json），默认当前目录向上找")
    p.add_argument("--json", action="store_true", help="附带 JSON 输出")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, level_file_class=True):
        sp.add_argument("--json", action="store_true", help="附带 JSON 输出")
        sp.add_argument("--level", choices=["high", "mid", "low"])
        sp.add_argument("--include-exempt", action="store_true",
                        help="包含豁免/不可测项（默认隐藏）")
        if level_file_class:
            sp.add_argument("--file", help="按源文件路径子串过滤")
            sp.add_argument("--class", dest="class_", help="按类名子串过滤")
            sp.add_argument("--kw", help="关键字（函数名/签名/文件）")
        sp.add_argument("--limit", type=int)

    common(sub.add_parser("stats", help="总览统计"))
    sp = sub.add_parser("top", help="按分数排序的未测函数")
    sp.add_argument("n", nargs="?", type=int)
    common(sub.add_parser("todo", help="未覆盖待写清单"))
    sp = sub.add_parser("file", help="某源文件的覆盖状态")
    common(sp)
    sp.add_argument("file", help="文件路径子串")
    sp = sub.add_parser("class", help="某类的覆盖状态")
    common(sp)
    sp.add_argument("class_", help="类名子串")
    sp = sub.add_parser("search", help="模糊搜索")
    common(sp)
    sp.add_argument("kw", help="关键字")
    sub.add_parser("info", help="单个函数详情").add_argument("name", help="函数名或 QN 子串")
    sp = sub.add_parser("covered", help="已覆盖清单")
    common(sp)
    sp.add_argument("--show-cases", action="store_true", help="列出具体用例名")
    common(sub.add_parser("weak", help="弱覆盖（复杂但用例少）"))
    sp = sub.add_parser("by-test-file", help="测试文件反查覆盖函数")
    sp.add_argument("test_file", help="测试文件路径子串")
    sp.add_argument("--limit", type=int)
    sp = sub.add_parser("files", help="按源文件汇总覆盖进度")
    sp.add_argument("--sort", choices=["uncovered", "pct", "name"],
                    default="uncovered")
    common(sp, level_file_class=False)
    common(sub.add_parser("classes", help="按类汇总未测排行"))
    sp = sub.add_parser("pending", help="待人工评审队列")
    sp.add_argument("--all", action="store_true", help="含已处理项")
    common(sp, level_file_class=False)
    common(sub.add_parser("exempt", help="豁免清单"))
    common(sub.add_parser("export", help="导出任务包 JSON"))

    # export 的位置参数 n 与 limit 冲突处理：忽略位置参数，用 --limit
    return p


def main():
    sys.exit(main_no_exit())


def main_no_exit(argv=None):
    """供单元测试调用的入口（不 sys.exit，返回退出码）。"""
    global args_json_global
    parser = build_parser()
    args = parser.parse_args(argv)
    args_json_global = args.json or getattr(args, "json", False)

    # file/class/search 位置参数映射到过滤字段
    if args.cmd == "file":
        args.file = args.file
    if args.cmd == "class":
        args.class_ = args.class_
    if args.cmd == "search":
        args.kw = args.kw

    proj = find_project_dir(args.project)
    inv = Inv(proj)

    dispatch = {
        "stats": cmd_stats, "todo": cmd_todo, "top": cmd_top,
        "file": cmd_file, "class": cmd_class, "search": cmd_search,
        "info": cmd_info, "covered": cmd_covered, "weak": cmd_weak,
        "by-test-file": cmd_by_test_file, "files": cmd_files,
        "classes": cmd_classes, "pending": cmd_pending,
        "exempt": cmd_exempt, "export": cmd_export,
    }
    dispatch[args.cmd](inv, args)
    return 0


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # 管道下游提前退出（如 | head）时安静退出
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
