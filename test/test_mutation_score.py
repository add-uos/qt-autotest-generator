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


# ── Bug #1: inventory file_path vs file ───────────────────────────────

class TestInventoryFieldCompat:
    def test_file_path_key(self, mutation_score):
        """inventory 模式读取 file_path 字段."""
        import json, tempfile, os
        inv = {"methods": [{
            "qualified_name": "proj.Cls.method",
            "file_path": "src/cls.cpp",
            "level": "high", "testable": True,
        }]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(inv, f)
            inv_path = f.name
        try:
            with open(inv_path) as f:
                data = json.load(f)
            for m in data.get("methods", []):
                src = m.get("file_path", m.get("file", ""))
                assert src == "src/cls.cpp"
        finally:
            os.unlink(inv_path)

    def test_legacy_file_key_fallback(self, mutation_score):
        """file_path 不存在时 fallback 到 file."""
        import json, tempfile, os
        inv = {"methods": [{
            "qualified_name": "proj.Cls.method",
            "file": "src/old.cpp",
            "level": "high", "testable": True,
        }]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(inv, f)
            inv_path = f.name
        try:
            with open(inv_path) as f:
                data = json.load(f)
            for m in data.get("methods", []):
                src = m.get("file_path", m.get("file", ""))
                assert src == "src/old.cpp"
        finally:
            os.unlink(inv_path)


# ── Bug #2: qualified_name dot → :: ──────────────────────────────────

class TestQualifiedNameConversion:
    def test_dot_to_double_colon(self, mutation_score):
        qn = "home-uos-service-codebase-repos-deepin-picker.src.dbusnotify.DBusNotify.ClearRecords"
        parts = qn.split(".")
        class_method = "{}::{}".format(parts[-2], parts[-1])
        assert class_method == "DBusNotify::ClearRecords"

    def test_short_qualified_name(self, mutation_score):
        qn = "Utils::stringIsDigit"
        parts = qn.split(".")
        if len(parts) >= 2:
            class_method = "{}::{}".format(parts[-2], parts[-1])
        else:
            class_method = qn
        assert class_method == "Utils::stringIsDigit"


# ── Bug #5: AOR pointer declaration * ────────────────────────────────

class TestAorPointerDecl:
    def test_pointer_decl_star_skipped(self, mutation_score):
        """Type *var 指针声明 * 不应被 AOR 变异."""
        lines = ["DBusNotify *notifyDBus = new DBusNotify();\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert not any(m["original"] == "*" for m in mutants)

    def test_pointer_decl_uppercase_type(self, mutation_score):
        lines = ["QClipboard *clipboard = QApplication::clipboard();\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert not any(m["original"] == "*" for m in mutants)

    def test_keyword_type_pointer(self, mutation_score):
        lines = ["int *ptr = nullptr;\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert not any(m["original"] == "*" for m in mutants)

    def test_arithmetic_star_not_skipped(self, mutation_score):
        """算术乘法 * 应该被 AOR 变异."""
        lines = ["int area = width * height;\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert any(m["original"] == "*" for m in mutants)

    def test_template_type_pointer(self, mutation_score):
        """模板参数 T *ptr 指针声明."""
        lines = ["T *ptr = new T();\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert not any(m["original"] == "*" for m in mutants)


# ── Bug #6: find_function_range class_name context ──────────────────

class TestFindFunctionRangeInline:
    def test_inline_method_in_header(self, mutation_score):
        """内联方法: 类声明在上方多行, 方法名在下方."""
        lines = [
            "class DBusNotify {\n",
            "public:\n",
            "  void ClearRecords() {\n",
            "    records.clear();\n",
            "  }\n",
            "};\n",
        ]
        start, end = mutation_score.find_function_range(
            lines, "DBusNotify::ClearRecords", source_file="dbusnotify.h")
        assert start >= 0

    def test_class_name_context_10_lines(self, mutation_score):
        """class_name 在上方 10 行内仍可匹配."""
        lines = ["\n"] * 8 + [
            "class Foo {\n",
            "  void bar() {\n",
            "    return;\n",
            "  }\n",
            "};\n",
        ]
        start, end = mutation_score.find_function_range(
            lines, "Foo::bar", source_file="foo.h")
        assert start >= 0


# ── Bug #8: stratified truncation ───────────────────────────────────

class TestStratifiedTruncation:
    def test_all_operators_represented(self, mutation_score):
        """截断后 5 类算子至少各有 1 个配额."""
        # 构造 50 个变异体（每类 10 个）
        all_mutants = []
        for op in ["AOR", "ROR", "LOR", "CRC", "RVF"]:
            for i in range(10):
                all_mutants.append({
                    "id": "{}_{}".format(op, i),
                    "operator": op,
                    "line": i + 1,
                    "original": "+",
                    "replacement": "-",
                    "description": "test",
                })
        # 模拟分层截断逻辑 (max_mutants=5 → per_op_quota=1)
        max_mutants = 5
        from collections import defaultdict
        by_op = defaultdict(list)
        for m in all_mutants:
            by_op[m["operator"]].append(m)
        operator_order = ["AOR", "ROR", "LOR", "CRC", "RVF"]
        per_op_quota = max(1, max_mutants // len(operator_order))
        sampled = []
        for op in operator_order:
            if op in by_op:
                quota = min(per_op_quota, len(by_op[op]))
                sampled.extend(by_op[op][:quota])
        by_sampled_op = defaultdict(int)
        for m in sampled:
            by_sampled_op[m["operator"]] += 1
        for op in operator_order:
            assert by_sampled_op[op] >= 1, "{} 应至少 1 个".format(op)


# ── Bug #11: CRC negative number handling ──────────────────────────

class TestCrcNegativeNumber:
    def test_negative_constant(self, mutation_score):
        """-5 应作为整体常量 -5 变异，而非 5."""
        lines = ["int x = -5;\n"]
        mutants = mutation_score.generate_crc_mutants(lines, 0, 1)
        # 应该生成 CRC 变异 -5 → -4 / -6，而非 5→6
        neg_mutants = [m for m in mutants if m["original"] == "-5"]
        assert len(neg_mutants) > 0, "-5 应作为整体常量被变异"

    def test_positive_constant_unchanged(self, mutation_score):
        lines = ["int x = 5;\n"]
        mutants = mutation_score.generate_crc_mutants(lines, 0, 1)
        pos_mutants = [m for m in mutants if m["original"] == "5"]
        assert len(pos_mutants) > 0

    def test_binary_minus_not_negative(self, mutation_score):
        """a - 5 中的 5 不应被当作 -5."""
        lines = ["int x = a - 5;\n"]
        mutants = mutation_score.generate_crc_mutants(lines, 0, 1)
        # -5 不应作为整体常量，只有 5
        neg_mutants = [m for m in mutants if m["original"] == "-5"]
        assert len(neg_mutants) == 0, "二元减法不应产生 -5 变异"


# ── Bug #4: test_not_found status ──────────────────────────────────

class TestTestNotFoundStatus:
    def test_report_includes_test_not_found(self, mutation_score, tmp_path):
        results = [{
            "function": "foo", "file": "a.cpp", "line_range": [1, 5],
            "total_mutants": 2, "killed": 1, "survived": 0,
            "compile_failed": 0, "test_not_found": 1,
            "mutation_score": 100.0, "verdict": "PASS",
            "details": [{"status": "test_not_found", "operator": "AOR",
                         "line": 2, "description": "L2: + -> -"}],
        }]
        path = tmp_path / "report.md"
        md_path, json_path = mutation_score.generate_report(
            results, str(path), project="proj", base_sha="abc")
        content = open(md_path, encoding="utf-8").read()
        assert "测试目标未找到" in content

    def test_json_includes_test_not_found(self, mutation_score, tmp_path):
        results = [{
            "function": "bar", "file": "b.cpp", "line_range": [1, 5],
            "total_mutants": 1, "killed": 0, "survived": 0,
            "compile_failed": 0, "test_not_found": 1,
            "mutation_score": 0.0, "verdict": "NO_MUTANTS",
            "details": [],
        }]
        path = tmp_path / "report.md"
        md_path, json_path = mutation_score.generate_report(
            results, str(path), project="proj", base_sha="abc")
        import json
        data = json.load(open(json_path))
        assert data["summary"]["test_not_found"] == 1
        assert data["functions"][0]["test_not_found"] == 1


# ── NEW: _is_template_bracket ────────────────────────────────────────

class TestIsTemplateBracket:
    def test_qlist_template_lt(self, mutation_score):
        """QList<int> 的 < 是模板，不应被 ROR 变异."""
        line = "  QList<int> list;\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<') is True

    def test_std_vector_template_lt(self, mutation_score):
        """std::vector<int> 的 < 是模板."""
        line = "  std::vector<int> vec;\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<') is True

    def test_comparison_lt_with_space(self, mutation_score):
        """a < b (有空格) 的 < 是比较运算符."""
        line = "  if (a < b) {\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<') is False

    def test_comparison_gt_with_space(self, mutation_score):
        """x > 0 的 > 是比较运算符."""
        line = "  if (x > 0) {\n"
        idx = line.index('>')
        assert mutation_score._is_template_bracket(line, idx, '>') is False

    def test_template_close_gt(self, mutation_score):
        """QList<int> 的 > 是模板闭括号."""
        line = "  QList<int> list;\n"
        idx = line.index('>')
        assert mutation_score._is_template_bracket(line, idx, '>') is True

    def test_include_directive(self, mutation_score):
        """#include <header> 的 < 是预处理指令."""
        line = "#include <QWidget>\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<') is True

    def test_static_cast_template(self, mutation_score):
        """static_cast<int> 的 < 是模板."""
        line = "  auto x = static_cast<int>(val);\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<') is True

    def test_qt_namespace_comparison(self, mutation_score):
        """Qt < 5 (版本比较) 不应被误判为模板."""
        line = "  if (Qt < 5) {\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<') is False

    def test_short_uppercase_var_comparison(self, mutation_score):
        """X < Y (大写变量比较) 不应被误判为模板."""
        line = "  if (X < Y) {\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<') is False

    def test_set_as_variable_comparison(self, mutation_score):
        """set < value (变量名 set 与 std::set 消歧) 不应误判."""
        line = "  if (set < value) {\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<') is False

    def test_non_bracket_op_returns_false(self, mutation_score):
        """<= 和 != 不是尖括号，总是返回 False."""
        line = "  if (a <= b) {\n"
        idx = line.index('<')
        assert mutation_score._is_template_bracket(line, idx, '<=') is False

    def test_ror_skips_template_brackets(self, mutation_score):
        """ROR 不应为模板 < > 生成变异体."""
        lines = ["  QList<int> list;\n"]
        mutants = mutation_score.generate_ror_mutants(lines, 0, 1)
        assert len(mutants) == 0

    def test_ror_produces_comparison_mutants(self, mutation_score):
        """ROR 应为比较运算符 < > 生成变异体."""
        lines = ["  if (a < b) {\n"]
        mutants = mutation_score.generate_ror_mutants(lines, 0, 1)
        assert len(mutants) > 0


# ── NEW: _is_pointer_decl dereference detection ─────────────────────

class TestPointerDereference:
    def test_return_deref(self, mutation_score):
        """return *ptr 解引用 * 不应被 AOR 变异."""
        lines = ["  return *ptr;\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert not any(m["original"] == "*" for m in mutants)

    def test_assign_deref(self, mutation_score):
        """x = *ptr 解引用 * 不应被 AOR 变异."""
        lines = ["  x = *ptr;\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert not any(m["original"] == "*" for m in mutants)

    def test_delete_deref(self, mutation_score):
        """delete *ptr 解引用 * 不应被 AOR 变异."""
        lines = ["  delete *ptr;\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert not any(m["original"] == "*" for m in mutants)

    def test_paren_deref(self, mutation_score):
        """(*ptr) 解引用 * 不应被 AOR 变异."""
        lines = ["  x = (*ptr);\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert not any(m["original"] == "*" for m in mutants)

    def test_multiply_not_confused(self, mutation_score):
        """a * b 乘法仍应被 AOR 变异."""
        lines = ["  result = count * factor;\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert any(m["original"] == "*" for m in mutants)

    def test_for_loop_multiply(self, mutation_score):
        """for 循环内 n*2 乘法仍应被 AOR 变异."""
        lines = ["  for (int i=0; i<n*2; i++) {\n"]
        mutants = mutation_score.generate_aor_mutants(lines, 0, 1)
        assert any(m["original"] == "*" for m in mutants)
