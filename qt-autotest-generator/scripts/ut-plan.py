#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""ut-plan.py — 测试规划器（L3，doc/gitnexus-ut-workflow-v2.md §11 R2）

survey → 骨架 → 指标 → 分级 → 分块 → 写 `.ut-plan.json`。

设计要点（v2.3）：
  - 数据面唯一通道 = graph-access.RestQueryClient（REST /api/query）；
  - 分位数本地计算（服务端无 percentile 函数），plan 阶段一次算定冻结进
    `quantile` —— 同一 plan 内结果可复现，跨项目随分布自适应；
  - 方法体精确重建：File.content + startLine/endLine 行切片（file-slice），
    切片失败降级符号 content（symbol-snippet，库内截 5000 字符）；
  - 分级：score = 0.35·s(lines) + 0.25·s(cc) + 0.25·s(in_deg)
    + 0.10·is_public + 0.05·min(param_count,5)/5；
  - 分块：一块 = 一个类（HAS_METHOD 边），自由函数按文件聚合 standalone，
    autotests/ 下标 test-file 默认 skipped；
  - 超 MAX_BLOCK_BYTES 的块拆子块（每子块 ≤ MAX_SUBBLOCK_METHODS 个方法）。

用法:
  # 建档：全量规划（秒级——骨架 5s + 内容分批 + 本地计算）
  python3 ut-plan.py plan --repo dde-file-manager -o .ut-plan.json

  # 选项：--content-min-lines 8（cc 精确计算的方法体行数下限）
  #       --module src/plugins      （只规划该模块）
  #       --limit-methods 2000      （冒烟）
"""

import argparse
import fnmatch
import json
import math
import os
import re
import sys
from collections import defaultdict

import importlib.util as _ilu

_here = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name, filename):
    """按路径加载同目录连字符脚本（graph-access.py），注册为可 import 模块。"""
    if name in sys.modules:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, os.path.join(_here, filename))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_graph_access = _load_sibling("graph_access", "graph-access.py")
GraphAccessError = _graph_access.GraphAccessError
RestQueryClient = _graph_access.RestQueryClient
count_branches = _graph_access.count_branches
slice_body = _graph_access.slice_body

# ── 常量区（可调可审） ────────────────────────────────────────────────

PLAN_VERSION = "2.3"

# 分级权重（§5.2，固化）
WEIGHTS = {"lines": 0.35, "cc": 0.25, "in_deg": 0.25, "is_public": 0.10, "params": 0.05}
HIGH_SCORE = 0.55

# 分位归一锚点（§5.2：P50→0.25 P75→0.5 P90→0.75 ≥P90→1.0，线性内插）
QUANTILE_ANCHORS = ((50, 0.25), (75, 0.5), (90, 0.75))

# 存取器判定（low 通道）
ACCESSOR_RE = re.compile(r"^(get|set|is|has)", re.I)

# 内容拉取：lines ≥ 此值才拉方法体算 cc（小方法纯文本一眼看穿）
CONTENT_MIN_LINES = 8

# 块组装上限（字节，§6）：超限拆子块
MAX_BLOCK_BYTES = 48 * 1024
MAX_SUBBLOCK_METHODS = 6

# 块状态机（§6）：pending → selected → done/failed；failed 可回 pending 重试
BLOCK_STATES = {"pending", "selected", "done", "failed"}

# 测试路径前缀（与 graph-access.test_edges 一致）
TEST_PREFIXES = ("tests/", "test/", "autotests/")


def is_test_path(file_path):
    return any(file_path.startswith(p) for p in TEST_PREFIXES)


# ── 分位数与归一（本地计算，plan 阶段冻结） ──────────────────────────

def quantile(sorted_vals, p):
    """线性插值分位数（sorted_vals 升序；空列表返回 0）。"""
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def compute_quantiles(values):
    """{p50, p75, p90} 冻结用。"""
    s = sorted(values)
    return {"p50": quantile(s, 50), "p75": quantile(s, 75), "p90": quantile(s, 90)}


def norm_score(value, q):
    """分位归一：P50→0.25，P75→0.5，P90→0.75，≥P90→1.0，线性内插。

    低于 P50 线性缩到 (0, 0.25]（按 P50 值为零点）。
    """
    if value <= 0:
        return 0.0
    if value >= q["p90"]:
        # 退化分布（p50==p90，全项目同值）：无区分度，给中性 0.5 而非 1.0
        # （否则全同值方法集体满分，分级失真——真机 dde-file-manager 验收发现的边界）
        return 1.0 if q["p90"] > q["p50"] else 0.5
    if value >= q["p75"]:
        return 0.5 + 0.25 * (value - q["p75"]) / max(q["p90"] - q["p75"], 1e-9)
    if value >= q["p50"]:
        return 0.25 + 0.25 * (value - q["p50"]) / max(q["p75"] - q["p50"], 1e-9)
    return 0.25 * value / max(q["p50"], 1e-9)


# ── 模块归属（目录前缀本地聚合，永不失效的兜底） ──────────────────────

def module_of(file_path):
    """模块 = filePath 顶层两级目录（src/dfm-base）；无目录归 '(root)'。"""
    parts = file_path.split("/")
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[:2]) if parts[0] in ("src", "lib", "services", "plugins") else parts[0]


# ── 规划主流程 ────────────────────────────────────────────────────────

def fetch_plan_inputs(client, repo, module=None):
    """REST 采集 plan 全量输入（4 次聚合请求 + 内容分批）。"""
    inputs = {}
    inputs["counts"] = client.count_symbols(repo)
    inputs["skeleton"] = client.method_skeleton(repo)
    inputs["classes"] = client.class_skeleton(repo)
    inputs["indegree"] = {r["id"]: r["indegree"] for r in client.call_indegree(repo)}
    inputs["testcov"] = {r["id"]: r for r in client.test_edges(repo)}
    inputs["parents"] = client.method_parents(repo)
    # out_degree（调用出边聚合；plan 用于块 complexity 概览，非分级因子）
    rows = client.query(
        "MATCH (m:Method)-[r:CodeRelation]->(t) WHERE r.type='CALLS' "
        "RETURN m.id AS id, count(r) AS outdeg", repo=repo)
    inputs["outdegree"] = {r["id"]: r["outdeg"] for r in rows}
    if module:
        inputs["skeleton"] = [m for m in inputs["skeleton"]
                              if m["filePath"] == module
                              or m["filePath"].startswith(module.rstrip("/") + "/")]
    return inputs


def build_method_records(inputs, content_min_lines=CONTENT_MIN_LINES, client=None,
                         progress=None):
    """骨架 → 方法记录：行数/入出度/参数/可见性 + 需要时拉内容算 cc。"""
    skeleton = inputs["skeleton"]
    indeg, outdeg = inputs["indegree"], inputs["outdegree"]
    testcov = inputs["testcov"]

    need_content = [m for m in skeleton
                    if (m["endLine"] or 0) - (m["startLine"] or 0) + 1 >= content_min_lines]
    files_needed = sorted({m["filePath"] for m in need_content if m.get("filePath")})
    file_contents = {}
    if client is not None and files_needed:
        file_contents = client.file_contents(files_needed)
        if progress:
            progress(f"内容拉取: {len(file_contents)}/{len(files_needed)} 文件")

    # 符号 content 降级用（切片失败的 id 集合，一次批量补）
    records = []
    slice_failed = []
    for m in skeleton:
        lines = (m["endLine"] or 0) - (m["startLine"] or 0) + 1
        mid = m["id"]
        fp = m.get("filePath") or ""
        rec = {
            "id": mid, "name": m.get("name") or "", "file_path": fp,
            "lines": lines, "start_line": m.get("startLine"),
            "end_line": m.get("endLine"),
            "in_degree": indeg.get(mid, 0), "out_degree": outdeg.get(mid, 0),
            "param_count": m.get("parameterCount") or 0,
            "is_public": bool(m.get("isExported")),
            "content_bytes": m.get("content_bytes") or 0,
            "tested": None, "cc_proxy": 0, "body_source": None,
        }
        cov = testcov.get(mid)
        if cov:
            rec["tested"] = {"files": cov["test_files"], "cases": cov["test_count"]}
        if m in need_content:
            content = file_contents.get(fp)
            body = slice_body(content, m["startLine"], m["endLine"]) if content else ""
            if body:
                rec["cc_proxy"] = count_branches(body)
                rec["body_source"] = "file-slice"
            else:
                slice_failed.append(mid)
                rec["body_source"] = "pending"
        else:
            rec["body_source"] = "none"  # 行数不足阈值，不拉内容（文本一眼可读）
        records.append(rec)

    # 切片失败降级：符号 content（截 5000 字符，cc 记下界性质——body_source 标注）
    if slice_failed and client is not None:
        syms = client.symbol_contents(slice_failed)
        for rec in records:
            if rec["body_source"] == "pending" and rec["id"] in syms:
                rec["cc_proxy"] = count_branches(syms[rec["id"]])
                rec["body_source"] = "symbol-snippet"
    for rec in records:
        if rec["body_source"] == "pending":
            rec["body_source"] = "none"  # 内容拉取后仍无体（如声明/外部函数）
    return records


def classify(records, weights=WEIGHTS, high_score=HIGH_SCORE):
    """分位数冻结 → 逐方法 score/level（§5.2）。返回 (records, quantile)。"""
    quantile = {
        "lines": compute_quantiles([r["lines"] for r in records]),
        "cc": compute_quantiles([r["cc_proxy"] for r in records]),
        "in_deg": compute_quantiles([r["in_degree"] for r in records]),
    }
    for r in records:
        accessor = (ACCESSOR_RE.match(r["name"])
                    and r["lines"] <= 3 and r["cc_proxy"] <= 1)
        hidden = (not r["is_public"] and r["in_degree"] <= 1 and r["lines"] <= 5)
        score = (weights["lines"] * norm_score(r["lines"], quantile["lines"])
                 + weights["cc"] * norm_score(r["cc_proxy"], quantile["cc"])
                 + weights["in_deg"] * norm_score(r["in_degree"], quantile["in_deg"])
                 + weights["is_public"] * (1.0 if r["is_public"] else 0.0)
                 + weights["params"] * min(r["param_count"], 5) / 5.0)
        r["score"] = round(score, 4)
        if score >= high_score:
            r["level"] = "high"
        elif accessor or hidden:
            r["level"] = "low"
        else:
            r["level"] = "mid"
    return records, quantile


def build_blocks(records, classes, parents, max_block_bytes=MAX_BLOCK_BYTES,
                 max_subblock_methods=MAX_SUBBLOCK_METHODS):
    """分块（§6）：一块 = 一类；自由函数按文件 standalone；测试类 test-file。

    超 48KB 的块拆子块（≤6 方法/子块）；block_id 顺序编号。
    """
    by_class = defaultdict(list)   # parent_id（Class id）→ 方法
    by_file = defaultdict(list)    # 自由函数（File DEFINES）→ 按文件
    for p in parents:
        target = by_class if p["edge_type"] == "HAS_METHOD" else by_file
        target[p["method_id"]].append(p)

    cls_info = {c["id"]: c for c in classes}

    # 类内方法映射：method_id → parent_id；自由函数 → 所在文件
    method_class = {}
    method_file = {}
    for p in parents:
        if p["edge_type"] == "HAS_METHOD":
            method_class.setdefault(p["method_id"], p["parent_id"])
        else:
            method_file.setdefault(p["method_id"], p["parent_file"])

    groups = {}   # block_key → {"kind","name","file_path","methods":[...]}
    for rec in records:
        cid = method_class.get(rec["id"])
        if cid and cid in cls_info:
            c = cls_info[cid]
            kind = "test-file" if is_test_path(c["filePath"]) else "class"
            key = (kind, cid)
            g = groups.setdefault(key, {
                "kind": kind,
                "name": c.get("name") or "", "file_path": c["filePath"],
                "methods": []})
        else:
            fp = method_file.get(rec["id"]) or rec["file_path"]
            key = ("standalone", fp)
            g = groups.setdefault(key, {
                "kind": "standalone", "name": "(free functions)",
                "file_path": fp, "methods": []})
        g["methods"].append(rec)

    blocks = []
    for (kind, key), g in sorted(groups.items(), key=lambda kv: (kv[0][1] or "", kv[0][0])):
        methods = sorted(g["methods"], key=lambda r: -r["score"])
        status = "skipped" if kind == "test-file" else "pending"
        # 拆子块：context_bytes 超限或方法过多
        subblocks = []
        cur, cur_bytes = [], 0
        for m in methods:
            mb = m["content_bytes"] + 120  # 方法元数据固定开销估算
            if cur and (cur_bytes + mb > max_block_bytes
                        or len(cur) >= max_subblock_methods):
                subblocks.append(cur)
                cur, cur_bytes = [], 0
            cur.append(m)
            cur_bytes += mb
        if cur:
            subblocks.append(cur)

        if len(subblocks) == 1:
            blocks.append(_make_block(kind, g, methods, status))
        else:
            for i, sub in enumerate(subblocks):
                blocks.append(_make_block(kind, g, sub, status, part=(i + 1, len(subblocks))))
    for i, b in enumerate(blocks):
        b["block_id"] = f"B{i:04d}"
    return blocks


def _make_block(kind, group, methods, status, part=None):  # noqa: ARG001
    level_summary = defaultdict(int)
    for m in methods:
        level_summary[m["level"]] += 1
    high_ratio = level_summary["high"] / max(len(methods), 1)
    # 拆分后的子块 name 带部件号（B0007→ UrlRoute#1/3）
    name = group["name"] + (f"#{part[0]}/{part[1]}" if part else "")
    ctx_bytes = sum(m["content_bytes"] for m in methods) + 80 * len(methods)
    return {
        "kind": kind, "node_id": group.get("node_id"), "name": name,
        "file_path": group["file_path"],
        "cluster": module_of(group["file_path"]),
        "methods": methods,
        "level_summary": dict(level_summary),
        "priority": round(high_ratio * math.log2(1 + len(methods)), 4),
        "context_bytes": ctx_bytes,
        "status": status,
    }


def build_plan(client, repo, module=None, content_min_lines=CONTENT_MIN_LINES,
               progress=None):
    """survey → plan 全流程 → .ut-plan.json 数据结构。"""
    counts = client.count_symbols(repo)
    if progress:
        progress(f"survey: {counts}")
    inputs = fetch_plan_inputs(client, repo, module=module)
    if progress:
        progress(f"骨架 {len(inputs['skeleton'])} 方法 / {len(inputs['classes'])} 类")
    records = build_method_records(inputs, content_min_lines=content_min_lines,
                                   client=client, progress=progress)
    records, quantile = classify(records)
    blocks = build_blocks(records, inputs["classes"], inputs["parents"])

    level_all = defaultdict(int)
    for r in records:
        level_all[r["level"]] += 1
    modules = defaultdict(int)
    for r in records:
        modules[module_of(r["file_path"])] += 1

    plan = {
        "version": PLAN_VERSION,
        "repo": repo,
        "survey": {
            "classes": counts.get("classes", 0),
            "methods": len(records),
            "files": counts.get("files", 0),
            "call_edges": counts.get("calls", 0),
            "modules": dict(sorted(modules.items(), key=lambda kv: -kv[1])),
            "top_in_degree": [
                f"{r['id'].split(':')[1].rsplit('/', 1)[-1]}:{r['name']}({r['in_degree']})"
                for r in sorted(records, key=lambda x: -x["in_degree"])[:10]
                if r["in_degree"] > 0],
        },
        "quantile": quantile,
        "blocks": blocks,
        "stats": {
            "blocks": len(blocks),
            "methods": len(records),
            "high": level_all["high"], "mid": level_all["mid"], "low": level_all["low"],
        },
    }
    return plan


def cmd_select(plan_path, mode="full", module=None, limit=None, by_block=None):
    """select：标记本批要做的块（full/delta/module 三模式，§7）。

    - full:   按 priority 降序全量选中 pending 块（可选 limit 截断）
    - module: 只选 cluster == module 的块
    - delta:  跳过已 done 的块，选中其余 pending（增量重跑）
    返回选中的 block_id 列表（就地写回 plan 文件 status=selected）。
    """
    plan = load_plan(plan_path)
    blocks = plan["blocks"]
    selected = []
    if by_block:
        wanted = set(by_block)
        for b in blocks:
            if b["block_id"] in wanted and b["status"] in ("pending", "selected"):
                b["status"] = "selected"
                selected.append(b["block_id"])
    else:
        for b in sorted(blocks, key=lambda x: -x["priority"]):
            if b["status"] != "pending":
                continue
            if mode == "module" and module and b["cluster"] != module:
                continue
            selected.append(b["block_id"])
            b["status"] = "selected"
            if limit and len(selected) >= limit:
                break
    save_plan(plan, plan_path)
    return selected


def cmd_update(plan_path, block_ids, status="done"):
    """update：块状态机流转（selected→done/failed/pending）。返回实际更新数。"""
    if status not in BLOCK_STATES:
        raise ValueError(f"非法状态 {status!r}，可选 {sorted(BLOCK_STATES)}")
    plan = load_plan(plan_path)
    wanted = set(block_ids)
    n = 0
    for b in plan["blocks"]:
        if b["block_id"] in wanted:
            b["status"] = status
            n += 1
    save_plan(plan, plan_path)
    return n


def cmd_show(plan_path, brief=True):
    """show：stdout 打印 plan 摘要（块清单/状态/分级汇总）。"""
    plan = load_plan(plan_path)
    s = plan["stats"]
    print(f"repo={plan['repo']} version={plan['version']} "
          f"blocks={s['blocks']} methods={s['methods']} "
          f"(high {s['high']} · mid {s['mid']} · low {s['low']})")
    status_cnt = defaultdict(int)
    for b in plan["blocks"]:
        status_cnt[b["status"]] += 1
    print("status:", dict(sorted(status_cnt.items())))
    if not brief:
        for b in plan["blocks"]:
            print(f"  {b['block_id']} {b['status']:9s} pr={b['priority']:<7g} "
                  f"{b['kind']:10s} {b['name'][:36]:36s} {b['level_summary']}")


def load_plan(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_plan(plan, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)


# ── CLI ───────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ut-plan.py", description="测试规划器（L3）：分级 + 分块 → .ut-plan.json")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="建档规划（survey → plan）")
    p.add_argument("--repo", help="仓库名（默认 QTAG_GN_REPO）")
    p.add_argument("-o", "--output", default=".ut-plan.json")
    p.add_argument("--module", help="只规划该目录前缀（单模块模式）")
    p.add_argument("--content-min-lines", type=int, default=CONTENT_MIN_LINES)
    p.add_argument("--limit-methods", type=int, help="冒烟：截断方法数（调试用）")

    s = sub.add_parser("select", help="标记本批要做的块（full/delta/module）")
    s.add_argument("plan", help=".ut-plan.json 路径")
    s.add_argument("--mode", choices=("full", "delta", "module"), default="full")
    s.add_argument("--module", help="module 模式：cluster 名")
    s.add_argument("--limit", type=int, help="最多选中 N 块")
    s.add_argument("--block", action="append", help="指定 block_id（可重复）")

    u = sub.add_parser("update", help="块状态流转（done/failed/pending）")
    u.add_argument("plan", help=".ut-plan.json 路径")
    u.add_argument("blocks", nargs="+", help="block_id 列表")
    u.add_argument("--status", choices=sorted(BLOCK_STATES), default="done")

    w = sub.add_parser("show", help="查看 plan 摘要")
    w.add_argument("plan", help=".ut-plan.json 路径")
    w.add_argument("--all", action="store_true", help="打印全部块")

    args = parser.parse_args(argv)

    if args.command == "plan":
        client = RestQueryClient(repo=args.repo or os.environ.get("QTAG_GN_REPO"))
        repo = client.repo
        if not repo:
            print("ut-plan: 需要 --repo 或 QTAG_GN_REPO", file=sys.stderr)
            return 2

        def progress(msg):
            print(f"[plan] {msg}", file=sys.stderr, flush=True)

        try:
            plan = build_plan(client, repo, module=args.module,
                              content_min_lines=args.content_min_lines,
                              progress=progress)
        except GraphAccessError as e:
            print(f"ut-plan: 硬终止（kind={e.kind}）: {e}", file=sys.stderr)
            return 2
        if args.limit_methods:
            # 冒烟截断：只保留第一个块
            plan["blocks"] = plan["blocks"][:1]
            b0 = plan["blocks"][0]
            plan["stats"] = {"blocks": 1, "methods": len(b0["methods"]),
                             "high": b0["level_summary"].get("high", 0),
                             "mid": b0["level_summary"].get("mid", 0),
                             "low": b0["level_summary"].get("low", 0)}
        save_plan(plan, args.output)
        st = plan["stats"]
        print(f"→ {args.output}: {st['blocks']} 块 / {st['methods']} 方法 "
              f"(high {st['high']} · mid {st['mid']} · low {st['low']})", file=sys.stderr)
        return 0

    if args.command == "select":
        selected = cmd_select(args.plan, mode=args.mode, module=args.module,
                              limit=args.limit, by_block=args.block)
        print(f"selected {len(selected)}: {' '.join(selected[:10])}"
              f"{' …' if len(selected) > 10 else ''}")
        return 0

    if args.command == "update":
        n = cmd_update(args.plan, args.blocks, status=args.status)
        print(f"updated {n} blocks → {args.status}")
        return 0

    if args.command == "show":
        cmd_show(args.plan, brief=not args.all)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
