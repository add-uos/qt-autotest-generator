"""export-defects.py 健壮性测试。

聚焦纯函数与 I/O 逻辑的边界场景：severity 计算、统计重算、
SHA 归档、截断、Markdown 渲染、文件 I/O 往返。
"""
import json

import pytest


# ── _calc_severity ────────────────────────────────────────────────────

class TestCalcSeverity:
    def test_known_runtime_high(self, export_defects):
        assert export_defects._calc_severity("source_defect_runtime", "mid") == "high"

    def test_known_compile_high(self, export_defects):
        assert export_defects._calc_severity("source_defect_compile", "low") == "high"

    def test_known_logic_mid(self, export_defects):
        assert export_defects._calc_severity("source_defect_logic", "low") == "mid"

    def test_needs_manual_mid(self, export_defects):
        assert export_defects._calc_severity("needs_manual", "low") == "mid"

    def test_unknown_type_low(self, export_defects):
        assert export_defects._calc_severity("unknown_type", "low") == "low"

    def test_mid_elevated_by_high_level(self, export_defects):
        # method_level == high 且 severity == mid → 升级为 high
        assert export_defects._calc_severity("source_defect_logic", "high") == "high"

    def test_high_not_elevated_further(self, export_defects):
        assert export_defects._calc_severity("source_defect_runtime", "high") == "high"

    def test_low_not_elevated_by_high(self, export_defects):
        # severity 已经 low，method_level=high 不影响 low
        assert export_defects._calc_severity("unknown_type", "high") == "low"


# ── _recalc_stats ─────────────────────────────────────────────────────

class TestRecalcStats:
    def test_empty_defects(self, export_defects):
        data = {"defects": []}
        stats = export_defects._recalc_stats(data)
        assert stats["total"] == 0
        assert stats["by_status"]["open"] == 0

    def test_missing_defects_key(self, export_defects):
        data = {}
        stats = export_defects._recalc_stats(data)
        assert stats["total"] == 0

    def test_mixed_statuses(self, export_defects):
        data = {"defects": [
            {"status": "open", "severity": "high", "type_category": "compile",
             "method_qn": "A", "class_qn": "C"},
            {"status": "fixed", "severity": "mid", "type_category": "logic",
             "method_qn": "B", "class_qn": "D"},
            {"status": "open", "severity": "low", "type_category": "runtime",
             "method_qn": "A", "class_qn": "C"},  # 同 method_qn
        ]}
        stats = export_defects._recalc_stats(data)
        assert stats["total"] == 3
        assert stats["by_status"]["open"] == 2
        assert stats["by_severity"]["high"] == 1
        assert stats["affected_methods"] == 1  # 只有 A 是 open
        assert stats["affected_classes"] == 1  # 只有 C 是 open/reopened

    def test_reopened_counted(self, export_defects):
        data = {"defects": [
            {"status": "reopened", "severity": "mid", "type_category": "manual",
             "method_qn": "X", "class_qn": "Y"},
        ]}
        stats = export_defects._recalc_stats(data)
        assert stats["by_status"]["reopened"] == 1
        assert stats["affected_methods"] == 1

    def test_default_status_is_open(self, export_defects):
        # 缺 status 键默认 open
        data = {"defects": [{"severity": "low", "type_category": "manual"}]}
        stats = export_defects._recalc_stats(data)
        assert stats["by_status"]["open"] == 1

    def test_unknown_status_key(self, export_defects):
        # 未知 status 值不会崩溃，但 .get(st, 0) 会处理
        data = {"defects": [{"status": "unknown_st", "severity": "low", "type_category": "manual"}]}
        stats = export_defects._recalc_stats(data)
        # 未知 status 会被 .get(st, 0) 计入
        assert stats["total"] == 1


# ── _archive_on_sha_change ────────────────────────────────────────────

class TestArchiveOnShaChange:
    def test_sha_unchanged(self, export_defects):
        data = {"base_sha": "abc123", "defects": [{"x": 1}]}
        result = export_defects._archive_on_sha_change(data, "abc123")
        assert result is False
        assert data["defects"] == [{"x": 1}]

    def test_sha_changed(self, export_defects):
        data = {"base_sha": "old", "defects": [{"x": 1}], "stats": {"total": 1}}
        result = export_defects._archive_on_sha_change(data, "new")
        assert result is True
        assert data["defects"] == []
        assert data["base_sha"] == "new"
        assert "old" in data.get("history", {})

    def test_new_sha_none_no_archive(self, export_defects):
        data = {"base_sha": "old", "defects": [{"x": 1}]}
        result = export_defects._archive_on_sha_change(data, None)
        assert result is False

    def test_new_sha_empty_no_archive(self, export_defects):
        data = {"base_sha": "old", "defects": [{"x": 1}]}
        result = export_defects._archive_on_sha_change(data, "")
        assert result is False

    def test_no_old_sha_still_archives(self, export_defects):
        # base_sha 为 None 时，新 sha 来了直接设置，不归档旧数据
        data = {"base_sha": None, "defects": [{"x": 1}]}
        result = export_defects._archive_on_sha_change(data, "new_sha")
        assert result is True
        assert data["base_sha"] == "new_sha"
        assert data["defects"] == []
        # 无 old_sha → history 不会写入 None 键
        assert None not in data.get("history", {})

    def test_multiple_archives_accumulate(self, export_defects):
        data = {"base_sha": "v1", "defects": [{"a": 1}], "stats": {}}
        export_defects._archive_on_sha_change(data, "v2")
        data["defects"] = [{"b": 2}]
        export_defects._archive_on_sha_change(data, "v3")
        assert "v1" in data["history"]
        assert "v2" in data["history"]


# ── _truncate ─────────────────────────────────────────────────────────

class TestTruncate:
    def test_normal_truncate(self, export_defects):
        assert export_defects._truncate("hello world", 5) == "hello…"

    def test_short_text_unchanged(self, export_defects):
        assert export_defects._truncate("hi", 10) == "hi"

    def test_exact_length(self, export_defects):
        assert export_defects._truncate("abc", 3) == "abc"

    def test_empty_string(self, export_defects):
        assert export_defects._truncate("", 10) == ""

    def test_none_returns_empty(self, export_defects):
        assert export_defects._truncate(None, 10) == ""

    def test_zero_max_len(self, export_defects):
        assert export_defects._truncate("text", 0) == "…"

    def test_non_string_input(self, export_defects):
        assert export_defects._truncate(123, 5) == "123"


# ── _render_md_table / _render_fixed_table ────────────────────────────

class TestRenderMdTable:
    def test_empty_list(self, export_defects):
        assert export_defects._render_md_table([]) == ""

    def test_single_defect(self, export_defects):
        defects = [{"class_name": "C", "method_name": "m", "test_case_name": "t",
                    "file_path": "f.cpp", "file_line": 10, "type_category": "runtime",
                    "evidence": "ev", "suggestion": "s"}]
        out = export_defects._render_md_table(defects)
        assert "| C |" in out
        assert "runtime" in out

    def test_missing_keys(self, export_defects):
        defects = [{}]
        out = export_defects._render_md_table(defects)
        assert isinstance(out, str)

    def test_long_evidence_truncated(self, export_defects):
        defects = [{"evidence": "x" * 100}]
        out = export_defects._render_md_table(defects)
        assert "…" in out


class TestRenderFixedTable:
    def test_empty_list(self, export_defects):
        assert export_defects._render_fixed_table([]) == ""

    def test_single_fixed(self, export_defects):
        defects = [{"class_name": "C", "method_name": "m",
                    "test_case_name": "t", "fixed_in_sha": "abc123def456",
                    "fixed_at": "2024-01-01T12:00:00Z"}]
        out = export_defects._render_fixed_table(defects)
        assert "| C |" in out
        assert "abc123de" in out  # sha[:8]

    def test_none_fixed_at(self, export_defects):
        defects = [{"class_name": "C", "fixed_in_sha": "abc",
                    "fixed_at": None}]
        out = export_defects._render_fixed_table(defects)
        assert isinstance(out, str)


# ── load_defects / save_defects I/O ───────────────────────────────────

class TestDefectsIO:
    def test_load_nonexistent_returns_skeleton(self, export_defects, tmp_path):
        path = tmp_path / "nope.json"
        data = export_defects.load_defects(str(path))
        assert data["version"] == 1
        assert data["defects"] == []
        assert data["base_sha"] is None

    def test_save_and_load_roundtrip(self, export_defects, tmp_path):
        path = tmp_path / "defects.json"
        original = {"version": 1, "base_sha": "sha1", "project": "p",
                    "defects": [{"id": 1}], "stats": {"total": 1}, "history": {}}
        export_defects.save_defects(str(path), original)
        loaded = export_defects.load_defects(str(path))
        assert loaded["defects"] == [{"id": 1}]
        assert loaded["base_sha"] == "sha1"

    def test_load_legacy_missing_keys(self, export_defects, tmp_path):
        """旧版文件缺 version/defects/stats/history 时自动补全。"""
        path = tmp_path / "legacy.json"
        path.write_text('{"base_sha": "x"}')
        data = export_defects.load_defects(str(path))
        assert data["version"] == 1
        assert data["defects"] == []
        assert "stats" in data
        assert "history" in data

    def test_save_creates_parent_dirs(self, export_defects, tmp_path):
        path = tmp_path / "sub" / "dir" / "defects.json"
        export_defects.save_defects(str(path), {"version": 1, "defects": []})
        assert path.exists()

    def test_save_atomic_no_partial(self, export_defects, tmp_path):
        """写入失败时不应留 .tmp 残留（用正常路径验证原子写入流程）。"""
        path = tmp_path / "defects.json"
        export_defects.save_defects(str(path), {"version": 1, "defects": []})
        assert not (tmp_path / "defects.tmp").exists()
        assert path.exists()
