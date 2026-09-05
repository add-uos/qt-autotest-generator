"""mcp-scan.py test-mapping 集成测试。

验证 Mode 1 fetch 天然采集 test_* 覆盖数据的核心契约：
  - 业务函数（discover/build_test_mapping/update_inventory_test_mapping）正确性
  - fetch 非增量路径触发 test_* 采集并回写
  - fetch 增量路径不重采（靠 overlay 保留）
  - --skip-test-mapping 跳过采集
  - test-mapping 子命令（独立回写入口）

fetch 集成测试 stub open_adapter 返回 StubAdapter（GitNexusAdapter 替身，
采集方法全返空并记录调用），只验证 test_* 采集分支是否被正确触发。
adapter 采集逻辑本身的测试见 test_gitnexus_adapter.py。
"""
import json
import types

import pytest


# ── _tm_normalize_qn ──────────────────────────────────────────────────

class TestNormalizeQn:
    def test_strips_server_project_prefix(self, fetch_mcp_data):
        """MCP qn 带服务端仓库前缀，去首段项目前缀。"""
        qn = "home-uos-service-codebase-repos-deepin-reader.src.Foo.add"
        # 去硬编码前缀 → deepin-reader.src.Foo.add → 首段 deepin-reader 含 '-' 去掉
        assert fetch_mcp_data._tm_normalize_qn(qn) == "src.Foo.add"

    def test_strips_local_project_prefix(self, fetch_mcp_data):
        """inventory qn 带本地路径前缀，去首段项目前缀。"""
        qn = "home-zhy-debug-deepin-reader.reader.Foo.add"
        # 首段 home-zhy-debug-deepin-reader startswith home → 去掉
        assert fetch_mcp_data._tm_normalize_qn(qn) == "reader.Foo.add"

    def test_both_prefixes_align(self, fetch_mcp_data):
        """MCP 与 inventory 不同前缀归一化后一致。"""
        mcp_qn = "home-uos-service-codebase-repos-deepin-reader.reader.A.foo"
        inv_qn = "home-zhy-debug-deepin-reader.reader.A.foo"
        assert fetch_mcp_data._tm_normalize_qn(mcp_qn) == fetch_mcp_data._tm_normalize_qn(inv_qn) == "reader.A.foo"

    def test_no_project_prefix_passthrough(self, fetch_mcp_data):
        """已是路径段（无项目前缀特征）不动。"""
        assert fetch_mcp_data._tm_normalize_qn("reader.Foo.add") == "reader.Foo.add"

    def test_empty(self, fetch_mcp_data):
        assert fetch_mcp_data._tm_normalize_qn("") == ""

    def test_legacy_normalized_key_idempotent(self, fetch_mcp_data):
        """旧 mapping 文件已去 home-uos- 前缀的 key，二次归一化不误伤。"""
        # deepin-reader.A.foo：首段 deepin-reader 含 '-' 会被去掉 → A.foo
        # 这是预期行为（旧格式 key 也会被进一步归一化，与 MCP 侧对齐）
        assert fetch_mcp_data._tm_normalize_qn("deepin-reader.A.foo") == "A.foo"

    def test_strips_multiple_leading_dash_segments(self, fetch_mcp_data):
        """多个含 '-' 的前导段一次性剥掉（如 home-uos-service-...-deepin-reader）。"""
        qn = "home-uos-service-codebase-repos-deepin-reader.reader.A.foo"
        assert fetch_mcp_data._tm_normalize_qn(qn) == "reader.A.foo"

    def test_strips_single_leading_dash_segment(self, fetch_mcp_data):
        """单个含 '-' 的前导段（如 home-zhy-debug-deepin-reader）被剥掉。"""
        qn = "home-zhy-debug-deepin-reader.reader.A.foo"
        assert fetch_mcp_data._tm_normalize_qn(qn) == "reader.A.foo"

    def test_preserves_middle_dash_segment(self, fetch_mcp_data):
        """路径中间含 '-' 的段（如 3rdparty.deepin-pdfium）保留，只剥前导。"""
        qn = "deepin-reader.3rdparty.deepin-pdfium-3rdparty.src.pdfium.Foo.bar"
        assert fetch_mcp_data._tm_normalize_qn(qn) == "3rdparty.deepin-pdfium-3rdparty.src.pdfium.Foo.bar"

    def test_preserves_no_dash_source_path(self, fetch_mcp_data):
        """无 '-' 的源码路径段（reader/document/browser/sheet）保留。"""
        qn = "home-uos-service-codebase-repos-deepin-reader.reader.document.PDFModel.foo"
        assert fetch_mcp_data._tm_normalize_qn(qn) == "reader.document.PDFModel.foo"


# ── discover_test_modules ─────────────────────────────────────────
# 已随 GitNexus 改造移入 GitNexusAdapter.discover_test_modules，
# 其行为测试（ut_* 过滤 / 测试目录前缀）见 test_gitnexus_adapter.py。

# ── build_test_mapping ────────────────────────────────────────────────

class TestBuildTestMapping:
    def test_basic(self, fetch_mcp_data):
        s2t = {
            "home-uos-service-codebase-repos-deepin-reader.A.foo": {"tests/ut_a.cpp"},
            "home-zhy-debug-deepin-reader.A.bar": {"tests/ut_a.cpp", "tests/ut_b.cpp"},
        }
        m = fetch_mcp_data.build_test_mapping(s2t)
        # 不同项目前缀归一化后都去首段 → A.foo / A.bar
        assert m["A.foo"]["test_cover_count"] == 1
        assert m["A.foo"]["test_files"] == ["tests/ut_a.cpp"]
        assert m["A.bar"]["test_cover_count"] == 2
        assert sorted(m["A.bar"]["test_files"]) == ["tests/ut_a.cpp", "tests/ut_b.cpp"]

    def test_with_cases(self, fetch_mcp_data):
        s2t = {"A.foo": {"tests/ut_a.cpp"}}
        f2c = {"tests/ut_a.cpp": ["Suite.Case1", "Suite.Case2"]}
        m = fetch_mcp_data.build_test_mapping(s2t, f2c)
        assert m["A.foo"]["test_cases"] == ["Suite.Case1", "Suite.Case2"]

    def test_empty(self, fetch_mcp_data):
        assert fetch_mcp_data.build_test_mapping({}) == {}


# ── update_inventory_test_mapping ─────────────────────────────────────

class TestUpdateInventoryTestMapping:
    def test_writes_cover_fields(self, fetch_mcp_data):
        inv = {"methods": [{"qualified_name": "A.foo", "usecase_count": 0}]}
        mapping = {"A.foo": {"test_cover_count": 2,
                             "test_files": ["ut_a.cpp", "ut_b.cpp"],
                             "test_cases": ["S.C1", "S.C2"]}}
        updated, unmatched, um = fetch_mcp_data.update_inventory_test_mapping(inv, mapping)
        m = inv["methods"][0]
        assert updated == 1 and unmatched == 0
        assert m["test_cover_count"] == 2
        assert m["test_files"] == ["ut_a.cpp", "ut_b.cpp"]
        assert m["test_cases"] == ["S.C1", "S.C2"]
        assert m["test_source"] == "mcp_calls"
        # usecase_count 取 max(原值 0, test_cover_count 2) = 2
        assert m["usecase_count"] == 2

    def test_usecase_count_max_preserves_higher(self, fetch_mcp_data):
        """Mode 2 已写的精确 usecase_count（>test_cover_count）不被覆盖。"""
        inv = {"methods": [{"qualified_name": "A.foo", "usecase_count": 10}]}
        mapping = {"A.foo": {"test_cover_count": 2, "test_files": ["ut_a.cpp"], "test_cases": []}}
        fetch_mcp_data.update_inventory_test_mapping(inv, mapping)
        assert inv["methods"][0]["usecase_count"] == 10  # max(10, 2) = 10

    def test_zero_cover_not_written(self, fetch_mcp_data):
        """test_cover_count=0 不回写（不污染未覆盖方法）。"""
        inv = {"methods": [{"qualified_name": "A.foo", "usecase_count": 0}]}
        mapping = {"A.foo": {"test_cover_count": 0, "test_files": [], "test_cases": []}}
        updated, _, _ = fetch_mcp_data.update_inventory_test_mapping(inv, mapping)
        assert updated == 0
        assert "test_cover_count" not in inv["methods"][0]

    def test_unmatched_preserved(self, fetch_mcp_data):
        """未匹配方法原值不动。"""
        inv = {"methods": [
            {"qualified_name": "A.foo", "usecase_count": 5, "test_cover_count": 1},
            {"qualified_name": "B.bar", "usecase_count": 0},
        ]}
        mapping = {"A.foo": {"test_cover_count": 3, "test_files": ["ut_a.cpp"], "test_cases": []}}
        updated, unmatched, _ = fetch_mcp_data.update_inventory_test_mapping(inv, mapping)
        assert updated == 1 and unmatched == 1
        # B.bar 原值不动
        assert inv["methods"][1]["usecase_count"] == 0
        assert "test_cover_count" not in inv["methods"][1]

    def test_qn_prefix_normalization(self, fetch_mcp_data):
        """MCP qn 与 inventory qn 前缀不同，归一化后匹配。"""
        inv = {"methods": [{"qualified_name": "home-zhy-debug-deepin-reader.A.foo"}]}
        # MCP qn 带服务端前缀，build_test_mapping 归一化后 key = A.foo
        mapping = fetch_mcp_data.build_test_mapping(
            {"home-uos-service-codebase-repos-deepin-reader.A.foo": {"tests/ut_a.cpp"}})
        updated, _, _ = fetch_mcp_data.update_inventory_test_mapping(inv, mapping)
        assert updated == 1


# ── load_test_mapping_from_file ───────────────────────────────────────

class TestLoadTestMappingFromFile:
    def test_new_format(self, fetch_mcp_data, tmp_path):
        data = {"A.foo": {"test_cover_count": 2, "test_files": ["ut_a.cpp"], "test_cases": []}}
        p = tmp_path / "m.json"
        p.write_text(json.dumps(data))
        m = fetch_mcp_data.load_test_mapping_from_file(str(p))
        assert m["A.foo"]["test_cover_count"] == 2

    def test_legacy_usecase_count_format(self, fetch_mcp_data, tmp_path):
        data = {"A.foo": {"usecase_count": 3, "test_files": ["ut_a.cpp"]}}
        p = tmp_path / "m.json"
        p.write_text(json.dumps(data))
        m = fetch_mcp_data.load_test_mapping_from_file(str(p))
        assert m["A.foo"]["test_cover_count"] == 3

    def test_raw_list_format(self, fetch_mcp_data, tmp_path):
        data = {"A.foo": ["ut_a.cpp", "ut_b.cpp"]}
        p = tmp_path / "m.json"
        p.write_text(json.dumps(data))
        m = fetch_mcp_data.load_test_mapping_from_file(str(p))
        assert m["A.foo"]["test_cover_count"] == 2


# ── fetch 集成分支：非增量触发采集 ──────────────────────────────────────

class StubAdapter:
    """GitNexusAdapter 替身：采集方法全返空，调用记录在 self.calls。"""

    def __init__(self):
        self.calls = []

    def check_drift(self):
        return ("", "")

    def collect_methods(self, file_patterns=None, limit=2000):
        self.calls.append(("collect_methods", file_patterns, limit))
        return [], []

    def collect_inheritance(self):
        self.calls.append(("collect_inheritance",))
        return [], [], [], []

    def collect_dbus_slots(self, adaptors):
        self.calls.append(("collect_dbus_slots", len(adaptors)))
        return []

    def collect_qt_macros(self, file_patterns=None):
        self.calls.append(("collect_qt_macros", file_patterns))
        return {}, {}

    def discover_test_modules(self):
        self.calls.append(("discover_test_modules",))
        return []

    def collect_all_calls(self, modules):
        self.calls.append(("collect_all_calls", len(modules)))
        return {}

    def fetch_test_cases(self, modules):
        self.calls.append(("fetch_test_cases", len(modules)))
        return {}


class TestFetchIntegrationTestMapping:
    """验证 cmd_fetch 在非增量路径触发 test_* 采集，增量/skip 不触发。

    stub open_adapter 返回 StubAdapter，mock 掉 cmd_fetch 的 MCP 前置依赖，
    只断言 test_* 采集分支是否被触发。
    """

    def _stub_fetch_deps(self, monkeypatch, fetch_mcp_data):
        """stub open_adapter / resolve_base_sha，返回可检查调用的 StubAdapter。"""
        stub = StubAdapter()
        monkeypatch.setattr(fetch_mcp_data, "open_adapter", lambda *a, **kw: stub)
        monkeypatch.setattr(fetch_mcp_data, "resolve_base_sha",
                            lambda c, p, e=None: "stubsha")
        return stub

    def _make_fetch_args(self, tmp_path, incremental=False, skip_tm=False,
                         existing=None):
        """构造 cmd_fetch 的 args namespace。"""
        out = tmp_path / ".ut-inventory.json"
        return types.SimpleNamespace(
            cmd="fetch",
            project="stub-proj",
            file_pattern=None,
            output=str(out),
            mcp_url="http://stub",
            repo_root=None,
            limit=2000,
            base_sha=None,
            summary=False,
            keep_dump=False,
            incremental=incremental,
            existing=str(existing) if existing else None,
            skip_test_mapping=skip_tm,
        )

    def test_non_incremental_triggers_collection(self, monkeypatch, fetch_mcp_data, tmp_path):
        """非增量 fetch 必须触发 adapter 的 test_* 采集三连。"""
        stub = self._stub_fetch_deps(monkeypatch, fetch_mcp_data)

        def fake_discover():
            stub.calls.append(("discover_test_modules",))
            return [{"name": "ut_a.cpp", "file_path": "tests/ut_a.cpp",
                     "out_degree": 1}]

        stub.discover_test_modules = fake_discover
        args = self._make_fetch_args(tmp_path, incremental=False)
        rc = fetch_mcp_data.cmd_fetch(args)
        assert rc == 0
        names = [c[0] for c in stub.calls]
        assert "discover_test_modules" in names
        assert "collect_all_calls" in names
        assert "fetch_test_cases" in names

    def test_skip_test_mapping_skips_collection(self, monkeypatch, fetch_mcp_data, tmp_path):
        """--skip-test-mapping 时不触发 test_* 采集。"""
        stub = self._stub_fetch_deps(monkeypatch, fetch_mcp_data)
        args = self._make_fetch_args(tmp_path, incremental=False, skip_tm=True)
        fetch_mcp_data.cmd_fetch(args)
        assert all(c[0] != "discover_test_modules" for c in stub.calls)

    def test_incremental_skips_collection(self, monkeypatch, fetch_mcp_data, tmp_path):
        """增量模式靠 overlay 保留 test_*，不重新采集。"""
        stub = self._stub_fetch_deps(monkeypatch, fetch_mcp_data)
        # 增量：stub build_inventory 产出含 "A" 的 inventory，让 overlay 能贴回
        monkeypatch.setattr(fetch_mcp_data, "build_mcp_dump", lambda *a, **kw: {})
        monkeypatch.setattr(fetch_mcp_data, "build_inventory",
            lambda dump, proj, sha, **kw: {
                "project": proj, "base_sha": sha,
                "methods": [{"qualified_name": "A", "level": "high", "source": "auto",
                             "usecase_count": 0, "testable": True, "name": "A",
                             "signature": "()", "factors": [], "file_path": "a.cpp",
                             "access": "public", "score": 1, "node_type": "Method"}],
                "scan_stats": {"testable": 1, "non_testable": 0, "review_pending": 0,
                               "high": 1, "mid": 0, "low": 0,
                               "usecase_covered": 0, "usecase_not_covered": 1},
                "review_queue": [], "gate_thresholds": {}, "file_overrides": {},
            })
        # 增量需要 --existing 指向旧 inventory（含 test_* 的人工标记）
        old_inv = tmp_path / "old.json"
        old_inv.write_text(json.dumps({
            "project": "stub-proj", "base_sha": "oldsha",
            "methods": [{"qualified_name": "A", "level": "high", "source": "manual",
                         "usecase_count": 2, "test_cover_count": 1,
                         "test_files": ["ut_a.cpp"], "test_cases": [], "test_source": "mcp_calls"}],
            "review_queue": [], "gate_thresholds": {},
        }))
        args = self._make_fetch_args(tmp_path, incremental=True, existing=old_inv)
        fetch_mcp_data.cmd_fetch(args)
        # 增量路径不触发 test_* 采集
        assert all(c[0] != "discover_test_modules" for c in stub.calls)
        # 但旧 test_* 通过 overlay 保留
        out = json.loads((tmp_path / ".ut-inventory.json").read_text())
        m = out["methods"][0]
        assert m["test_cover_count"] == 1
        assert m["test_files"] == ["ut_a.cpp"]

    def test_collection_failure_does_not_block_fetch(self, monkeypatch, fetch_mcp_data, tmp_path):
        """test_* 采集抛异常不阻断主流程（inventory 仍写出，仅缺 test_*）。"""
        stub = self._stub_fetch_deps(monkeypatch, fetch_mcp_data)

        def boom():
            raise RuntimeError("MCP boom")

        stub.discover_test_modules = boom
        args = self._make_fetch_args(tmp_path, incremental=False)
        rc = fetch_mcp_data.cmd_fetch(args)
        # 不抛异常，inventory 仍写出
        assert rc == 0
        out = json.loads((tmp_path / ".ut-inventory.json").read_text())
        assert "methods" in out


# ── cmd_test_mapping（独立子命令） ──────────────────────────────────────

class TestCmdTestMapping:
    def test_mapping_in_writes_cover_fields(self, fetch_mcp_data, tmp_path):
        """--mapping-in 跳过 MCP，从文件加载映射直接回写 inventory。"""
        inv = tmp_path / ".ut-inventory.json"
        inv.write_text(json.dumps({
            "project": "stub", "methods": [
                {"qualified_name": "A.foo", "usecase_count": 0},
            ],
        }))
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({
            "A.foo": {"test_cover_count": 1, "test_files": ["ut_a.cpp"],
                      "test_cases": ["S.C1"]},
        }))
        args = types.SimpleNamespace(
            project=None, inventory=str(inv),
            mapping_in=str(mapping_file), mapping_out=None,
            report=None, mcp_url="http://stub",
            dry_run=False, verbose=False,
        )
        rc = fetch_mcp_data.cmd_test_mapping(args)
        assert rc == 0
        out = json.loads(inv.read_text())
        assert out["methods"][0]["test_cover_count"] == 1
        assert out["methods"][0]["test_source"] == "mcp_calls"

    def test_dry_run_not_written(self, fetch_mcp_data, tmp_path):
        inv = tmp_path / ".ut-inventory.json"
        original = {"project": "stub", "methods": [{"qualified_name": "A", "usecase_count": 0}]}
        inv.write_text(json.dumps(original))
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"A": {"test_cover_count": 1, "test_files": ["ut_a.cpp"], "test_cases": []}}))
        args = types.SimpleNamespace(
            project=None, inventory=str(inv),
            mapping_in=str(mapping_file), mapping_out=None,
            report=None, mcp_url="http://stub",
            dry_run=True, verbose=False,
        )
        fetch_mcp_data.cmd_test_mapping(args)
        # dry-run：inventory 未改
        assert json.loads(inv.read_text()) == original

    def test_missing_project_and_mapping_in_errors(self, fetch_mcp_data, tmp_path):
        inv = tmp_path / ".ut-inventory.json"
        inv.write_text(json.dumps({"project": "", "methods": []}))
        args = types.SimpleNamespace(
            project=None, inventory=str(inv),
            mapping_in=None, mapping_out=None,
            report=None, mcp_url="http://stub",
            dry_run=False, verbose=False,
        )
        rc = fetch_mcp_data.cmd_test_mapping(args)
        assert rc == 1  # 必须指定 --project 或 --mapping-in

    def test_nonexistent_inventory_errors(self, fetch_mcp_data, tmp_path):
        args = types.SimpleNamespace(
            project="stub", inventory=str(tmp_path / "nope.json"),
            mapping_in=None, mapping_out=None,
            report=None, mcp_url="http://stub",
            dry_run=False, verbose=False,
        )
        rc = fetch_mcp_data.cmd_test_mapping(args)
        assert rc == 1


# ── dispatch 注册 ─────────────────────────────────────────────────────

class TestDispatchRegistration:
    def test_test_mapping_in_dispatch(self, fetch_mcp_data):
        """main_no_exit 的 dispatch 表必须含 test-mapping → cmd_test_mapping。"""
        # 间接验证：build_parser 注册了子命令
        parser = fetch_mcp_data.build_parser()
        # 解析 test-mapping --help 不抛异常即证明子命令注册成功
        args = parser.parse_args(["test-mapping", "--inventory", "x.json"])
        assert args.cmd == "test-mapping"
        assert args.inventory == "x.json"
