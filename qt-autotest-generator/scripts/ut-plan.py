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
import shutil
import subprocess
import sys
import time
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


# ── report（Phase 6）：按模块聚合 ───────────────────────────────

LEVEL_KEYS = ("high", "mid", "low")


def build_report(plan):
    """按模块（cluster）聚合块状态/分级/验证结果（纯函数，供 CLI 与 scorer 消费）。

    聚合键 = 块的 cluster（即 module_of(file_path)）；验证结果取各块
    last_verify.run 的 passed/failed 求和（未 verify 的块不计）。
    """
    mods = {}
    for b in plan["blocks"]:
        m = mods.setdefault(b["cluster"], {
            "module": b["cluster"], "blocks": 0, "methods": 0,
            "high": 0, "mid": 0, "low": 0,
            "pending": 0, "selected": 0, "done": 0, "failed": 0,
            "test_passed": 0, "test_failed": 0,
        })
        m["blocks"] += 1
        m["methods"] += len(b["methods"])
        for lv in LEVEL_KEYS:
            m[lv] += (b.get("level_summary") or {}).get(lv, 0)
        if b["status"] in BLOCK_STATES:
            m[b["status"]] += 1
        run = (b.get("last_verify") or {}).get("run") or {}
        m["test_passed"] += run.get("passed", 0)
        m["test_failed"] += run.get("failed", 0)
    modules = sorted(mods.values(), key=lambda x: (-x["methods"], x["module"]))
    keys = ["blocks", "methods", *LEVEL_KEYS,
            "pending", "selected", "done", "failed", "test_passed", "test_failed"]
    total = {k: sum(m[k] for m in modules) for k in keys}
    return {"repo": plan.get("repo"), "version": plan.get("version"),
            "modules": modules, "total": total}


def cmd_report(plan_path, as_json=False, top=None):
    """report：按模块聚合 plan（stdout 表格；--json 输出供 CI/scorer 消费）。"""
    rep = build_report(load_plan(plan_path))
    if as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return rep
    t = rep["total"]
    print(f"repo={rep['repo']} version={rep['version']} modules={len(rep['modules'])} "
          f"blocks={t['blocks']} methods={t['methods']}")
    print(f"{'module':<44} {'blk':>4} {'mtd':>5} {'hi':>5} {'mid':>5} {'low':>5}"
          f" {'pend':>5} {'sel':>4} {'done':>5} {'fail':>5} {'test':>6}")
    shown = rep["modules"][:top] if top else rep["modules"]
    for m in shown:
        print(f"{m['module'][:43]:<44} {m['blocks']:>4} {m['methods']:>5} "
              f"{m['high']:>5} {m['mid']:>5} {m['low']:>5} "
              f"{m['pending']:>5} {m['selected']:>4} {m['done']:>5} {m['failed']:>5} "
              f"{m['test_passed']:>6}")
    if top and len(rep["modules"]) > top:
        print(f"… 另有 {len(rep['modules']) - top} 个模块（--top 控制）")
    return rep


# ── generate（Phase 4，逐块）：上下文组装 → 生成会话输入 ────────────

PROMPT_CONVENTIONS = """\
## 生成约定（必须遵守）

1. 输出单个自包含 C++ 测试文件，框架 = GTest（TEST(Suite, Case) 宏，不用 gmock）。
2. 文件头必须有 SPDX 头：
   // SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
   //
   // SPDX-License-Identifier: GPL-3.0-or-later
3. 测试套名 = <被测类名>Test（如 SqliteHelperTest）；套内用例覆盖块内每个
   high 方法的主路径与关键分支；mid 方法覆盖主路径；纯存取器可不测。
4. include 风格：项目头用尖括号包含路径（参照头文件内容中的项目内路径推算），
   Qt 头单独分组。
5. 只能调用被测类公开可及的 API；私有方法不可直接调用（static inline 例外：
   若类本身可构造且方法为 public static，则可直接调）。
6. 禁止：网络/DBus/图形界面真实交互；必须能在 CI 无头环境跑通。
   涉及临时文件/数据库时用 QTemporaryDir 隔离。
7. 断言用 EXPECT_*/ASSERT_*；每个用例独立构造与清理，不依赖执行顺序。
8. 不要输出任何解释性文字，只输出代码。
"""


def _fmt_adjacency(neighbors, limit=30):
    """邻接清单 → ≤limit 行文本（谁调我/我调谁：名字+文件基名）。"""
    lines = []
    for n in neighbors[:limit]:
        base = n["peer_file"].rsplit("/", 1)[-1] if n["peer_file"] else "?"
        lines.append(f"- ({n['direction']}) {n['peer']}  [{base}]")
    if len(neighbors) > limit:
        lines.append(f"- …另有 {len(neighbors) - limit} 条略")
    return "\n".join(lines) if lines else "（无调用边）"


def _find_existing_tests(repo_root, class_name):
    """本地 autotests/ 下找已测试骨架（grep 类名，取首个命中文件 ≤4KB 摘录）。"""
    if not repo_root:
        return None
    at_dir = os.path.join(repo_root, "autotests")
    if not os.path.isdir(at_dir):
        return None
    import subprocess as _sp
    try:
        r = _sp.run(["grep", "-rl", "--include=*.cpp", class_name, at_dir],
                    capture_output=True, text=True, timeout=30)
        hits = [x for x in r.stdout.splitlines() if x]
    except Exception:
        return None
    if not hits:
        return None
    path = hits[0]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return {"path": os.path.relpath(path, repo_root),
                    "excerpt": f.read(4096)}
    except OSError:
        return None


def cmd_generate(plan_path, block_id, client, out_dir=".ut-gen",
                 repo_root=None, max_bytes=MAX_BLOCK_BYTES):
    """generate：按 §6 组装单块上下文 → <out_dir>/<block_id>/context.md。

    组装件：块信息 + 类头文件内容 + 方法清单与方法体（行切片）+ 一跳邻接
    （≤30 条，只有名字+路径不带内容）+ 既有测试骨架（≤4KB）+ 固定生成约定。
    超限从尾部截断方法体（方法已按 score 降序，high 优先保留）。
    就地回写块状态 pending→selected（failed 允许重试）。
    返回 context.md 路径。
    """
    plan = load_plan(plan_path)
    block = next((b for b in plan["blocks"] if b["block_id"] == block_id), None)
    if block is None:
        raise ValueError(f"块 {block_id} 不存在")
    if block["status"] == "done":
        raise ValueError(f"块 {block_id} 已 done，如需重做先 update 回 pending")
    if block["status"] == "pending":
        block["status"] = "selected"
        save_plan(plan, plan_path)

    mids = [m["id"] for m in block["methods"]]
    # 文件集：块文件 + 各方法文件（去重）——头文件内容优先
    files = list(dict.fromkeys(
        [block["file_path"]] + [m["file_path"] for m in block["methods"]]))
    contents = client.file_contents(files)
    # 行切片方法体；失败降级符号 content
    bodies, missed = {}, [m["id"] for m in block["methods"]
                          if m.get("body_source") != "file-slice"]
    for m in block["methods"]:
        c = contents.get(m["file_path"])
        body = slice_body(c, m["start_line"], m["end_line"]) if c else ""
        if body:
            bodies[m["id"]] = body
    still = [mid for mid in mids if mid not in bodies]
    if still:
        syms = client.symbol_contents(still)
        bodies.update(syms)
    neighbors = client.method_neighbors(mids)
    existing = _find_existing_tests(repo_root, block["name"].split("#")[0])

    parts = [f"# 生成上下文：块 {block['block_id']} {block['name']}\n",
             f"仓库 {plan['repo']} · kind={block['kind']} · file={block['file_path']} "
             f"· levels={block['level_summary']} · priority={block['priority']}\n",
             "\n## 方法清单与方法体（按 score 降序）\n"]
    for m in block["methods"]:
        parts.append(f"\n### {m['name']}  (id={m['id']})\n"
                     f"lines={m['lines']} ({m['start_line']}-{m['end_line']}) "
                     f"cc={m['cc_proxy']} in_deg={m['in_degree']} "
                     f"public={m['is_public']} level={m['level']} "
                     f"body_source={m['body_source']}\n"
                     f"```cpp\n{bodies.get(m['id'], '// （无内容）')}\n```\n")
    parts.append("\n## 相关文件内容\n")
    for fp in files:
        parts.append(f"\n### 文件 {fp}\n```cpp\n{contents.get(fp, '// （未取到）')}\n```\n")
    parts.append(f"\n## 一跳邻接（≤30 条，只列名字与路径）\n{_fmt_adjacency(neighbors)}\n")
    if existing:
        parts.append(f"\n## 既有测试骨架（{existing['path']} 摘录）\n"
                     f"```cpp\n{existing['excerpt']}\n```\n")
    parts.append("\n" + PROMPT_CONVENTIONS)

    text = "".join(parts)
    # 上限按字节计：先编码再切片（中文多字节直接按字符切会超限）
    raw = text.encode("utf-8")
    if len(raw) > max_bytes:
        text = (raw[:max_bytes].decode("utf-8", errors="ignore")
                + f"\n… [truncated to fit {max_bytes} bytes]\n")
    ctx_dir = os.path.join(out_dir, block_id)
    os.makedirs(ctx_dir, exist_ok=True)
    ctx_path = os.path.join(ctx_dir, "context.md")
    with open(ctx_path, "w", encoding="utf-8") as f:
        f.write(text)
    return ctx_path


# ── verify（Phase 5）：编译 + 跑测 + 状态回写 ───────────────────────

def _default_runner(cmd, cwd=None, timeout=600):
    """子进程执行（verify 的真实通道；测试中注入替身）。"""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def _parse_suite_name(test_file):
    """从测试文件解析 GTest 套名（首个 TEST(X, ...) 的 X）。"""
    with open(test_file, "r", encoding="utf-8", errors="replace") as f:
        m = re.search(r"\bTEST(?:_F)?\s*\(\s*(\w+)\s*,", f.read())
    return m.group(1) if m else None


def _module_paths(cluster, repo_root, dest_dir=None, build_dir="build-autotests"):
    """cluster → (dest_dir, target, binary_path)。

    dfm-base 特例：autotests/libs/dfm-base（与既有仓库布局一致）；
    其它模块默认 autotests/libs/<tail>，可用 --dest-dir 覆盖。
    """
    tail = cluster.split("/")[-1] if cluster else "misc"
    dest = dest_dir or f"autotests/libs/{tail}"
    target = f"ut-{tail}"
    binary = os.path.join(build_dir, "autotests", "libs", tail, target)
    return dest, target, binary


def cmd_verify(plan_path, block_id, repo_root, test_file=None,
               build_dir="build-autotests", dest_dir=None, skip_run=False,
               runner=_default_runner):
    """verify：拷入测试文件 → cmake 构建 → 跑测 → 回写块状态。

    编译+跑测通过 → done；任一步失败 → failed（last_verify 存错误摘要）。
    返回 (ok, detail)。
    """
    plan = load_plan(plan_path)
    block = next((b for b in plan["blocks"] if b["block_id"] == block_id), None)
    if block is None:
        raise ValueError(f"块 {block_id} 不存在")

    gen_dir = os.path.join(os.path.dirname(os.path.abspath(plan_path)),
                           ".ut-gen", block_id)
    if not test_file:
        cands = sorted(
            f for f in os.listdir(gen_dir) if f.startswith("test_")
            and f.endswith(".cpp")) if os.path.isdir(gen_dir) else []
        if not cands:
            raise ValueError(f"{gen_dir} 下无 test_*.cpp，先用 --test-file 指定")
        test_file = os.path.join(gen_dir, cands[0])
    suite = _parse_suite_name(test_file)

    dest, target, binary = _module_paths(block["cluster"], repo_root,
                                         dest_dir=dest_dir, build_dir=build_dir)
    abs_dest_dir = os.path.join(repo_root, dest)
    os.makedirs(abs_dest_dir, exist_ok=True)
    # 已在仓库内则原位，否则拷入
    test_rel = os.path.relpath(os.path.abspath(test_file), repo_root)
    if test_rel.startswith(".."):
        test_rel = os.path.join(dest, os.path.basename(test_file))
        shutil.copy(test_file, os.path.join(repo_root, test_rel))

    errs, logs = [], []
    ok = True
    # 1. 重新 configure（GLOB 拾取新文件）+ 构建目标
    for cmd, desc in (
        (["cmake", "-S", repo_root, "-B", build_dir], "cmake configure"),
        (["cmake", "--build", build_dir, "--target", target, "-j", "4"],
         f"build {target}"),
    ):
        r = runner(cmd, cwd=repo_root)
        logs.append(f"$ {' '.join(cmd)}\n{(r.stdout or '')[-2000:]}")
        if r.returncode != 0:
            errs.append(f"{desc} 失败（exit {r.returncode}）:\n{(r.stderr or '')[-2000:]}")
            ok = False
            break
    # 2. 跑测（gtest_filter 限定本块套名）
    run_summary = None
    if ok and not skip_run:
        if not os.path.exists(os.path.join(repo_root, binary)):
            errs.append(f"二进制不存在：{binary}")
            ok = False
        else:
            # gtest 仅识别 --gtest_filter=X 等号形式（空格分隔会打印 usage 并静默退出）
            filt = [f"--gtest_filter={suite}.*"] if suite else []
            r = runner([os.path.join(".", binary)] + filt, cwd=repo_root)
            logs.append(f"$ {binary} {' '.join(filt)}\n{(r.stdout or '')[-3000:]}")
            passed = re.search(r"\[  PASSED  \]\s*(\d+) tests?", r.stdout or "")
            failed = re.search(r"\[  FAILED  \]\s*(\d+) tests?", r.stdout or "")
            run_summary = {"passed": int(passed.group(1)) if passed else 0,
                           "failed": int(failed.group(1)) if failed else 0,
                           "exit": r.returncode}
            if r.returncode != 0:
                errs.append(f"跑测失败（exit {r.returncode}）:\n{(r.stderr or '')[-1500:]}")
                ok = False

    # 3. 状态回写（原子写盘）
    block["status"] = "done" if ok else "failed"
    block["last_verify"] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "test_file": test_rel, "suite": suite,
        "run": run_summary,
        "error": "\n".join(errs)[-2000:] if errs else None,
    }
    save_plan(plan, plan_path)
    return ok, {"block": block_id, "status": block["status"],
                "test_file": test_rel, "run": run_summary,
                "error": block["last_verify"]["error"]}


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

    rp = sub.add_parser("report", help="按模块聚合 plan（状态/分级/验证结果）")
    rp.add_argument("plan", help=".ut-plan.json 路径")
    rp.add_argument("--json", action="store_true", help="输出 JSON（供 CI/scorer 消费）")
    rp.add_argument("--top", type=int, help="只显示前 N 个模块（按方法数）")

    g = sub.add_parser("generate", help="单块上下文组装（生成会话输入）")
    g.add_argument("plan", help=".ut-plan.json 路径")
    g.add_argument("--block", required=True, help="block_id")
    g.add_argument("--out-dir", default=".ut-gen")
    g.add_argument("--repo-root", help="本地仓库根（找既有测试骨架用）")
    g.add_argument("--repo", help="仓库名（默认 QTAG_GN_REPO）")

    v = sub.add_parser("verify", help="拷入测试→cmake 构建→跑测→回写状态")
    v.add_argument("plan", help=".ut-plan.json 路径")
    v.add_argument("--block", required=True, help="block_id")
    v.add_argument("--repo-root", required=True, help="本地仓库根")
    v.add_argument("--test-file", help="测试文件（默认 .ut-gen/<block>/test_*.cpp）")
    v.add_argument("--build-dir", default="build-autotests")
    v.add_argument("--dest-dir", help="拷入目录（默认 autotests/libs/<模块>）")
    v.add_argument("--skip-run", action="store_true", help="只编译不跑测")

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

    if args.command == "report":
        cmd_report(args.plan, as_json=args.json, top=args.top)
        return 0

    if args.command == "generate":
        client = RestQueryClient(repo=args.repo or os.environ.get("QTAG_GN_REPO"))
        if not client.repo:
            print("ut-plan: 需要 --repo 或 QTAG_GN_REPO", file=sys.stderr)
            return 2
        try:
            ctx = cmd_generate(args.plan, args.block, client,
                               out_dir=args.out_dir, repo_root=args.repo_root)
        except (ValueError, GraphAccessError) as e:
            print(f"ut-plan: generate 失败: {e}", file=sys.stderr)
            return 2
        print(f"context → {ctx}")
        return 0

    if args.command == "verify":
        try:
            ok, detail = cmd_verify(args.plan, args.block, args.repo_root,
                                    test_file=args.test_file,
                                    build_dir=args.build_dir,
                                    dest_dir=args.dest_dir,
                                    skip_run=args.skip_run)
        except (ValueError, subprocess.TimeoutExpired) as e:
            print(f"ut-plan: verify 失败: {e}", file=sys.stderr)
            return 2
        if detail["error"]:
            print(detail["error"], file=sys.stderr)
        print(f"{detail['block']} → {detail['status']} "
              f"({detail['test_file']}, run={detail['run']})")
        return 0 if ok else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
