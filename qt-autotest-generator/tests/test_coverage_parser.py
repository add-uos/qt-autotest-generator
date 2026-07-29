# SPDX-FileCopyrightText: 2025 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for report_generator.parsers.coverage_parser.CoverageParser
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "resources"))

from report_generator.parsers.coverage_parser import CoverageParser


class TestCoverageParser:
    """Tests for CoverageParser class"""

    def setup_method(self):
        self.tmp_dir = Path(__file__).parent / "fixtures" / "tmp_coverage"
        self.build_dir = self.tmp_dir / "build"
        self.report_dir = self.tmp_dir / "reports"
        self.project_root = self.tmp_dir / "project"

        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.project_root.mkdir(parents=True, exist_ok=True)

        self.parser = CoverageParser(
            self.build_dir, self.report_dir, self.project_root
        )

    def teardown_method(self):
        import shutil
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def test_parse_no_coverage(self):
        result = self.parser.parse_coverage_data(False, 0)
        assert result["success"] is False
        assert result["line_coverage"] == 0.0

    def test_parse_no_output_file(self):
        result = self.parser.parse_coverage_data(True, 5)
        assert result["success"] is True
        assert result["duration"] == 5
        assert result["line_coverage"] == 0.0

    def test_parse_with_lcov_output(self):
        lcov_content = """
Reading tracefile coverage.info
Summary coverage rate:
  lines......: 85.5% (170 of 200 lines)
  functions..: 90.0% (18 of 20 functions)
  branches...: 75.0% (30 of 40 branches)
"""
        (self.report_dir / "coverage_output.log").write_text(lcov_content)

        result = self.parser.parse_coverage_data(True, 3)
        assert result["success"] is True
        assert result["line_coverage"] == 85.5
        assert result["function_coverage"] == 90.0
        assert result["branch_coverage"] == 75.0
