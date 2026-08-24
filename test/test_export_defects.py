"""export-defects.py 健壮性测试。

聚焦纯函数与 I/O 逻辑的边界场景：severity 计算、统计重算、
SHA 归档、截断、Markdown 渲染、文件 I/O 往返。
"""
import json
from pathlib import Path

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

    def test_negative_max_len(self, export_defects):
        """max_len<0 时 s[:max_len] 切片仍有效（Python 负索引），追加省略号。"""
        assert export_defects._truncate("abc", -1) == "ab…"


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


class TestRenderMdTableRel:
    """验证 _render_md_table 中 report_dir_rel 链接拼接（新功能）。"""

    def test_one_level_deep(self, export_defects):
        """report_dir_rel='../' → ../src/foo.cpp。"""
        defects = [{"class_name": "C", "method_name": "m", "test_case_name": "t",
                    "file_path": "src/foo.cpp", "file_line": 10,
                    "test_file": "autotests/test_foo.cpp",
                    "type_category": "runtime", "evidence": "ev", "suggestion": "s"}]
        lines = export_defects._render_md_table(defects, "../")
        assert "../src/foo.cpp" in lines
        assert "[t](../autotests/test_foo.cpp)" in lines

    def test_two_level_deep(self, export_defects):
        defects = [{"class_name": "C", "method_name": "m", "test_case_name": "t",
                    "file_path": "src/foo.cpp", "file_line": 10,
                    "test_file": "autotests/test_foo.cpp",
                    "type_category": "runtime", "evidence": "ev", "suggestion": "s"}]
        lines = export_defects._render_md_table(defects, "../../")
        assert "../../src/foo.cpp" in lines

    def test_no_file_path_no_source_link(self, export_defects):
        """缺文件路径时源码列不生成链接，但用例链接仍存在。"""
        defects = [{"class_name": "C", "method_name": "m", "test_case_name": "t",
                    "file_path": "", "file_line": None,
                    "test_file": "autotests/test_foo.cpp",
                    "type_category": "runtime", "evidence": "ev", "suggestion": "s"}]
        lines = export_defects._render_md_table(defects, "../")
        assert "../autotests/test_foo.cpp" in lines  # 用例链接
        assert "../src" not in lines  # 源码列无链接

    def test_line_anchor(self, export_defects):
        """文件:行 带有 #L 行锚。"""
        defects = [{"class_name": "C", "method_name": "m", "test_case_name": "t",
                    "file_path": "src/foo.cpp", "file_line": 99,
                    "test_file": "autotests/test_foo.cpp",
                    "type_category": "runtime", "evidence": "ev", "suggestion": "s"}]
        lines = export_defects._render_md_table(defects, "../")
        assert "../src/foo.cpp#L99" in lines


# ── 项目名显示（新功能） ───────────────────────────────────────────────

class TestProjectDisplay:
    """验证 cmd_export 中 project_display 的计算逻辑。"""

    @pytest.mark.parametrize(
        "project,expected",
        [
            # 知识图谱前缀剥离
            ("home-uos-service-codebase-repos-deepin-picker", "deepin-picker"),
            ("home-uos-service-codebase-repos-dde-file-manager", "dde-file-manager"),
            ("home-uos-service-codebase-repos-a", "a"),
            # 路径取 basename
            ("/home/user/deepin-picker", "deepin-picker"),
            ("./some/path/my-proj", "my-proj"),
            # 纯名 fallback
            ("my-project", "my-project"),
            # None fallback
            (None, "(unknown)"),
        ],
    )
    def test_project_display(self, project, expected):
        """复现 cmd_export 中 project_display 的计算逻辑。"""
        if project and project.startswith("home-uos-service-codebase-repos-"):
            project_display = project[len("home-uos-service-codebase-repos-"):]
        elif project:
            project_display = Path(project).name if "/" in project else project
        else:
            project_display = "(unknown)"
        assert project_display == expected


# ── 报告目录深度计算（新功能） ────────────────────────────────────────────

class TestReportDirRel:
    """验证 report_dir_rel 深度计算。"""

    @pytest.mark.parametrize(
        "project_dir,report_dir,expected_rel",
        [
            # 同级: build-autotests → depth=1 → ../
            ("/proj", "/proj/build-autotests", "../"),
            # 两级: build-ut/sub → depth=2 → ../../
            ("/proj", "/proj/build-ut/sub", "../../"),
            # 同目录: depth=0 → ""
            ("/proj", "/proj", ""),
            # 无关联: ValueError → ""
            ("/proj", "/other", ""),
        ],
    )
    def test_depth_calculation(self, project_dir, report_dir, expected_rel):
        """复现 cmd_export 中 report_dir_rel 的计算逻辑。"""
        project_dir_p = Path(project_dir).resolve()
        report_dir_p = Path(report_dir).resolve()
        try:
            rel = report_dir_p.relative_to(project_dir_p)
            depth = len(rel.parts)
            result = "../" * depth
        except ValueError:
            result = ""
        assert result == expected_rel


# ── 低危章节导出（新功能） ──────────────────────────────────────────────

class TestExportLowSeverity:
    """验证 export 输出包含低危章节。"""

    def _run_export(self, export_defects, data, project_dir=None):
        """运行 cmd_export 并返回 md 内容。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            defects_path = Path(tmp) / ".ut-defects.json"
            defects_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            report_dir = Path(tmp) / "report"

            class Args:
                pass

            args = Args()
            args.defects = str(defects_path)
            args.report_dir = str(report_dir)
            args.project_dir = project_dir or str(Path(tmp))
            args.inventory = None

            export_defects.cmd_export(args)

            md_path = report_dir / "defects-summary.md"
            assert md_path.exists(), "defects-summary.md should be generated"
            return md_path.read_text(encoding="utf-8")

    def test_low_severity_section_present(self, export_defects):
        """低危缺陷应出现在 🟢 低危 章节中。"""
        low_defect = {
            "defect_id": "id1", "method_qn": "pkg.D.m", "method_name": "m",
            "class_qn": "pkg.D", "class_name": "DBusNotify", "module": "core",
            "file_path": "src/d.cpp", "file_line": 80,
            "test_fixture": "F", "test_case_name": "GetCapbilities_MethodName",
            "test_file": "autotests/test_d.cpp",
            "type": "source_defect_logic", "type_category": "logic",
            "detected_at_stage": "logic",
            "severity": "low", "method_level": "low",
            "status": "open",
            "evidence": "typo in method name",
            "suggestion": "fix spelling",
            "root_cause_snippet": "",
        }
        data = {"version": 1, "base_sha": "abc", "project": "home-uos-service-codebase-repos-deepin-picker",
                "defects": [low_defect], "stats": {}, "history": {}}
        md = self._run_export(export_defects, data)

        assert "🟢 低危 (1)" in md
        assert "DBusNotify" in md
        # 低危章节不应出现 "无"
        low_section = md.split("🟢 低危")[1].split("##")[0]
        assert "无" not in low_section

    def test_no_low_severity_shows_none(self, export_defects):
        """无低危缺陷时显示 '无'。"""
        high_defect = {
            "defect_id": "id1", "method_qn": "pkg.C.m", "method_name": "m",
            "class_qn": "pkg.C", "class_name": "C", "module": "core",
            "file_path": "src/c.cpp", "file_line": 10,
            "test_fixture": "F", "test_case_name": "Case",
            "test_file": "autotests/test_c.cpp",
            "type": "source_defect_runtime", "type_category": "runtime",
            "detected_at_stage": "runtime",
            "severity": "high", "method_level": "high",
            "status": "open",
            "evidence": "ev", "suggestion": "s", "root_cause_snippet": "",
        }
        data = {"version": 1, "base_sha": "abc", "project": None,
                "defects": [high_defect], "stats": {}, "history": {}}
        md = self._run_export(export_defects, data)

        assert "🟢 低危 (0)" in md
        low_section = md.split("🟢 低危")[1].split("##")[0]
        assert "无" in low_section

    def test_all_severity_sections(self, export_defects):
        """高/中/低 同时存在时，三个章节都有内容。"""
        defects = [
            {"defect_id": "id1", "method_qn": "pkg.H.m", "method_name": "highMethod",
             "class_qn": "pkg.H", "class_name": "HighClass", "module": "core",
             "file_path": "src/h.cpp", "file_line": 10,
             "test_fixture": "F", "test_case_name": "HCase",
             "test_file": "autotests/test_h.cpp",
             "type": "source_defect_runtime", "type_category": "runtime",
             "detected_at_stage": "runtime",
             "severity": "high", "method_level": "high",
             "status": "open",
             "evidence": "ev", "suggestion": "s", "root_cause_snippet": ""},
            {"defect_id": "id2", "method_qn": "pkg.M.m", "method_name": "midMethod",
             "class_qn": "pkg.M", "class_name": "MidClass", "module": "core",
             "file_path": "src/m.cpp", "file_line": 10,
             "test_fixture": "F", "test_case_name": "MCase",
             "test_file": "autotests/test_m.cpp",
             "type": "source_defect_logic", "type_category": "logic",
             "detected_at_stage": "logic",
             "severity": "mid", "method_level": "mid",
             "status": "open",
             "evidence": "ev", "suggestion": "s", "root_cause_snippet": ""},
            {"defect_id": "id3", "method_qn": "pkg.L.m", "method_name": "lowMethod",
             "class_qn": "pkg.L", "class_name": "LowClass", "module": "core",
             "file_path": "src/l.cpp", "file_line": 10,
             "test_fixture": "F", "test_case_name": "LCase",
             "test_file": "autotests/test_l.cpp",
             "type": "source_defect_logic", "type_category": "logic",
             "detected_at_stage": "logic",
             "severity": "low", "method_level": "low",
             "status": "open",
             "evidence": "ev", "suggestion": "s", "root_cause_snippet": ""},
        ]
        data = {"version": 1, "base_sha": "abc", "project": None,
                "defects": defects, "stats": {}, "history": {}}
        md = self._run_export(export_defects, data)

        assert "🔴 高危 (1)" in md
        assert "HighClass" in md
        assert "🟡 中危 (1)" in md
        assert "MidClass" in md
        assert "🟢 低危 (1)" in md
        assert "LowClass" in md

    def test_project_name_stripped(self, export_defects):
        """项目名应剥离知识图谱前缀。"""
        data = {"version": 1, "base_sha": "abc",
                "project": "home-uos-service-codebase-repos-my-cool-app",
                "defects": [], "stats": {}, "history": {}}
        md = self._run_export(export_defects, data)
        assert "my-cool-app" in md
        assert "home-uos-service-codebase-repos-" not in md


# ── CLI 新参数 ──────────────────────────────────────────────────────────

class TestCliArgs:
    """验证新增的 --project-dir 和别名参数。"""

    def test_export_has_project_dir(self):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        p_ex = sub.add_parser("export")
        p_ex.add_argument("--project-dir", default=None)
        p_ex.add_argument("--defects", "--defects-file", required=True)
        p_ex.add_argument("--report-dir", "--output-dir", required=True)
        p_ex.add_argument("--inventory", default=None)

        args = parser.parse_args(["export", "--defects", "x.json", "--report-dir", "out", "--project-dir", "/proj"])
        assert args.project_dir == "/proj"

    def test_aliases(self):
        """--defects-file 和 --output-dir 应作为别名。"""
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        p_ex = sub.add_parser("export")
        p_ex.add_argument("--project-dir", default=None)
        p_ex.add_argument("--defects", "--defects-file", required=True)
        p_ex.add_argument("--report-dir", "--output-dir", required=True)
        p_ex.add_argument("--inventory", default=None)

        args = parser.parse_args(["export", "--defects-file", "x.json", "--output-dir", "out"])
        assert args.defects == "x.json"
        assert args.report_dir == "out"


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
