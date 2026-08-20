"""stale-test-cleanup.py 健壮性测试。

聚焦测试块提取、方法匹配、注释、计数与报告渲染的边界场景。
"""
import pytest


# ── extract_test_blocks ───────────────────────────────────────────────

class TestExtractTestBlocks:
    def test_normal_test_f(self, stale_test_cleanup):
        content = "TEST_F(CalcTest, Add_Positive) {\n  EXPECT_EQ(add(1,2), 3);\n}"
        blocks = stale_test_cleanup.extract_test_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "TEST_F"
        assert blocks[0]["fixture"] == "CalcTest"
        assert blocks[0]["case"] == "Add_Positive"

    def test_normal_test_p(self, stale_test_cleanup):
        content = "TEST_P(DataTest, CheckValues) {\n  EXPECT_GT(v, 0);\n}"
        blocks = stale_test_cleanup.extract_test_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "TEST_P"

    def test_no_blocks(self, stale_test_cleanup):
        assert stale_test_cleanup.extract_test_blocks("int main() {}") == []

    def test_empty_string(self, stale_test_cleanup):
        assert stale_test_cleanup.extract_test_blocks("") == []

    def test_multiple_blocks(self, stale_test_cleanup):
        content = (
            "TEST_F(T, A) { assert(1); }\n"
            "TEST_F(T, B) { assert(2); }\n"
        )
        blocks = stale_test_cleanup.extract_test_blocks(content)
        assert len(blocks) == 2

    def test_nested_braces(self, stale_test_cleanup):
        content = "TEST_F(T, Complex) {\n  if (x) {\n    y();\n  }\n}"
        blocks = stale_test_cleanup.extract_test_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["end"] > blocks[0]["start"]

    def test_unclosed_brace_skipped(self, stale_test_cleanup):
        # 未闭合的大括号 → body_end=None → 块被跳过不崩溃
        content = "TEST_F(T, Bad) {\n  if (x) {\n"
        blocks = stale_test_cleanup.extract_test_blocks(content)
        # body_end 为 None 时不添加到结果
        assert len(blocks) == 0

    def test_method_hint_extracted(self, stale_test_cleanup):
        content = "TEST_F(T, Add_Positive) { assert(1); }"
        blocks = stale_test_cleanup.extract_test_blocks(content)
        assert blocks[0]["method_hint"] == "add"


# ── extract_instantiate_blocks ────────────────────────────────────────

class TestExtractInstantiateBlocks:
    def test_normal(self, stale_test_cleanup):
        content = "INSTANTIATE_TEST_SUITE_P(Ints, MyTest, Values(1,2,3));"
        blocks = stale_test_cleanup.extract_instantiate_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["prefix"] == "Ints"
        assert blocks[0]["suite"] == "MyTest"

    def test_no_blocks(self, stale_test_cleanup):
        assert stale_test_cleanup.extract_instantiate_blocks("int x;") == []

    def test_empty_string(self, stale_test_cleanup):
        assert stale_test_cleanup.extract_instantiate_blocks("") == []


# ── method_matches_case ──────────────────────────────────────────────

class TestMethodMatchesCase:
    def test_pascal_prefix(self, stale_test_cleanup):
        assert stale_test_cleanup.method_matches_case("add", "Add_Positive") is True

    def test_case_insensitive(self, stale_test_cleanup):
        assert stale_test_cleanup.method_matches_case("add", "add_something") is True

    def test_exact_match(self, stale_test_cleanup):
        assert stale_test_cleanup.method_matches_case("add", "Add") is True

    def test_no_match(self, stale_test_cleanup):
        assert stale_test_cleanup.method_matches_case("add", "Subtract_Things") is False

    def test_empty_method_name(self, stale_test_cleanup):
        # 空 method_name → PascalCase 空字符串 → startsWith("") → True
        # 实际上空字符串前缀匹配任何字符串
        assert stale_test_cleanup.method_matches_case("", "Add") is True


# ── comment_out_block ────────────────────────────────────────────────

class TestCommentOutBlock:
    def test_normal(self, stale_test_cleanup):
        result = stale_test_cleanup.comment_out_block("int x = 1;", "removed")
        assert result.startswith("// removed")
        assert "// int x = 1;" in result

    def test_empty_text(self, stale_test_cleanup):
        result = stale_test_cleanup.comment_out_block("", "reason")
        assert "// reason" in result

    def test_multiline(self, stale_test_cleanup):
        result = stale_test_cleanup.comment_out_block("a\nb\nc", "r")
        lines = result.split("\n")
        assert len(lines) == 4  # reason + 3 lines

    def test_empty_reason(self, stale_test_cleanup):
        result = stale_test_cleanup.comment_out_block("code", "")
        assert result.startswith("// ")


# ── count_active_cases_by_method ─────────────────────────────────────

class TestCountActiveCasesByMethod:
    def test_normal(self, stale_test_cleanup):
        content = (
            "TEST_F(CalcTest, Add_Pos) { assert(1); }\n"
            "TEST_F(CalcTest, Add_Neg) { assert(2); }\n"
            "TEST_F(CalcTest, Sub_Pos) { assert(3); }\n"
        )
        counts = stale_test_cleanup.count_active_cases_by_method(content, "Calc")
        assert counts.get("add") == 2
        assert counts.get("sub") == 1

    def test_commented_lines_skipped(self, stale_test_cleanup):
        content = (
            "TEST_F(CalcTest, Add_Pos) { assert(1); }\n"
            "// TEST_F(CalcTest, Add_Neg) { assert(2); }\n"
        )
        counts = stale_test_cleanup.count_active_cases_by_method(content, "Calc")
        assert counts.get("add") == 1

    def test_no_matching_fixture(self, stale_test_cleanup):
        content = "TEST_F(OtherTest, Add_Pos) { assert(1); }\n"
        counts = stale_test_cleanup.count_active_cases_by_method(content, "Calc")
        assert counts == {}

    def test_empty_content(self, stale_test_cleanup):
        assert stale_test_cleanup.count_active_cases_by_method("", "Calc") == {}


# ── render_report ────────────────────────────────────────────────────

class TestRenderReport:
    def test_basic_report(self, stale_test_cleanup):
        report = {
            "removed_methods": [{"name": "add", "class_qn": "Calc", "level": "high"}],
            "cleaned_cases": 3,
            "cleaned_instantiates": 1,
            "cleaned_files": ["test_calc.cpp"],
            "updated_inventory": True,
            "stale_files": [],
        }
        md = stale_test_cleanup.render_report(report)
        assert "过时测试清理报告" in md
        assert "add" in md
        assert "test_calc.cpp" in md

    def test_empty_removed(self, stale_test_cleanup):
        report = {
            "removed_methods": [],
            "cleaned_cases": 0,
            "cleaned_instantiates": 0,
            "cleaned_files": [],
            "updated_inventory": False,
        }
        md = stale_test_cleanup.render_report(report)
        assert "过时测试清理报告" in md
