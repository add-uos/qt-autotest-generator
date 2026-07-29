# SPDX-FileCopyrightText: 2025 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for generate-report.py import fix (C1 bug regression test).
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "resources" / "scripts"
RESOURCES_DIR = Path(__file__).parent.parent / "resources"


class TestGenerateReportImport:
    """Regression test for C1: report_generator relative import bug"""

    def test_generate_report_uses_package_import(self):
        content = (SCRIPTS_DIR / "generate-report.py").read_text()
        assert "from report_generator.main import" in content
        assert "from main import" not in content

    def test_sys_path_points_to_resources_not_package(self):
        content = (SCRIPTS_DIR / "generate-report.py").read_text()
        assert "resources_dir" in content
        assert "script_dir.parent" in content

    def test_report_generator_importable_as_package(self):
        sys.path.insert(0, str(RESOURCES_DIR))
        try:
            from report_generator.main import TestReportGenerator
            assert TestReportGenerator is not None
        finally:
            sys.path.remove(str(RESOURCES_DIR))
