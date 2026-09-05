"""GitNexusAdapter 采集逻辑测试（FakeCypherClient 模拟 MCPClient 边界）。

FakeCypherClient 模拟的是 call_tool 已解码后的返回形态（{cols, rows, total}），
不重复测试 markdown 编解码（见 test_gitnexus_codec.py）。本文件聚焦：
  - collect_methods：行 schema → mcp_dump 字段对齐、qn 分配/消歧、is_test
  - parent_class_map / in_degree_map / collect_inheritance / collect_dbus_slots
  - collect_qt_macros（本地宏扫描）、discover_test_modules / collect_all_calls
  - fetch_test_cases（本地 TEST_F）、fetch_method_bodies（切片/消歧/降级）
  - find_repo 分页 / repo_head_sha / check_drift / resolve_base_sha / open_adapter
"""
import subprocess
import types

import pytest


# ════════════════════════════════════════════════════════════════════════════
# FakeCypherClient：模拟 GitNexus MCPClient（按语句子串匹配返回表）
# ════════════════════════════════════════════════════════════════════════════

class FakeCypherClient:
    """call_tool('cypher') 按 tables 子串匹配返回 {cols, rows, total}。

    tables 键按插入顺序匹配（首个命中即返回）；list_repos/context 单独配置。
    """

    def __init__(self, tables=None, list_repos=None, context=None):
        self.tables = tables or {}
        # list_repos：dict（每次同响应）或 [page1, page2, ...]（按序弹出，模拟分页）
        if isinstance(list_repos, list):
            self._repo_pages = list(list_repos)
        else:
            self._repo_pages = [list_repos or {"repositories": []}]
        self.context_resp = context
        self.session_id = "stub-session-id"
        self.url = "http://stub"
        self.statements = []
        self.context_calls = []
        self.list_calls = []

    def initialize(self):
        pass

    def call_tool(self, name, arguments, retries=3):
        if name == "list_repos":
            self.list_calls.append(arguments)
            idx = min(len(self.list_calls) - 1, len(self._repo_pages) - 1)
            return self._repo_pages[idx]
        if name == "context":
            self.context_calls.append(arguments)
            return self.context_resp
        stmt = arguments["statement"]
        self.statements.append(stmt)
        for key, table in self.tables.items():
            if key in stmt:
                return {"cols": table[0], "rows": table[1],
                        "total": len(table[1])}
        return {"cols": [], "rows": [], "total": 0}


def make_adapter(fetch_mcp_data, tables=None, project="demo-proj",
                 repo_root=None, list_repos=None, context=None):
    c = FakeCypherClient(tables=tables, list_repos=list_repos, context=context)
    return fetch_mcp_data.GitNexusAdapter(c, project=project, repo_root=repo_root), c


# ── 本地仓库样例（fetch_test_cases / fetch_method_bodies / 宏扫描用） ──────

CALC_SRC = """#include "calc.h"
namespace demo {
int Calc::add(int a, int b) {
    if (a > 0) {
        return a + b;
    }
    return 0;
}
int Calc::mul(int a, int b) {
    return a * b;
}
}
"""  # add: L3-L8, mul: L9-L11

HELPER_SRC = """#include "helper.h"
int CalcHelper::add(int a) {
    return a + 1;
}
"""  # add: L2-L4

UT_SRC = """#include <gtest/gtest.h>
#include "calc.h"
TEST_F(CalcTest, AddHandlesZero) {
    Calc c;
    EXPECT_EQ(c.add(0, 0), 0);
}
TEST_F(CalcTest, AddPositive) {
    Calc c;
    EXPECT_GT(c.add(1, 2), 0);
}
"""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "calc.cpp").write_text(CALC_SRC)
    (root / "src" / "helper.cpp").write_text(HELPER_SRC)
    (root / "tests" / "ut_calc.cpp").write_text(UT_SRC)
    return root


# ════════════════════════════════════════════════════════════════════════════
# collect_methods / _method_row / _assign_qualified_names
# ════════════════════════════════════════════════════════════════════════════

def _collect_tables(rows_m=(), rows_f=(), rows_parent=(), rows_indeg=()):
    return {
        "MATCH (m:Method)": (["name", "filePath", "startLine", "endLine"],
                             rows_m),
        "MATCH (f:Function)": (["name", "filePath", "startLine", "endLine"],
                               list(rows_f)),
        "HAS_METHOD": (["parent", "clabels", "filePath", "name", "sline"],
                       list(rows_parent)),
        "CALLS": (["filePath", "name", "sline"], list(rows_indeg)),
    }


class TestCollectMethods:
    def test_row_schema_alignment(self, fetch_mcp_data):
        """mcp_dump 行字段与旧 search_graph 路径对齐（下游评分零改动）。"""
        tables = _collect_tables(
            rows_m=[["add", "src/calc.cpp", 3, 8]],
            rows_parent=[["Calc", ["Class"], "src/calc.cpp", "add", 3]],
            rows_indeg=[("src/calc.cpp", "add", 3), ("src/calc.cpp", "add", 3)],
        )
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        methods, functions = adapter.collect_methods(limit=100)
        assert len(methods) == 1 and functions == []
        m = methods[0]
        assert m["name"] == "add"
        assert m["file_path"] == "src/calc.cpp"
        assert m["label"] == "Method"
        assert m["lines"] == 6  # end - start + 1
        assert m["in_degree"] == 2
        assert m["parent_class"] == "Calc"
        assert m["qualified_name"] == "Calc.add"
        assert m["is_test"] is False
        for k in ("complexity", "cognitive", "loop_count", "loop_depth",
                  "alloc_in_loop", "recursive", "transitive_loop_depth",
                  "linear_scan_in_loop", "param_count", "signature", "out"):
            assert k in m

    def test_function_bare_qn(self, fetch_mcp_data):
        """无父类的 Function → 裸名 qn。"""
        tables = _collect_tables(rows_f=[["main", "src/main.cpp", 1, 5]])
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        methods, functions = adapter.collect_methods()
        assert functions[0]["qualified_name"] == "main"
        assert functions[0]["label"] == "Function"

    def test_qn_collision_disambiguated_by_stem(self, fetch_mcp_data):
        """同名裸函数分布在不同文件 → 追加 @文件名主干。"""
        tables = _collect_tables(rows_f=[
            ["log", "src/a.cpp", 1, 3],
            ["log", "src/b.cpp", 1, 3],
        ])
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        _, functions = adapter.collect_methods()
        qns = {f["file_path"]: f["qualified_name"] for f in functions}
        assert qns["src/a.cpp"] == "log@a"
        assert qns["src/b.cpp"] == "log@b"

    def test_is_test_path_detection(self, fetch_mcp_data):
        tables = _collect_tables(rows_m=[["T", "tests/ut_t.cpp", 1, 4]])
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        methods, _ = adapter.collect_methods()
        assert methods[0]["is_test"] is True

    def test_local_metrics_from_repo(self, fetch_mcp_data, repo):
        """repo_root 提供时复杂度/签名来自本地切片。"""
        tables = _collect_tables(
            rows_m=[["add", "src/calc.cpp", 3, 8]],
            rows_parent=[["Calc", ["Class"], "src/calc.cpp", "add", 3]],
        )
        adapter, _ = make_adapter(fetch_mcp_data, tables, repo_root=str(repo))
        methods, _ = adapter.collect_methods()
        m = methods[0]
        assert m["complexity"] == 2      # 1 + if
        assert m["signature"] == "int Calc::add(int a, int b)"
        assert m["param_count"] == 2

    def test_pattern_filters_via_where(self, fetch_mcp_data):
        """file_patterns → 每个 pattern 一条 STARTS WITH WHERE 子句。"""
        tables = _collect_tables()
        adapter, c = make_adapter(fetch_mcp_data, tables)
        adapter.collect_methods(file_patterns=["src/**", "plugins/**"], limit=100)
        method_stmts = [s for s in c.statements if "MATCH (m:Method)" in s]
        assert len(method_stmts) == 2
        assert "m.filePath STARTS WITH 'src/'" in method_stmts[0]
        assert "m.filePath STARTS WITH 'plugins/'" in method_stmts[1]


class TestAssignQualifiedNames:
    def _rows(self, *specs):
        """specs: (name, file_path, parent_class, startLine)"""
        return [{"name": n, "file_path": fp, "parent_class": pc,
                 "startLine": sl} for n, fp, pc, sl in specs]

    def test_unique_bases_untouched(self, fetch_mcp_data):
        rows = self._rows(("foo", "src/a.cpp", "A", 1), ("bar", "src/a.cpp", "A", 5))
        fetch_mcp_data.GitNexusAdapter._assign_qualified_names(rows)
        assert rows[0]["qualified_name"] == "A.foo"
        assert rows[1]["qualified_name"] == "A.bar"

    def test_collision_appends_stem(self, fetch_mcp_data):
        rows = self._rows(("foo", "src/one.cpp", "", 1),
                          ("foo", "src/two.cpp", "", 1))
        fetch_mcp_data.GitNexusAdapter._assign_qualified_names(rows)
        assert rows[0]["qualified_name"] == "foo@one"
        assert rows[1]["qualified_name"] == "foo@two"

    def test_same_file_collision_appends_line(self, fetch_mcp_data):
        """同文件同基名（重载）→ 首个 @文件名，后续追加 @行号。"""
        rows = self._rows(("foo", "src/a.cpp", "", 10),
                          ("foo", "src/a.cpp", "", 20))
        fetch_mcp_data.GitNexusAdapter._assign_qualified_names(rows)
        assert rows[0]["qualified_name"] == "foo@a"
        assert rows[1]["qualified_name"] == "foo@a@20"


# ════════════════════════════════════════════════════════════════════════════
# parent_class_map / in_degree_map
# ════════════════════════════════════════════════════════════════════════════

class TestParentClassMap:
    def test_skips_file_labeled_sources(self, fetch_mcp_data):
        """File 节点的 HAS_METHOD（自由函数归属）不当作父类。"""
        tables = {
            "HAS_METHOD": (
                ["parent", "clabels", "filePath", "name", "sline"],
                [["Calc", ["Class"], "src/calc.cpp", "add", 3],
                 ["main.cpp", ["File"], "src/main.cpp", "main", 1]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        pm = adapter.parent_class_map()
        assert pm[("src/calc.cpp", "add", 3)] == "Calc"
        assert ("src/main.cpp", "main", 1) not in pm

    def test_clabels_as_json_string(self, fetch_mcp_data):
        """labels 序列化为 JSON 字符串 → 解析。"""
        tables = {
            "HAS_METHOD": (
                ["parent", "clabels", "filePath", "name", "sline"],
                [["Calc", '["Class"]', "src/calc.cpp", "add", 3]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        assert adapter.parent_class_map()[("src/calc.cpp", "add", 3)] == "Calc"

    def test_cached(self, fetch_mcp_data):
        tables = {"HAS_METHOD": (["parent", "clabels", "filePath", "name", "sline"], [])}
        adapter, c = make_adapter(fetch_mcp_data, tables)
        adapter.parent_class_map()
        adapter.parent_class_map()
        assert len(c.statements) == 1


class TestInDegreeMap:
    def test_aggregates_calls(self, fetch_mcp_data):
        tables = {
            "CALLS": (["filePath", "name", "sline"],
                      [["src/calc.cpp", "add", 3], ["src/calc.cpp", "add", 3],
                       ["src/calc.cpp", "mul", 9]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        im = adapter.in_degree_map()
        assert im[("src/calc.cpp", "add", 3)] == 2
        assert im[("src/calc.cpp", "mul", 9)] == 1


# ════════════════════════════════════════════════════════════════════════════
# collect_inheritance / collect_dbus_slots
# ════════════════════════════════════════════════════════════════════════════

class TestCollectInheritance:
    def _tables(self, rows):
        return {"EXTENDS": (["name", "filePath", "base"], rows)}

    def test_bucketed_by_whitelist(self, fetch_mcp_data):
        rows = [
            ["MyAdaptor", "src/da.cpp", "QDBusAbstractAdaptor"],
            ["MyClient", "src/dc.cpp", "QDBusAbstractInterface"],
            ["MyThread", "src/t.cpp", "QThread"],
            ["MyWidget", "src/w.cpp", "QWidget"],
        ]
        adapter, _ = make_adapter(fetch_mcp_data, self._tables(rows))
        da, dc, conc, gui = adapter.collect_inheritance()
        assert da == [{"name": "MyAdaptor", "qualified_name": "MyAdaptor",
                       "file_path": "src/da.cpp",
                       "base_classes": ["QDBusAbstractAdaptor"]}]
        assert dc[0]["name"] == "MyClient"
        assert conc[0]["name"] == "MyThread"
        assert gui[0]["name"] == "MyWidget"

    def test_multi_base_merged(self, fetch_mcp_data):
        """一个类命中多个白名单基类 → base_classes 合并排序。"""
        rows = [["W", "src/w.cpp", "QWidget"], ["W", "src/w.cpp", "QMainWindow"]]
        adapter, _ = make_adapter(fetch_mcp_data, self._tables(rows))
        _, _, _, gui = adapter.collect_inheritance()
        assert len(gui) == 1
        assert gui[0]["base_classes"] == ["QMainWindow", "QWidget"]

    def test_test_path_excluded(self, fetch_mcp_data):
        rows = [["T", "tests/ut_t.cpp", "QThread"]]
        adapter, _ = make_adapter(fetch_mcp_data, self._tables(rows))
        _, _, conc, _ = adapter.collect_inheritance()
        assert conc == []

    def test_non_whitelisted_base_ignored(self, fetch_mcp_data):
        rows = [["Foo", "src/foo.cpp", "QObject"]]
        adapter, _ = make_adapter(fetch_mcp_data, self._tables(rows))
        da, dc, conc, gui = adapter.collect_inheritance()
        assert (da, dc, conc, gui) == ([], [], [], [])


class TestCollectDbusSlots:
    def test_filters_ctor_dtor_emit(self, fetch_mcp_data):
        tables = {
            "HAS_METHOD": (
                ["name"],
                [["MyAdaptor"],           # 构造函数（与类同名）
                 ["~MyAdaptor"],          # 析构
                 ["emitReady"],           # emit* 信号
                 ["GetValue"], ["SetValue"]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        slots = adapter.collect_dbus_slots(
            [{"name": "MyAdaptor", "qualified_name": "MyAdaptor"}])
        assert slots == {"MyAdaptor": ["GetValue", "SetValue"]}

    def test_empty_slots_skipped(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data)
        assert adapter.collect_dbus_slots([]) == {}


# ════════════════════════════════════════════════════════════════════════════
# collect_qt_macros（本地宏扫描）
# ════════════════════════════════════════════════════════════════════════════

class TestCollectQtMacros:
    def test_local_scan(self, fetch_mcp_data, repo):
        (repo / "src" / "calc.cpp").write_text(
            CALC_SRC + "\n// inv\nQ_INVOKABLE void extra();\n")
        tables = {
            "MATCH (f:File)": (["filePath"],
                               [["src/calc.cpp"], ["src/helper.cpp"],
                                ["src/notes.txt"]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables, repo_root=str(repo))
        inv, plugins = adapter.collect_qt_macros()
        # notes.txt 非 C/C++ 扩展 → 排除；helper.cpp 无宏
        assert inv == {} and plugins == {}

    def test_q_invokable_collected(self, fetch_mcp_data, repo):
        (repo / "src" / "calc.cpp").write_text(
            "class Calc {\nQ_INVOKABLE int add(int a, int b);\n};\n")
        tables = {"MATCH (f:File)": (["filePath"], [["src/calc.cpp"]])}
        adapter, _ = make_adapter(fetch_mcp_data, tables, repo_root=str(repo))
        inv, _ = adapter.collect_qt_macros()
        assert inv == {"Calc": ["add"]}

    def test_plugin_metadata_by_stem(self, fetch_mcp_data, repo):
        (repo / "src" / "myplugin.cpp").write_text(
            "class P : public QObject {\n"
            "Q_PLUGIN_METADATA(IID \"x\")\n};\n")
        tables = {"MATCH (f:File)": (["filePath"], [["src/myplugin.cpp"]])}
        adapter, _ = make_adapter(fetch_mcp_data, tables, repo_root=str(repo))
        _, plugins = adapter.collect_qt_macros()
        assert plugins == {"myplugin": True}

    def test_no_repo_root_skips(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data)
        assert adapter.collect_qt_macros() == ({}, {})


# ════════════════════════════════════════════════════════════════════════════
# discover_test_modules / collect_all_calls / fetch_test_cases
# ════════════════════════════════════════════════════════════════════════════

class TestDiscoverTestModules:
    def test_ut_files_only(self, fetch_mcp_data):
        tables = {
            "MATCH (f:File)": (["filePath"],
                               [["tests/ut_foo.cpp"], ["tests/helper.cpp"],
                                ["tests/ut_bar.h"], ["src/main.cpp"]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        mods = adapter.discover_test_modules()
        names = [m["name"] for m in mods]
        # ut_* 过滤 + helper.cpp/main.cpp 排除（保持图谱行序，不额外排序）
        assert names == ["ut_foo.cpp", "ut_bar.h"]
        assert mods[0]["out_degree"] == 0

    def test_test_dir_prefixes(self, fetch_mcp_data):
        tables = {
            "MATCH (f:File)": (["filePath"],
                               [["test/ut_a.cpp"], ["autotests/ut_b.cpp"]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        assert len(adapter.discover_test_modules()) == 2

    def test_legacy_test_prefix_files(self, fetch_mcp_data):
        """旧快照 test_*.cpp 命名（如 dde-file-manager 图谱基线）同样命中。"""
        tables = {
            "MATCH (f:File)": (["filePath"],
                               [["autotests/old/x/test_foo.cpp"],
                                ["autotests/old/x/testmain.cpp"],
                                ["autotests/old/x/main.cpp"]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        names = [m["name"] for m in adapter.discover_test_modules()]
        assert names == ["test_foo.cpp"]


class TestCollectAllCalls:
    def test_targets_aggregated(self, fetch_mcp_data):
        tables = {
            "HAS_METHOD": (["parent", "clabels", "filePath", "name", "sline"],
                           [["Calc", ["Class"], "src/calc.cpp", "add", 3]]),
            "CALLS": (["srcFile", "name", "filePath", "startLine"],
                      [["tests/ut_a.cpp", "add", "src/calc.cpp", 3],
                       ["tests/ut_b.cpp", "add", "src/calc.cpp", 3]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        mods = [{"name": "ut_a.cpp", "file_path": "tests/ut_a.cpp", "out_degree": 0},
                {"name": "ut_b.cpp", "file_path": "tests/ut_b.cpp", "out_degree": 0}]
        out = adapter.collect_all_calls(mods)
        assert out == {"Calc.add": {"tests/ut_a.cpp", "tests/ut_b.cpp"}}
        # out_degree 回填：有 CALLS 出边的模块记 1
        assert all(m["out_degree"] == 1 for m in mods)

    def test_test_dir_target_excluded(self, fetch_mcp_data):
        """被测目标在测试目录 → stub，剔除。"""
        tables = {
            "CALLS": (["srcFile", "name", "filePath", "startLine"],
                      [["tests/ut_a.cpp", "helper_run", "tests/helper.cpp", 1]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables)
        mods = [{"name": "ut_a.cpp", "file_path": "tests/ut_a.cpp", "out_degree": 0}]
        assert adapter.collect_all_calls(mods) == {}

    def test_no_modules(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data)
        assert adapter.collect_all_calls([]) == {}


class TestFetchTestCases:
    def test_local_testf_parse(self, fetch_mcp_data, repo):
        adapter, _ = make_adapter(fetch_mcp_data, repo_root=str(repo))
        mods = [{"name": "ut_calc.cpp", "file_path": "tests/ut_calc.cpp",
                 "out_degree": 1}]
        out = adapter.fetch_test_cases(mods)
        assert out["tests/ut_calc.cpp"] == ["CalcTest.AddHandlesZero",
                                            "CalcTest.AddPositive"]

    def test_missing_file_skipped(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data)
        mods = [{"name": "ut_x.cpp", "file_path": "tests/ut_x.cpp",
                 "out_degree": 0}]
        assert adapter.fetch_test_cases(mods) == {}


# ════════════════════════════════════════════════════════════════════════════
# fetch_method_bodies（切片 / 消歧 / 降级）
# ════════════════════════════════════════════════════════════════════════════

class TestFetchMethodBodies:
    def test_local_slice(self, fetch_mcp_data, repo):
        tables = {
            "HAS_METHOD": (
                ["parent", "name", "filePath", "startLine", "endLine"],
                [["Calc", "add", "src/calc.cpp", 3, 8]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables, repo_root=str(repo))
        out = adapter.fetch_method_bodies("Calc", [
            {"qualified_name": "Calc.add", "name": "add",
             "signature": "int Calc::add(int a, int b)", "complexity": 2}])
        assert "out" not in out
        entry = out["Calc.add"]
        assert entry["body"].startswith("int Calc::add(int a, int b)")
        assert entry["complexity"] == 2
        assert "error" not in entry

    def test_overload_disambiguation(self, fetch_mcp_data, repo):
        """同名方法多个候选 →（类名匹配，参数个数差）排序取最优。"""
        tables = {
            "HAS_METHOD": (
                ["parent", "name", "filePath", "startLine", "endLine"],
                [["CalcHelper", "add", "src/helper.cpp", 2, 4],
                 ["Calc", "add", "src/calc.cpp", 3, 8]]),
        }
        adapter, _ = make_adapter(fetch_mcp_data, tables, repo_root=str(repo))
        out = adapter.fetch_method_bodies("Calc", [
            {"qualified_name": "Calc.add", "name": "add",
             "signature": "int Calc::add(int a, int b)", "complexity": 0}])
        assert "int Calc::add(int a, int b)" in out["Calc.add"]["body"]

    def test_context_fallback_marks_truncated(self, fetch_mcp_data):
        """本地文件缺失 → context content 降级（5016 截断，标 truncated）。"""
        tables = {
            "HAS_METHOD": (
                ["parent", "name", "filePath", "startLine", "endLine"],
                [["Calc", "add", "src/gone.cpp", 1, 4]]),
        }
        ctx = {"status": "found",
               "symbol": {"content": "int Calc::add(int a, int b) { return a; }"}}
        adapter, _ = make_adapter(fetch_mcp_data, tables, repo_root=None, context=ctx)
        out = adapter.fetch_method_bodies("Calc", [
            {"qualified_name": "Calc.add", "name": "add", "signature": "",
             "complexity": 0}])
        assert out["Calc.add"]["body"] == "int Calc::add(int a, int b) { return a; }"
        assert out["Calc.add"]["truncated"] is True

    def test_graph_miss_sets_error(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data)
        out = adapter.fetch_method_bodies("Calc", [
            {"qualified_name": "Calc.add", "name": "add", "signature": "",
             "complexity": 0}])
        assert out["Calc.add"]["body"] == ""
        assert "error" in out["Calc.add"]

    def test_no_methods(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data)
        assert adapter.fetch_method_bodies("Calc", []) == {}


# ════════════════════════════════════════════════════════════════════════════
# find_repo / repo_head_sha / check_drift / resolve_base_sha / open_adapter
# ════════════════════════════════════════════════════════════════════════════

class TestFindRepo:
    def test_found(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data, list_repos={
            "repositories": [{"name": "other"}, {"name": "demo-proj",
                                                 "lastCommit": "ABC"}]})
        info = adapter.find_repo()
        assert info["lastCommit"] == "ABC"

    def test_not_found_returns_none(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data, list_repos={
            "repositories": [{"name": "other"}]})
        assert adapter.find_repo() is None
    def test_pagination_beyond_first_page(self, fetch_mcp_data):
        """第一页满 200 条 → 继续翻页直到找到。"""
        page1 = [{"name": "r%02d" % i} for i in range(200)]
        adapter, c = make_adapter(fetch_mcp_data, list_repos=[
            {"repositories": page1},
            {"repositories": [{"name": "demo-proj", "lastCommit": "D1"}]},
        ])
        info = adapter.find_repo()
        assert info["lastCommit"] == "D1"
        assert c.list_calls[0]["offset"] == 0
        assert c.list_calls[1]["offset"] == 200

    def test_non_dict_response(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data, list_repos=None)
        assert adapter.find_repo() is None


class TestRepoHeadSha:
    def test_last_commit(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data, list_repos={
            "repositories": [{"name": "demo-proj", "lastCommit": "276e9d8"}]})
        assert adapter.repo_head_sha() == "276e9d8"

    def test_missing_repo_empty(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data)
        assert adapter.repo_head_sha() == ""


class TestCheckDrift:
    def test_git_repo_head(self, fetch_mcp_data, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        (root / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        adapter, _ = make_adapter(fetch_mcp_data, repo_root=str(root),
                                  list_repos={"repositories": [
                                      {"name": "demo-proj", "lastCommit": head}]})
        local, graph = adapter.check_drift()
        assert local == head and graph == head

    def test_no_git_dir_empty_local(self, fetch_mcp_data, tmp_path):
        adapter, _ = make_adapter(fetch_mcp_data, repo_root=str(tmp_path))
        local, graph = adapter.check_drift()
        assert local == "" and graph == ""


class TestResolveBaseSha:
    def test_explicit_wins(self, fetch_mcp_data):
        adapter, c = make_adapter(fetch_mcp_data)
        assert fetch_mcp_data.resolve_base_sha(
            adapter, "demo-proj", explicit="MYSHA") == "MYSHA"
        assert c.list_calls == []  # 显式传入不查图谱

    def test_from_graph_last_commit(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data, list_repos={
            "repositories": [{"name": "demo-proj", "lastCommit": "G1"}]})
        assert fetch_mcp_data.resolve_base_sha(adapter, "demo-proj") == "G1"

    def test_missing_last_commit_unknown(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data, list_repos={
            "repositories": [{"name": "demo-proj"}]})
        assert fetch_mcp_data.resolve_base_sha(adapter, "demo-proj") == "unknown"

    def test_exception_unknown(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data, list_repos={
            "repositories": RuntimeError("conn lost")})
        assert fetch_mcp_data.resolve_base_sha(adapter, "demo-proj") == "unknown"


class TestOpenAdapter:
    def _patch_mcp_client(self, monkeypatch, fetch_mcp_data, client):
        cls = fetch_mcp_data.MCPClient  # 先取类再替换模块属性
        monkeypatch.setattr(fetch_mcp_data, "MCPClient", lambda **kw: client)
        monkeypatch.setattr(cls, "initialize", lambda self: None)

    def test_success(self, fetch_mcp_data, monkeypatch):
        client = FakeCypherClient(list_repos={
            "repositories": [{"name": "demo-proj", "lastCommit": "A"}]})
        self._patch_mcp_client(monkeypatch, fetch_mcp_data, client)
        adapter = fetch_mcp_data.open_adapter("demo-proj", "http://stub")
        assert adapter.project == "demo-proj"

    def test_unindexed_exits_2(self, fetch_mcp_data, monkeypatch, capsys):
        """仓库未索引 → SystemExit(2)（一等失败）。"""
        client = FakeCypherClient(list_repos={"repositories": [{"name": "x"}]})
        self._patch_mcp_client(monkeypatch, fetch_mcp_data, client)
        with pytest.raises(SystemExit) as ei:
            fetch_mcp_data.open_adapter("demo-proj", "http://stub")
        assert ei.value.code == 2
        assert "未索引" in capsys.readouterr().err


# ════════════════════════════════════════════════════════════════════════════
# is_test_path / slice_body 边界
# ════════════════════════════════════════════════════════════════════════════

class TestPathAndSlice:
    @pytest.mark.parametrize("path,expected", [
        ("tests/ut_a.cpp", True),
        ("src/test/helper.cpp", True),
        ("autotests/ut_b.h", True),
        ("src/main.cpp", False),
        ("contest/foo.cpp", False),   # 'test' 子串但非路径段
        ("", False),
    ])
    def test_is_test_path(self, fetch_mcp_data, path, expected):
        assert fetch_mcp_data.GitNexusAdapter.is_test_path(path) is expected

    def test_slice_body_exact(self, fetch_mcp_data, repo):
        adapter, _ = make_adapter(fetch_mcp_data, repo_root=str(repo))
        body = adapter.slice_body("src/calc.cpp", 3, 8)
        assert body.splitlines()[0] == "int Calc::add(int a, int b) {"
        assert len(body.splitlines()) == 6

    def test_slice_body_out_of_range(self, fetch_mcp_data, repo):
        adapter, _ = make_adapter(fetch_mcp_data, repo_root=str(repo))
        assert adapter.slice_body("src/calc.cpp", 9999, 10000) == ""
        assert adapter.slice_body("src/missing.cpp", 1, 2) == ""

    def test_slice_body_no_repo(self, fetch_mcp_data):
        adapter, _ = make_adapter(fetch_mcp_data)
        assert adapter.slice_body("src/calc.cpp", 1, 2) == ""


# ════════════════════════════════════════════════════════════════════════════
# list_repos_all 并发全量遍历 / find_repo 缓存
# ════════════════════════════════════════════════════════════════════════════

class TestListReposAll:
    def test_parallel_uses_pagination_total(self, fetch_mcp_data, monkeypatch):
        """真机响应带 pagination.total + workers>1 → 剩余页并发拉取并按序拼接。"""
        monkeypatch.setenv("QTAG_LIST_REPOS_WORKERS", "4")
        first = {"repositories": [{"name": "r%03d" % i} for i in range(200)],
                 "pagination": {"total": 450, "limit": 200, "offset": 0,
                                "returned": 200, "hasMore": True}}
        created = []

        class FakePageClient:
            def __init__(self, url=None, timeout=None, extra_headers=None):
                self.init_calls = 0
                created.append(self)

            def initialize(self):
                self.init_calls += 1

            def call_tool(self, name, arguments):
                assert name == "list_repos"
                off = arguments["offset"]
                assert arguments["limit"] == 200
                if off == 200:
                    return {"repositories": [{"name": "r%03d" % i}
                                             for i in range(200, 400)]}
                if off == 400:
                    return {"repositories": [{"name": "r%03d" % i}
                                             for i in range(400, 450)]}
                raise AssertionError(f"unexpected offset {off}")

        monkeypatch.setattr(fetch_mcp_data, "MCPClient", FakePageClient)
        main_client = types.SimpleNamespace(
            url="http://x", timeout=5, extra_headers={"A": "b"},
            call_tool=lambda name, args: first)
        repos = fetch_mcp_data.list_repos_all(main_client)
        assert [r["name"] for r in repos] == ["r%03d" % i for i in range(450)]
        assert len(created) == 2                      # 2 个剩余页，各一个会话
        assert all(c.init_calls == 1 for c in created)

    def test_no_pagination_sequential_fallback(self, fetch_mcp_data):
        """无 pagination 元数据（桩/旧响应）→ 顺序翻页兜底，直到短页。"""
        p1 = [{"name": "a%03d" % i} for i in range(200)]
        p2 = [{"name": "b%02d" % i} for i in range(3)]
        c = FakeCypherClient(list_repos=[{"repositories": p1},
                                         {"repositories": p2}])
        repos = fetch_mcp_data.list_repos_all(c)
        assert len(repos) == 203
        assert c.list_calls[0]["offset"] == 0
        assert c.list_calls[1]["offset"] == 200

    def test_single_page_probes_next_then_stops(self, fetch_mcp_data):
        """无 pagination 单页 → 顺序兜底探测下一页，短页即停。"""
        c = FakeCypherClient(list_repos=[{"repositories": [{"name": "only"}]},
                                         {"repositories": []}])
        assert [r["name"] for r in fetch_mcp_data.list_repos_all(c)] == ["only"]
        assert c.list_calls[0]["offset"] == 0
        assert c.list_calls[1]["offset"] == 200

    def test_non_dict_response(self, fetch_mcp_data):
        c = FakeCypherClient(list_repos=None)
        assert fetch_mcp_data.list_repos_all(c) == []


class TestFindRepoCache:
    def test_second_call_hits_cache(self, fetch_mcp_data):
        """find_repo 结果缓存：第二次调用不再发起 list_repos。"""
        adapter, c = make_adapter(fetch_mcp_data, list_repos={
            "repositories": [{"name": "demo-proj", "lastCommit": "C1"}]})
        assert adapter.find_repo()["lastCommit"] == "C1"
        n = len(c.list_calls)
        assert adapter.find_repo()["lastCommit"] == "C1"
        assert len(c.list_calls) == n                 # 缓存命中，零新调用

    def test_miss_then_hit_same_session(self, fetch_mcp_data):
        """未索引（None）也缓存搜索结论，check_drift/resolve_base_sha 不重复遍历。"""
        adapter, c = make_adapter(fetch_mcp_data, list_repos={
            "repositories": [{"name": "other"}]})
        assert adapter.repo_head_sha() == ""
        n = len(c.list_calls)
        assert adapter.repo_head_sha() == ""
        assert len(c.list_calls) == n
