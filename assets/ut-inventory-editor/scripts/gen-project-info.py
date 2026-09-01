#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Uniontech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later
"""
gen-project-info.py — 整合项目远端/分支信息 → project-branches.json

数据源（优先级递降）:
  1. CSV 产品信息表（--csv, 默认 ~/debug/product_info_*.csv）
  2. deepin-project-downloader-backen.py 的 PROJECT_REPOS（GitHub 仓库确认）
  3. fallback: master

用法:
  python3 gen-project-info.py                       # 默认路径
  python3 gen-project-info.py --csv /path/x.csv -o project-branches.json
"""

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV_GLOB = "~/debug/product_info_*.csv"
DEFAULT_DOWNLOADER = "/usr/bin/deepin-project-downloader-backen.py"
DEFAULT_OUT = SCRIPT_DIR / "project-branches.json"
DEFAULT_BRANCH = "master"
DEFAULT_ORG = "linuxdeepin"


def find_latest_csv():
    cands = sorted(Path("/home/zhy/debug").glob("product_info_*.csv")) if Path("/home/zhy/debug").is_dir() else []
    return cands[-1] if cands else None


def load_csv(path):
    """CSV → {项目名: {branch, host, type, pkgs}}"""
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("项目名") or "").strip()
            if not name:
                continue
            branch = (row.get("分支") or "").strip()
            host = (row.get("代码仓库") or "").strip().lower()
            out[name] = {
                "branch": branch if branch and branch != "NA" else "",
                "host": "gerrit" if "gerrit" in host else "github",
                "type": (row.get("类型") or "").strip(),
                "pkgs": [p.strip() for p in (row.get("包名") or "").splitlines() if p.strip()],
            }
    return out


def load_downloader_repos(path):
    """downloader PROJECT_REPOS → {项目名: github_repo_name}"""
    try:
        src = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return dict(re.findall(
        r'"([\w.\-]+)":\s*\{[^{}]*?"github":\s*"https://github\.com/linuxdeepin/([\w.\-]+)\.git"',
        src, re.S))


def main():
    ap = argparse.ArgumentParser(description="生成 project-branches.json")
    ap.add_argument("--csv", default=None, help="产品信息 CSV（默认取 ~/debug 最新 product_info_*.csv）")
    ap.add_argument("--downloader", default=DEFAULT_DOWNLOADER, help="downloader 后端脚本路径")
    ap.add_argument("-o", "--output", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    csv_path = Path(args.csv) if args.csv else find_latest_csv()
    if not csv_path or not csv_path.is_file():
        print("❌ 找不到产品信息 CSV（可用 --csv 指定）", file=sys.stderr)
        sys.exit(1)

    csv_data = load_csv(csv_path)
    dl_repos = load_downloader_repos(args.downloader)
    print(f"📥 CSV: {csv_path} ({len(csv_data)} 项目)")
    print(f"📥 downloader PROJECT_REPOS: {len(dl_repos)} 仓库")

    projects = {}
    all_names = sorted(set(csv_data) | set(dl_repos))
    for name in all_names:
        c = csv_data.get(name, {})
        dl = dl_repos.get(name)
        branch = c.get("branch") or ""
        source = "csv" if branch else ("downloader" if dl else "fallback")
        if not branch:
            branch = DEFAULT_BRANCH
        entry = {
            "org": DEFAULT_ORG,
            "branch": branch,
            "host": c.get("host") or ("github" if dl else "github"),
            "source": source,
        }
        if c.get("host") == "gerrit":
            entry["github_mirror"] = f"{DEFAULT_ORG}/{dl or name}"
        projects[name] = entry

    doc = {
        "_meta": {
            "generated": date.today().isoformat(),
            "csv": str(csv_path),
            "default_branch": DEFAULT_BRANCH,
            "org": DEFAULT_ORG,
            "count": len(projects),
        },
        "projects": projects,
    }
    Path(args.output).write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    print(f"✅ 写入 {args.output} ({len(projects)} 项目)")
    print("   分支分布:", dict(Counter(p["branch"] for p in projects.values())))
    print("   来源分布:", dict(Counter(p["source"] for p in projects.values())))


if __name__ == "__main__":
    main()
