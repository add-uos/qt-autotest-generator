"""跨脚本 .ut-inventory.json CRUD 契约测试。

数据流核心不变量：所有脚本围绕 .ut-inventory.json 读写，任一环 schema 变更
必须在这里显式确认。链路：

  CREATE   mcp-scan build_inventory（图谱 → 建表，usecase_count=0 初始）
  UPDATE-A fetch-test-mapping update_inventory（Mode 1 fetch 采集 / 独立 test-mapping 回写 test_*）
  UPDATE-B mode2-ops usecase（Mode 2 写完测试 → 回写 usecase_count）
  UPDATE-C stale-test-cleanup（清理 stale 测试 → usecase_count + test_* 同步）
  READ     mode2-ops plan / utq / coverage-report

每个契约测试验证相邻两阶段的字段衔接；全链路测试验证端到端不变量：
"清理后方法回到 todo / 写完测试立即离开 todo（双信号防重复写）"。
"""
import json

import pytest


# ── 共享构造 helper ──────────────────────────────────────────────────

def _dump_method(name="add", cls="proj.src.Calc", file="src/calc.cpp"):
    """构造 MCP dump 中的方法条目（fetch 采集的原始形态）。

    注意：build_inventory 从 parent_class 推导 class_qn（取短名），
    dump 条目自身的 class_qn 字段不消费。
    """
    return {
        "qualified_name": f"{cls}.{name}",
        "name": name,
        "parent_class": cls,
        "file_path": file,
        "access": "public",
        "signature": f"int {name}(int, int)",
    }


def _minimal_dump(methods):
    """最小 mcp_dump（build_inventory 的输入）。"""
    return {
        "project": "proj",
        "methods": methods,
        "functions": [],
        "classes": [],
        "dbus_classes": [],
        "dbus_interface_classes": [],
        "concurrent_classes": [],
        "gui_classes": [],
        "dbus_slots": [],
        "dbus_signals": {},
        "q_invokables": [],
        "q_plugins": [],
        "in_degree_p75_nonzero": 0,
    }


def _write_inv(path, inv):
    path.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")
    return path


TEST_CPP = """\
#include <gtest/gtest.h>

TEST_F(CalcTest, Add_Normal) { EXPECT_EQ(add(1, 2), 3); }
TEST_F(CalcTest, Add_Edge) { EXPECT_EQ(add(-1, -1), -2); }
TEST_F(CalcTest, Clear_All) { clear(); }
"""


# ── CREATE → READ：建表产出可直接被消费方读取 ────────────────────────

class TestCreateToRead:
    def test_plan_consumes_build_inventory_output(self, scan_inventory,
                                                  plan_test_classes):
        """CREATE 产出 → mode2-ops build_plan 直接消费（无中间转换）。"""
        inv = scan_inventory.build_inventory(_minimal_dump(
            [_dump_method("add"), _dump_method("clear")]), "proj", "sha1")
        classes, free = plan_test_classes.build_plan(inv)
        assert len(classes) == 1
        assert classes[0]["name"] == "Calc"
        assert classes[0]["method_count"] == 2

    def test_create_initializes_usecase_count(self, scan_inventory):
        """CREATE 必须初始化 usecase_count=0（utq 双信号依赖字段存在）。"""
        inv = scan_inventory.build_inventory(
            _minimal_dump([_dump_method("add")]), "proj", "sha1")
        m = inv["methods"][0]
        assert m["usecase_count"] == 0
        assert m.get("test_cover_count", 0) == 0

    def test_schema_contract_fields(self, scan_inventory):
        """CREATE 产出字段集 ⊇ READ 方（plan/utq）依赖的字段集。"""
        required_by_readers = {
            "qualified_name", "name", "class_qn", "file_path",
            "level", "testable", "usecase_count", "score",
        }
        inv = scan_inventory.build_inventory(
            _minimal_dump([_dump_method("add")]), "proj", "sha1")
        for m in inv["methods"]:
            missing = required_by_readers - set(m)
            assert not missing, f"CREATE 输出缺 READ 依赖字段: {missing}"


# ── UPDATE-A：Mode 1 fetch / test-mapping 回写 test_* 字段 ──────────────

class TestExternalMappingUpdate:
    def test_writes_test_fields(self, fetch_test_mapping):
        inv = {"methods": [
            {"qualified_name": "proj.src.Calc.add", "usecase_count": 0},
        ]}
        mapping = fetch_test_mapping.build_mapping(
            {"proj.src.Calc.add": {"autotests/test_calc.cpp"}},
            {"autotests/test_calc.cpp": ["Add_Normal", "Add_Edge"]})
        updated, unmatched, _ = fetch_test_mapping.update_inventory(inv, mapping)
        m = inv["methods"][0]
        assert updated == 1 and unmatched == 0
        assert m["test_cover_count"] == 1
        assert m["test_files"] == ["autotests/test_calc.cpp"]
        assert m["test_cases"] == ["Add_Normal", "Add_Edge"]
        assert m["test_source"] == "mcp_calls"

    def test_usecase_takes_max(self, fetch_test_mapping):
        """已有 Mode 2 精确计数保留，否则用覆盖文件数作下界。"""
        inv = {"methods": [
            {"qualified_name": "proj.src.Calc.add", "usecase_count": 5},
        ]}
        mapping = fetch_test_mapping.build_mapping(
            {"proj.src.Calc.add": {"a.cpp", "b.cpp"}})
        fetch_test_mapping.update_inventory(inv, mapping)
        assert inv["methods"][0]["usecase_count"] == 5  # max(5, 2)

    def test_unmatched_preserved(self, fetch_test_mapping):
        inv = {"methods": [
            {"qualified_name": "proj.src.Calc.add", "usecase_count": 3},
        ]}
        mapping = fetch_test_mapping.build_mapping({"proj.other.foo": {"a.cpp"}})
        updated, unmatched, _ = fetch_test_mapping.update_inventory(inv, mapping)
        m = inv["methods"][0]
        assert updated == 0 and unmatched == 1
        assert m["usecase_count"] == 3
        assert "test_cover_count" not in m

    def test_normalize_qn_bridge(self, fetch_test_mapping):
        """两侧 qn 归一化后能对上：inventory 带仓库前缀、dump 不带。

        update_inventory 对 method qn 和 mapping key 都过 normalize_qn，
        必须保证两侧不同形态归一化到同一值才能匹配成功。
        """
        from_inventory = ("home-uos-service-codebase-repos-"
                          "deepin-image-viewer.src.Calc.add")
        from_dump = "deepin-image-viewer.src.Calc.add"
        assert fetch_test_mapping.normalize_qn(from_inventory) == from_dump
        # 幂等：已归一化的不再变化
        assert fetch_test_mapping.normalize_qn(from_dump) == from_dump


# ── UPDATE-A → READ：外部回写后查询工具正确识别 ───────────────────────

class TestUpdateAToRead:
    def test_utq_reads_external_fields(self, fetch_test_mapping, utq, tmp_path):
        inv = {"methods": [
            {"qualified_name": "proj.src.Calc.add", "name": "add",
             "class_qn": "proj.src.Calc", "file_path": "src/calc.cpp",
             "level": "high", "testable": True, "score": 3,
             "usecase_count": 0},
        ]}
        mapping = fetch_test_mapping.build_mapping(
            {"proj.src.Calc.add": {"autotests/test_calc.cpp"}})
        fetch_test_mapping.update_inventory(inv, mapping)
        _write_inv(tmp_path / ".ut-inventory.json", inv)
        q = utq.Inv(tmp_path)
        m = q.methods[0]
        assert q.is_covered(m) is True
        assert q.is_todo(m) is False


# ── UPDATE-B → READ：Mode 2 回写后双信号防重复写 ──────────────────────

class TestUpdateBToRead:
    def test_usecase_write_exits_todo(self, update_usecase_count, utq, tmp_path):
        """Mode 2 写完测试立即回写 usecase_count → utq todo 不再含该方法。

        防重复写核心场景：Mode 1 fetch 未采集 test_*（--skip-test-mapping / MCP 无 tests/ 索引）。
        """
        test_file = tmp_path / "test_calc.cpp"
        test_file.write_text(TEST_CPP, encoding="utf-8")
        inv_path = tmp_path / ".ut-inventory.json"
        _write_inv(inv_path, {"methods": [
            {"qualified_name": "proj.src.Calc.add", "name": "add",
             "class_qn": "proj.src.Calc", "file_path": "src/calc.cpp",
             "level": "high", "testable": True, "score": 3,
             "usecase_count": 0},
            {"qualified_name": "proj.src.Calc.clear", "name": "clear",
             "class_qn": "proj.src.Calc", "file_path": "src/calc.cpp",
             "level": "low", "testable": True, "score": 1,
             "usecase_count": 0},
        ]})
        rc = update_usecase_count.usecase_main_no_exit([
            "--test-file", str(test_file),
            "--inventory", str(inv_path),
            "--class", "Calc",
        ])
        assert rc == 0
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        by_name = {m["name"]: m for m in inv["methods"]}
        assert by_name["add"]["usecase_count"] == 2   # Add_Normal + Add_Edge
        assert by_name["clear"]["usecase_count"] == 1  # Clear_All
        q = utq.Inv(tmp_path)
        assert all(not q.is_todo(m) for m in q.methods)  # 双信号：全部已有测试


# ── UPDATE-C：清理后 usecase_count 与 test_* 字段同步（语义一致）──────

class TestUpdateCConsistency:
    def _setup(self, tmp_path):
        test_dir = tmp_path / "autotests"
        test_dir.mkdir()
        (test_dir / "test_calc.cpp").write_text(TEST_CPP, encoding="utf-8")
        inv = {"methods": [
            {"qualified_name": "proj.src.Calc.add", "name": "add",
             "class_qn": "proj.src.Calc", "file_path": "src/calc.cpp",
             "level": "high", "testable": True, "score": 3,
             "usecase_count": 2, "test_cover_count": 1,
             "test_files": ["autotests/test_calc.cpp"],
             "test_cases": ["Add_Normal", "Add_Edge"], "test_source": "mcp_calls"},
            {"qualified_name": "proj.src.Calc.clear", "name": "clear",
             "class_qn": "proj.src.Calc", "file_path": "src/calc.cpp",
             "level": "low", "testable": True, "score": 1,
             "usecase_count": 1, "test_cover_count": 1,
             "test_files": ["autotests/test_calc.cpp"],
             "test_cases": ["Clear_All"], "test_source": "mcp_calls"},
        ]}
        inv_path = tmp_path / ".ut-inventory.json"
        _write_inv(inv_path, inv)
        return test_dir, inv_path

    def test_cleanup_resets_all_coverage_fields(self, stale_test_cleanup,
                                                utq, tmp_path):
        """源码删除 add → 清理其用例 → 三个字段同步归零/清空。

        修复前：只重置 usecase_count，test_cover_count 留旧值 → utq 仍显示
        "已覆盖"，语义不一致。
        """
        test_dir, inv_path = self._setup(tmp_path)
        report = stale_test_cleanup.cleanup_removed_methods(
            str(test_dir), str(inv_path),
            [{"name": "add", "class_qn": "proj.src.Calc"}])
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        by_name = {m["name"]: m for m in inv["methods"]}
        gone = by_name["add"]
        assert gone["usecase_count"] == 0
        assert gone["test_cover_count"] == 0
        assert gone["test_files"] == []
        assert gone["test_cases"] == []
        assert "test_source" not in gone
        # 未删方法不受影响
        keep = by_name["clear"]
        assert keep["usecase_count"] == 1
        assert keep["test_cover_count"] == 1
        # 端到端：清理后 utq 判定回到 todo（可重新写测试）
        q = utq.Inv(tmp_path)
        add_m = next(m for m in q.methods if m["name"] == "add")
        clear_m = next(m for m in q.methods if m["name"] == "clear")
        assert q.is_todo(add_m) is True
        assert q.is_todo(clear_m) is False
        assert report["cleaned_cases"] == 2  # Add_Normal + Add_Edge

    def test_cleanup_keeps_shared_file_coverage(self, stale_test_cleanup,
                                                tmp_path):
        """方法被多个测试文件覆盖，清理其一 → 只移除该文件，计数重算。"""
        test_dir, inv_path = self._setup(tmp_path)
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        add_m = next(m for m in inv["methods"] if m["name"] == "add")
        add_m["test_files"] = ["autotests/test_calc.cpp",
                               "autotests/test_extra.cpp"]
        add_m["test_cover_count"] = 2
        _write_inv(inv_path, inv)
        stale_test_cleanup.cleanup_removed_methods(
            str(test_dir), str(inv_path),
            [{"name": "add", "class_qn": "proj.src.Calc"}])
        inv2 = json.loads(inv_path.read_text(encoding="utf-8"))
        gone = next(m for m in inv2["methods"] if m["name"] == "add")
        assert gone["test_files"] == ["autotests/test_extra.cpp"]
        assert gone["test_cover_count"] == 1


# ── 全链路：CREATE → UPDATE-A/B → READ → UPDATE-C → READ ─────────────

class TestFullPipeline:
    def test_end_to_end(self, scan_inventory, fetch_test_mapping,
                        update_usecase_count, stale_test_cleanup, utq,
                        tmp_path):
        # CREATE：建表
        inv = scan_inventory.build_inventory(
            _minimal_dump([_dump_method("add")]), "proj", "sha1")
        inv_path = _write_inv(tmp_path / ".ut-inventory.json", inv)
        q = utq.Inv(tmp_path)
        assert q.is_todo(q.methods[0]) is True  # 初始 todo

        # UPDATE-B：Mode 2 写完测试回写（外部工具未跑）
        test_file = tmp_path / "test_calc.cpp"
        test_file.write_text(TEST_CPP, encoding="utf-8")
        rc = update_usecase_count.usecase_main_no_exit([
            "--test-file", str(test_file), "--inventory", str(inv_path),
            "--class", "Calc"])
        assert rc == 0
        q = utq.Inv(tmp_path)
        assert q.is_todo(q.methods[0]) is False  # 双信号离开 todo

        # UPDATE-A：外部工具随后回写 test_* 字段（回写 inv_path，使 UPDATE-C 能读到）
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        assert inv["methods"][0]["usecase_count"] == 2  # Mode 2 精确计数不被下界覆盖
        mapping = fetch_test_mapping.build_mapping(
            {"proj.src.Calc.add": {str(test_file)}})
        fetch_test_mapping.update_inventory(inv, mapping)
        assert inv["methods"][0]["test_cover_count"] == 1
        assert inv["methods"][0]["usecase_count"] == 2  # max(2, 1)
        _write_inv(inv_path, inv)  # 回写：UPDATE-C 必须读到 test_* 才能验证 A→C 衔接

        # UPDATE-C：源码删除 → 清理 → test_* 同步归零 → 回到 todo
        report = stale_test_cleanup.cleanup_removed_methods(
            str(tmp_path), str(inv_path),
            [{"name": "add", "class_qn": "proj.src.Calc"}])
        assert report["cleaned_cases"] == 2
        cleaned = json.loads(inv_path.read_text(encoding="utf-8"))["methods"][0]
        # A→C 衔接验证：UPDATE-A 写入的 test_* 被 UPDATE-C 同步清理
        assert cleaned["test_cover_count"] == 0
        assert cleaned["test_files"] == []
        assert cleaned["usecase_count"] == 0
        q = utq.Inv(tmp_path)
        assert q.is_todo(q.methods[0]) is True


# ── reconcile 增量重建保留外部 test_* 字段（防回归）────────────────

class TestReconcilePreservesExternalFields:
    """reconcile（git HEAD 漂移 → mcp-scan fetch --incremental）不得清空外部
    fetch-test-mapping 采集的 test_* 覆盖字段。回归守护：修复前
    extract_human_overlay 白名单不含 test_*，全量重建后整组丢失。
    """

    def test_reconcile_preserves_test_fields(self, scan_inventory,
                                             fetch_test_mapping, utq, tmp_path):
        # CREATE 建表
        dump = _minimal_dump([_dump_method("add")])
        inv_v1 = scan_inventory.build_inventory(dump, "proj", "sha_v1")
        inv_path = _write_inv(tmp_path / ".ut-inventory.json", inv_v1)

        # UPDATE-B：Mode 2 写完回写 usecase_count
        inv_v1["methods"][0]["usecase_count"] = 2
        # UPDATE-A：Mode 1 fetch / test-mapping 回写 test_* 字段
        mapping = fetch_test_mapping.build_mapping(
            {"proj.src.Calc.add": {"autotests/test_calc.cpp"}})
        fetch_test_mapping.update_inventory(inv_v1, mapping)
        _write_inv(inv_path, inv_v1)
        before = inv_v1["methods"][0]
        assert before["test_cover_count"] == 1
        assert before["test_files"] == ["autotests/test_calc.cpp"]
        assert before["usecase_count"] == 2  # max(2, 1) 保留精确计数

        # reconcile：HEAD 漂移 → 全量重建 + overlay 回写（模拟 --incremental --existing）
        inv_v2 = scan_inventory.build_inventory(dump, "proj", "sha_v2")
        overlay = scan_inventory.extract_human_overlay(inv_v1)
        scan_inventory.apply_overlay_to_methods(inv_v2["methods"], overlay)

        after = inv_v2["methods"][0]
        # usecase_count 保留
        assert after["usecase_count"] == 2
        # test_* 全部保留（修复前这里全变 None）
        assert after["test_cover_count"] == 1
        assert after["test_files"] == ["autotests/test_calc.cpp"]
        assert after["test_source"] == "mcp_calls"
        # 端到端：reconcile 后 utq 仍判已覆盖（双信号两条都在）
        _write_inv(inv_path, inv_v2)
        q = utq.Inv(tmp_path)
        assert q.is_covered(q.methods[0]) is True
        assert q.is_todo(q.methods[0]) is False

    def test_reconcile_preserves_cleaned_state(self, scan_inventory,
                                               fetch_test_mapping, utq, tmp_path):
        """stale-test-cleanup 归零的 test_* 不被 reconcile 翻案复活。"""
        dump = _minimal_dump([_dump_method("add")])
        inv_v1 = scan_inventory.build_inventory(dump, "proj", "sha_v1")
        inv_v1["methods"][0]["usecase_count"] = 2
        mapping = fetch_test_mapping.build_mapping(
            {"proj.src.Calc.add": {"autotests/test_calc.cpp"}})
        fetch_test_mapping.update_inventory(inv_v1, mapping)
        # stale-test-cleanup 清理后：test_* 归零/清空、test_source pop、usecase=0
        inv_v1["methods"][0].update({
            "usecase_count": 0, "test_cover_count": 0,
            "test_files": [], "test_cases": [],
        })
        inv_v1["methods"][0].pop("test_source", None)

        inv_v2 = scan_inventory.build_inventory(dump, "proj", "sha_v2")
        overlay = scan_inventory.extract_human_overlay(inv_v1)
        scan_inventory.apply_overlay_to_methods(inv_v2["methods"], overlay)
        after = inv_v2["methods"][0]
        # 清理结果保留：不复活旧 test_* 值
        assert after["usecase_count"] == 0
        assert after.get("test_cover_count", 0) == 0
        assert after.get("test_files", []) == []
        assert "test_source" not in after
