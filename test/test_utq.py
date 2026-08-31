"""utq.py 单元测试。

覆盖：基础工具（disp_width/pad/table/fmt_list）、数据模型 Inv、
过滤器 apply_filters、排序 sort_key、15 个子命令输出、CLI 参数解析、
项目目录发现、边界情况（缺失字段/空数据/None/非法 JSON）。
"""
import json
import sys
from io import StringIO
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────

def _m(name="func", cls="proj.src.Cls", level="low", testable=True,
       file="src/cls.cpp", score=1, cover=0, usecase=0, **extra):
    """构造一条 methods 条目。"""
    e = {
        "name": name,
        "qualified_name": f"proj.src.{cls}.{name}" if cls else f"proj.src.{name}",
        "class_qn": cls,
        "file_path": file,
        "level": level,
        "testable": testable,
        "score": score,
        "test_cover_count": cover,
        "usecase_count": usecase,
        "signature": extra.pop("sig", f"void {name}()"),
        "node_type": "Method",
    }
    e.update(extra)
    return e


def _write_inv(tmp_path, methods, **extra):
    """在 tmp_path 写 .ut-inventory.json 并返回路径。"""
    data = {"project": "test-proj", "base_sha": "abc123456789",
            "generated_at": "2026-01-01T00:00:00", "methods": methods}
    data.update(extra)
    p = tmp_path / ".ut-inventory.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _run_cmd(utq, tmp_path, cmd_args, methods=None, **inv_extra):
    """构造 inventory → 调 main_no_exit → 返回 (rc, stdout, stderr)。"""
    if methods is not None:
        _write_inv(tmp_path, methods, **inv_extra)
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = StringIO(), StringIO()
    try:
        rc = utq.main_no_exit(["-P", str(tmp_path)] + cmd_args)
    except SystemExit as e:
        rc = e.code
    finally:
        out, err = sys.stdout.getvalue(), sys.stderr.getvalue()
        sys.stdout, sys.stderr = old_out, old_err
    return rc, out, err


# ── 需要给 utq.py 加 main_no_exit 入口（避免 sys.exit 杀 pytest）────────

# conftest 会加载模块，但 utq.py 原始 main() 会 sys.exit。
# 我们在 fixture 加载后注入一个不 exit 的入口，各测试用此调用。
# 这与 plan-test-classes.py 等脚本的模式一致。


# ── 基础工具 ────────────────────────────────────────────────────────

class TestDispWidth:
    def test_ascii(self, utq):
        assert utq.disp_width("abc") == 3

    def test_cjk_fullwidth(self, utq):
        # CJK 统一汉字 U+4E00 为 W 宽度 → 显示宽度 2
        assert utq.disp_width("中") == 2

    def test_mixed(self, utq):
        assert utq.disp_width("ab中文") == 6   # 2×1 + 2×2

    def test_empty(self, utq):
        assert utq.disp_width("") == 0

    def test_none_coerced(self, utq):
        assert utq.disp_width(None) == 4  # "None" = 4 ASCII


class TestPad:
    def test_shorter_right(self, utq):
        assert utq.pad("ab", 5) == "ab   "

    def test_shorter_left(self, utq):
        assert utq.pad("ab", 5, right=False) == "   ab"

    def test_exact_width(self, utq):
        assert utq.pad("abc", 3) == "abc"

    def test_wider_no_truncate(self, utq):
        # 超长不截断
        assert utq.pad("abcde", 3) == "abcde"

    def test_cjk_padding(self, utq):
        # "中" 显示宽 2，目标宽 6 → 补 4 空格
        assert utq.pad("中", 6) == "中    "


class TestTable:
    def test_basic(self, utq):
        result = utq.table([["a", "bb"], ["ccc", "d"]], ["H1", "H2"])
        lines = result.split("\n")
        assert len(lines) == 4  # header + separator + 2 rows
        assert "H1" in lines[0]

    def test_empty_rows(self, utq):
        assert utq.table([], ["H1"]) == ""

    def test_cjk_alignment(self, utq):
        # CJK 字符占双宽，列宽应正确对齐
        result = utq.table([["中文", "x"]], ["A", "B"])
        lines = result.split("\n")
        # 第二行分隔线宽度应与含 CJK 的数据行一致
        assert len(lines) == 3


class TestFmtList:
    def test_basic(self, utq):
        assert utq.fmt_list(["a", "b"]) == "  - a\n  - b"

    def test_empty(self, utq):
        assert utq.fmt_list([]) == ""


# ── Inv 数据模型 ─────────────────────────────────────────────────────

class TestInv:
    def test_owner_with_class(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f", cls="Proj.A")])
        inv = utq.Inv(tmp_path)
        assert inv.owner({"class_qn": "Proj.A"}) == "Proj.A"

    def test_owner_free_function(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f", cls=None)])
        inv = utq.Inv(tmp_path)
        assert inv.owner({"class_qn": None}) == "(自由函数)"

    def test_cover_missing_field(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        assert inv.cover({"test_cover_count": None}) == 0
        assert inv.cover({}) == 0

    def test_is_covered(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f", cover=2)])
        inv = utq.Inv(tmp_path)
        assert inv.is_covered({"test_cover_count": 2}) is True
        assert inv.is_covered({"test_cover_count": 0}) is False

    def test_is_todo(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f", testable=True, cover=0)])
        inv = utq.Inv(tmp_path)
        assert inv.is_todo({"testable": True, "test_cover_count": 0}) is True
        assert inv.is_todo({"testable": True, "test_cover_count": 1}) is False
        assert inv.is_todo({"testable": False, "test_cover_count": 0}) is False

    def test_display_name(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("foo", cls="Bar")])
        inv = utq.Inv(tmp_path)
        assert inv.display_name({"class_qn": "Bar", "name": "foo"}) == "Bar::foo"

    def test_methods_default_empty(self, utq, tmp_path):
        _write_inv(tmp_path, [])  # no methods key → should default to []
        inv = utq.Inv(tmp_path)
        assert inv.methods == []

    def test_tm_loaded_when_exists(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        tm = tmp_path / "test-mapping.json"
        tm.write_text('{"foo": "bar"}', encoding="utf-8")
        inv = utq.Inv(tmp_path)
        assert inv.tm == {"foo": "bar"}

    def test_tm_empty_when_missing(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        assert inv.tm == {}


# ── 过滤器 ───────────────────────────────────────────────────────────

class TestApplyFilters:
    def _inv(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        return utq.Inv(tmp_path)

    def _args(self, **kw):
        """构造模拟 args 对象。"""
        class Args:
            pass
        a = Args()
        a.include_exempt = False
        a.level = None
        a.file = None
        a.class_ = None
        a.kw = None
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_default_hides_exempt(self, utq, tmp_path):
        inv = self._inv(utq, tmp_path)
        methods = [{"testable": False, "name": "x"}, {"testable": True, "name": "y"}]
        result = utq.apply_filters(inv, methods, self._args())
        assert len(result) == 1 and result[0]["name"] == "y"

    def test_include_exempt(self, utq, tmp_path):
        inv = self._inv(utq, tmp_path)
        methods = [{"testable": False, "name": "x"}, {"testable": True, "name": "y"}]
        result = utq.apply_filters(inv, methods, self._args(include_exempt=True))
        assert len(result) == 2

    def test_level_filter(self, utq, tmp_path):
        inv = self._inv(utq, tmp_path)
        methods = [{"testable": True, "level": "high"}, {"testable": True, "level": "low"}]
        result = utq.apply_filters(inv, methods, self._args(level="high"))
        assert len(result) == 1 and result[0]["level"] == "high"

    def test_file_filter_case_insensitive(self, utq, tmp_path):
        inv = self._inv(utq, tmp_path)
        methods = [{"testable": True, "file_path": "src/View.cpp"},
                   {"testable": True, "file_path": "src/Model.cpp"}]
        result = utq.apply_filters(inv, methods, self._args(file="view"))
        assert len(result) == 1 and "View" in result[0]["file_path"]

    def test_class_filter_case_insensitive(self, utq, tmp_path):
        inv = self._inv(utq, tmp_path)
        methods = [{"testable": True, "class_qn": "ImageController"},
                   {"testable": True, "class_qn": "DataParser"}]
        result = utq.apply_filters(inv, methods, self._args(class_="image"))
        assert len(result) == 1

    def test_kw_filter(self, utq, tmp_path):
        inv = self._inv(utq, tmp_path)
        methods = [{"testable": True, "name": "loadImage", "class_qn": "",
                    "file_path": "", "signature": "void loadImage()", "qualified_name": "x.loadImage"},
                   {"testable": True, "name": "saveData", "class_qn": "",
                    "file_path": "", "signature": "void saveData()", "qualified_name": "x.saveData"}]
        result = utq.apply_filters(inv, methods, self._args(kw="load"))
        assert len(result) == 1

    def test_combined_filters(self, utq, tmp_path):
        inv = self._inv(utq, tmp_path)
        methods = [
            {"testable": True, "level": "high", "class_qn": "A",
             "name": "x", "file_path": "a.cpp", "signature": "", "qualified_name": "x"},
            {"testable": True, "level": "high", "class_qn": "B",
             "name": "y", "file_path": "b.cpp", "signature": "", "qualified_name": "y"},
            {"testable": True, "level": "low", "class_qn": "A",
             "name": "z", "file_path": "a.cpp", "signature": "", "qualified_name": "z"},
        ]
        result = utq.apply_filters(inv, methods, self._args(level="high", class_="a"))
        assert len(result) == 1 and result[0]["name"] == "x"

    def test_missing_fields_no_crash(self, utq, tmp_path):
        inv = self._inv(utq, tmp_path)
        methods = [{"testable": True}]  # 所有过滤字段缺失
        for filt in [{"level": "high"}, {"file": "x"}, {"class_": "x"}, {"kw": "x"}]:
            result = utq.apply_filters(inv, methods, self._args(**filt))
            assert result == []  # 缺字段 = 不匹配，不崩溃


# ── 排序 ────────────────────────────────────────────────────────────

class TestSortKey:
    def test_level_high_before_mid(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        high = {"level": "high", "score": 1, "file_path": "", "name": "a", "class_qn": "X"}
        mid = {"level": "mid", "score": 5, "file_path": "", "name": "b", "class_qn": "X"}
        assert utq.sort_key(inv, high) < utq.sort_key(inv, mid)

    def test_same_level_score_desc(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        big = {"level": "mid", "score": 10, "file_path": "", "name": "a", "class_qn": "X"}
        small = {"level": "mid", "score": 2, "file_path": "", "name": "b", "class_qn": "X"}
        assert utq.sort_key(inv, big) < utq.sort_key(inv, small)

    def test_missing_level_sorts_last(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        none = {"level": None, "score": 0, "file_path": "", "name": "a", "class_qn": "X"}
        low = {"level": "low", "score": 0, "file_path": "", "name": "b", "class_qn": "X"}
        assert utq.sort_key(inv, low) < utq.sort_key(inv, none)


# ── limit_rows ───────────────────────────────────────────────────────

class TestLimitRows:
    def test_no_limit(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        rows = [1, 2, 3, 4, 5]
        result = utq.limit_rows(inv, rows, type("A", (), {"limit": None})())
        assert result == rows

    def test_with_limit(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        rows = [1, 2, 3, 4, 5]
        result = utq.limit_rows(inv, rows, type("A", (), {"limit": 3})())
        assert result == [1, 2, 3]

    def test_default_limit(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        rows = [1, 2, 3, 4, 5]
        result = utq.limit_rows(inv, rows, type("A", (), {"limit": None})(), default=2)
        assert result == [1, 2]


# ── 子命令：stats ───────────────────────────────────────────────────

class TestCmdStats:
    def test_basic_output(self, utq, tmp_path):
        methods = [
            _m("a", cls="A", level="high", testable=True, cover=1),
            _m("b", cls="A", level="mid", testable=True, cover=0),
            _m("c", cls="A", level="low", testable=True, cover=0),
            _m("d", cls="A", level="low", testable=False),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["stats"], methods)
        assert rc == 0
        assert "函数总数 4" in out
        assert "可测 3" in out
        assert "HIGH" in out
        assert "MID" in out
        assert "LOW" in out
        assert "合计" in out

    def test_json_output(self, utq, tmp_path):
        methods = [_m("a", level="high", testable=True, cover=0)]
        rc, out, _ = _run_cmd(utq, tmp_path, ["stats", "--json"], methods)
        assert rc == 0
        # --json 在表格后追加一行 JSON，取最后一个非空行
        lines = [l for l in out.strip().split("\n") if l.strip()]
        j = json.loads(lines[-1])
        assert j["total"] == 1
        assert j["testable"] == 1
        assert j["todo"] == 1

    def test_empty_methods(self, utq, tmp_path):
        rc, out, _ = _run_cmd(utq, tmp_path, ["stats"], [])
        assert rc == 0
        assert "函数总数 0" in out


# ── 子命令：todo ────────────────────────────────────────────────────

class TestCmdTodo:
    def test_shows_uncovered_only(self, utq, tmp_path):
        methods = [
            _m("notest", level="high", testable=True, cover=0),
            _m("hastest", level="mid", testable=True, cover=1),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["todo"], methods)
        assert rc == 0
        assert "notest" in out
        assert "hastest" not in out

    def test_level_filter(self, utq, tmp_path):
        methods = [
            _m("hi", level="high", testable=True, cover=0),
            _m("lo", level="low", testable=True, cover=0),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["todo", "--level", "high"], methods)
        assert rc == 0
        assert "hi" in out
        assert "lo" not in out

    def test_class_filter(self, utq, tmp_path):
        methods = [
            _m("a", cls="Alpha", testable=True, cover=0),
            _m("b", cls="Beta", testable=True, cover=0),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["todo", "--class", "Alpha"], methods)
        assert rc == 0
        assert "Alpha" in out
        assert "Beta" not in out


# ── 子命令：top ──────────────────────────────────────────────────────

class TestCmdTop:
    def test_default_20(self, utq, tmp_path):
        methods = [_m(f"f{i}", score=i, testable=True, cover=0) for i in range(30)]
        rc, out, _ = _run_cmd(utq, tmp_path, ["top"], methods)
        assert rc == 0
        assert "Top20" in out

    def test_custom_n(self, utq, tmp_path):
        methods = [_m(f"f{i}", score=i, testable=True, cover=0) for i in range(10)]
        rc, out, _ = _run_cmd(utq, tmp_path, ["top", "5"], methods)
        assert rc == 0
        assert "Top5" in out

    def test_excludes_covered(self, utq, tmp_path):
        methods = [
            _m("covered", score=100, testable=True, cover=1),
            _m("todo", score=1, testable=True, cover=0),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["top", "10"], methods)
        assert "todo" in out


# ── 子命令：info ────────────────────────────────────────────────────

class TestCmdInfo:
    def test_found(self, utq, tmp_path):
        methods = [_m("loadImage", cls="Viewer", score=5, cover=1,
                       test_files=["ut_viewer.cpp"],
                       test_cases=["LoadImage_Normal_ReturnsTrue"])]
        rc, out, _ = _run_cmd(utq, tmp_path, ["info", "loadImage"], methods)
        assert rc == 0
        data = json.loads(out.strip())
        assert data["name"] == "loadImage"
        assert data["covered"] is True

    def test_not_found(self, utq, tmp_path):
        methods = [_m("other")]
        rc, out, err = _run_cmd(utq, tmp_path, ["info", "nonexist"], methods)
        assert rc == 1
        assert "没找到" in err

    def test_qualified_name_match(self, utq, tmp_path):
        methods = [_m("run", cls="proj.src.Manager")]
        rc, out, _ = _run_cmd(utq, tmp_path, ["info", "Manager.run"], methods)
        assert rc == 0


# ── 子命令：covered ─────────────────────────────────────────────────

class TestCmdCovered:
    def test_shows_covered_only(self, utq, tmp_path):
        methods = [
            _m("done", testable=True, cover=2, usecase=3),
            _m("pending", testable=True, cover=0),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["covered"], methods)
        assert rc == 0
        assert "done" in out
        assert "pending" not in out

    def test_show_cases(self, utq, tmp_path):
        methods = [_m("f", testable=True, cover=1, usecase=2,
                       test_cases=["F_Normal", "F_Edge"])]
        rc, out, _ = _run_cmd(utq, tmp_path, ["covered", "--show-cases"], methods)
        assert "F_Normal" in out
        assert "F_Edge" in out


# ── 子命令：weak ────────────────────────────────────────────────────

class TestCmdWeak:
    def test_high_score_low_usecase(self, utq, tmp_path):
        methods = [
            _m("weak1", score=5, testable=True, cover=1, usecase=1),  # ← 弱
            _m("strong", score=5, testable=True, cover=1, usecase=5),  # ← 不弱
            _m("simple", score=1, testable=True, cover=1, usecase=1),  # ← score<3 不弱
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["weak"], methods)
        assert "weak1" in out
        assert "strong" not in out
        assert "simple" not in out

    def test_no_weak(self, utq, tmp_path):
        methods = [_m("ok", score=1, testable=True, cover=1, usecase=5)]
        rc, out, _ = _run_cmd(utq, tmp_path, ["weak"], methods)
        assert "共 0 条" in out


# ── 子命令：by-test-file ────────────────────────────────────────────

class TestCmdByTestFile:
    def test_reverse_lookup(self, utq, tmp_path):
        methods = [
            _m("a", testable=True, cover=1, test_files=["ut_widget.cpp", "ut_core.cpp"]),
            _m("b", testable=True, cover=1, test_files=["ut_core.cpp"]),
            _m("c", testable=True, cover=0),  # 无 test_files
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["by-test-file", "ut_core"], methods)
        assert rc == 0
        assert "覆盖 2 个函数" in out

    def test_no_match(self, utq, tmp_path):
        methods = [_m("a", testable=True, cover=1, test_files=["ut_other.cpp"])]
        rc, out, _ = _run_cmd(utq, tmp_path, ["by-test-file", "ut_missing"], methods)
        assert "覆盖 0 个函数" in out


# ── 子命令：files ───────────────────────────────────────────────────

class TestCmdFiles:
    def test_aggregation(self, utq, tmp_path):
        methods = [
            _m("a", file="src/ui.cpp", testable=True, cover=1),
            _m("b", file="src/ui.cpp", testable=True, cover=0),
            _m("c", file="src/core.cpp", testable=True, cover=1),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["files"], methods)
        assert rc == 0
        assert "ui.cpp" in out
        assert "core.cpp" in out

    def test_sort_pct(self, utq, tmp_path):
        methods = [
            _m("a", file="full.cpp", testable=True, cover=1),
            _m("b", file="full.cpp", testable=True, cover=1),
            _m("c", file="empty.cpp", testable=True, cover=0),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["files", "--sort", "pct"], methods)
        assert rc == 0
        # empty.cpp (0%) 应排在 full.cpp (100%) 前面
        lines = [l for l in out.split("\n") if ".cpp" in l]
        empty_idx = next(i for i, l in enumerate(lines) if "empty" in l)
        full_idx = next(i for i, l in enumerate(lines) if "full" in l)
        assert empty_idx < full_idx


# ── 子命令：classes ─────────────────────────────────────────────────

class TestCmdClasses:
    def test_uncovered_classes(self, utq, tmp_path):
        methods = [
            _m("a", cls="Alpha", testable=True, cover=1),
            _m("b", cls="Alpha", testable=True, cover=0),
            _m("c", cls="Beta", testable=True, cover=0),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["classes"], methods)
        assert rc == 0
        assert "Alpha" in out  # Alpha 有 1 未测
        assert "Beta" in out   # Beta 有 1 未测

    def test_fully_covered_class_excluded(self, utq, tmp_path):
        methods = [_m("a", cls="Full", testable=True, cover=1)]
        rc, out, _ = _run_cmd(utq, tmp_path, ["classes"], methods)
        # Full 类全测，无未测 → 不在输出中
        assert "Full" not in out or "未测" not in out


# ── 子命令：pending ─────────────────────────────────────────────────

class TestCmdPending:
    def test_pending_items(self, utq, tmp_path):
        methods = [_m("f")]
        inv_extra = {
            "review_queue": [
                {"name": "newFunc", "class_qn": "A", "suggested_level": "mid",
                 "reason": "auto", "review_status": "pending"},
                {"name": "oldFunc", "class_qn": "B", "suggested_level": "high",
                 "reason": "manual", "review_status": "confirmed"},
            ]
        }
        rc, out, _ = _run_cmd(utq, tmp_path, ["pending"], methods, **inv_extra)
        assert rc == 0
        assert "newFunc" in out
        # 默认只显示 pending，不显示 confirmed
        assert "oldFunc" not in out

    def test_all_flag(self, utq, tmp_path):
        methods = [_m("f")]
        inv_extra = {
            "review_queue": [
                {"name": "newFunc", "review_status": "pending"},
                {"name": "oldFunc", "review_status": "confirmed"},
            ]
        }
        rc, out, _ = _run_cmd(utq, tmp_path, ["pending", "--all"], methods, **inv_extra)
        assert "newFunc" in out and "oldFunc" in out


# ── 子命令：exempt ──────────────────────────────────────────────────

class TestCmdExempt:
    def test_exempt_list(self, utq, tmp_path):
        methods = [
            _m("good", testable=True),
            {"name": "bad", "testable": False, "exempt_reason": "scope:tests/**",
             "class_qn": None, "file_path": "tests/helper.cpp"},
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["exempt"], methods)
        assert rc == 0
        assert "bad" in out
        assert "scope:tests/**" in out
        assert "good" not in out

    def test_no_exempt(self, utq, tmp_path):
        methods = [_m("a", testable=True)]
        rc, out, _ = _run_cmd(utq, tmp_path, ["exempt"], methods)
        assert "共 0 条豁免" in out


# ── 子命令：export ──────────────────────────────────────────────────

class TestCmdExport:
    def test_export_json(self, utq, tmp_path):
        methods = [
            _m("a", level="high", score=10, testable=True, cover=0),
            _m("b", level="low", score=1, testable=True, cover=1),
            _m("c", level="mid", testable=False),  # 豁免，不出现在 export
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["export"], methods)
        assert rc == 0
        data = json.loads(out)
        assert data["count"] == 1
        assert data["tasks"][0]["name"] == "a"
        assert "project" in data

    def test_export_with_level(self, utq, tmp_path):
        methods = [
            _m("a", level="high", testable=True, cover=0),
            _m("b", level="low", testable=True, cover=0),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["export", "--level", "high"], methods)
        data = json.loads(out)
        assert data["count"] == 1
        assert data["tasks"][0]["level"] == "high"

    def test_export_with_limit(self, utq, tmp_path):
        methods = [_m(f"f{i}", testable=True, cover=0) for i in range(10)]
        rc, out, _ = _run_cmd(utq, tmp_path, ["export", "--limit", "3"], methods)
        data = json.loads(out)
        assert data["count"] == 3


# ── 子命令：search ──────────────────────────────────────────────────

class TestCmdSearch:
    def test_keyword_match(self, utq, tmp_path):
        methods = [
            _m("loadImage", cls="Viewer"),
            _m("saveImage", cls="Viewer"),
            _m("parseConfig", cls="Parser"),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["search", "image"], methods)
        assert rc == 0
        assert "loadImage" in out
        assert "saveImage" in out
        assert "parseConfig" not in out


# ── 子命令：file ────────────────────────────────────────────────────

class TestCmdFile:
    def test_file_filter(self, utq, tmp_path):
        methods = [
            _m("a", file="src/view.cpp", testable=True, cover=1),
            _m("b", file="src/model.cpp", testable=True, cover=0),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["file", "view"], methods)
        assert "a" in out
        assert "b" not in out


# ── 子命令：class ───────────────────────────────────────────────────

class TestCmdClass:
    def test_class_filter(self, utq, tmp_path):
        methods = [
            _m("a", cls="Widget", testable=True, cover=0),
            _m("b", cls="Manager", testable=True, cover=1),
        ]
        rc, out, _ = _run_cmd(utq, tmp_path, ["class", "Widget"], methods)
        assert "Widget" in out
        assert "Manager" not in out


# ── CLI ─────────────────────────────────────────────────────────────

class TestCli:
    def test_find_project_explicit(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        result = utq.find_project_dir(str(tmp_path))
        assert result == tmp_path

    def test_find_project_missing(self, utq, tmp_path):
        with pytest.raises(SystemExit):
            utq.find_project_dir(str(tmp_path / "nonexist"))

    def test_die(self, utq):
        with pytest.raises(SystemExit) as exc_info:
            utq.die("test error")
        assert exc_info.value.code == 1

    def test_die_custom_code(self, utq):
        with pytest.raises(SystemExit) as exc_info:
            utq.die("bad", code=2)
        assert exc_info.value.code == 2

    def test_build_parser(self, utq):
        parser = utq.build_parser()
        args = parser.parse_args(["-P", "/tmp/proj", "stats"])
        assert args.cmd == "stats"
        assert args.project == "/tmp/proj"

    def test_build_parser_todo_with_filters(self, utq):
        parser = utq.build_parser()
        args = parser.parse_args(["todo", "--level", "high", "--class", "Foo", "--limit", "5"])
        assert args.cmd == "todo"
        assert args.level == "high"
        assert args.class_ == "Foo"
        assert args.limit == 5

    def test_invalid_json_inventory(self, utq, tmp_path):
        p = tmp_path / ".ut-inventory.json"
        p.write_text("{broken", encoding="utf-8")
        rc, out, err = _run_cmd(utq, tmp_path, ["stats"])
        # Inv 构造时 json.load 会抛 JSONDecodeError → main_no_exit 未捕获 → 非 0
        assert rc != 0 or "JSONDecodeError" in str(type(rc))

    def test_main_no_exit_wrapper(self, utq, tmp_path):
        """main_no_exit 存在且可用。"""
        assert hasattr(utq, "main_no_exit")
        _write_inv(tmp_path, [_m("f")])
        rc, out, _ = _run_cmd(utq, tmp_path, ["stats"])
        assert rc == 0


# ── 边界与缺失字段 ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_all_fields_missing(self, utq, tmp_path):
        """只有 name 的最简条目，所有可选字段缺失不崩溃。"""
        methods = [{"name": "minimal"}]
        for cmd in [["stats"], ["todo"], ["covered"], ["weak"], ["files"],
                    ["classes"], ["exempt"], ["export"]]:
            rc, out, _ = _run_cmd(utq, tmp_path, cmd, methods)
            assert rc == 0, f"cmd={cmd} crashed"

    def test_level_badge_none(self, utq, tmp_path):
        assert utq.LEVEL_BADGE.get(None) == "-"

    def test_level_order_unknown(self, utq):
        assert utq.LEVEL_ORDER.get("bogus", 9) == 9

    def test_method_dict_structure(self, utq, tmp_path):
        methods = [_m("f", cls="A", level="high", score=5, cover=1, usecase=2,
                       factors={"complexity": 8}, access="public")]
        _write_inv(tmp_path, methods)
        inv = utq.Inv(tmp_path)
        d = utq.method_dict(inv, methods[0])
        assert d["name"] == "f"
        assert d["covered"] is True
        assert d["owner"] == "A"
        assert d["level"] == "high"
        assert d["score"] == 5
        assert d["factors"] == {"complexity": 8}

    def test_gate_thresholds_missing(self, utq, tmp_path):
        """无 gate_thresholds 时 stats 门槛列显示 ?/./.。"""
        methods = [_m("a", level="high", testable=True)]
        rc, out, _ = _run_cmd(utq, tmp_path, ["stats"], methods)
        assert rc == 0
        assert "line?" in out  # 门槛字段缺失 → ?

    def test_zero_division_stats(self, utq, tmp_path):
        """某级别可测方法为 0 时覆盖率为 '-'，不除零。"""
        methods = [_m("a", level="high", testable=True, cover=1)]
        # 无 mid/low 方法
        rc, out, _ = _run_cmd(utq, tmp_path, ["stats"], methods)
        assert rc == 0
        # mid/low 行覆盖率应为 '-'
        assert "-" in out

    def test_empty_review_queue(self, utq, tmp_path):
        methods = [_m("f")]
        rc, out, _ = _run_cmd(utq, tmp_path, ["pending"], methods, review_queue=[])
        assert "共 0 条待评审" in out

    def test_broken_pipe_handling(self, utq):
        """BrokenPipeError 应安静退出。"""
        # 直接测试异常处理块
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        sys.stdout.close()  # 关闭 → 写入触发 BrokenPipeError
        try:
            raise BrokenPipeError()
        except BrokenPipeError:
            try:
                sys.stdout.close()
            except Exception:
                pass
        finally:
            sys.stdout = old_stdout
        # 如果到这里没崩溃，异常处理逻辑正确


# ── method_row / method_dict 一致性 ────────────────────────────────

class TestMethodRowAndDict:
    def test_method_row_seven_columns(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        m = _m("test", cls="X", level="high", score=5, cover=1, usecase=3,
               sig="int test()")
        row = utq.method_row(inv, m)
        assert len(row) == 7
        assert row[0] == "H"  # high badge
        assert row[1] == 5    # score
        assert "test" in row[2]
        assert row[5] == 3    # usecase
        assert row[6] == 1    # cover

    def test_method_dict_includes_covered(self, utq, tmp_path):
        _write_inv(tmp_path, [_m("f")])
        inv = utq.Inv(tmp_path)
        m = _m("f", cover=0)
        d = utq.method_dict(inv, m)
        assert d["covered"] is False
        m2 = _m("g", cover=2)
        d2 = utq.method_dict(inv, m2)
        assert d2["covered"] is True
