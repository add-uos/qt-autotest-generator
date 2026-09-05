"""GitNexus 响应编解码与本地度量测试（mcp-scan.py ResponseCodec 段）。

覆盖实测 GitNexus MCP 的响应形态（见 doc/gitnexus-适配分析.md）：
  - parse_tool_result：字符串化 JSON / 多段拼接 / markdown 表包裹 / 错误分类
  - markdown_rows：引号感知管道切分、多行单元格、'-' → None
  - cypher 助手：_cypher_str / _file_where / _derive_return_keys / paginate_cypher
  - 本地度量：compute_body_metrics / _signature_from_body / _param_count_from_signature
  - Qt 宏扫描：scan_qt_macros_in_file
"""
import json

import pytest


# ════════════════════════════════════════════════════════════════════════════
# parse_tool_result
# ════════════════════════════════════════════════════════════════════════════

class TestParseToolResult:
    def test_dict_passthrough(self, fetch_mcp_data):
        assert fetch_mcp_data.parse_tool_result({"a": 1}) == {"a": 1}

    def test_list_passthrough(self, fetch_mcp_data):
        assert fetch_mcp_data.parse_tool_result([1, 2]) == [1, 2]

    def test_non_str_non_dict_passthrough(self, fetch_mcp_data):
        assert fetch_mcp_data.parse_tool_result(42) == 42

    def test_empty_string(self, fetch_mcp_data):
        assert fetch_mcp_data.parse_tool_result("") == {"cols": [], "rows": [], "total": 0}

    def test_whitespace_only(self, fetch_mcp_data):
        assert fetch_mcp_data.parse_tool_result("  \n ") == {"cols": [], "rows": [], "total": 0}

    def test_stringified_json(self, fetch_mcp_data):
        """工具结果统一为字符串化 JSON → 解析为 dict。"""
        raw = json.dumps({"status": "ok", "n": 3})
        assert fetch_mcp_data.parse_tool_result(raw) == {"status": "ok", "n": 3}

    def test_multi_segment_takes_first(self, fetch_mcp_data):
        """大分页尾部多段拼接 → raw_decode 取首段。"""
        raw = '{"a": 1}{"b": 2}'
        assert fetch_mcp_data.parse_tool_result(raw) == {"a": 1}

    def test_error_prefix_raises_non_retryable(self, fetch_mcp_data):
        """'Error: Prepare failed: ...' → GraphQueryError（不可重试）。"""
        with pytest.raises(fetch_mcp_data.GraphQueryError) as ei:
            fetch_mcp_data.parse_tool_result("Error: Prepare failed: bad query")
        assert ei.value.retryable is False

    def test_ladybugdb_unavailable_retryable(self, fetch_mcp_data):
        """'Error: LadybugDB unavailable'（索引重建锁）→ 可重试。"""
        with pytest.raises(fetch_mcp_data.GraphQueryError) as ei:
            fetch_mcp_data.parse_tool_result(
                "Error: LadybugDB unavailable, index is rebuilding the index")
        assert ei.value.retryable is True

    def test_shadow_pages_retryable(self, fetch_mcp_data):
        with pytest.raises(fetch_mcp_data.GraphQueryError) as ei:
            fetch_mcp_data.parse_tool_result("Error: waiting for shadow pages")
        assert ei.value.retryable is True

    def test_error_dict_raises(self, fetch_mcp_data):
        """{"error": "Prepare failed: ..."} → GraphQueryError。"""
        with pytest.raises(fetch_mcp_data.GraphQueryError) as ei:
            fetch_mcp_data.parse_tool_result({"error": "Prepare failed: syntax"})
        assert ei.value.retryable is False

    def test_error_dict_retryable(self, fetch_mcp_data):
        with pytest.raises(fetch_mcp_data.GraphQueryError) as ei:
            fetch_mcp_data.parse_tool_result({"error": "LadybugDB unavailable"})
        assert ei.value.retryable is True

    def test_markdown_table_wrapped(self, fetch_mcp_data):
        """cypher 结果包裹为 {"markdown": "| ... |"} → 展开 {cols, rows, total}。"""
        md = '| m.name |\n| --- |\n| "add" |'
        raw = json.dumps({"markdown": md, "row_count": 1})
        out = fetch_mcp_data.parse_tool_result(raw)
        assert out["cols"] == ["m.name"]
        assert out["rows"] == [["add"]]
        assert out["total"] == 1

    def test_double_wrapped_json_string(self, fetch_mcp_data):
        """字符串内嵌 JSON 字符串（实测 list_repos 大分页形态）→ 双重解包。"""
        inner = json.dumps({"repositories": [{"name": "p"}]})
        raw = json.dumps(inner)
        assert fetch_mcp_data.parse_tool_result(raw) == {"repositories": [{"name": "p"}]}

    def test_plain_text_without_structure_passthrough(self, fetch_mcp_data):
        """非 JSON 非 markdown 的纯文本 → 原样返回。"""
        assert fetch_mcp_data.parse_tool_result("just some text") == "just some text"


# ════════════════════════════════════════════════════════════════════════════
# markdown_rows / _split_md_cells / _decode_md_cell
# ════════════════════════════════════════════════════════════════════════════

class TestSplitMdCells:
    def test_basic_row(self, fetch_mcp_data):
        cells, trailing, in_q = fetch_mcp_data._split_md_cells('| "add" | "src/a.cpp" |')
        assert cells == ["", '"add"', '"src/a.cpp"']
        assert trailing == ""
        assert in_q is False

    def test_quoted_pipe_stays_in_cell(self, fetch_mcp_data):
        cells, _, _ = fetch_mcp_data._split_md_cells('| "a|b" | 2 |')
        assert cells == ["", '"a|b"', "2"]

    def test_unterminated_quote(self, fetch_mcp_data):
        cells, trailing, in_q = fetch_mcp_data._split_md_cells('| "multi')
        assert in_q is True
        assert trailing == '"multi'  # 残留保留引号字符（内部缓冲用）


class TestDecodeMdCell:
    def test_dash_is_none(self, fetch_mcp_data):
        assert fetch_mcp_data._decode_md_cell("-") is None

    def test_empty_is_none(self, fetch_mcp_data):
        assert fetch_mcp_data._decode_md_cell("") is None

    def test_json_string(self, fetch_mcp_data):
        assert fetch_mcp_data._decode_md_cell('"src/a.cpp"') == "src/a.cpp"

    def test_json_number_string(self, fetch_mcp_data):
        assert fetch_mcp_data._decode_md_cell('"42"') == "42"

    def test_bare_number_stays_string(self, fetch_mcp_data):
        assert fetch_mcp_data._decode_md_cell("42") == "42"

    def test_multiline_fallback_strips_quotes(self, fetch_mcp_data):
        """多行单元格（真实换行破坏 JSON 合法性）→ 去引号保留内文。"""
        cell = '"line1\nline2"'
        assert fetch_mcp_data._decode_md_cell(cell) == "line1\nline2"

    def test_truncated_content_fallback(self, fetch_mcp_data):
        """5016 截断破坏 JSON 字符串合法性（缺闭合）→ 降级去引号。"""
        cell = '"void f() { return \\"abc\\"'
        assert fetch_mcp_data._decode_md_cell(cell) == 'void f() { return \\"abc\\'


class TestMarkdownRows:
    def _md(self, cols, rows):
        """构造 GitNexus 风格 markdown 表（单元格 JSON 引号串，NULL 用 '-'）。"""
        lines = ["| " + " | ".join(cols) + " |",
                 "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in rows:
            cells = [json.dumps(c) if isinstance(c, str) else "-" for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    def test_basic_table(self, fetch_mcp_data):
        out = fetch_mcp_data.markdown_rows(self._md(
            ["m.name", "m.filePath"], [["add", "src/a.cpp"]]))
        assert out["cols"] == ["m.name", "m.filePath"]
        assert out["rows"] == [["add", "src/a.cpp"]]

    def test_empty_input(self, fetch_mcp_data):
        assert fetch_mcp_data.markdown_rows("") == {"cols": [], "rows": []}

    def test_none_input(self, fetch_mcp_data):
        assert fetch_mcp_data.markdown_rows(None) == {"cols": [], "rows": []}

    def test_no_table(self, fetch_mcp_data):
        assert fetch_mcp_data.markdown_rows("plain text") == {"cols": [], "rows": []}

    def test_null_cells(self, fetch_mcp_data):
        out = fetch_mcp_data.markdown_rows(self._md(
            ["n", "p"], [["add", None]]))
        assert out["rows"] == [["add", None]]

    def test_cell_with_pipe_char(self, fetch_mcp_data):
        out = fetch_mcp_data.markdown_rows(self._md(
            ["n"], [["a|b"]]))
        assert out["rows"] == [["a|b"]]

    def test_multiline_cell(self, fetch_mcp_data):
        """单元格含真实换行（多行 content）→ 续行缓冲后完整还原。"""
        md = ('| m.name | m.content |\n| --- | --- |\n'
              '| "f" | "line1\nline2" |\n| "g" | "x" |')
        out = fetch_mcp_data.markdown_rows(md)
        assert out["cols"] == ["m.name", "m.content"]
        assert out["rows"] == [["f", "line1\nline2"], ["g", "x"]]

    def test_multiline_cell_with_pipe_inside(self, fetch_mcp_data):
        """多行单元格内含管道符 → 不误切。"""
        md = ('| n | c |\n| --- | --- |\n| "f" | "a\nb|c" |')
        out = fetch_mcp_data.markdown_rows(md)
        assert out["rows"] == [["f", "a\nb|c"]]

    def test_separator_only_after_header(self, fetch_mcp_data):
        """分隔行在表头之后 → 跳过，不混入 rows。"""
        md = '| n |\n| --- |\n| "a" |'
        out = fetch_mcp_data.markdown_rows(md)
        assert out["rows"] == [["a"]]

    def test_row_short_padded(self, fetch_mcp_data):
        """数据行短于表头列数 → cypher_rows 补 None（在 adapter 层验证）。"""
        # markdown_rows 本身按切分结果返回，不补列
        out = fetch_mcp_data.markdown_rows('| a | b |\n| --- | --- |\n| "x" |')
        assert out["rows"] == [["x"]]


# ════════════════════════════════════════════════════════════════════════════
# cypher 助手
# ════════════════════════════════════════════════════════════════════════════

class TestCypherHelpers:
    def test_cypher_str(self, fetch_mcp_data):
        assert fetch_mcp_data._cypher_str("src") == "'src'"

    def test_cypher_str_escapes_quote(self, fetch_mcp_data):
        assert fetch_mcp_data._cypher_str("it's") == "'it\\'s'"

    def test_file_where_simple_prefix(self, fetch_mcp_data):
        where = fetch_mcp_data._file_where("m", ["src/**"])
        assert "m.filePath STARTS WITH 'src/'" in where

    def test_file_where_middle_segment(self, fetch_mcp_data):
        where = fetch_mcp_data._file_where("f", ["**/x/**"])
        assert "f.filePath CONTAINS '/x/'" in where

    def test_file_where_multi_pattern_or(self, fetch_mcp_data):
        where = fetch_mcp_data._file_where("m", ["src/**", "plugins/**"])
        assert where.count("STARTS WITH") == 2
        assert " OR " in where

    def test_file_where_none(self, fetch_mcp_data):
        """无 pattern → None（调用方按 falsy 判断拼不拼 WHERE）。"""
        assert fetch_mcp_data._file_where("m", None) is None
        assert fetch_mcp_data._file_where("m", []) is None

    def test_derive_return_keys(self, fetch_mcp_data):
        cols = "m.name AS name, m.filePath AS filePath, m.startLine AS sline"
        assert fetch_mcp_data._derive_return_keys(cols) == ["name", "filePath", "sline"]

    def test_derive_return_keys_single(self, fetch_mcp_data):
        assert fetch_mcp_data._derive_return_keys("f.filePath AS filePath") == ["filePath"]


class _FakeClient:
    """模拟 MCPClient：call_tool('cypher') 返回一页 rows。"""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def call_tool(self, name, arguments, retries=3):
        assert name == "cypher"
        self.statements.append(arguments["statement"])
        return {"cols": [], "rows": self.rows, "total": len(self.rows)}


class TestPaginateCypher:
    def test_single_page_skip_zero_first(self, fetch_mcp_data):
        c = _FakeClient([["a"], ["b"]])
        out = fetch_mcp_data.paginate_cypher(
            c, "MATCH (m:Method)", "m.name AS name", "name")
        assert out["rows"] == [["a"], ["b"]]
        stmt = c.statements[0]
        # GitNexus 硬约束：SKIP 必须在 LIMIT 之前；首页从 SKIP 0 开始
        assert "SKIP 0" in stmt
        assert "ORDER BY name" in stmt
        assert "LIMIT 500" in stmt
        assert stmt.index("SKIP") < stmt.index("LIMIT")

    def test_multi_page_skips(self, fetch_mcp_data):
        """第一页满页 → 继续翻页，SKIP 在 LIMIT 之前。"""
        page = [["r%d" % i] for i in range(500)]

        class PageClient:
            def __init__(self):
                self.statements = []

            def call_tool(self, name, arguments, retries=3):
                self.statements.append(arguments["statement"])
                stmt = arguments["statement"]
                if "SKIP 0" in stmt:
                    return {"cols": [], "rows": page, "total": 501}
                if "SKIP 500" in stmt:
                    return {"cols": [], "rows": [["last"]], "total": 501}
                return {"cols": [], "rows": [], "total": 501}

        c = PageClient()
        out = fetch_mcp_data.paginate_cypher(
            c, "MATCH (m:Method)", "m.name AS name", "name")
        assert out["rows"] == page + [["last"]]
        assert len(c.statements) == 2
        # GitNexus 硬约束：SKIP 必须在 LIMIT 之前
        for stmt in c.statements:
            assert stmt.index("SKIP") < stmt.index("LIMIT")


# ════════════════════════════════════════════════════════════════════════════
# 本地度量
# ════════════════════════════════════════════════════════════════════════════

class TestSignatureFromBody:
    def test_simple(self, fetch_mcp_data):
        body = "int add(int a, int b)\n{\n    return a + b;\n}\n"
        assert fetch_mcp_data._signature_from_body(body) == "int add(int a, int b)"

    def test_collapses_whitespace(self, fetch_mcp_data):
        body = "void   f( int  x )\n{"
        assert fetch_mcp_data._signature_from_body(body) == "void f( int x )"

    def test_no_parens_returns_empty(self, fetch_mcp_data):
        assert fetch_mcp_data._signature_from_body("just text") == ""

    def test_empty(self, fetch_mcp_data):
        assert fetch_mcp_data._signature_from_body("") == ""


class TestParamCount:
    def test_zero_params(self, fetch_mcp_data):
        assert fetch_mcp_data._param_count_from_signature("void f()") == 0

    def test_two_params(self, fetch_mcp_data):
        assert fetch_mcp_data._param_count_from_signature("int add(int a, int b)") == 2

    def test_nested_brackets_one_param(self, fetch_mcp_data):
        """数组参数内逗号不计数（顶层逗号感知）。"""
        assert fetch_mcp_data._param_count_from_signature(
            "void f(int a[2], int b)") == 2

    def test_empty_signature(self, fetch_mcp_data):
        assert fetch_mcp_data._param_count_from_signature("") == 0


class TestComputeBodyMetrics:
    def test_empty_body(self, fetch_mcp_data):
        out = fetch_mcp_data.compute_body_metrics("")
        assert out["complexity"] == 0 and out["loop_count"] == 0
        assert out["recursive"] is False

    def test_simple_method(self, fetch_mcp_data):
        body = "int add(int a, int b)\n{\n    return a + b;\n}\n"
        out = fetch_mcp_data.compute_body_metrics(body, "add")
        assert out["complexity"] == 1
        assert out["loop_count"] == 0
        assert out["param_count"] == 2
        assert out["signature"] == "int add(int a, int b)"

    def test_if_branch_counts(self, fetch_mcp_data):
        body = "void f(int x)\n{\n    if (x > 0) {\n        x++;\n    }\n}\n"
        out = fetch_mcp_data.compute_body_metrics(body, "f")
        assert out["complexity"] == 2  # 1 + if

    def test_loop_count_and_depth(self, fetch_mcp_data):
        body = ("void f()\n{\n"
                "    for (int i = 0; i < 10; i++) {\n"
                "        while (i) {\n"
                "            i--;\n"
                "        }\n"
                "    }\n"
                "}\n")
        out = fetch_mcp_data.compute_body_metrics(body, "f")
        assert out["loop_count"] == 2
        assert out["loop_depth"] == 2

    def test_alloc_in_loop(self, fetch_mcp_data):
        body = ("void f()\n{\n"
                "    for (int i = 0; i < 10; i++) {\n"
                "        auto p = new Item(i);\n"
                "    }\n"
                "}\n")
        out = fetch_mcp_data.compute_body_metrics(body, "f")
        assert out["alloc_in_loop"] == 1

    def test_recursive_detection(self, fetch_mcp_data):
        body = ("int fib(int n)\n{\n"
                "    if (n < 2) return n;\n"
                "    return fib(n - 1) + fib(n - 2);\n"
                "}\n")
        out = fetch_mcp_data.compute_body_metrics(body, "fib")
        assert out["recursive"] is True

    def test_non_recursive_name_mention(self, fetch_mcp_data):
        """签名行出现的自身名字（1 次）不算递归。"""
        body = ("int fib(int n)\n{\n"
                "    return other(n);\n"
                "}\n")
        out = fetch_mcp_data.compute_body_metrics(body, "fib")
        assert out["recursive"] is False


class TestScanQtMacros:
    def test_q_invokable(self, fetch_mcp_data):
        text = ("class Calc : public QObject {\n"
                "    Q_OBJECT\n"
                "public:\n"
                "    Q_INVOKABLE int add(int a, int b);\n"
                "    Q_INVOKABLE void reset();\n"
                "};\n")
        out = fetch_mcp_data.scan_qt_macros_in_file(text)
        assert out[0] == {"Calc": ["add", "reset"]}
        assert out[1] is False

    def test_q_plugin_metadata(self, fetch_mcp_data):
        text = ("class Plugin : public QObject {\n"
                "    Q_PLUGIN_METADATA(IID \"org.demo.Plugin\")\n"
                "};\n")
        out = fetch_mcp_data.scan_qt_macros_in_file(text)
        assert out[1] is True

    def test_no_macros(self, fetch_mcp_data):
        out = fetch_mcp_data.scan_qt_macros_in_file("int main() { return 0; }\n")
        assert out == ({}, False)


# ════════════════════════════════════════════════════════════════════════════
# MCPClient.call_tool 重试分类
# ════════════════════════════════════════════════════════════════════════════

class _FakeResp:
    """模拟 urlopen 响应（read/headers）。"""

    def __init__(self, body):
        self._body = body.encode()
        self.headers = {"Mcp-Session-Id": "sess"}

    def read(self):
        return self._body


def _rpc_text(text):
    """构造 tools/call 成功响应（text block 内容为 text）。"""
    return json.dumps({"jsonrpc": "2.0", "id": 1,
                       "result": {"content": [{"type": "text", "text": text}]}})


class TestCallToolRetryClassification:
    def _client(self, fetch_mcp_data, monkeypatch, bodies):
        """MCPClient + urlopen 依次返回 bodies；initialize/time.sleep 打桩。"""
        import urllib.request as _ur
        it = iter(bodies)
        monkeypatch.setattr(_ur, "urlopen", lambda req, timeout=None: _FakeResp(next(it)))
        c = fetch_mcp_data.MCPClient(url="http://stub")
        c.initialize = lambda: None
        monkeypatch.setattr(fetch_mcp_data.time, "sleep", lambda s: None)
        return c

    def test_retryable_error_then_success(self, fetch_mcp_data, monkeypatch):
        """LadybugDB unavailable → 退避重试 + 重新 initialize 后成功。"""
        reinit = {"n": 0}
        c = self._client(fetch_mcp_data, monkeypatch, [
            _rpc_text("Error: LadybugDB unavailable, rebuilding"),
            _rpc_text(json.dumps({"cols": [], "rows": [], "total": 0})),
        ])
        c.initialize = lambda: reinit.__setitem__("n", reinit["n"] + 1)
        out = c.call_tool("cypher", {"statement": "MATCH (n) RETURN 1"})
        assert out["rows"] == []
        assert reinit["n"] == 1  # 重试前重新 initialize

    def test_non_retryable_immediate_raise(self, fetch_mcp_data, monkeypatch):
        """Prepare failed → 立即抛 GraphQueryError，不重试。"""
        c = self._client(fetch_mcp_data, monkeypatch, [
            _rpc_text("Error: Prepare failed: syntax error")])
        with pytest.raises(fetch_mcp_data.GraphQueryError) as ei:
            c.call_tool("cypher", {"statement": "BAD"})
        assert ei.value.retryable is False

    def test_rpc_error_wrapped(self, fetch_mcp_data, monkeypatch):
        """JSON-RPC error 对象 → GraphQueryError（非 retryable）。"""
        body = json.dumps({"jsonrpc": "2.0", "id": 1,
                           "error": {"code": -32602, "message": "bad"}})
        c = self._client(fetch_mcp_data, monkeypatch, [body])
        with pytest.raises(fetch_mcp_data.GraphQueryError):
            c.call_tool("cypher", {"statement": "BAD"})

    def test_empty_content_returns_empty_table(self, fetch_mcp_data, monkeypatch):
        c = self._client(fetch_mcp_data, monkeypatch, [_rpc_text("")])
        out = c.call_tool("cypher", {"statement": "RETURN 1"})
        assert out == {"cols": [], "rows": [], "total": 0}
