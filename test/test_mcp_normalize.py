"""mcp-scan.py 编解码与报告渲染测试（GitNexus 迁移后保留段）。

旧 codebase-memory-mcp 文本归一化层（_lines_range_to_int/_tokenize_text_row/
_parse_query_graph_text/_flatten_search_graph/_normalize_search_code/
_normalize_mcp_response）已随 GitNexus 单栈改造删除，对应用例移至：
  - test_gitnexus_codec.py：parse_tool_result / markdown_rows / 错误分类
  - test_gitnexus_adapter.py：GitNexusAdapter 采集逻辑（FakeCypherClient）

本文件保留仍存在且语义未变的部分：
  - MCPClient._parse_body：HTTP 响应体解析（SSE/纯 JSON）
  - render_test_mapping_report：test-mapping Markdown 报告渲染
"""
import pytest


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
