# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for report_generator.parsers.test_parser.TestOutputParser
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "resources"))

from report_generator.parsers.test_parser import TestOutputParser


class TestTestOutputParser:
    """Tests for TestOutputParser class"""

    def setup_method(self):
        self.report_dir = Path(__file__).parent / "fixtures" / "tmp_test_parser"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.parser = TestOutputParser(self.report_dir)

    def teardown_method(self):
        import shutil
        if self.report_dir.exists():
            shutil.rmtree(self.report_dir)

    def test_parse_no_output_file(self):
        result = self.parser.parse_test_output(True, 10)
        assert result["passed"] is True
        assert result["duration"] == 10
        assert result["total_tests"] == 0

    def test_parse_with_output_file(self):
        output_content = """
100% tests passed, 0 tests failed out of 15

Total Test time (real) = 3.50 sec
"""
        (self.report_dir / "test_output.log").write_text(output_content)

        result = self.parser.parse_test_output(True, 4)
        assert result["passed"] is True
        assert result["total_tests"] == 15
        assert result["passed_tests"] == 15
        assert result["failed_tests"] == 0

    def test_parse_with_failures(self):
        output_content = """
93% tests passed, 1 tests failed out of 15

The following tests FAILED:
    15 - test_calculator (Failed)
"""
        (self.report_dir / "test_output.log").write_text(output_content)

        result = self.parser.parse_test_output(False, 5)
        assert result["passed"] is False
        assert result["total_tests"] == 15
        assert result["failed_tests"] == 1
        assert result["passed_tests"] == 14
