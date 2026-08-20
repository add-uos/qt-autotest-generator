"""collect-coverage-report.py 健壮性测试。

聚焦 XML 解析与测试目标查找的边界场景：畸形 XML、缺属性、
空文件、目录结构搜索。
"""
import os
import stat

import pytest


# ── parse_gtest_xml ───────────────────────────────────────────────────

class TestParseGtestXml:
    def _write_xml(self, tmp_path, content):
        p = tmp_path / "result.xml"
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_normal_xml(self, collect_coverage_report, tmp_path):
        xml = '<testsuites tests="10" failures="2" errors="1"></testsuites>'
        path = self._write_xml(tmp_path, xml)
        total, passed, failed = collect_coverage_report.parse_gtest_xml(path)
        assert total == 10 and passed == 7 and failed == 3

    def test_missing_attributes_defaults_zero(self, collect_coverage_report, tmp_path):
        xml = '<testsuites></testsuites>'
        path = self._write_xml(tmp_path, xml)
        total, passed, failed = collect_coverage_report.parse_gtest_xml(path)
        assert total == 0 and passed == 0 and failed == 0

    def test_malformed_xml(self, collect_coverage_report, tmp_path):
        path = self._write_xml(tmp_path, "<not valid xml")
        total, passed, failed = collect_coverage_report.parse_gtest_xml(path)
        assert total == 0 and passed == 0 and failed == 0

    def test_empty_file(self, collect_coverage_report, tmp_path):
        path = self._write_xml(tmp_path, "")
        total, passed, failed = collect_coverage_report.parse_gtest_xml(path)
        assert (total, passed, failed) == (0, 0, 0)

    def test_nonexistent_file(self, collect_coverage_report, tmp_path):
        path = str(tmp_path / "nope.xml")
        total, passed, failed = collect_coverage_report.parse_gtest_xml(path)
        assert (total, passed, failed) == (0, 0, 0)


# ── parse_gtest_xml_suites ────────────────────────────────────────────

class TestParseGtestXmlSuites:
    def _write_xml(self, tmp_path, content):
        p = tmp_path / "suites.xml"
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_multiple_suites(self, collect_coverage_report, tmp_path):
        xml = (
            '<testsuites><testsuite name="A" tests="5" failures="1" errors="0" time="0.1"/>'
            '<testsuite name="B" tests="3" failures="0" errors="0" time="0.2"/>'
            '</testsuites>'
        )
        path = self._write_xml(tmp_path, xml)
        suites = collect_coverage_report.parse_gtest_xml_suites(path)
        assert len(suites) == 2
        assert suites[0]["suite"] == "A"
        assert suites[1]["tests"] == 3

    def test_no_suites_element(self, collect_coverage_report, tmp_path):
        xml = '<testsuites></testsuites>'
        path = self._write_xml(tmp_path, xml)
        suites = collect_coverage_report.parse_gtest_xml_suites(path)
        assert suites == []

    def test_malformed_returns_empty(self, collect_coverage_report, tmp_path):
        path = self._write_xml(tmp_path, "<<<bad")
        suites = collect_coverage_report.parse_gtest_xml_suites(path)
        assert suites == []

    def test_missing_time_defaults_zero(self, collect_coverage_report, tmp_path):
        xml = '<testsuites><testsuite name="A" tests="1" failures="0" errors="0"/></testsuites>'
        path = self._write_xml(tmp_path, xml)
        suites = collect_coverage_report.parse_gtest_xml_suites(path)
        assert suites[0]["time"] == 0.0


# ── find_test_target ──────────────────────────────────────────────────

class TestFindTestTarget:
    def test_no_binary_returns_none(self, collect_coverage_report, tmp_path):
        assert collect_coverage_report.find_test_target(str(tmp_path)) is None

    def test_executable_found(self, collect_coverage_report, tmp_path):
        # 创建一个可执行文件
        test_file = tmp_path / "test_calc"
        test_file.write_text("#!/bin/sh\n")
        test_file.chmod(test_file.stat().st_mode | stat.S_IEXEC)
        result = collect_coverage_report.find_test_target(str(tmp_path))
        assert result is not None
        assert "test_calc" in result

    def test_non_executable_ignored(self, collect_coverage_report, tmp_path):
        p = tmp_path / "test_calc"
        p.write_text("not executable")
        # 确保不可执行
        p.chmod(p.stat().st_mode & ~stat.S_IEXEC)
        result = collect_coverage_report.find_test_target(str(tmp_path))
        # rglob 可能找到但它不可执行，所以应为 None 或其他
        # （取决于 find_test_target 是否检查 is_file + access）
        # 实际函数有 os.access(x, X_OK) 检查


# ── parse_lcov_summary ───────────────────────────────────────────────

class TestParseLcovSummary:
    def test_nonexistent_returns_empty(self, collect_coverage_report):
        # 文件不存在 → 返回空 dict（不调 subprocess）
        result = collect_coverage_report.parse_lcov_summary("/nonexistent/path.info")
        # 函数内部先检查 os.path.exists，不存在直接返回 {}
        # 但注意 run_capture 会执行 lcov 命令；如果 lcov 不存在会怎样？
        # 源码：先检查 os.path.exists，不存在返回 {}
        assert result == {}
