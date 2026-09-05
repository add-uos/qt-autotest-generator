#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""fetch-mcp-data.py — 薄壳转发到 mcp-scan.py（GitNexus 单栈，唯一实现）。

GitNexus 迁移后，端到端采集逻辑全部收敛到 mcp-scan.py（fetch /
extract-branches 子命令），本文件仅保留历史 CLI 入口做兼容转发：

  - `extract-branches ...` 前缀 → mcp-scan.py extract-branches ...
  - 其余（默认）              → mcp-scan.py fetch ...

参数 1:1 透传（--project/--output/--file-pattern/--mcp-url/--limit/
--base-sha/--summary/--keep-dump/--incremental/--existing/--repo-root）。
旧版独有的 --branch / --org 已废弃（GitNexus 基线取 list_repos.lastCommit），
传入时丢弃并警告。
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MCP_SCAN = os.path.join(SCRIPT_DIR, "mcp-scan.py")

DEPRECATED_FLAGS = ("--branch", "--org")


def main():
    argv = sys.argv[1:]
    subcmd = "fetch"
    if argv and argv[0] == "extract-branches":
        subcmd, argv = argv[0], argv[1:]

    kept, skip_next = [], False
    for a in argv:
        if skip_next:                       # 废弃旗标的取值
            skip_next = False
            continue
        if a in DEPRECATED_FLAGS:
            print(f"⚠️  {a} 已废弃（GitNexus 基线取 list_repos.lastCommit），忽略",
                  file=sys.stderr)
            skip_next = True
            continue
        if a.startswith(("--branch=", "--org=")):
            print(f"⚠️  {a.split('=', 1)[0]} 已废弃，忽略", file=sys.stderr)
            continue
        kept.append(a)

    os.execv(sys.executable, [sys.executable, MCP_SCAN, subcmd, *kept])


if __name__ == "__main__":
    main()
