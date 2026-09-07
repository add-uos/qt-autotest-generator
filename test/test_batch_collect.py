# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""batch-collect.py 单元测试：run_step 超时与失败不抛异常。"""

import subprocess
import sys
import os

import pytest


class TestRunStep:
    def test_timeout_returns_failure_not_raise(self, batch_collect, tmp_path):
        """子进程超时 → (False, elapsed, tail)，不再抛 TimeoutExpired 崩编排。"""
        cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
        ok, elapsed, tail = batch_collect.run_step(cmd, "slow", tmp_path, timeout=1)
        assert ok is False
        assert elapsed >= 1.0
        assert "timeout" in tail

    def test_success_command(self, batch_collect, tmp_path):
        ok, _, _ = batch_collect.run_step(
            [sys.executable, "-c", "print('done')"], "ok", tmp_path, timeout=30)
        assert ok is True

    def test_failing_command_returns_false(self, batch_collect, tmp_path):
        ok, _, tail = batch_collect.run_step(
            [sys.executable, "-c", "raise SystemExit(3)"], "fail", tmp_path, timeout=30)
        assert ok is False
