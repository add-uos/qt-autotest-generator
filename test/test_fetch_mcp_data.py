"""fetch-mcp-data.py 健壮性测试。

聚焦纯函数与增量更新逻辑的边界场景：空输入、None、缺字段、畸形数据、
重复数据、异常路径。collect_methods 用 FakeClient mock MCPClient。
"""
import json
import math

import pytest


# ── FakeClient：模拟 MCPClient.call_tool ──────────────────────────────

class FakeClient:
    """按 (tool_name) → 返回值 / 异常 的映射模拟 MCPClient。"""

    def __init__(self, responses=None, default=None):
        self.responses = responses or {}   # {tool_name: retval | Exception}
        self.default = default
        self.calls = []                    # [(name, args), ...]

    def call_tool(self, name, arguments, retries=3):
        self.calls.append((name, arguments))
        if name in self.responses:
            val = self.responses[name]
            if isinstance(val, Exception):
                raise val
            return val
        return self.default


# ── _extract_class_from_qn ────────────────────────────────────────────

class TestExtractClassFromQn:
    def test_normal_method(self, fetch_mcp_data):
        assert fetch_mcp_data._extract_class_from_qn("proj.path.Calc.add") == "Calc"

    def test_two_segments(self, fetch_mcp_data):
        assert fetch_mcp_data._extract_class_from_qn("Calc.add") == "Calc"

    def test_single_segment(self, fetch_mcp_data):
        assert fetch_mcp_data._extract_class_from_qn("add") == "add"

    def test_empty_string(self, fetch_mcp_data):
        assert fetch_mcp_data._extract_class_from_qn("") is None

    def test_none(self, fetch_mcp_data):
        assert fetch_mcp_data._extract_class_from_qn(None) is None

    def test_dotted_only(self, fetch_mcp_data):
        # 只有分隔符的退化输入
        assert fetch_mcp_data._extract_class_from_qn("a.b") == "a"


# ── _parse_bases ──────────────────────────────────────────────────────

class TestParseBases:
    def test_list_passthrough(self, fetch_mcp_data):
        assert fetch_mcp_data._parse_bases(["QWidget", "QObject"]) == ["QWidget", "QObject"]

    def test_empty_list(self, fetch_mcp_data):
        assert fetch_mcp_data._parse_bases([]) == []

    def test_json_string(self, fetch_mcp_data):
        assert fetch_mcp_data._parse_bases('["QWidget", "QObject"]') == ["QWidget", "QObject"]

    def test_comma_separated(self, fetch_mcp_data):
        assert fetch_mcp_data._parse_bases("QWidget, QObject") == ["QWidget", "QObject"]

    def test_single_comma_string(self, fetch_mcp_data):
        assert fetch_mcp_data._parse_bases("QWidget") == ["QWidget"]

    def test_none(self, fetch_mcp_data):
        assert fetch_mcp_data._parse_bases(None) == []

    def test_empty_string(self, fetch_mcp_data):
        assert fetch_mcp_data._parse_bases("") == []

    def test_invalid_json_falls_back_to_comma(self, fetch_mcp_data):
        # 非法 JSON → 按逗号分隔兜底
        assert fetch_mcp_data._parse_bases("{invalid") == ["{invalid"]

    def test_json_object_not_list(self, fetch_mcp_data):
        # 合法 JSON 但不是 list → 按逗号分隔兜底
        assert fetch_mcp_data._parse_bases('{"a":1}') == ['{"a":1}']


# ── _dedup_classes ────────────────────────────────────────────────────

class TestDedupClasses:
    def test_no_dup(self, fetch_mcp_data):
        classes = [{"qualified_name": "A"}, {"qualified_name": "B"}]
        assert fetch_mcp_data._dedup_classes(classes) == classes

    def test_dup_removed(self, fetch_mcp_data):
        classes = [{"qualified_name": "A", "x": 1}, {"qualified_name": "A", "x": 2}]
        result = fetch_mcp_data._dedup_classes(classes)
        assert len(result) == 1
        assert result[0]["x"] == 1  # 保留第一个

    def test_empty(self, fetch_mcp_data):
        assert fetch_mcp_data._dedup_classes([]) == []

    def test_missing_qualified_name_kept(self, fetch_mcp_data):
        # 无 qn 的不进 seen，全部保留
        classes = [{"x": 1}, {"x": 2}]
        assert fetch_mcp_data._dedup_classes(classes) == classes


# ── resolve_base_sha ──────────────────────────────────────────────────

class TestResolveBaseSha:
    def test_explicit_wins(self, fetch_mcp_data):
        c = FakeClient(responses={"index_status": {"git": {"head_sha": "AAA"}}})
        assert fetch_mcp_data.resolve_base_sha(c, "proj", explicit="MYSHA") == "MYSHA"
        assert c.calls == []  # 显式传入不查图谱

    def test_auto_from_head_sha(self, fetch_mcp_data):
        c = FakeClient(responses={"index_status": {"git": {"head_sha": "276e9d8"}}})
        assert fetch_mcp_data.resolve_base_sha(c, "proj") == "276e9d8"

    def test_fallback_to_base_sha(self, fetch_mcp_data):
        c = FakeClient(responses={"index_status": {"git": {"base_sha": "BBB"}}})
        assert fetch_mcp_data.resolve_base_sha(c, "proj") == "BBB"

    def test_exception_returns_unknown(self, fetch_mcp_data):
        c = FakeClient(responses={"index_status": RuntimeError("conn lost")})
        assert fetch_mcp_data.resolve_base_sha(c, "proj") == "unknown"

    def test_no_git_field(self, fetch_mcp_data):
        c = FakeClient(responses={"index_status": {"status": "ready"}})
        assert fetch_mcp_data.resolve_base_sha(c, "proj") == "unknown"

    def test_non_dict_response(self, fetch_mcp_data):
        c = FakeClient(responses={"index_status": None})
        assert fetch_mcp_data.resolve_base_sha(c, "proj") == "unknown"


# ── compute_p75_nonzero ───────────────────────────────────────────────

class TestComputeP75Nonzero:
    def test_empty_falls_back_to_5(self, fetch_mcp_data):
        assert fetch_mcp_data.compute_p75_nonzero([]) == 5

    def test_all_zero_falls_back_to_5(self, fetch_mcp_data):
        methods = [{"in_degree": 0}, {"in_degree": 0}]
        assert fetch_mcp_data.compute_p75_nonzero(methods) == 5

    def test_single_nonzero(self, fetch_mcp_data):
        assert fetch_mcp_data.compute_p75_nonzero([{"in_degree": 7}]) == 7

    def test_normal_distribution(self, fetch_mcp_data):
        # 4 个值: [1,2,3,10], P75 = ceil(0.75*4)-1 = index 2 → 3
        methods = [{"in_degree": v} for v in [10, 1, 3, 2]]
        assert fetch_mcp_data.compute_p75_nonzero(methods) == 3

    def test_missing_in_degree_key(self, fetch_mcp_data):
        # 缺 in_degree 字段视为 0，被过滤
        assert fetch_mcp_data.compute_p75_nonzero([{}, {"in_degree": 5}]) == 5

    def test_none_in_degree(self, fetch_mcp_data):
        assert fetch_mcp_data.compute_p75_nonzero([{"in_degree": None}, {"in_degree": 4}]) == 4


# ── extract_human_overlay ─────────────────────────────────────────────

class TestExtractHumanOverlay:
    def test_empty(self, fetch_mcp_data):
        assert fetch_mcp_data.extract_human_overlay({}) == {}

    def test_no_human_marks(self, fetch_mcp_data):
        inv = {"methods": [{"qualified_name": "A", "level": "high", "source": "auto"}]}
        assert fetch_mcp_data.extract_human_overlay(inv) == {}

    def test_manual_level(self, fetch_mcp_data):
        inv = {"methods": [{"qualified_name": "A", "level": "high", "source": "manual"}]}
        assert fetch_mcp_data.extract_human_overlay(inv) == {"A": {"source": "manual", "level": "high"}}

    def test_confirmed_review(self, fetch_mcp_data):
        inv = {"methods": [{"qualified_name": "A", "review_status": "confirmed"}]}
        assert fetch_mcp_data.extract_human_overlay(inv) == {"A": {"review_status": "confirmed"}}

    def test_usecase_count(self, fetch_mcp_data):
        inv = {"methods": [{"qualified_name": "A", "usecase_count": 3}]}
        assert fetch_mcp_data.extract_human_overlay(inv) == {"A": {"usecase_count": 3}}

    def test_zero_usecase_skipped(self, fetch_mcp_data):
        inv = {"methods": [{"qualified_name": "A", "usecase_count": 0}]}
        assert fetch_mcp_data.extract_human_overlay(inv) == {}

    def test_missing_qn_skipped(self, fetch_mcp_data):
        inv = {"methods": [{"level": "high", "source": "manual"}]}
        assert fetch_mcp_data.extract_human_overlay(inv) == {}

    def test_preserves_external_cover_fields(self, fetch_mcp_data):
        """Mode 1 fetch / test-mapping 采集的 test_* 字段必须进 overlay，reconcile 才保留。"""
        inv = {"methods": [{"qualified_name": "A",
                            "test_cover_count": 2,
                            "test_files": ["ut_a.cpp", "ut_b.cpp"],
                            "test_cases": ["Add_Normal", "Add_Edge"],
                            "test_source": "mcp_calls"}]}
        assert fetch_mcp_data.extract_human_overlay(inv) == {
            "A": {"test_cover_count": 2,
                  "test_files": ["ut_a.cpp", "ut_b.cpp"],
                  "test_cases": ["Add_Normal", "Add_Edge"],
                  "test_source": "mcp_calls"}}

    def test_zeroed_cover_fields_not_extracted(self, fetch_mcp_data):
        """stale-test-cleanup 归零后（test_cover_count=0/test_files=[]）不被提取，
        reconcile 不翻案清理结果。"""
        inv = {"methods": [{"qualified_name": "A", "usecase_count": 0,
                            "test_cover_count": 0, "test_files": [],
                            "test_cases": []}]}  # test_source 已 pop
        assert fetch_mcp_data.extract_human_overlay(inv) == {}


# ── apply_overlay_to_methods ──────────────────────────────────────────

class TestApplyOverlay:
    def test_empty(self, fetch_mcp_data):
        applied, lost = fetch_mcp_data.apply_overlay_to_methods([], {})
        assert applied == 0 and lost == []

    def test_applied(self, fetch_mcp_data):
        new = [{"qualified_name": "A", "level": "low"}]
        overlay = {"A": {"level": "high", "source": "manual"}}
        applied, lost = fetch_mcp_data.apply_overlay_to_methods(new, overlay)
        assert applied == 1
        assert new[0]["level"] == "high"
        assert lost == []

    def test_lost(self, fetch_mcp_data):
        new = [{"qualified_name": "A"}]
        overlay = {"A": {"level": "high"}, "B": {"level": "mid"}}
        applied, lost = fetch_mcp_data.apply_overlay_to_methods(new, overlay)
        assert applied == 1
        assert len(lost) == 1
        assert lost[0]["qualified_name"] == "B"

    def test_applies_cover_fields_to_fresh_build(self, fetch_mcp_data):
        """全量重建产出的 method 无 test_* 键，overlay 把外部回写值贴回去。"""
        new = [{"qualified_name": "A", "level": "high", "usecase_count": 0}]
        overlay = {"A": {"usecase_count": 2, "test_cover_count": 1,
                         "test_files": ["ut_a.cpp"],
                         "test_cases": ["Add_Normal", "Add_Edge"],
                         "test_source": "mcp_calls"}}
        applied, lost = fetch_mcp_data.apply_overlay_to_methods(new, overlay)
        m = new[0]
        assert applied == 1 and lost == []
        assert m["usecase_count"] == 2
        assert m["test_cover_count"] == 1
        assert m["test_files"] == ["ut_a.cpp"]
        assert m["test_cases"] == ["Add_Normal", "Add_Edge"]
        assert m["test_source"] == "mcp_calls"


# ── merge_review_queue ────────────────────────────────────────────────

class TestMergeReviewQueue:
    def test_empty_both(self, fetch_mcp_data):
        assert fetch_mcp_data.merge_review_queue([], [], set()) == []

    def test_none_both(self, fetch_mcp_data):
        assert fetch_mcp_data.merge_review_queue(None, None, set()) == []

    def test_confirmed_kept(self, fetch_mcp_data):
        old = [{"qualified_name": "A", "review_status": "confirmed"}]
        assert fetch_mcp_data.merge_review_queue([], old, {"A"}) == old

    def test_confirmed_dropped_when_method_gone(self, fetch_mcp_data):
        old = [{"qualified_name": "A", "review_status": "confirmed"}]
        assert fetch_mcp_data.merge_review_queue([], old, set()) == []

    def test_pending_appended(self, fetch_mcp_data):
        new = [{"qualified_name": "B", "review_status": "pending"}]
        assert fetch_mcp_data.merge_review_queue(new, [], set()) == new

    def test_pending_suppressed_when_confirmed(self, fetch_mcp_data):
        old = [{"qualified_name": "A", "review_status": "confirmed"}]
        new = [{"qualified_name": "A", "review_status": "pending"}]
        result = fetch_mcp_data.merge_review_queue(new, old, {"A"})
        assert len(result) == 1
        assert result[0]["review_status"] == "confirmed"


# ── compute_diff ──────────────────────────────────────────────────────

class TestComputeDiff:
    def _inv(self, methods):
        return {"methods": methods}

    def test_empty_both(self, fetch_mcp_data):
        diff = fetch_mcp_data.compute_diff(self._inv([]), self._inv([]), 0, [])
        assert all(diff[k] == [] for k in ("added", "removed", "sig_changed", "level_changed"))

    def test_added(self, fetch_mcp_data):
        diff = fetch_mcp_data.compute_diff(
            self._inv([]), self._inv([{"qualified_name": "A"}]), 0, [])
        assert len(diff["added"]) == 1

    def test_removed(self, fetch_mcp_data):
        diff = fetch_mcp_data.compute_diff(
            self._inv([{"qualified_name": "A"}]), self._inv([]), 0, [])
        assert len(diff["removed"]) == 1

    def test_sig_changed(self, fetch_mcp_data):
        old = self._inv([{"qualified_name": "A", "signature": "int()"}])
        new = self._inv([{"qualified_name": "A", "signature": "void()"}])
        diff = fetch_mcp_data.compute_diff(old, new, 0, [])
        assert len(diff["sig_changed"]) == 1

    def test_level_changed_auto(self, fetch_mcp_data):
        old = self._inv([{"qualified_name": "A", "level": "low", "source": "auto"}])
        new = self._inv([{"qualified_name": "A", "level": "high", "source": "auto"}])
        diff = fetch_mcp_data.compute_diff(old, new, 0, [])
        assert len(diff["level_changed"]) == 1

    def test_level_unchanged_for_manual(self, fetch_mcp_data):
        old = self._inv([{"qualified_name": "A", "level": "low", "source": "manual"}])
        new = self._inv([{"qualified_name": "A", "level": "high", "source": "manual"}])
        diff = fetch_mcp_data.compute_diff(old, new, 0, [])
        assert len(diff["level_changed"]) == 0


# ── _md_table / render_diff_report ────────────────────────────────────

class TestMdTable:
    def test_basic(self, fetch_mcp_data):
        out = fetch_mcp_data._md_table(["a", "b"], [[1, 2]])
        assert len(out) == 3
        assert out[0] == "| a | b |"

    def test_empty_rows(self, fetch_mcp_data):
        out = fetch_mcp_data._md_table(["a"], [])
        assert len(out) == 2  # header + separator

    def test_non_string_cells(self, fetch_mcp_data):
        out = fetch_mcp_data._md_table(["a"], [[None]])
        assert "None" in out[2]


class TestRenderDiffReport:
    def _empty_diff(self, fetch_mcp_data):
        return {"added": [], "removed": [], "sig_changed": [],
                "level_changed": [], "preserved": 0, "lost": []}

    def test_empty_diff(self, fetch_mcp_data):
        report = fetch_mcp_data.render_diff_report(
            self._empty_diff(fetch_mcp_data), "proj", "old", "new")
        assert "Inventory 增量更新报告" in report
        assert "`proj`" in report

    def test_with_added(self, fetch_mcp_data):
        d = self._empty_diff(fetch_mcp_data)
        d["added"] = [{"qualified_name": "A", "class_qn": "Cls", "level": "high", "factors": []}]
        report = fetch_mcp_data.render_diff_report(d, "proj", "old", "new")
        assert "新增方法" in report
        assert "A" in report


# ── build_mcp_dump ────────────────────────────────────────────────────

class TestBuildMcpDump:
    def test_structure(self, fetch_mcp_data):
        dump = fetch_mcp_data.build_mcp_dump(
            "proj", [], [], [], [], [], [], {}, {}, {}, 5)
        assert dump["project"] == "proj"
        assert dump["methods"] == []
        assert dump["in_degree_p75_nonzero"] == 5
        assert "dbus_signals" in dump and dump["dbus_signals"] == {}


# ── collect_methods（用 FakeClient mock） ──────────────────────────────

class TestCollectMethods:
    def _mk_data(self, results, total=None, has_more=False):
        return {"results": results, "total": total if total is not None else len(results),
                "has_more": has_more}

    def test_empty_single_page(self, fetch_mcp_data):
        c = FakeClient(default=self._mk_data([], 0, False))
        methods, functions = fetch_mcp_data.collect_methods(c, "proj")
        assert methods == [] and functions == []

    def test_single_pattern(self, fetch_mcp_data):
        c = FakeClient(default=self._mk_data(
            [{"qualified_name": "A", "in_degree": 1}], 1, False))
        methods, functions = fetch_mcp_data.collect_methods(c, "proj", ["src/**"])
        assert len(methods) == 1

    def test_multi_pattern_dedup(self, fetch_mcp_data):
        # 两个 pattern 返回相同节点 → 去重
        c = FakeClient(default=self._mk_data(
            [{"qualified_name": "dup", "in_degree": 1}], 1, False))
        methods, functions = fetch_mcp_data.collect_methods(
            c, "proj", ["src/**", "src/plugins/**"])
        assert len(methods) == 1  # 去重后只剩 1 个

    def test_none_pattern_full_query(self, fetch_mcp_data):
        c = FakeClient(default=self._mk_data([], 0, False))
        fetch_mcp_data.collect_methods(c, "proj", None)
        # None → 单轮全量，不传 file_pattern
        for name, args in c.calls:
            assert "file_pattern" not in args

    def test_no_qn_nodes_kept(self, fetch_mcp_data):
        # 无 qualified_name 的节点不被去重，全部保留
        c = FakeClient(default=self._mk_data(
            [{"in_degree": 1}, {"in_degree": 2}], 2, False))
        methods, functions = fetch_mcp_data.collect_methods(c, "proj")
        assert len(methods) == 2

    def test_pagination(self, fetch_mcp_data):
        # 模拟分页：第一页 has_more=True，第二页 has_more=False
        responses = [
            self._mk_data([{"qualified_name": "A", "in_degree": 1}], 2, True),
            self._mk_data([{"qualified_name": "B", "in_degree": 1}], 2, False),
        ]
        call_count = [0]

        class PagingClient:
            def __init__(self):
                self.calls = []

            def call_tool(self, name, arguments, retries=3):
                self.calls.append((name, arguments))
                idx = call_count[0]
                call_count[0] += 1
                return responses[min(idx, len(responses) - 1)]

        c = PagingClient()
        methods, functions = fetch_mcp_data.collect_methods(c, "proj", limit=1)
        assert len(methods) == 2
