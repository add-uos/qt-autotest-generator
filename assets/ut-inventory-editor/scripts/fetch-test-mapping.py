#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""fetch-test-mapping.py — 薄壳转发到 mcp-scan.py（GitNexus 单栈，唯一实现）。

GitNexus 迁移后，test-mapping 逻辑收敛到 mcp-scan.py 的 `test-mapping`
子命令，本文件仅保留历史 CLI 入口做兼容转发，全部参数 1:1 透传：
--project / --inventory / --mapping-in / --mapping-out / --report /
--mcp-url / --repo-root / --dry-run / --verbose
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MCP_SCAN = os.path.join(SCRIPT_DIR, "mcp-scan.py")


def main():
    os.execv(sys.executable,
             [sys.executable, MCP_SCAN, "test-mapping", *sys.argv[1:]])


if __name__ == "__main__":
    main()
