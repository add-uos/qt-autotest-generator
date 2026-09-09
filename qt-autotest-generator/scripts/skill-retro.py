#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""skill-retro.py —— Mode 7 技能复盘：结构化沉淀实战中的技能问题，供自动迭代。

六类问题 taxonomy（category）：
  G 文档缺口   规则没写/写了没读到/有歧义        → references/*.md 补丁
  R 规则缺陷   Iron Law 冲突/不可执行/过约束     → SKILL.md 修订
  S 脚本缺陷   脚本 bug/输出误导/缺能力          → scripts/*.py 修复
  Q 生成质量   用例反模式/漏分支/断言弱          → test-code-gen/test-types 补强
  E 工具链坑   编译器/gtest/CMake/测试基建坑     → environment-check/build-verifier 附录
  T 触发路由   该触发没触发/触发错模式           → SKILL.md description + evals

severity：P0 结果不可信（假绿/数据错）｜P1 效率受损（耗时/走弯路）｜P2 体验瑕疵。

子命令：
  add      新增问题记录（upsert 到 backlog，id 自动 {category}-{序号}）
  list     列出记录（--status/--severity/--category 过滤）
  resolve  标记已解决（--note 记录修复位置）
  reopen   重新打开
  report   生成 Markdown 复盘报告（--out 目录）
  eval-suggest  从 open 项生成 evals 候选 JSON 片段（stdout）

backlog 默认锚定技能目录 retro/backlog.json，--backlog 可覆盖。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

SKILL = "qt-autotest-generator"
VERSION = "1.0.0"
DEFAULT_BACKLOG = Path(__file__).resolve().parent.parent / "retro" / "backlog.json"

CATEGORIES = {
    "G": "文档缺口", "R": "规则缺陷", "S": "脚本缺陷",
    "Q": "生成质量", "E": "工具链坑", "T": "触发路由",
}
SEVERITIES = ("P0", "P1", "P2")
STATUSES = ("open", "resolved")
CATEGORY_KEYS = tuple(CATEGORIES)


def _load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "items": []}


def _save(data, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _next_id(data, category):
    """id 取现有最大序号+1（非 count）：删除中间项后不会复用 id 防冲突。"""
    prefix = f"{category}-"
    seqs = [int(i["id"].split("-", 1)[1])
            for i in data["items"] if i["id"].startswith(prefix)
            and i["id"].split("-", 1)[1].isdigit()]
    return f"{category}-{(max(seqs) + 1 if seqs else 1):03d}"


def cmd_add(backlog, category, title, severity="P2", evidence="", fix="",
            status="open", note=""):
    """新增问题记录；id 按 {category}-{序号} 自增，返回 id。"""
    if category not in CATEGORIES:
        raise ValueError(f"category 须为 {'/'.join(CATEGORY_KEYS)}")
    if severity not in SEVERITIES:
        raise ValueError(f"severity 须为 {'/'.join(SEVERITIES)}")
    if status not in STATUSES:
        raise ValueError(f"status 须为 {'/'.join(STATUSES)}")
    data = _load(backlog)
    item = {
        "id": _next_id(data, category),
        "category": category, "title": title, "severity": severity,
        "evidence": evidence, "fix": fix, "status": status, "note": note,
        "created": time.strftime("%Y-%m-%d"),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data["items"].append(item)
    _save(data, backlog)
    return item["id"]


def _find(data, item_id):
    for it in data["items"]:
        if it["id"] == item_id:
            return it
    raise ValueError(f"未找到记录 {item_id}")


def cmd_resolve(backlog, item_id, note=""):
    """标记 resolved；note 记录修复位置（commit/文件/小节）。"""
    data = _load(backlog)
    it = _find(data, item_id)
    it["status"] = "resolved"
    it["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if note:
        it["note"] = note
    _save(data, backlog)
    return it


def cmd_reopen(backlog, item_id, note=""):
    """重新打开（回归复发/修复不彻底）。"""
    data = _load(backlog)
    it = _find(data, item_id)
    it["status"] = "open"
    it["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if note:
        it["note"] = note
    _save(data, backlog)
    return it


def cmd_list(backlog, status=None, severity=None, category=None):
    """过滤列出记录（不写盘）。"""
    data = _load(backlog)
    items = data["items"]
    if status:
        items = [i for i in items if i["status"] == status]
    if severity:
        items = [i for i in items if i["severity"] == severity]
    if category:
        items = [i for i in items if i["category"] == category]
    return items


def build_report(items, backlog_path):
    """纯函数：items → Markdown 复盘报告。"""
    n_total = len(items)
    n_open = sum(1 for i in items if i["status"] == "open")
    n_resolved = n_total - n_open
    lines = [
        f"# 技能复盘报告（{SKILL}）",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')} ｜ backlog：{backlog_path}",
        f"> 共 {n_total} 项：open {n_open} / resolved {n_resolved}",
        "",
        "## 分类统计",
        "",
        "| 分类 | open | resolved |",
        "|---|---|---|",
    ]
    for key, name in CATEGORIES.items():
        sub = [i for i in items if i["category"] == key]
        o = sum(1 for i in sub if i["status"] == "open")
        r = len(sub) - o
        if sub:
            lines.append(f"| {key} {name} | {o} | {r} |")
    for sev in SEVERITIES:
        sub = [i for i in items if i["status"] == "open" and i["severity"] == sev]
        if not sub:
            continue
        lines += ["", f"## {sev}（{'结果不可信' if sev == 'P0' else '效率受损' if sev == 'P1' else '体验瑕疵'}，open）", ""]
        for i in sub:
            lines.append(f"### {i['id']} {i['title']}")
            lines.append("")
            if i.get("evidence"):
                lines.append(f"- 证据：{i['evidence']}")
            if i.get("fix"):
                lines.append(f"- 修复建议：{i['fix']}")
            if i.get("note"):
                lines.append(f"- 备注：{i['note']}")
            lines.append("")
    resolved = [i for i in items if i["status"] == "resolved"]
    if resolved:
        lines += ["## 已解决（本实战沉淀）", ""]
        for i in resolved:
            lines.append(f"- **{i['id']}** {i['title']} —— {i.get('fix') or i.get('note') or '已修复'}")
        lines.append("")
    return "\n".join(lines)


def cmd_report(backlog, out_dir=None):
    """生成报告；out_dir 给定时写 retro-report.md，否则返回文本。"""
    items = _load(backlog)["items"]
    md = build_report(items, os.path.abspath(backlog))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "retro-report.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        return path
    return md


def cmd_eval_suggest(backlog, severity=None, all_status=False):
    """生成 evals 候选片段（JSON 数组，stdout 供粘贴进 evals/*.json）。

    默认取 open 项（待验证的行为约束）；--all 时含 resolved
    （固化为回归 eval，验证修复不复发）。
    """
    items = cmd_list(backlog, status=None if all_status else "open",
                     severity=severity)
    out = []
    for i in items:
        out.append({
            "id": f"retro-{i['id'].lower()}",
            "source": i["id"],
            "category": i["category"],
            "severity": i["severity"],
            "title": i["title"],
            "prompt_hint": f"在涉及「{i['title']}」的场景下验证技能行为",
            "success_criteria": [f"遵循修复建议：{i['fix']}"] if i.get("fix") else [],
            "failure_indicators": [i["evidence"]] if i.get("evidence") else [],
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="skill-retro.py",
                                 description="Mode 7 技能复盘：问题沉淀与自动迭代")
    ap.add_argument("--backlog", default=str(DEFAULT_BACKLOG),
                    help="backlog JSON 路径（默认技能目录 retro/backlog.json）")
    sub = ap.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="新增问题记录")
    a.add_argument("--category", required=True, choices=CATEGORY_KEYS,
                   help="G文档/R规则/S脚本/Q生成质量/E工具链/T触发")
    a.add_argument("--title", required=True)
    a.add_argument("--severity", choices=SEVERITIES, default="P2")
    a.add_argument("--evidence", default="", help="复现/引用证据")
    a.add_argument("--fix", default="", help="修复建议（改哪个文件哪一节）")
    a.add_argument("--status", choices=STATUSES, default="open")
    a.add_argument("--note", default="")

    l = sub.add_parser("list", help="列出记录")
    l.add_argument("--status", choices=STATUSES)
    l.add_argument("--severity", choices=SEVERITIES)
    l.add_argument("--category", choices=CATEGORY_KEYS)

    r = sub.add_parser("resolve", help="标记已解决")
    r.add_argument("id")
    r.add_argument("--note", default="", help="修复位置（commit/文件/小节）")

    ro = sub.add_parser("reopen", help="重新打开")
    ro.add_argument("id")
    ro.add_argument("--note", default="")

    rp = sub.add_parser("report", help="生成 Markdown 复盘报告")
    rp.add_argument("--out", help="输出目录（缺省打印 stdout）")

    ev = sub.add_parser("eval-suggest", help="生成 evals 候选 JSON（stdout）")
    ev.add_argument("--severity", choices=SEVERITIES)
    ev.add_argument("--all", action="store_true",
                    help="含 resolved 项（固化为回归 eval）")

    args = ap.parse_args(argv)
    try:
        if args.command == "add":
            print(cmd_add(args.backlog, args.category, args.title,
                          severity=args.severity, evidence=args.evidence,
                          fix=args.fix, status=args.status, note=args.note))
            return 0
        if args.command == "list":
            items = cmd_list(args.backlog, status=args.status,
                             severity=args.severity, category=args.category)
            if not items:
                print("(空)")
                return 0
            for i in items:
                print(f"{i['id']} [{i['severity']}/{i['status']}] {i['title']}"
                      + (f"  → {i['fix']}" if i.get("fix") else ""))
            return 0
        if args.command == "resolve":
            it = cmd_resolve(args.backlog, args.id, note=args.note)
            print(f"{it['id']} → resolved")
            return 0
        if args.command == "reopen":
            it = cmd_reopen(args.backlog, args.id, note=args.note)
            print(f"{it['id']} → open")
            return 0
        if args.command == "report":
            result = cmd_report(args.backlog, out_dir=args.out)
            if args.out:
                print(f"→ {result}")
            else:
                print(result)
            return 0
        if args.command == "eval-suggest":
            print(json.dumps(cmd_eval_suggest(args.backlog,
                                              severity=args.severity,
                                              all_status=args.all),
                             ensure_ascii=False, indent=1))
            return 0
    except ValueError as e:
        print(f"skill-retro: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
