"""coverage-by-level.py 健壮性测试。

聚焦辅助函数与 lcov 解析的边界场景：缺字段、空数据、目录匹配。
"""
import pytest


# ── m_file / m_class / m_name ────────────────────────────────────────

class TestAccessors:
    def test_m_file_present(self, coverage_by_level):
        assert coverage_by_level.m_file({"file_path": "a.cpp"}) == "a.cpp"

    def test_m_file_fallback(self, coverage_by_level):
        assert coverage_by_level.m_file({"file": "b.cpp"}) == "b.cpp"

    def test_m_file_missing(self, coverage_by_level):
        assert coverage_by_level.m_file({}) == ""

    def test_m_class_present(self, coverage_by_level):
        assert coverage_by_level.m_class({"class_qn": "Calc"}) == "Calc"

    def test_m_class_missing(self, coverage_by_level):
        assert coverage_by_level.m_class({}) == ""

    def test_m_name_present(self, coverage_by_level):
        assert coverage_by_level.m_name({"name": "add"}) == "add"

    def test_m_name_missing(self, coverage_by_level):
        assert coverage_by_level.m_name({}) == ""


# ── build_ranges ──────────────────────────────────────────────────────

class TestBuildRanges:
    def test_normal(self, coverage_by_level):
        rec = {"fns": [(1, "m1"), (5, "m2")], "da": {1: 0, 3: 1, 5: 0, 8: 1}}
        ranges = coverage_by_level.build_ranges(rec)
        assert len(ranges) == 2
        assert ranges[0] == (1, 5, "m1")
        assert ranges[1] == (5, 9, "m2")

    def test_single_fn(self, coverage_by_level):
        rec = {"fns": [(10, "main")], "da": {10: 0, 20: 1}}
        ranges = coverage_by_level.build_ranges(rec)
        assert len(ranges) == 1
        assert ranges[0][2] == "main"

    def test_empty_fns(self, coverage_by_level):
        rec = {"fns": [], "da": {1: 0}}
        ranges = coverage_by_level.build_ranges(rec)
        assert ranges == []

    def test_empty_da(self, coverage_by_level):
        rec = {"fns": [(1, "f")], "da": {}}
        ranges = coverage_by_level.build_ranges(rec)
        # max_line = 0, end = 0 + 1 = 1
        assert len(ranges) == 1
        assert ranges[0] == (1, 1, "f")

    def test_missing_fns_key(self, coverage_by_level):
        rec = {"da": {}}
        with pytest.raises(KeyError):
            coverage_by_level.build_ranges(rec)

    def test_missing_da_key(self, coverage_by_level):
        rec = {"fns": []}
        with pytest.raises(KeyError):
            coverage_by_level.build_ranges(rec)


# ── class_matches ─────────────────────────────────────────────────────

class TestClassMatches:
    def test_exact_match(self, coverage_by_level):
        m = {"class_qn": "Calc"}
        assert coverage_by_level.class_matches(m, "Calc") is True

    def test_suffix_match(self, coverage_by_level):
        m = {"class_qn": "proj.path.Calc"}
        assert coverage_by_level.class_matches(m, "Calc") is True

    def test_full_qn_match(self, coverage_by_level):
        m = {"class_qn": "proj.path.Calc"}
        assert coverage_by_level.class_matches(m, "proj.path.Calc") is True

    def test_no_match(self, coverage_by_level):
        m = {"class_qn": "proj.path.Calc"}
        assert coverage_by_level.class_matches(m, "Widget") is False

    def test_empty_class_qn(self, coverage_by_level):
        m = {"class_qn": ""}
        assert coverage_by_level.class_matches(m, "Calc") is False

    def test_missing_class_qn(self, coverage_by_level):
        m = {}
        assert coverage_by_level.class_matches(m, "Calc") is False

    def test_kw_empty(self, coverage_by_level):
        m = {"class_qn": "Calc"}
        assert coverage_by_level.class_matches(m, "") is False


# ── parse_lcov ────────────────────────────────────────────────────────

class TestParseLcov:
    def test_normal(self, coverage_by_level, tmp_path):
        lcov = (
            "SF:src/calc.cpp\n"
            "FN:10,add\n"
            "FNDA:3,add\n"
            "DA:11,1\n"
            "DA:12,0\n"
            "end_of_record\n"
        )
        p = tmp_path / "coverage.info"
        p.write_text(lcov, encoding="utf-8")
        result = coverage_by_level.parse_lcov(str(p))
        assert "src/calc.cpp" in result
        rec = result["src/calc.cpp"]
        assert rec["fns"] == [(10, "add")]
        assert rec["fnda"]["add"] == 3
        assert rec["da"][11] == 1
        assert rec["da"][12] == 0

    def test_multiple_files(self, coverage_by_level, tmp_path):
        lcov = (
            "SF:a.cpp\nFN:1,f1\nend_of_record\n"
            "SF:b.cpp\nFN:2,f2\nend_of_record\n"
        )
        p = tmp_path / "cov.info"
        p.write_text(lcov, encoding="utf-8")
        result = coverage_by_level.parse_lcov(str(p))
        assert len(result) == 2

    def test_no_end_of_record(self, coverage_by_level, tmp_path):
        # 缺 end_of_record：最后一个文件的 cur 不会被重置
        lcov = "SF:a.cpp\nFN:1,f1\n"
        p = tmp_path / "cov.info"
        p.write_text(lcov, encoding="utf-8")
        result = coverage_by_level.parse_lcov(str(p))
        assert "a.cpp" in result

    def test_empty_file(self, coverage_by_level, tmp_path):
        p = tmp_path / "empty.info"
        p.write_text("", encoding="utf-8")
        result = coverage_by_level.parse_lcov(str(p))
        assert result == {}

    def test_no_sf_lines(self, coverage_by_level, tmp_path):
        # 没有 SF 行，FN/DA 数据会被忽略（cur=None）
        lcov = "FN:1,f1\nDA:1,0\n"
        p = tmp_path / "nosf.info"
        p.write_text(lcov, encoding="utf-8")
        result = coverage_by_level.parse_lcov(str(p))
        assert result == {}
