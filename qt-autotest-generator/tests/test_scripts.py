# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for shell scripts: variable definitions, SPDX headers, basic structure.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "resources" / "scripts"


class TestGenerateCmakeUtils:
    """Tests for generate-cmake-utils.sh"""

    def test_script_has_spdx_header(self):
        content = (SCRIPTS_DIR / "generate-cmake-utils.sh").read_text()
        assert "SPDX-FileCopyrightText" in content
        assert "SPDX-License-Identifier" in content

    def test_script_defines_script_dir(self):
        content = (SCRIPTS_DIR / "generate-cmake-utils.sh").read_text()
        assert "SCRIPT_DIR=" in content

    def test_script_defines_autotest_root(self):
        content = (SCRIPTS_DIR / "generate-cmake-utils.sh").read_text()
        assert "AUTOTEST_ROOT=" in content

    def test_script_has_set_e(self):
        content = (SCRIPTS_DIR / "generate-cmake-utils.sh").read_text()
        assert "set -e" in content


class TestGenerateRunner:
    """Tests for generate-runner.sh"""

    def test_script_has_spdx_header(self):
        content = (SCRIPTS_DIR / "generate-runner.sh").read_text()
        assert "SPDX-FileCopyrightText" in content
        assert "SPDX-License-Identifier" in content

    def test_script_defines_script_dir(self):
        content = (SCRIPTS_DIR / "generate-runner.sh").read_text()
        assert "SCRIPT_DIR=" in content

    def test_script_defines_autotest_root(self):
        content = (SCRIPTS_DIR / "generate-runner.sh").read_text()
        assert "AUTOTEST_ROOT=" in content


class TestSetupCodebaseMemory:
    """Tests for setup-codebase-memory.sh"""

    def test_script_has_spdx_header(self):
        content = (SCRIPTS_DIR / "setup-codebase-memory.sh").read_text()
        assert "SPDX-FileCopyrightText" in content
        assert "SPDX-License-Identifier" in content

    def test_script_has_version_check(self):
        content = (SCRIPTS_DIR / "setup-codebase-memory.sh").read_text()
        assert "version_ge" in content
        assert "CBM_MIN_VERSION" in content

    def test_script_has_exit_codes(self):
        content = (SCRIPTS_DIR / "setup-codebase-memory.sh").read_text()
        assert "exit 1" in content
        assert "exit 2" in content
        assert "exit 3" in content
