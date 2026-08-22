#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_extract_branches.py — fetch-mcp-data.py extract-branches 子命令的单元测试

覆盖范围（self-checker.md §2c 分支清单交叉验证的脚本化部分）：
  1. strip_cpp_comments_and_strings  — 注释/字符串里的关键字不被误数
  2. extract_branches                — 各分支类型计数正确 + early return 剔除末尾
  3. parse_declared_branches         — 段落定位 + 多方法隔离 + 缺失兜底
  4. cross_check_branches            — MISSING / NOT_MAPPED / pass 三态
  5. select_class_methods            — 命名空间场景 + testable 过滤
  6. fetch_method_bodies             — mock client，多字段名兜底 + 失败处理

运行:
  cd scripts && python3 -m unittest tests.test_extract_branches -v
  # 或直接：
  python3 tests/test_extract_branches.py

零第三方依赖，仅用标准库 unittest（与技能 requirements.txt 一致）。
fetch-mcp-data.py 文件名含连字符无法 import，用 importlib 动态加载。
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# ── 动态加载 fetch-mcp-data.py（文件名含连字符，不能直接 import） ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_FETCH_PATH = os.path.join(_SCRIPT_DIR, os.pardir, "fetch-mcp-data.py")
_spec = importlib.util.spec_from_file_location("fetch_mcp_data", _FETCH_PATH)
fetch_mcp_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_mcp_data)


# ═══════════════════════════════════════════════════════════════════════
# 1. strip_cpp_comments_and_strings
# ═══════════════════════════════════════════════════════════════════════

class TestStripCommentsAndStrings(unittest.TestCase):

    def test_block_comment_removed(self):
        code = "if (x) { /* if (y) return; */ }"
        cleaned = fetch_mcp_data.strip_cpp_comments_and_strings(code)
        self.assertNotIn("if (y)", cleaned)

    def test_line_comment_removed(self):
        code = "int a = 1; // if (z) case foo:\nreturn a;"
        cleaned = fetch_mcp_data.strip_cpp_comments_and_strings(code)
        self.assertNotIn("if (z)", cleaned)
        self.assertNotIn("case foo", cleaned)

    def test_string_literal_emptied(self):
        code = 'QString s = "if (x) case 1: throw"; return s;'
        cleaned = fetch_mcp_data.strip_cpp_comments_and_strings(code)
        self.assertNotIn("if (x)", cleaned)
        self.assertNotIn("case 1", cleaned)
        self.assertNotIn("throw", cleaned)

    def test_char_literal_emptied(self):
        code = "char c = '?'; int x = c == '?' ? 1 : 2;"
        cleaned = fetch_mcp_data.strip_cpp_comments_and_strings(code)
        # 字符 '?' 被清空为 ''，但代码结构保留
        self.assertNotIn("'?'", cleaned)

    def test_preserves_code_structure(self):
        code = "if (x) return 1; else return 2;"
        cleaned = fetch_mcp_data.strip_cpp_comments_and_strings(code)
        self.assertIn("if (x)", cleaned)
        self.assertIn("return 1", cleaned)
        self.assertIn("return 2", cleaned)

    def test_multiline_block_comment(self):
        code = "/*\n * if (a)\n * for (b)\n */\nif (x) return 1;"
        cleaned = fetch_mcp_data.strip_cpp_comments_and_strings(code)
        self.assertNotIn("if (a)", cleaned)
        self.assertNotIn("for (b)", cleaned)
        self.assertIn("if (x)", cleaned)

    def test_escaped_quote_in_string(self):
        code = 'QString s = "he said \\"if (x)\\""; return 1;'
        cleaned = fetch_mcp_data.strip_cpp_comments_and_strings(code)
        self.assertNotIn("if (x)", cleaned)
        self.assertIn("return 1", cleaned)


# ═══════════════════════════════════════════════════════════════════════
# 2. extract_branches
# ═══════════════════════════════════════════════════════════════════════

class TestExtractBranches(unittest.TestCase):

    def test_empty_body_returns_zeros(self):
        r = fetch_mcp_data.extract_branches("")
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["if"], 0)

    def test_none_body_returns_zeros(self):
        r = fetch_mcp_data.extract_branches(None)
        self.assertEqual(r["total"], 0)

    def test_single_if(self):
        body = "if (x > 0) return 1; return 0;"
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["if"], 1)
        # 1 个 if + 1 个 return(末尾剔除后) → early return 计 1，total=2
        self.assertEqual(r["return"], 1)
        self.assertEqual(r["total"], 2)

    def test_else_if_counts_as_if(self):
        body = "if (a) return 1; else if (b) return 2; return 0;"
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["if"], 2)  # if + else if 都匹配 \bif\s*\(

    def test_trailing_return_not_counted(self):
        # 只有末尾 return，无提前 return
        body = "int x = 1; return x;"
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["return"], 0)
        self.assertEqual(r["total"], 0)

    def test_multiple_early_returns(self):
        # 3 个 return，末尾 1 个剔除 → early return = 2
        body = "if (a) return 1; if (b) return 2; return 0;"
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["return"], 2)

    def test_for_loop(self):
        body = "int s = 0; for (int i = 0; i < n; ++i) s += i; return s;"
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["for"], 1)

    def test_while_loop(self):
        body = "while (!done) { if (x) done = true; } return 0;"
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["while"], 1)
        self.assertEqual(r["if"], 1)

    def test_switch_case(self):
        body = ("switch (c) { case 1: return 1; case 2: return 2; "
                "default: return 0; }")
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["case"], 2)
        self.assertEqual(r["default"], 1)  # test-types §4.2 要求覆盖 default

    def test_throw(self):
        body = "if (x < 0) throw std::invalid_argument(\"bad\"); return x;"
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["throw"], 1)

    def test_ternary(self):
        body = "int x = cond ? 1 : 2; return x;"
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["ternary"], 1)

    def test_comments_not_counted(self):
        body = ("// if (commented) case 99: throw\n"
                "/* for (i) while (j) */\n"
                "if (real) return 1; return 0;")
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["if"], 1)
        self.assertEqual(r["case"], 0)
        self.assertEqual(r["throw"], 0)
        self.assertEqual(r["for"], 0)
        self.assertEqual(r["while"], 0)

    def test_string_keywords_not_counted(self):
        body = ('QString msg = "if (x) case 1: throw for while"; '
                "if (real) return 1; return 0;")
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["if"], 1)
        self.assertEqual(r["case"], 0)
        self.assertEqual(r["throw"], 0)

    def test_complex_method_total(self):
        body = (
            "int parse(QString s) {\n"
            "  if (s.isEmpty()) return 0;        // B1: if + early return\n"
            "  if (!s.contains('=')) return -1;  // B2: if + early return\n"
            "  for (int i = 0; i < s.size(); ++i) {  // B3: for\n"
            "    if (s[i] == ';') continue;\n"
            "  }\n"
            "  return s.size() > 10 ? 10 : s.size();  // B4: ternary\n"
            "}"
        )
        r = fetch_mcp_data.extract_branches(body)
        self.assertEqual(r["if"], 3)      # 3 个 if
        self.assertEqual(r["for"], 1)
        self.assertEqual(r["ternary"], 1)
        self.assertEqual(r["return"], 2)  # 3 return - 1 末尾 = 2
        self.assertEqual(r["case"], 0)
        self.assertEqual(r["throw"], 0)
        # total = 3(if) + 1(for) + 1(ternary) + 2(return) = 7
        self.assertEqual(r["total"], 7)


# ═══════════════════════════════════════════════════════════════════════
# 3. parse_declared_branches
# ═══════════════════════════════════════════════════════════════════════

SAMPLE_TEST_CONTENT = """\
// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
// SPDX-License-Identifier: GPL-3.0-or-later

// 分支清单（来源：Calculator::parse(QString)）
// B1: input.isEmpty()           -> return false
// B2: !input.contains('=')      -> return false
// B3: parts.size() > 2          -> return false
// B4: ok && value parsed        -> return true
//
// 用例映射：
// - Parse_EmptyInput_ReturnsFalse           -> B1
// - Parse_NoEquals_ReturnsFalse             -> B2

// 分支清单（来源：Calculator::compute(int)）
// B1: n < 0                     -> return abs(n)
// B2: n == 0                    -> return 0

#include <gtest/gtest.h>

TEST_F(CalculatorTest, Parse_EmptyInput_ReturnsFalse) {
    // Arrange
    // Act
    // Assert
}
"""


class TestParseDeclaredBranches(unittest.TestCase):

    def test_counts_branches_for_named_method(self):
        n = fetch_mcp_data.parse_declared_branches(
            SAMPLE_TEST_CONTENT, "parse")
        self.assertEqual(n, 4)  # B1-B4

    def test_counts_branches_for_compute_method(self):
        n = fetch_mcp_data.parse_declared_branches(
            SAMPLE_TEST_CONTENT, "compute")
        self.assertEqual(n, 2)  # B1-B2

    def test_method_not_in_content_returns_zero(self):
        n = fetch_mcp_data.parse_declared_branches(
            SAMPLE_TEST_CONTENT, "nonexistent")
        self.assertEqual(n, 0)

    def test_empty_method_name_counts_all(self):
        n = fetch_mcp_data.parse_declared_branches(
            SAMPLE_TEST_CONTENT, "")
        # parse 的 4 + compute 的 2 = 6
        self.assertEqual(n, 6)

    def test_no_section_returns_zero(self):
        content = "// just a comment\n#include <gtest/gtest.h>\n"
        n = fetch_mcp_data.parse_declared_branches(content, "parse")
        self.assertEqual(n, 0)

    def test_section_isolation_between_methods(self):
        # parse 段落不应计入 compute 的分支
        n = fetch_mcp_data.parse_declared_branches(
            SAMPLE_TEST_CONTENT, "parse")
        self.assertEqual(n, 4)
        # 确认没有把 compute 的 B1/B2 算进来

    def test_fullwidth_parens_section(self):
        # 全角括号版本（中文文档常见）
        content = (
            " // 分支清单（来源：Foo::bar()）\n"
            " // B1: cond1 -> x\n"
            " // B2: cond2 -> y\n"
        )
        n = fetch_mcp_data.parse_declared_branches(content, "bar")
        self.assertEqual(n, 2)

    def test_halfwidth_parens_section(self):
        # 半角括号版本
        content = (
            " // 分支清单(来源：Foo::baz())\n"
            " // B1: cond1 -> x\n"
        )
        n = fetch_mcp_data.parse_declared_branches(content, "baz")
        self.assertEqual(n, 1)

    def test_short_method_name_not_substring_matched(self):
        # 回归：方法名 "a" 不应误命中 "来源：Foo::parse" 段落
        content = (
            "// 分支清单（来源：Foo::parse）\n"
            "// B1: empty -> 0\n"
            "// B2: no eq -> -1\n"
        )
        n = fetch_mcp_data.parse_declared_branches(content, "a")
        self.assertEqual(n, 0)

    def test_method_name_not_prefix_matched(self):
        # 回归：方法 "parse" 不应误命中 "来源：Foo::parseHelper" 段落
        content = (
            "// 分支清单（来源：Foo::parseHelper）\n"
            "// B1: x -> y\n"
        )
        n = fetch_mcp_data.parse_declared_branches(content, "parse")
        self.assertEqual(n, 0)
        # 但 parseHelper 自己能命中
        self.assertEqual(fetch_mcp_data.parse_declared_branches(content, "parseHelper"), 1)


# ═══════════════════════════════════════════════════════════════════════
# 4. cross_check_branches
# ═══════════════════════════════════════════════════════════════════════

class TestCrossCheckBranches(unittest.TestCase):

    def test_missing_branch_list_when_complex_and_no_declaration(self):
        r = fetch_mcp_data.cross_check_branches(
            real_total=5, declared_count=0, is_complex=True)
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "MISSING_BRANCH_LIST")
        self.assertEqual(r[1], "error")

    def test_not_missing_when_simple_and_no_declaration(self):
        # 简单方法（complex<10 且分支<3）无清单 → 允许跳过（§4.1），不判违规
        r = fetch_mcp_data.cross_check_branches(
            real_total=2, declared_count=0, is_complex=False)
        self.assertIsNone(r)

    def test_simple_with_incomplete_list_still_not_mapped(self):
        # 简单方法声明了清单但不完整 → 仍判 NOT_MAPPED（声明了就得完整）
        r = fetch_mcp_data.cross_check_branches(
            real_total=2, declared_count=1, is_complex=False)
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "BRANCH_NOT_MAPPED")

    def test_branch_not_mapped_when_declared_less_than_real(self):
        r = fetch_mcp_data.cross_check_branches(
            real_total=5, declared_count=3, is_complex=True)
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "BRANCH_NOT_MAPPED")
        self.assertIn("declared=3", r[2])
        self.assertIn("actual=5", r[2])

    def test_pass_when_declared_equals_real(self):
        r = fetch_mcp_data.cross_check_branches(
            real_total=4, declared_count=4, is_complex=True)
        self.assertIsNone(r)

    def test_pass_when_declared_exceeds_real(self):
        # 声明 ≥ 真实即通过（声明多一些不算违规）
        r = fetch_mcp_data.cross_check_branches(
            real_total=3, declared_count=5, is_complex=True)
        self.assertIsNone(r)

    def test_pass_when_zero_real_zero_declared_simple(self):
        r = fetch_mcp_data.cross_check_branches(
            real_total=0, declared_count=0, is_complex=False)
        self.assertIsNone(r)

    def test_not_mapped_when_zero_real_but_complex_missing(self):
        # complex 但真实分支 0（空方法体）且无声明 → MISSING（complex 优先）
        r = fetch_mcp_data.cross_check_branches(
            real_total=0, declared_count=0, is_complex=True)
        self.assertEqual(r[0], "MISSING_BRANCH_LIST")

    def test_message_format(self):
        r = fetch_mcp_data.cross_check_branches(
            real_total=7, declared_count=2, is_complex=True)
        self.assertEqual(r[2], "declared=2 actual=7")


# ═══════════════════════════════════════════════════════════════════════
# 5. select_class_methods
# ═══════════════════════════════════════════════════════════════════════

class TestSelectClassMethods(unittest.TestCase):

    SAMPLE_INVENTORY = {
        "methods": [
            {"qualified_name": "proj.ns.Calculator.parse",
             "name": "parse", "class_qn": "ns.Calculator",
             "testable": True, "complexity": 15},
            {"qualified_name": "proj.ns.Calculator.compute",
             "name": "compute", "class_qn": "ns.Calculator",
             "testable": True, "complexity": 5},
            {"qualified_name": "proj.ns.Calculator.~Calculator",
             "name": "~Calculator", "class_qn": "ns.Calculator",
             "testable": False, "complexity": 1},
            {"qualified_name": "proj.other.Parser.run",
             "name": "run", "class_qn": "other.Parser",
             "testable": True, "complexity": 8},
        ]
    }

    def test_selects_class_by_short_name(self):
        ms = fetch_mcp_data.select_class_methods(
            self.SAMPLE_INVENTORY, "Calculator")
        self.assertEqual(len(ms), 2)  # parse + compute（析构 non-testable 排除）

    def test_excludes_non_testable(self):
        ms = fetch_mcp_data.select_class_methods(
            self.SAMPLE_INVENTORY, "Calculator")
        names = [m["name"] for m in ms]
        self.assertNotIn("~Calculator", names)

    def test_excludes_other_classes(self):
        ms = fetch_mcp_data.select_class_methods(
            self.SAMPLE_INVENTORY, "Calculator")
        names = [m["name"] for m in ms]
        self.assertNotIn("run", names)

    def test_selects_other_class(self):
        ms = fetch_mcp_data.select_class_methods(
            self.SAMPLE_INVENTORY, "Parser")
        self.assertEqual(len(ms), 1)
        self.assertEqual(ms[0]["name"], "run")

    def test_empty_inventory(self):
        ms = fetch_mcp_data.select_class_methods(
            {"methods": []}, "Calculator")
        self.assertEqual(ms, [])

    def test_no_matching_class(self):
        ms = fetch_mcp_data.select_class_methods(
            self.SAMPLE_INVENTORY, "NonExistent")
        self.assertEqual(ms, [])

    def test_simple_class_qn_no_namespace(self):
        inv = {"methods": [
            {"qualified_name": "proj.Calc.add", "name": "add",
             "class_qn": "Calc", "testable": True, "complexity": 3},
        ]}
        ms = fetch_mcp_data.select_class_methods(inv, "Calc")
        self.assertEqual(len(ms), 1)


# ═══════════════════════════════════════════════════════════════════════
# 6. fetch_method_bodies（mock client）
# ═══════════════════════════════════════════════════════════════════════

class TestFetchMethodBodies(unittest.TestCase):

    def _make_methods(self):
        return [
            {"qualified_name": "proj.Calc.parse", "name": "parse",
             "complexity": 15},
            {"qualified_name": "proj.Calc.compute", "name": "compute",
             "complexity": 5},
        ]

    def test_dict_body_field(self):
        client = MagicMock()
        client.call_tool.side_effect = [
            {"body": "if (x) return 1; return 0;"},
            {"body": "return 0;"},
        ]
        result = fetch_mcp_data.fetch_method_bodies(
            client, "proj", self._make_methods())
        self.assertIn(result["proj.Calc.parse"]["body"], result["proj.Calc.parse"]["body"])
        self.assertTrue(result["proj.Calc.parse"]["body"])
        self.assertEqual(result["proj.Calc.compute"]["body"], "return 0;")

    def test_dict_code_field_fallback(self):
        client = MagicMock()
        client.call_tool.return_value = {"code": "if (x) return 1; return 0;"}
        result = fetch_mcp_data.fetch_method_bodies(
            client, "proj", self._make_methods())
        self.assertEqual(result["proj.Calc.parse"]["body"],
                         "if (x) return 1; return 0;")

    def test_dict_source_field_fallback(self):
        client = MagicMock()
        client.call_tool.return_value = {"source": "for (;;) break;"}
        result = fetch_mcp_data.fetch_method_bodies(
            client, "proj", self._make_methods())
        self.assertEqual(result["proj.Calc.parse"]["body"], "for (;;) break;")

    def test_string_return_fallback(self):
        client = MagicMock()
        client.call_tool.return_value = "if (x) return 1; return 0;"
        result = fetch_mcp_data.fetch_method_bodies(
            client, "proj", self._make_methods())
        self.assertEqual(result["proj.Calc.parse"]["body"],
                         "if (x) return 1; return 0;")

    def test_empty_dict_body(self):
        client = MagicMock()
        client.call_tool.return_value = {}
        result = fetch_mcp_data.fetch_method_bodies(
            client, "proj", self._make_methods())
        self.assertEqual(result["proj.Calc.parse"]["body"], "")

    def test_call_failure_recorded_as_error(self):
        client = MagicMock()
        client.call_tool.side_effect = RuntimeError("connection refused")
        result = fetch_mcp_data.fetch_method_bodies(
            client, "proj", self._make_methods())
        self.assertEqual(result["proj.Calc.parse"]["body"], "")
        self.assertIn("error", result["proj.Calc.parse"])
        self.assertIn("connection refused",
                      result["proj.Calc.parse"]["error"])

    def test_skips_methods_without_qualified_name(self):
        client = MagicMock()
        client.call_tool.return_value = {"body": "return 0;"}
        methods = [
            {"qualified_name": "proj.Calc.parse", "name": "parse", "complexity": 5},
            {"name": "noqn", "complexity": 5},  # 无 qn
        ]
        result = fetch_mcp_data.fetch_method_bodies(client, "proj", methods)
        self.assertEqual(len(result), 1)
        self.assertIn("proj.Calc.parse", result)

    def test_preserves_name_and_complexity(self):
        client = MagicMock()
        client.call_tool.return_value = {"body": "return 0;"}
        result = fetch_mcp_data.fetch_method_bodies(
            client, "proj", self._make_methods())
        self.assertEqual(result["proj.Calc.parse"]["name"], "parse")
        self.assertEqual(result["proj.Calc.parse"]["complexity"], 15)

    def test_passes_correct_arguments_to_call_tool(self):
        client = MagicMock()
        client.call_tool.return_value = {"body": "return 0;"}
        methods = [{"qualified_name": "proj.Calc.parse",
                    "name": "parse", "complexity": 5}]
        fetch_mcp_data.fetch_method_bodies(client, "myproj", methods)
        client.call_tool.assert_called_once_with(
            "get_code_snippet",
            {"project": "myproj",
             "qualified_name": "proj.Calc.parse"})


# ═══════════════════════════════════════════════════════════════════════
# 7. 端到端集成：纯函数组合（不连真实 MCP）
# ═══════════════════════════════════════════════════════════════════════

class TestIntegrationPureFunctions(unittest.TestCase):
    """模拟 self-checker §2c 的判定流程，验证纯函数组合正确。"""

    def test_complex_method_with_matching_branch_list_passes(self):
        # 真实方法体：2 if + 2 early return(3 return - 末尾1) = 4 分支
        body = "if (a) return 1; if (b) return 2; return 0;"
        # 声明 4 个分支（if 和 early return 各自独立计数，符合 test-types §4.2）
        content = (
            "// 分支清单（来源：Foo::bar()）\n"
            "// B1: a -> return 1\n"
            "// B2: !a fallthrough\n"
            "// B3: b -> return 2\n"
            "// B4: !b fallthrough -> return 0\n"
        )
        real = fetch_mcp_data.extract_branches(body)
        declared = fetch_mcp_data.parse_declared_branches(content, "bar")
        is_complex = real["total"] >= 3
        res = fetch_mcp_data.cross_check_branches(
            real["total"], declared, is_complex)
        self.assertIsNone(res)  # 通过
        self.assertEqual(real["total"], 4)
        self.assertEqual(declared, 4)

    def test_complex_method_missing_branch_list(self):
        body = "if (a) return 1; if (b) return 2; if (c) return 3; return 0;"
        content = "// no branch list here\n#include <gtest/gtest.h>\n"
        real = fetch_mcp_data.extract_branches(body)
        declared = fetch_mcp_data.parse_declared_branches(content, "bar")
        is_complex = real["total"] >= 3
        res = fetch_mcp_data.cross_check_branches(
            real["total"], declared, is_complex)
        self.assertEqual(res[0], "MISSING_BRANCH_LIST")

    def test_complex_method_branch_not_mapped(self):
        # 真实 3 分支，只声明 1 个
        body = "if (a) return 1; if (b) return 2; return 0;"
        content = (
            "// 分支清单（来源：Foo::bar()）\n"
            "// B1: a -> return 1\n"
        )
        real = fetch_mcp_data.extract_branches(body)
        declared = fetch_mcp_data.parse_declared_branches(content, "bar")
        is_complex = real["total"] >= 3
        res = fetch_mcp_data.cross_check_branches(
            real["total"], declared, is_complex)
        self.assertEqual(res[0], "BRANCH_NOT_MAPPED")
        self.assertIn("declared=1", res[2])
        self.assertIn(f"actual={real['total']}", res[2])

    def test_simple_method_no_list_no_violation(self):
        # 简单方法（real=0）无清单 → pass（simple + declared==0 允许跳过）
        body = "return 42;"
        content = "// no list\n"
        real = fetch_mcp_data.extract_branches(body)
        declared = fetch_mcp_data.parse_declared_branches(content, "bar")
        is_complex = False  # real_total=0, complexity 低
        res = fetch_mcp_data.cross_check_branches(
            real["total"], declared, is_complex)
        # real=0, declared=0, simple → pass
        self.assertIsNone(res)


# ═══════════════════════════════════════════════════════════════════════
# 8. 子命令分流（main 入口）
# ═══════════════════════════════════════════════════════════════════════

class TestSubcommandDispatch(unittest.TestCase):
    """验证 extract-branches 子命令不破坏原 inventory 入口。"""

    def test_extract_branches_dispatched(self):
        # 模拟 sys.argv，验证分流逻辑（不实际运行，只检查是否进入分支）
        orig_argv = sys.argv
        try:
            sys.argv = ["fetch-mcp-data.py", "extract-branches",
                        "--help"]  # --help 让 argparse 退出
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    fetch_mcp_data.main()
        finally:
            sys.argv = orig_argv

    def test_inventory_mode_not_dispatched(self):
        # 无 extract-branches 子命令 → 走 inventory 主流程
        # inventory --help 会 SystemExit(0)
        orig_argv = sys.argv
        try:
            sys.argv = ["fetch-mcp-data.py", "--help"]
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    fetch_mcp_data.main()
        finally:
            sys.argv = orig_argv


# ═══════════════════════════════════════════════════════════════════════
# 9. 端到端冲烟（run_extract_branches 入口，monkeypatch MCPClient）
# ═══════════════════════════════════════════════════════════════════════

class TestRunExtractBranchesSmoke(unittest.TestCase):
    """验证 run_extract_branches 胶水层：参数解析/文件IO/classname 推断/输出/退出码。

    monkeypatch MCPClient 避免连真实 MCP；fetch_method_bodies 用预设 body。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="branch_smoke_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _make_inventory(self, classname, methods):
        """构造最小 inventory：方法列表 + class_qn。"""
        ms = []
        for name, complexity in methods:
            ms.append({
                "qualified_name": f"proj.{classname}.{name}",
                "name": name,
                "class_qn": classname,
                "testable": True,
                "complexity": complexity,
            })
        return {"version": 1, "project": "proj", "methods": ms}

    def _patch_mcp(self, bodies):
        """返回一个 patch 上下文，MCPClient 返回调用 get_code_snippet 的 mock。"""
        mock_client = MagicMock()
        mock_client.initialize.return_value = None
        def fake_call_tool(name, args):
            if name == "get_code_snippet":
                qn = args.get("qualified_name")
                body = bodies.get(qn, "")
                return {"body": body}
            return {}
        mock_client.call_tool.side_effect = fake_call_tool
        return patch.object(fetch_mcp_data, "MCPClient", return_value=mock_client)

    def _run_main(self, argv, bodies):
        """运行 main()，捕获 stdout + 退出码。

        main() 总是以 sys.exit(0|1) 结束，所以总是 raise SystemExit。
        返回 (exit_code, stdout_str)。
        """
        orig_argv = sys.argv
        try:
            sys.argv = argv
            with self._patch_mcp(bodies):
                with contextlib.redirect_stdout(io.StringIO()) as buf:
                    try:
                        fetch_mcp_data.main()
                        code = 0  # 理论上不会到达
                    except SystemExit as e:
                        code = e.code if isinstance(e.code, int) else 1
            return code, buf.getvalue()
        finally:
            sys.argv = orig_argv

    def test_no_testable_methods_skips(self):
        inv = self._write(".ut-inventory.json",
                          json.dumps(self._make_inventory("Foo", [])))
        testf = self._write("test_foo.cpp", "// empty\n")
        code, out = self._run_main(
            ["fmd.py", "extract-branches",
             "--project", "proj", "--test-file", testf,
             "--inventory", inv], {})
        self.assertEqual(code, 0)
        self.assertIn("methods:0", out)

    def test_pass_when_branch_list_matches(self):
        body = ("int parse(QString s) {\n"
                "  if (s.isEmpty()) return 0;\n"
                "  if (!s.contains('=')) return -1;\n"
                "  return s.size();\n"
                "}")
        # real: 2 if + 2 early return(3 return-1末尾) = 4
        test_content = (
            "// SPDX-FileCopyrightText: 2026 UnionTech\n"
            "// SPDX-License-Identifier: GPL-3.0-or-later\n\n"
            "// 分支清单（来源：Foo::parse）\n"
            "// B1: empty -> return 0\n"
            "// B2: no equals -> return -1\n"
            "// B3: has equals -> parse\n"
            "// B4: return size\n"
            "#include <gtest/gtest.h>\n"
        )
        inv = self._write(".ut-inventory.json",
                          json.dumps(self._make_inventory("Foo",
                              [("parse", 15)])))
        testf = self._write("test_foo.cpp", test_content)
        code, out = self._run_main(
            ["fmd.py", "extract-branches",
             "--project", "proj", "--test-file", testf,
             "--inventory", inv], {"proj.Foo.parse": body})
        self.assertEqual(code, 0)
        self.assertIn("0 errors", out)

    def test_fail_with_missing_branch_list(self):
        body = ("int parse(QString s) {\n"
                "  if (s.isEmpty()) return 0;\n"
                "  if (!s.contains('=')) return -1;\n"
                "  if (s.size() > 10) return 10;\n"
                "  return s.size();\n"
                "}")
        # real: 3 if + 3 early return = 6, complex, 无声明 → MISSING
        test_content = "#include <gtest/gtest.h>\n"  # 无分支清单
        inv = self._write(".ut-inventory.json",
                          json.dumps(self._make_inventory("Foo",
                              [("parse", 20)])))
        testf = self._write("test_foo.cpp", test_content)
        code, out = self._run_main(
            ["fmd.py", "extract-branches",
             "--project", "proj", "--test-file", testf,
             "--inventory", inv], {"proj.Foo.parse": body})
        self.assertEqual(code, 1)  # 有 error → 退出码 1
        self.assertIn("MISSING_BRANCH_LIST", out)
        self.assertIn("1 errors", out)

    def test_fail_with_branch_not_mapped(self):
        body = ("int compute(int n) {\n"
                "  if (n < 0) return -n;\n"
                "  if (n == 0) return 0;\n"
                "  return n * 2;\n"
                "}")
        # real: 2 if + 2 early return = 4，只声明 1 → NOT_MAPPED
        test_content = (
            "// 分支清单（来源：Foo::compute）\n"
            "// B1: n<0 -> return -n\n"
            "#include <gtest/gtest.h>\n"
        )
        inv = self._write(".ut-inventory.json",
                          json.dumps(self._make_inventory("Foo",
                              [("compute", 12)])))
        testf = self._write("test_foo.cpp", test_content)
        code, out = self._run_main(
            ["fmd.py", "extract-branches",
             "--project", "proj", "--test-file", testf,
             "--inventory", inv], {"proj.Foo.compute": body})
        self.assertEqual(code, 1)
        self.assertIn("BRANCH_NOT_MAPPED", out)
        self.assertIn("declared=1", out)

    def test_explicit_class_overrides_filename(self):
        body = "return 42;"  # real=0, simple
        test_content = "#include <gtest/gtest.h>\n"
        inv = self._write(".ut-inventory.json",
                          json.dumps(self._make_inventory("Bar",
                              [("baz", 3)])))
        # 文件名 test_foo.cpp 但显式指定 --class Bar
        testf = self._write("test_foo.cpp", test_content)
        code, out = self._run_main(
            ["fmd.py", "extract-branches",
             "--project", "proj", "--test-file", testf,
             "--inventory", inv, "--class", "Bar"],
            {"proj.Bar.baz": body})
        self.assertEqual(code, 0)
        self.assertIn("class:Bar", out)
        self.assertIn("0 errors", out)

    def test_output_json_file_written(self):
        body = "return 42;"
        test_content = "#include <gtest/gtest.h>\n"
        inv = self._write(".ut-inventory.json",
                          json.dumps(self._make_inventory("Foo",
                              [("parse", 3)])))
        testf = self._write("test_foo.cpp", test_content)
        out_json = os.path.join(self.tmpdir, "branch.json")
        code, _ = self._run_main(
            ["fmd.py", "extract-branches",
             "--project", "proj", "--test-file", testf,
             "--inventory", inv, "-o", out_json],
            {"proj.Foo.parse": body})
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(out_json))
        with open(out_json, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["class"], "Foo")
        self.assertEqual(data["checked"], 1)
        self.assertEqual(data["violations"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
