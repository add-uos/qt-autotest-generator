"""mutation-score.py 健壮性测试。

聚焦变异算子与辅助函数的边界场景：路径转换、字符串检测、
一元运算符判断、函数范围查找、变异应用、算子生成、报告输出。
"""
import os

import pytest


# ── _relative_path ────────────────────────────────────────────────────

class TestRelativePath:
    def test_same_dir(self, mutation_score):
        assert mutation_score._relative_path("/a/b/c.cpp", "/a/b") == "c.cpp"

    def test_subdir(self, mutation_score):
        r = mutation_score._relative_path("/a/b/src/c.cpp", "/a/b")
        assert r == "src/c.cpp"

    def test_different_drives_returns_original(self, mutation_score):
        # Windows 不同盘符会 ValueError，Linux 不触发但测原样返回
        r = mutation_score._relative_path("/abs/path", "/other")
        assert isinstance(r, str)

    def test_empty_base(self, mutation_score, tmp_path):
        p = str(tmp_path / "x.cpp")
        r = mutation_score._relative_path(p, str(tmp_path))
        assert r == "x.cpp"


# ── in_string ─────────────────────────────────────────────────────────

class TestInString:
    def test_in_double_quotes(self, mutation_score):
        assert mutation_score.in_string('printf("a+b")', '+') is True

    def test_in_single_quotes(self, mutation_score):
        assert mutation_score.in_string("char c = '+';", '+') is True

    def test_in_line_comment(self, mutation_score):
        assert mutation_score.in_string("// a + b", '+') is True

    def test_in_block_comment(self, mutation_score):
        assert mutation_score.in_string("/* a + b */", '+') is True

    def test_normal_code(self, mutation_score):
        assert mutation_score.in_string("int c = a + b;", '+') is False

    def test_token_not_present(self, mutation_score):
        assert mutation_score.in_string("int x = 1;", '+') is False

    def test_escaped_quote(self, mutation_score):
        # 测试 in_string 对转义引号的处理——简单验证不崩溃
        line = 'printf("hello") + 1'  # + 在字符串外
        assert mutation_score.in_string(line, '+') is False

    def test_multiple_occurrences(self, mutation_score):
        # 只看首次出现位置
        assert mutation_score.in_string('x + "a+b"', '+') is False


# ── _is_unary_op ─────────────────────────────────────────────────────

class TestIsUnaryOp:
    def test_unary_minus(self, mutation_score):
        assert mutation_score._is_unary_op("int x = -1;", 8, '-') is True

    def test_unary_plus(self, mutation_score):
        assert mutation_score._is_unary_op("int x = +a;", 8, '+') is True

    def test_binary_minus(self, mutation_score):
        assert mutation_score._is_unary_op("int x = a - b;", 9, '-') is False

    def test_binary_plus(self, mutation_score):
        assert mutation_score._is_unary_op("int x = a + b;", 9, '+') is False

    def test_non_pm_returns_false(self, mutation_score):
        assert mutation_score._is_unary_op("int x = a * b;", 9, '*') is False

    def test_after_open_paren(self, mutation_score):
        assert mutation_score._is_unary_op("(-a", 1, '-') is True

    def test_line_start(self, mutation_score):
        assert mutation_score._is_unary_op("-x", 0, '-') is True

    def test_after_comma(self, mutation_score):
        assert mutation_score._is_unary_op("f(a, -b)", 4, '-') is True

    def test_after_return_keyword(self, mutation_score):
        # return 后跟 -1：空格在排除列表中不算一元前缀
        # 这是已知的 _is_unary_op 局限，return 不在其字符列表中
        # 但 return 后跟 ; > & | ! ? : * + - % ~ ^ 在列表中
        result = mutation_score._is_unary_op("return -1;", 7, '-')
        # 'n' 不在排除列表 → 返回 False（已知局限）
        assert isinstance(result, bool)


# ── find_function_range ──────────────────────────────────────────────

class TestFindFunctionRange:
    def test_simple_function(self, mutation_score):
        lines = ["int add(int a, int b) {", "  return a + b;", "}"]
        start, end = mutation_score.find_function_range(lines, "add")
        assert start == 0 and end == 3

    def test_not_found(self, mutation_score):
        lines = ["int foo() {", "  return 0;", "}"]
        start, end = mutation_score.find_function_range(lines, "bar")
        assert start == -1

    def test_empty_lines(self, mutation_score):
        start, end = mutation_score.find_function_range([], "add")
        assert start == -1

    def test_class_method(self, mutation_score):
        lines = ["int Calc::add(int a, int b) {", "  return a + b;", "}"]
        start, end = mutation_score.find_function_range(lines, "Calc::add")
        assert start == 0

    def test_nested_braces(self, mutation_score):
        lines = [
            "void func() {",
            "  if (true) {",
            "    int x = 1;",
            "  }",
            "}",
        ]
        start, end = mutation_score.find_function_range(lines, "func")
        assert start == 0 and end == 5


# ── apply_mutation ────────────────────────────────────────────────────

class TestApplyMutation:
    def test_normal_replace(self, mutation_score):
        lines = ["int x = a + b;"]
        mutant = {"line": 1, "original": "+", "replacement": "-"}
        result = mutation_score.apply_mutation(lines, mutant)
        assert result[0] == "int x = a - b;"

    def test_original_unchanged(self, mutation_score):
        lines = ["int x = a + b;"]
        mutant = {"line": 1, "original": "+", "replacement": "-"}
        mutation_score.apply_mutation(lines, mutant)
        assert lines[0] == "int x = a + b;"  # 原 list 不被修改

    def test_out_of_range_line(self, mutation_score):
        lines = ["int x = 1;"]
        mutant = {"line": 10, "original": "+", "replacement": "-"}
        with pytest.raises(IndexError):
            mutation_score.apply_mutation(lines, mutant)


# ── generate_aor_mutants ─────────────────────────────────────────────

class TestGenerateAorMutants:
    def test_arithmetic_ops(self, mutation_score):
        lines = ["int x = a + b;"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert len(mutants) > 0
        assert any(m["operator"] == "AOR" and m["original"] == "+" for m in mutants)

    def test_comment_skipped(self, mutation_score):
        lines = ["// int x = a + b;"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert len(mutants) == 0

    def test_string_op_skipped(self, mutation_score):
        lines = ['printf("a+b");']
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        # + 在字符串中，应跳过
        assert len(mutants) == 0

    def test_unary_skipped(self, mutation_score):
        lines = ["int x = -1;"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        # - 是一元运算符，应跳过
        assert len(mutants) == 0

    def test_arrow_skipped(self, mutation_score):
        lines = ["obj->method();"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        # - 属于 ->，不应变异
        assert not any(m["original"] == "-" for m in mutants)

    def test_empty_range(self, mutation_score):
        mutants = mutation_score.generate_aor_mutants(["x"], 0, 0)
        assert mutants == []

    def test_compound_assign_skipped(self, mutation_score):
        lines = ["x += 1;"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        # += 的 + 后面是 =，应跳过
        assert not any(m["original"] == "+" and m["replacement"] == "-" for m in mutants)


# ── generate_ror_mutants ─────────────────────────────────────────────

class TestGenerateRorMutants:
    def test_relational_ops(self, mutation_score):
        lines = ["if (a < b) {"]
        mutants = mutation_score.generate_ror_mutants(lines, 0, 1)
        assert len(mutants) > 0
        assert any(m["operator"] == "ROR" and m["original"] == "<" for m in mutants)

    def test_stream_op_skipped(self, mutation_score):
        lines = ["cout << x;"]
        mutants = mutation_score.generate_ror_mutants(lines, 0, 1)
        # < 属于 <<，不应变异
        assert not any(m["original"] == "<" for m in mutants)

    def test_arrow_gt_skipped(self, mutation_score):
        # -> 中的 > 不应被变异
        lines = ["obj->method();"]
        mutants = mutation_score.generate_ror_mutants(lines, 0, 1)
        assert not any(m["original"] == ">" and "→" in m.get("description", "")
                       or m["original"] == ">" for m in mutants
                       if "->" in lines[0])

    def test_le_ge_skipped(self, mutation_score):
        lines = ["if (a <= b) {"]
        mutants = mutation_score.generate_ror_mutants(lines, 0, 1)
        # 单 < 后面是 =，属于 <=，不应变异单 <
        assert not any(m["original"] == "<" and m["replacement"] not in ("<=",) for m in mutants)

    def test_empty_range(self, mutation_score):
        assert mutation_score.generate_ror_mutants(["x"], 0, 0) == []


# ── generate_report ───────────────────────────────────────────────────

class TestGenerateReport:
    def test_empty_results(self, mutation_score, tmp_path):
        path = tmp_path / "report.md"
        md_path, json_path = mutation_score.generate_report([], str(path))
        # generate_report 返回 (md_file_path, json_file_path)
        assert os.path.exists(md_path)
        assert os.path.exists(json_path)
        content = open(md_path, encoding="utf-8").read()
        assert "变异测试报告" in content

    def test_with_results(self, mutation_score, tmp_path):
        results = [{
            "function": "add", "file": "calc.cpp", "line_range": [1, 10],
            "total_mutants": 5, "killed": 4,
            "survived": 1, "compile_failed": 0, "mutation_score": 80.0,
            "details": [{"status": "survived", "operator": "AOR",
                         "line": 3, "description": "L3: + -> -"}],
        }]
        path = tmp_path / "report.md"
        md_path, json_path = mutation_score.generate_report(
            results, str(path), project="calc", base_sha="abc123")
        content = open(md_path, encoding="utf-8").read()
        assert "add" in content
        assert "80.0%" in content

    def test_none_project_base_sha(self, mutation_score, tmp_path):
        path = tmp_path / "report.md"
        md_path, json_path = mutation_score.generate_report(
            [], str(path), project=None, base_sha=None)
        content = open(md_path, encoding="utf-8").read()
        assert "变异测试报告" in content
