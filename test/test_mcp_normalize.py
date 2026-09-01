"""mcp-scan.py MCP 响应归一化与采集函数测试。

覆盖此前无单测的纯函数与 MCP 交互函数：
  - _lines_range_to_int：行范围串 → 整数计数
  - _tokenize_text_row：query_graph 文本行分词
  - _parse_query_graph_text：query_graph 纯文本响应解析
  - _flatten_search_graph：search_graph tree model 展平
  - _normalize_search_code：search_code full 模式归一化
  - _normalize_mcp_response：按 tool_name 分派归一化
  - MCPClient._parse_body：SSE / 纯 JSON 响应体解析
  - collect_calls_for_module：单模块 CALLS 边查询
  - collect_all_calls：批量 CALLS 采集 + 过滤
  - fetch_test_cases：TEST_F 用例名采集
  - render_test_mapping_report：Markdown 报告渲染

采集函数用 FakeClient mock MCPClient.call_tool，只验证解析与过滤逻辑。
"""
import json

import pytest


# ── FakeClient：复用 test_fetch_mcp_data / test_fetch_test_mapping 的 mock 模式 ─

class FakeClient:
    """按 tool_name → 返回值 映射模拟 MCPClient。"""

    def __init__(self, responses=None, default=None):
        self.responses = responses or {}
        self.default = default
        self.calls = []

    def call_tool(self, name, arguments, retries=3):
        self.calls.append((name, arguments))
        if name in self.responses:
            return self.responses[name]
        return self.default


# ════════════════════════════════════════════════════════════════════════════
# _lines_range_to_int
# ════════════════════════════════════════════════════════════════════════════

class TestLinesRangeToInt:
    def test_int_passthrough(self, fetch_mcp_data):
        assert fetch_mcp_data._lines_range_to_int(84) == 84

    def test_zero_int(self, fetch_mcp_data):
        assert fetch_mcp_data._lines_range_to_int(0) == 0

    def test_range_string(self, fetch_mcp_data):
        """行范围 "251-334" → 84（end - start + 1）。"""
        assert fetch_mcp_data._lines_range_to_int("251-334") == 84

    def test_single_line_range(self, fetch_mcp_data):
        """单行范围 "10-10" → 1。"""
        assert fetch_mcp_data._lines_range_to_int("10-10") == 1

    def test_bare_int_string(self, fetch_mcp_data):
        """裸整数串 "100" → 100。"""
        assert fetch_mcp_data._lines_range_to_int("100") == 100

    def test_empty_string(self, fetch_mcp_data):
        assert fetch_mcp_data._lines_range_to_int("") == 0

    def test_dash_only(self, fetch_mcp_data):
        """裸 "-" 表示 null → 0。"""
        assert fetch_mcp_data._lines_range_to_int("-") == 0

    def test_negative_int(self, fetch_mcp_data):
        """负数 "-5" 不走范围分支（startswith '-'），直接 int → -5。"""
        assert fetch_mcp_data._lines_range_to_int("-5") == -5

    def test_none(self, fetch_mcp_data):
        assert fetch_mcp_data._lines_range_to_int(None) == 0

    def test_garbage_string(self, fetch_mcp_data):
        assert fetch_mcp_data._lines_range_to_int("abc") == 0

    def test_malformed_range(self, fetch_mcp_data):
        """多段 "1-2-3" 范围解析失败 → 尝试整体 int 失败 → 0。"""
        assert fetch_mcp_data._lines_range_to_int("1-2-3") == 0

    def test_whitespace_padded(self, fetch_mcp_data):
        assert fetch_mcp_data._lines_range_to_int("  10-20  ") == 11

    def test_float_ignored(self, fetch_mcp_data):
        """float 不在 int/str 分支 → 0。"""
        assert fetch_mcp_data._lines_range_to_int(3.14) == 0


# ════════════════════════════════════════════════════════════════════════════
# _tokenize_text_row
# ════════════════════════════════════════════════════════════════════════════

class TestTokenizeTextRow:
    def test_bare_tokens(self, fetch_mcp_data):
        assert fetch_mcp_data._tokenize_text_row("a b c") == ["a", "b", "c"]

    def test_quoted_string(self, fetch_mcp_data):
        assert fetch_mcp_data._tokenize_text_row('"hello"') == ["hello"]

    def test_quoted_with_space(self, fetch_mcp_data):
        """引号内的空格不拆分。"""
        assert fetch_mcp_data._tokenize_text_row('"a b"') == ["a b"]

    def test_dash_is_none(self, fetch_mcp_data):
        """裸 '-' → None（表示 null）。"""
        assert fetch_mcp_data._tokenize_text_row("-") == [None]

    def test_mixed(self, fetch_mcp_data):
        assert fetch_mcp_data._tokenize_text_row('"add" "proj.A.add" -') == [
            "add", "proj.A.add", None]

    def test_empty_line(self, fetch_mcp_data):
        assert fetch_mcp_data._tokenize_text_row("") == []

    def test_empty_quoted(self, fetch_mcp_data):
        assert fetch_mcp_data._tokenize_text_row('""') == [""]

    def test_tab_separator(self, fetch_mcp_data):
        assert fetch_mcp_data._tokenize_text_row("a\tb") == ["a", "b"]

    def test_quoted_json_array_stays_string(self, fetch_mcp_data):
        """引号包裹的 JSON 数组串 → json.loads 返回字符串（转义解包），
        list 解析由 collect_calls_for_module 二次 json.loads 完成。"""
        inner = json.dumps(["Method"])      # '["Method"]'
        quoted = json.dumps(inner)           # '"[\\"Method\\"]"'
        result = fetch_mcp_data._tokenize_text_row(quoted)
        assert result == [inner]   # '回原始字符串 ["Method"]'

    def test_quoted_json_number(self, fetch_mcp_data):
        """引号包裹的数字是 JSON 字符串，不是 int。"""
        assert fetch_mcp_data._tokenize_text_row('"123"') == ["123"]

    def test_bare_stays_string(self, fetch_mcp_data):
        """裸值不做类型转换，永远是字符串。"""
        assert fetch_mcp_data._tokenize_text_row("123") == ["123"]

    def test_escaped_quote(self, fetch_mcp_data):
        """转义引号 \" 不提前终止引号串。"""
        result = fetch_mcp_data._tokenize_text_row('"he\\"llo"')
        assert result == ['he"llo']


# ════════════════════════════════════════════════════════════════════════════
# _parse_query_graph_text
# ════════════════════════════════════════════════════════════════════════════

class TestParseQueryGraphText:
    def test_full_format(self, fetch_mcp_data):
        text = (
            'rows: 2 (cols: target.name target.qualified_name target.file_path labels(target))\n'
            '  "add" "proj.A.add" "src/a.cpp" ["Method"]\n'
            '  "bar" "proj.A.bar" "src/b.cpp" ["Function"]\n'
            'total: 2\n'
        )
        out = fetch_mcp_data._parse_query_graph_text(text)
        assert out["cols"] == [
            "target.name", "target.qualified_name", "target.file_path", "labels(target)"]
        assert out["total"] == 2
        assert len(out["rows"]) == 2
        assert out["rows"][0][0] == "add"

    def test_empty_results(self, fetch_mcp_data):
        text = "rows: 0 (cols: a b)\ntotal: 0\n"
        out = fetch_mcp_data._parse_query_graph_text(text)
        assert out["rows"] == []
        assert out["total"] == 0
        assert out["cols"] == ["a", "b"]

    def test_no_total_line(self, fetch_mcp_data):
        """缺 total 行 → total 保持 0。"""
        text = 'rows: 1 (cols: x)\n  "v"\n'
        out = fetch_mcp_data._parse_query_graph_text(text)
        assert out["total"] == 0
        assert out["rows"] == [["v"]]

    def test_data_rows_must_be_indented(self, fetch_mcp_data):
        """顶格行不被当作数据行（仅缩进行是数据）。"""
        text = 'rows: 1 (cols: x)\nnotdata\ntotal: 1\n'
        out = fetch_mcp_data._parse_query_graph_text(text)
        assert out["rows"] == []  # "notdata" 顶格 → 不是数据行

    def test_null_values(self, fetch_mcp_data):
        """裸 '-' → None。"""
        text = 'rows: 1 (cols: a b)\n  "x" -\n'
        out = fetch_mcp_data._parse_query_graph_text(text)
        assert out["rows"][0] == ["x", None]

    def test_plain_text_passthrough(self, fetch_mcp_data):
        """纯文本无 rows/total 行 → 空 rows，total 0。"""
        out = fetch_mcp_data._parse_query_graph_text("Query returned no results")
        assert out == {"rows": [], "total": 0, "cols": []}


# ════════════════════════════════════════════════════════════════════════════
# _flatten_search_graph
# ════════════════════════════════════════════════════════════════════════════

class TestFlattenSearchGraph:
    def test_basic_flatten(self, fetch_mcp_data):
        result = {
            "total": 1,
            "cols": ["name", "label", "lines", "in", "out"],
            "groups": [{
                "qn_prefix": "proj.A",
                "file": "src/a.cpp",
                "rows": [["foo", "Method", "10-20", 3, 1]],
            }],
            "has_more": False,
        }
        out = fetch_mcp_data._flatten_search_graph(result)
        r = out["results"]
        assert len(r) == 1
        assert r[0]["qualified_name"] == "proj.A.foo"
        assert r[0]["file_path"] == "src/a.cpp"
        assert r[0]["lines"] == 11  # 10-20 → 11
        assert r[0]["in_degree"] == 3
        assert out["total"] == 1
        assert out["has_more"] is False

    def test_no_prefix(self, fetch_mcp_data):
        """qn_prefix 为空时 qualified_name = name。"""
        result = {
            "cols": ["name", "lines"],
            "groups": [{"qn_prefix": "", "file": "x.cpp", "rows": [["bar", 5]]}],
        }
        out = fetch_mcp_data._flatten_search_graph(result)
        assert out["results"][0]["qualified_name"] == "bar"

    def test_empty_groups(self, fetch_mcp_data):
        out = fetch_mcp_data._flatten_search_graph({"cols": [], "groups": []})
        assert out["results"] == []
        assert out["total"] == 0

    def test_lines_int_passthrough(self, fetch_mcp_data):
        """lines 已是 int → 原样返回。"""
        result = {"cols": ["name", "lines"], "groups": [
            {"qn_prefix": "p", "file": "f", "rows": [["x", 99]]}]}
        out = fetch_mcp_data._flatten_search_graph(result)
        assert out["results"][0]["lines"] == 99

    def test_no_in_column(self, fetch_mcp_data):
        """无 in 列时不加 in_degree。"""
        result = {"cols": ["name"], "groups": [
            {"qn_prefix": "p", "file": "f", "rows": [["x"]]}]}
        out = fetch_mcp_data._flatten_search_graph(result)
        assert "in_degree" not in out["results"][0]


# ════════════════════════════════════════════════════════════════════════════
# _normalize_search_code
# ════════════════════════════════════════════════════════════════════════════

class TestNormalizeSearchCode:
    def test_qn_file_mapped(self, fetch_mcp_data):
        result = {
            "cols": ["qn", "label", "file", "lines"],
            "rows": [["proj.A.foo", "Method", "src/a.cpp", 10]],
            "total": 1,
        }
        out = fetch_mcp_data._normalize_search_code(result)
        r = out["results"][0]
        assert r["qualified_name"] == "proj.A.foo"
        assert r["file_path"] == "src/a.cpp"
        assert r["qn"] == "proj.A.foo"
        assert r["file"] == "src/a.cpp"
        assert out["total"] == 1

    def test_preserves_other_fields(self, fetch_mcp_data):
        result = {"cols": ["qn", "complexity"], "rows": [["a.b", 5]]}
        out = fetch_mcp_data._normalize_search_code(result)
        assert out["results"][0]["complexity"] == 5

    def test_empty_rows(self, fetch_mcp_data):
        out = fetch_mcp_data._normalize_search_code({"cols": [], "rows": []})
        assert out["results"] == []

    def test_already_has_qualified_name(self, fetch_mcp_data):
        """已有 qualified_name 时不覆盖。"""
        result = {"cols": ["qn", "qualified_name"], "rows": [["a.b", "existing"]]}
        out = fetch_mcp_data._normalize_search_code(result)
        assert out["results"][0]["qualified_name"] == "existing"


# ════════════════════════════════════════════════════════════════════════════
# _normalize_mcp_response
# ════════════════════════════════════════════════════════════════════════════

class TestNormalizeMcpResponse:
    def test_query_graph_text(self, fetch_mcp_data):
        out = fetch_mcp_data._normalize_mcp_response(
            "query_graph", "rows: 1 (cols: x)\n  \"v\"\ntotal: 1\n")
        assert out["rows"] == [["v"]]
        assert out["total"] == 1

    def test_query_graph_plain_text(self, fetch_mcp_data):
        out = fetch_mcp_data._normalize_mcp_response("query_graph", "no results")
        assert out == {"rows": [], "total": 0, "cols": []}

    def test_search_graph_tree_flattened(self, fetch_mcp_data):
        result = {"cols": ["name"], "groups": [
            {"qn_prefix": "p", "file": "f", "rows": [["x"]]}]}
        out = fetch_mcp_data._normalize_mcp_response("search_graph", result)
        assert "results" in out and "groups" not in out

    def test_search_graph_already_flat_passthrough(self, fetch_mcp_data):
        result = {"results": [{"a": 1}]}
        out = fetch_mcp_data._normalize_mcp_response("search_graph", result)
        assert out is result

    def test_search_code_normalized(self, fetch_mcp_data):
        result = {"cols": ["qn"], "rows": [["a.b"]]}
        out = fetch_mcp_data._normalize_mcp_response("search_code", result)
        assert "results" in out

    def test_search_code_already_flat_passthrough(self, fetch_mcp_data):
        result = {"results": [{"a": 1}]}
        out = fetch_mcp_data._normalize_mcp_response("search_code", result)
        assert out is result

    def test_unknown_tool_dict_passthrough(self, fetch_mcp_data):
        result = {"foo": "bar"}
        out = fetch_mcp_data._normalize_mcp_response("other_tool", result)
        assert out is result

    def test_query_graph_dict_passthrough(self, fetch_mcp_data):
        """query_graph 但返回 dict（已 JSON）→ 原样返回。"""
        result = {"rows": [["x"]], "total": 1}
        out = fetch_mcp_data._normalize_mcp_response("query_graph", result)
        assert out is result

    def test_non_str_non_dict_passthrough(self, fetch_mcp_data):
        assert fetch_mcp_data._normalize_mcp_response("any", 123) == 123
        assert fetch_mcp_data._normalize_mcp_response("any", None) is None


# ════════════════════════════════════════════════════════════════════════════
# MCPClient._parse_body
# ════════════════════════════════════════════════════════════════════════════

class TestParseBody:
    def _client(self, fetch_mcp_data):
        return fetch_mcp_data.MCPClient(url="http://stub")

    def test_empty(self, fetch_mcp_data):
        assert self._client(fetch_mcp_data)._parse_body("") == {}

    def test_none(self, fetch_mcp_data):
        assert self._client(fetch_mcp_data)._parse_body(None) == {}

    def test_plain_json(self, fetch_mcp_data):
        body = '{"jsonrpc": "2.0", "result": {"content": []}}'
        out = self._client(fetch_mcp_data)._parse_body(body)
        assert out["jsonrpc"] == "2.0"

    def test_sse_single_data(self, fetch_mcp_data):
        body = 'data: {"jsonrpc": "2.0", "result": {}}\n\n'
        out = self._client(fetch_mcp_data)._parse_body(body)
        assert out["jsonrpc"] == "2.0"

    def test_sse_with_event_prefix(self, fetch_mcp_data):
        body = 'event: message\ndata: {"jsonrpc": "2.0", "result": {}}\n\n'
        out = self._client(fetch_mcp_data)._parse_body(body)
        assert out["jsonrpc"] == "2.0"

    def test_sse_no_data_returns_empty(self, fetch_mcp_data):
        """SSE 但无 data 行 → {}。"""
        body = "event: message\n\n"
        assert self._client(fetch_mcp_data)._parse_body(body) == {}

    def test_sse_multi_event_jsonrpc(self, fetch_mcp_data):
        """多 event：拼接失败后逐个解析取 jsonrpc response。"""
        body = 'data: {"a": 1}\ndata: {"jsonrpc": "2.0", "result": {}}\n'
        out = self._client(fetch_mcp_data)._parse_body(body)
        assert out.get("jsonrpc") == "2.0"

    def test_sse_split_data_joins(self, fetch_mcp_data):
        """单 JSON 跨多 data 行拼接后解析。"""
        part1 = '{"jsonrpc": "2.0", "resu'
        part2 = 'lt": {}}'
        body = f'data: {part1}\ndata: {part2}\n'
        out = self._client(fetch_mcp_data)._parse_body(body)
        assert out.get("jsonrpc") == "2.0"

    def test_plain_json_invalid_raises(self, fetch_mcp_data):
        """纯 JSON 非法 → json.loads 抛 JSONDecodeError（非 SSE 路径不吞异常）。"""
        import json as _json
        with pytest.raises(_json.JSONDecodeError):
            self._client(fetch_mcp_data)._parse_body("not json")


# ════════════════════════════════════════════════════════════════════════════
# collect_calls_for_module
# ════════════════════════════════════════════════════════════════════════════

class TestCollectCallsForModule:
    def test_parses_rows(self, fetch_mcp_data):
        c = FakeClient(default={"rows": [
            ["add", "proj.A.add", "src/a.cpp", ["Method"]],
            ["bar", "proj.A.bar", "src/b.cpp", ["Function"]],
        ]})
        out = fetch_mcp_data.collect_calls_for_module(c, "proj", "ut_foo.cpp")
        assert len(out) == 2
        assert out[0] == ("add", "proj.A.add", "src/a.cpp", ["Method"])

    def test_labels_as_json_string(self, fetch_mcp_data):
        """labels 以 JSON 字符串返回 → json.loads 解析为 list。"""
        c = FakeClient(default={"rows": [
            ["add", "proj.A.add", "src/a.cpp", '["Method"]'],
        ]})
        out = fetch_mcp_data.collect_calls_for_module(c, "proj", "ut_foo.cpp")
        assert out[0][3] == ["Method"]

    def test_labels_invalid_json_wrapped(self, fetch_mcp_data):
        """labels 非法 JSON 串 → 包成单元素 list。"""
        c = FakeClient(default={"rows": [
            ["add", "proj.A.add", "src/a.cpp", "garbage"],
        ]})
        out = fetch_mcp_data.collect_calls_for_module(c, "proj", "ut_foo.cpp")
        assert out[0][3] == ["garbage"]

    def test_short_row_skipped(self, fetch_mcp_data):
        """行元素 < 3 → 跳过。"""
        c = FakeClient(default={"rows": [["a", "b"]]})
        assert fetch_mcp_data.collect_calls_for_module(c, "proj", "ut.cpp") == []

    def test_row_without_labels(self, fetch_mcp_data):
        """行恰好 3 元素 → labels = []。"""
        c = FakeClient(default={"rows": [["add", "proj.A.add", "src/a.cpp"]]})
        out = fetch_mcp_data.collect_calls_for_module(c, "proj", "ut.cpp")
        assert out[0][3] == []

    def test_empty_rows(self, fetch_mcp_data):
        c = FakeClient(default={"rows": []})
        assert fetch_mcp_data.collect_calls_for_module(c, "proj", "ut.cpp") == []

    def test_null_values_become_empty(self, fetch_mcp_data):
        """None 值 → 空串（`or ""` 兜底）。"""
        c = FakeClient(default={"rows": [[None, None, None, ["Method"]]]})
        out = fetch_mcp_data.collect_calls_for_module(c, "proj", "ut.cpp")
        assert out[0] == ("", "", "", ["Method"])


# ════════════════════════════════════════════════════════════════════════════
# collect_all_calls
# ════════════════════════════════════════════════════════════════════════════

class TestCollectAllCalls:
    def _mod(self, name, file, out_degree):
        return {"name": name, "file_path": file, "out_degree": out_degree}

    def test_field_filtered(self, fetch_mcp_data):
        """纯 Field 节点（无 Method/Function label）→ 过滤。"""
        c = FakeClient(default={"rows": [
            ["fld", "proj.A.fld", "src/a.cpp", ["Field"]],
        ]})
        out = fetch_mcp_data.collect_all_calls(
            c, "proj", [self._mod("ut_x.cpp", "tests/ut_x.cpp", 1)])
        assert out == {}

    def test_tests_dir_filtered(self, fetch_mcp_data):
        """目标在 tests/ 目录 → 过滤（stub/辅助类）。"""
        c = FakeClient(default={"rows": [
            ["helper", "proj.tests.Helper.run", "tests/helper.cpp", ["Method"]],
        ]})
        out = fetch_mcp_data.collect_all_calls(
            c, "proj", [self._mod("ut_x.cpp", "tests/ut_x.cpp", 1)])
        assert out == {}

    def test_method_kept(self, fetch_mcp_data):
        """Method 目标 → 保留。"""
        c = FakeClient(default={"rows": [
            ["add", "proj.A.add", "src/a.cpp", ["Method"]],
        ]})
        out = fetch_mcp_data.collect_all_calls(
            c, "proj", [self._mod("ut_x.cpp", "tests/ut_x.cpp", 1)])
        assert "proj.A.add" in out
        assert out["proj.A.add"] == {"tests/ut_x.cpp"}

    def test_function_kept(self, fetch_mcp_data):
        c = FakeClient(default={"rows": [
            ["main", "proj.main", "src/main.cpp", ["Function"]],
        ]})
        out = fetch_mcp_data.collect_all_calls(
            c, "proj", [self._mod("ut_x.cpp", "tests/ut_x.cpp", 1)])
        assert "proj.main" in out

    def test_field_with_method_kept(self, fetch_mcp_data):
        """同时含 Field 和 Method label → 保留（Field 过滤需 Method/Function 缺席）。"""
        c = FakeClient(default={"rows": [
            ["x", "proj.A.x", "src/a.cpp", ["Field", "Method"]],
        ]})
        out = fetch_mcp_data.collect_all_calls(
            c, "proj", [self._mod("ut_x.cpp", "tests/ut_x.cpp", 1)])
        assert "proj.A.x" in out

    def test_no_labels_filtered(self, fetch_mcp_data):
        """空 labels（非 Method/Function）→ 过滤。"""
        c = FakeClient(default={"rows": [
            ["x", "proj.A.x", "src/a.cpp", []],
        ]})
        out = fetch_mcp_data.collect_all_calls(
            c, "proj", [self._mod("ut_x.cpp", "tests/ut_x.cpp", 1)])
        assert out == {}

    def test_zero_out_degree_not_queried(self, fetch_mcp_data):
        """out_degree=0 的模块不被查询。"""
        c = FakeClient(default={"rows": [["x", "proj.A.x", "src/a.cpp", ["Method"]]]})
        out = fetch_mcp_data.collect_all_calls(
            c, "proj", [self._mod("ut_dead.cpp", "tests/ut_dead.cpp", 0)])
        assert out == {}

    def test_multiple_test_files_aggregate(self, fetch_mcp_data):
        """同一源码被多个测试模块调用 → test_files 聚合为 set。"""
        responses = {"query_graph": {"rows": [
            ["add", "proj.A.add", "src/a.cpp", ["Method"]],
        ]}}
        c = FakeClient(responses=responses)
        mods = [self._mod("ut_a.cpp", "tests/ut_a.cpp", 1),
                self._mod("ut_b.cpp", "tests/ut_b.cpp", 1)]
        out = fetch_mcp_data.collect_all_calls(c, "proj", mods)
        assert out["proj.A.add"] == {"tests/ut_a.cpp", "tests/ut_b.cpp"}


# ════════════════════════════════════════════════════════════════════════════
# fetch_test_cases
# ════════════════════════════════════════════════════════════════════════════

class TestFetchTestCases:
    def _result(self, rows):
        return {"results": rows, "total": len(rows)}

    def test_parses_signature(self, fetch_mcp_data):
        """signature "(Suite, CaseName)" → "Suite.CaseName"。"""
        c = FakeClient(default=self._result([
            {"signature": "(CalcTest, AddHandlesZero)",
             "file_path": "tests/ut_calc.cpp"},
        ]))
        out = fetch_mcp_data.fetch_test_cases(c, "proj", [])
        assert out["tests/ut_calc.cpp"] == ["CalcTest.AddHandlesZero"]

    def test_whitespace_in_signature(self, fetch_mcp_data):
        c = FakeClient(default=self._result([
            {"signature": "( Suite , Case )", "file_path": "tests/ut.cpp"},
        ]))
        out = fetch_mcp_data.fetch_test_cases(c, "proj", [])
        assert out["tests/ut.cpp"] == ["Suite.Case"]

    def test_empty_signature_skipped(self, fetch_mcp_data):
        c = FakeClient(default=self._result([
            {"signature": "", "file_path": "tests/ut.cpp"},
        ]))
        assert fetch_mcp_data.fetch_test_cases(c, "proj", []) == {}

    def test_no_parens_skipped(self, fetch_mcp_data):
        """signature 无括号 → 正则不匹配 → 跳过。"""
        c = FakeClient(default=self._result([
            {"signature": "TEST_F", "file_path": "tests/ut.cpp"},
        ]))
        assert fetch_mcp_data.fetch_test_cases(c, "proj", []) == {}

    def test_missing_file_skipped(self, fetch_mcp_data):
        c = FakeClient(default=self._result([
            {"signature": "(S, C)", "file_path": ""},
        ]))
        assert fetch_mcp_data.fetch_test_cases(c, "proj", []) == {}

    def test_docstring_comment(self, fetch_mcp_data):
        """有 docstring → 追加注释。"""
        c = FakeClient(default=self._result([
            {"signature": "(S, C)", "file_path": "tests/ut.cpp",
             "docstring": "// adds two numbers"},
        ]))
        out = fetch_mcp_data.fetch_test_cases(c, "proj", [])
        assert "S.C" in out["tests/ut.cpp"][0]
        assert "// adds two numbers" in out["tests/ut.cpp"][0]

    def test_multiple_cases_one_file(self, fetch_mcp_data):
        c = FakeClient(default=self._result([
            {"signature": "(S, A)", "file_path": "tests/ut.cpp"},
            {"signature": "(S, B)", "file_path": "tests/ut.cpp"},
        ]))
        out = fetch_mcp_data.fetch_test_cases(c, "proj", [])
        assert out["tests/ut.cpp"] == ["S.A", "S.B"]

    def test_empty_results(self, fetch_mcp_data):
        c = FakeClient(default=self._result([]))
        assert fetch_mcp_data.fetch_test_cases(c, "proj", []) == {}


# ════════════════════════════════════════════════════════════════════════════
# render_test_mapping_report
# ════════════════════════════════════════════════════════════════════════════

class TestRenderTestMappingReport:
    def _summary(self):
        return {"total_modules": 5, "with_calls": 3,
                "covered_sources": 10, "total_calls": 25}

    def test_headers_present(self, fetch_mcp_data):
        report = fetch_mcp_data.render_test_mapping_report([], 0, "proj", self._summary())
        assert "# 函数↔单元测试映射报告" in report
        assert "`proj`" in report
        assert "测试模块: 5" in report
        assert "有 CALLS 关系: 3" in report

    def test_empty_methods(self, fetch_mcp_data):
        report = fetch_mcp_data.render_test_mapping_report([], 0, "proj", self._summary())
        assert "共 0 个方法" in report

    def test_updated_methods_table(self, fetch_mcp_data):
        methods = [
            {"name": "add", "qn": "A.add", "new_cover": 2, "new_uc": 2,
             "test_files": ["tests/ut_a.cpp", "tests/ut_b.cpp"]},
            {"name": "bar", "qn": "A.bar", "new_cover": 1, "new_uc": 1,
             "test_files": ["tests/ut_a.cpp"]},
        ]
        report = fetch_mcp_data.render_test_mapping_report(methods, 0, "proj", self._summary())
        assert "覆盖 2 个测试文件" in report
        assert "覆盖 1 个测试文件" in report
        assert "`A.add`" in report
        assert "ut_a.cpp" in report

    def test_unmatched_section(self, fetch_mcp_data):
        report = fetch_mcp_data.render_test_mapping_report([], 7, "proj", self._summary())
        assert "## 未匹配方法" in report
        assert "共 7 个" in report

    def test_no_unmatched_section_when_zero(self, fetch_mcp_data):
        report = fetch_mcp_data.render_test_mapping_report([], 0, "proj", self._summary())
        assert "## 未匹配方法" not in report

    def test_methods_sorted_by_cover_desc(self, fetch_mcp_data):
        methods = [
            {"name": "low", "qn": "A.low", "new_cover": 1, "new_uc": 1,
             "test_files": ["tests/ut_a.cpp"]},
            {"name": "high", "qn": "A.high", "new_cover": 5, "new_uc": 5,
             "test_files": ["tests/ut_x.cpp"]},
        ]
        report = fetch_mcp_data.render_test_mapping_report(methods, 0, "proj", self._summary())
        # 覆盖 5 的组应在覆盖 1 的组之前
        assert report.index("覆盖 5") < report.index("覆盖 1")
