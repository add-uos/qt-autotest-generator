# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for report_generator.main.TestReportGenerator
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "resources"))

from report_generator.main import TestReportGenerator


class TestTestReportGenerator:
    """Tests for TestReportGenerator class"""

    def setup_method(self):
        self.tmp_dir = Path(__file__).parent / "fixtures" / "tmp_test_main"
        self.build_dir = self.tmp_dir / "build"
        self.report_dir = self.tmp_dir / "reports"
        self.project_root = self.tmp_dir / "project"

        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.project_root.mkdir(parents=True, exist_ok=True)

        self.generator = TestReportGenerator(
            str(self.build_dir), str(self.report_dir), str(self.project_root)
        )

    def teardown_method(self):
        import shutil
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def test_init_sets_directories(self):
        assert self.generator.build_dir == self.build_dir
        assert self.generator.report_dir == self.report_dir
        assert self.generator.project_root == self.project_root

    def test_init_creates_parsers_and_generators(self):
        assert self.generator.test_parser is not None
        assert self.generator.coverage_parser is not None
        assert self.generator.html_generator is not None
        assert self.generator.csv_generator is not None

    def test_collect_build_info_no_cache(self):
        build_info = self.generator.collect_build_info()
        assert build_info["cmake_version"] == "Unknown"
        assert build_info["compiler"] == "Unknown"
        assert build_info["build_type"] == "Unknown"

    def test_collect_build_info_with_cache(self):
        cache_content = """
CMAKE_VERSION:INTERNAL=3.22.1
CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/g++
CMAKE_BUILD_TYPE:STRING=Debug
"""
        cache_file = self.build_dir / "CMakeCache.txt"
        cache_file.write_text(cache_content)

        build_info = self.generator.collect_build_info()
        assert build_info["cmake_version"] == "3.22.1"
        assert build_info["compiler"] == "g++"
        assert build_info["build_type"] == "Debug"

    def test_generate_report_success(self):
        with patch.object(self.generator.test_parser, 'parse_all_results', return_value={"passed": True}):
            with patch.object(self.generator, 'parse_coverage_data', return_value={"success": True}):
                with patch.object(self.generator, 'collect_build_info', return_value={}):
                    with patch.object(self.generator, 'generate_html_report', return_value="<html></html>"):
                        with patch.object(self.generator.csv_generator, 'generate_coverage_csv'):
                            result = self.generator.generate_report(True, 5, True, 3)
                            assert result is True
                            assert (self.report_dir / "test_report.html").exists()
                            assert (self.report_dir / "test_data.json").exists()

    def test_generate_report_failure(self):
        with patch.object(self.generator.test_parser, 'parse_all_results', side_effect=Exception("test error")):
            result = self.generator.generate_report(True, 5, True, 3)
            assert result is False
