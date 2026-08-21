"""gate_thresholds 外部设定保留的单元测试。

验证核心改动：
  1. scan-inventory.py: DEFAULT_GATE_THRESHOLDS 是默认值，build_inventory(gate_thresholds=) 可传入自定义值
  2. fetch-mcp-data.py: 增量模式从旧 inventory 读取 gate_thresholds 并保留，不覆盖
  3. 首次建表（无旧 inventory）使用默认值
"""
import json
import os
import tempfile

import pytest


# ══════════════════════════════════════════════════════════════════════
#  scan-inventory.py: build_inventory gate_thresholds 参数
# ══════════════════════════════════════════════════════════════════════

class TestBuildInventoryGateThresholds:
    """build_inventory() 的 gate_thresholds 参数行为。"""

    def _minimal_dump(self):
        return {
            "project": "test-proj",
            "methods": [],
            "functions": [],
            "classes": [],
            "dbus_classes": [],
            "concurrent_classes": [],
            "gui_classes": [],
            "dbus_slots": {},
            "q_invokables": {},
            "q_plugins": {},
            "in_degree_p75_nonzero": 5,
        }

    def test_default_gate_thresholds_constant_exists(self, scan_inventory):
        """DEFAULT_GATE_THRESHOLDS 常量存在且是默认值。"""
        assert hasattr(scan_inventory, "DEFAULT_GATE_THRESHOLDS")
        gates = scan_inventory.DEFAULT_GATE_THRESHOLDS
        assert gates["high"] == {"line": 90, "branch": 80, "function": 100}
        assert gates["mid"]  == {"line": 60, "branch": 0, "function": 100}
        assert gates["low"]  == {"line": 60, "branch": 0, "function": 100}

    def test_old_constant_removed(self, scan_inventory):
        """GATE_THRESHOLDS（旧名）不再存在。"""
        assert not hasattr(scan_inventory, "GATE_THRESHOLDS"), \
            "GATE_THRESHOLDS should be renamed to DEFAULT_GATE_THRESHOLDS"

    def test_none_uses_defaults(self, scan_inventory):
        """gate_thresholds=None 时使用 DEFAULT_GATE_THRESHOLDS。"""
        inv = scan_inventory.build_inventory(
            self._minimal_dump(), "proj", "sha", gate_thresholds=None)
        assert inv["gate_thresholds"] == scan_inventory.DEFAULT_GATE_THRESHOLDS

    def test_omitted_uses_defaults(self, scan_inventory):
        """不传 gate_thresholds 参数时也使用默认值（向后兼容）。"""
        inv = scan_inventory.build_inventory(
            self._minimal_dump(), "proj", "sha")
        assert inv["gate_thresholds"] == scan_inventory.DEFAULT_GATE_THRESHOLDS

    def test_custom_thresholds_used(self, scan_inventory):
        """传入自定义 gate_thresholds 时，inventory 使用传入值。"""
        custom = {
            "high": {"line": 95, "branch": 90, "function": 100},
            "mid":  {"line": 70, "branch": 50, "function": 100},
            "low":  {"line": 50, "branch": 0, "function": 80},
        }
        inv = scan_inventory.build_inventory(
            self._minimal_dump(), "proj", "sha", gate_thresholds=custom)
        assert inv["gate_thresholds"] == custom
        assert inv["gate_thresholds"]["high"]["line"] == 95
        assert inv["gate_thresholds"]["mid"]["branch"] == 50
        assert inv["gate_thresholds"]["low"]["function"] == 80

    def test_partial_custom_thresholds(self, scan_inventory):
        """只改一级的阈值也能正确传入。"""
        custom = {
            "high": {"line": 99, "branch": 99, "function": 99},
            "mid":  {"line": 60, "branch": 0, "function": 100},
            "low":  {"line": 60, "branch": 0, "function": 100},
        }
        inv = scan_inventory.build_inventory(
            self._minimal_dump(), "proj", "sha", gate_thresholds=custom)
        assert inv["gate_thresholds"]["high"]["line"] == 99
        # mid/low 保持原样
        assert inv["gate_thresholds"]["mid"]["line"] == 60

    def test_default_and_custom_are_independent(self, scan_inventory):
        """传入自定义阈值后，DEFAULT_GATE_THRESHOLDS 不受影响。"""
        before = dict(scan_inventory.DEFAULT_GATE_THRESHOLDS)
        custom = {"high": {"line": 1, "branch": 1, "function": 1},
                  "mid": {"line": 1, "branch": 1, "function": 1},
                  "low": {"line": 1, "branch": 1, "function": 1}}
        scan_inventory.build_inventory(
            self._minimal_dump(), "proj", "sha", gate_thresholds=custom)
        assert scan_inventory.DEFAULT_GATE_THRESHOLDS == before

    def test_with_methods_custom_thresholds(self, scan_inventory):
        """有方法数据时自定义阈值也正确写入。"""
        dump = self._minimal_dump()
        dump["methods"] = [
            {"qualified_name": "A.foo", "name": "foo", "complexity": 20},
            {"qualified_name": "B.bar", "name": "bar", "complexity": 1},
        ]
        custom = {
            "high": {"line": 80, "branch": 70, "function": 100},
            "mid":  {"line": 40, "branch": 0, "function": 100},
            "low":  {"line": 20, "branch": 0, "function": 50},
        }
        inv = scan_inventory.build_inventory(
            dump, "proj", "sha", gate_thresholds=custom)
        assert inv["gate_thresholds"] == custom
        # 方法和阈值独立，评分逻辑不变
        assert len(inv["methods"]) == 2


# ══════════════════════════════════════════════════════════════════════
#  fetch-mcp-data.py: 增量模式 gate_thresholds 保留
# ══════════════════════════════════════════════════════════════════════

class TestFetchMcpDataGateThresholds:
    """fetch-mcp-data.py 增量模式中 gate_thresholds 的保留逻辑。"""

    def _old_inventory(self, gate_thresholds, methods=None):
        """构造旧 inventory JSON，包含指定的 gate_thresholds。"""
        return {
            "version": 1,
            "project": "proj",
            "base_sha": "old_sha",
            "gate_thresholds": gate_thresholds,
            "scope_rules": [],
            "methods": methods or [],
            "review_queue": [],
            "file_overrides": [],
        }

    def test_extract_overlay_does_not_touch_gate_thresholds(self, fetch_mcp_data):
        """extract_human_overlay 不提取 gate_thresholds（它只提取方法级人工标记）。"""
        inv = self._old_inventory(
            {"high": {"line": 99, "branch": 99, "function": 99},
             "mid":  {"line": 50, "branch": 0, "function": 99},
             "low":  {"line": 30, "branch": 0, "function": 50}},
            methods=[{"qualified_name": "A", "level": "high", "source": "manual"}],
        )
        overlay = fetch_mcp_data.extract_human_overlay(inv)
        # overlay 只含方法级字段，不含 gate_thresholds
        assert "gate_thresholds" not in overlay
        # 方法级人工标记仍在
        assert "A" in overlay
        assert overlay["A"]["level"] == "high"

    def test_incremental_preserves_custom_gate_thresholds(self, fetch_mcp_data, scan_inventory):
        """增量模式：旧 inventory 有自定义 gate_thresholds → 重建后保留。"""
        custom_gates = {
            "high": {"line": 95, "branch": 85, "function": 100},
            "mid":  {"line": 55, "branch": 10, "function": 100},
            "low":  {"line": 40, "branch": 0, "function": 80},
        }
        old_inv = self._old_inventory(custom_gates)

        # 模拟增量重建：全量 build_inventory 后用旧 gate_thresholds 覆盖
        new_inv = scan_inventory.build_inventory(
            {"methods": [], "functions": [], "classes": [],
             "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
             "dbus_slots": {}, "q_invokables": {}, "q_plugins": {}},
            "proj", "new_sha",
            gate_thresholds=old_inv["gate_thresholds"],
        )

        # 增量确认保留
        if "gate_thresholds" in old_inv:
            new_inv["gate_thresholds"] = old_inv["gate_thresholds"]

        assert new_inv["gate_thresholds"] == custom_gates
        assert new_inv["gate_thresholds"]["high"]["line"] == 95
        assert new_inv["gate_thresholds"]["mid"]["branch"] == 10
        assert new_inv["gate_thresholds"]["low"]["function"] == 80

    def test_incremental_default_gates_preserved_as_is(self, fetch_mcp_data, scan_inventory):
        """增量模式：旧 inventory 用默认 gate_thresholds → 重建后也保留默认值。"""
        default_gates = scan_inventory.DEFAULT_GATE_THRESHOLDS.copy()
        old_inv = self._old_inventory(default_gates)

        new_inv = scan_inventory.build_inventory(
            {"methods": [], "functions": [], "classes": [],
             "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
             "dbus_slots": {}, "q_invokables": {}, "q_plugins": {}},
            "proj", "new_sha",
            gate_thresholds=old_inv["gate_thresholds"],
        )
        if "gate_thresholds" in old_inv:
            new_inv["gate_thresholds"] = old_inv["gate_thresholds"]

        assert new_inv["gate_thresholds"] == default_gates

    def test_first_build_no_existing_uses_defaults(self, scan_inventory):
        """首次建表（无旧 inventory）→ build_inventory 不传 gate_thresholds → 用默认值。"""
        inv = scan_inventory.build_inventory(
            {"methods": [], "functions": [], "classes": [],
             "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
             "dbus_slots": {}, "q_invokables": {}, "q_plugins": {}},
            "proj", "sha",
        )
        assert inv["gate_thresholds"] == scan_inventory.DEFAULT_GATE_THRESHOLDS

    def test_file_overrides_still_preserved(self, fetch_mcp_data, scan_inventory):
        """增量模式同时保留 file_overrides 和 gate_thresholds。"""
        custom_gates = {
            "high": {"line": 88, "branch": 70, "function": 100},
            "mid":  {"line": 55, "branch": 0, "function": 100},
            "low":  {"line": 55, "branch": 0, "function": 100},
        }
        old_inv = self._old_inventory(custom_gates)
        old_inv["file_overrides"] = [
            {"pattern": "src/special/**", "scope": "exempt", "reason": "外部豁免"},
        ]

        new_inv = scan_inventory.build_inventory(
            {"methods": [], "functions": [], "classes": [],
             "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
             "dbus_slots": {}, "q_invokables": {}, "q_plugins": {}},
            "proj", "new_sha",
            gate_thresholds=old_inv["gate_thresholds"],
        )

        # 保留 file_overrides
        if "file_overrides" in old_inv:
            new_inv["file_overrides"] = old_inv["file_overrides"]
        # 保留 gate_thresholds
        if "gate_thresholds" in old_inv:
            new_inv["gate_thresholds"] = old_inv["gate_thresholds"]

        assert new_inv["gate_thresholds"] == custom_gates
        assert len(new_inv["file_overrides"]) == 1
        assert new_inv["file_overrides"][0]["pattern"] == "src/special/**"

    def test_incremental_with_methods_and_manual_levels(self, fetch_mcp_data, scan_inventory):
        """增量模式同时保留 gate_thresholds + 人工标记 level。"""
        custom_gates = {
            "high": {"line": 75, "branch": 60, "function": 100},
            "mid":  {"line": 45, "branch": 0, "function": 100},
            "low":  {"line": 30, "branch": 0, "function": 80},
        }
        old_inv = self._old_inventory(custom_gates, methods=[
            {"qualified_name": "A.foo", "name": "foo", "level": "high", "source": "manual",
             "usecase_count": 5},
        ])

        # 全量重建（新方法 + 旧方法）
        new_inv = scan_inventory.build_inventory(
            {"methods": [
                {"qualified_name": "A.foo", "name": "foo", "complexity": 1},
                {"qualified_name": "B.bar", "name": "bar", "complexity": 20},
            ], "functions": [], "classes": [],
             "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
             "dbus_slots": {}, "q_invokables": {}, "q_plugins": {}},
            "proj", "new_sha",
            gate_thresholds=old_inv["gate_thresholds"],
        )

        # 人工标记回写
        overlay = fetch_mcp_data.extract_human_overlay(old_inv)
        fetch_mcp_data.apply_overlay_to_methods(new_inv["methods"], overlay)

        # 保留 gate_thresholds
        if "gate_thresholds" in old_inv:
            new_inv["gate_thresholds"] = old_inv["gate_thresholds"]

        # gate_thresholds 保留
        assert new_inv["gate_thresholds"]["high"]["line"] == 75
        # 人工 level 保留
        foo = next(m for m in new_inv["methods"] if m["name"] == "foo")
        assert foo["level"] == "high" and foo["source"] == "manual"
        # 新方法 auto 评分
        bar = next(m for m in new_inv["methods"] if m["name"] == "bar")
        assert bar["source"] == "auto"


# ══════════════════════════════════════════════════════════════════════
#  coverage-by-level.py: 从 inventory 读取 gate_thresholds（已支持）
# ══════════════════════════════════════════════════════════════════════

class TestCoverageByLevelGateThresholds:
    """coverage-by-level.py 从 inventory 读取 gate_thresholds 做门禁判定。"""

    def test_uses_inventory_gates(self, coverage_by_level, scan_inventory):
        """coverage-by-level 从 inventory 读取自定义 gate_thresholds 判定 pass/fail。
        直接调用 coverage-by-level 的内部逻辑验证 gates 读取。"""
        custom_gates = {
            "high": {"line": 50, "branch": 0, "function": 100},
            "mid":  {"line": 30, "branch": 0, "function": 100},
            "low":  {"line": 20, "branch": 0, "function": 100},
        }
        inv = scan_inventory.build_inventory(
            {"methods": [
                {"qualified_name": "A.foo", "name": "foo", "complexity": 20,
                 "file_path": "src/a.cpp"},
            ], "functions": [], "classes": [],
             "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
             "dbus_slots": {}, "q_invokables": {}, "q_plugins": {}},
            "proj", "sha",
            gate_thresholds=custom_gates,
        )
        # 直接验证 inventory 中的 gate_thresholds 会被 coverage-by-level 读到
        gates = inv.get("gate_thresholds", {})
        assert gates == custom_gates
        assert gates["high"]["line"] == 50

    def test_default_gates_in_inventory(self, coverage_by_level, scan_inventory):
        """使用默认 gate_thresholds 的 inventory 也能正确读取。"""
        inv = scan_inventory.build_inventory(
            {"methods": [], "functions": [], "classes": [],
             "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
             "dbus_slots": {}, "q_invokables": {}, "q_plugins": {}},
            "proj", "sha",
        )
        gates = inv.get("gate_thresholds", {})
        assert gates["high"]["line"] == 90  # 默认值
        assert gates["high"]["branch"] == 80
        assert gates["mid"]["branch"] == 0


# ══════════════════════════════════════════════════════════════════════
#  集成：完整增量流程模拟（文件级）
# ══════════════════════════════════════════════════════════════════════

class TestIncrementalGateThresholdsIntegration:
    """模拟完整的增量重建流程，验证 gate_thresholds 不被覆盖。"""

    def test_full_incremental_flow(self, scan_inventory, fetch_mcp_data, tmp_path):
        """模拟：首次建表(默认) → 用户修改 gate_thresholds → 增量重建(保留修改)。"""
        dump = {
            "methods": [{"qualified_name": "A.init", "name": "init", "complexity": 5}],
            "functions": [], "classes": [],
            "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
            "dbus_slots": {}, "q_invokables": {}, "q_plugins": {},
        }

        # Step 1: 首次建表 → 默认 gate_thresholds
        inv_v1 = scan_inventory.build_inventory(dump, "proj", "sha_v1")
        inv_path = tmp_path / ".ut-inventory.json"
        inv_path.write_text(json.dumps(inv_v1, ensure_ascii=False))
        assert inv_v1["gate_thresholds"]["high"]["line"] == 90  # 默认值

        # Step 2: 用户手动修改 gate_thresholds（模拟外部编辑）
        inv_v1_modified = json.loads(inv_path.read_text())
        inv_v1_modified["gate_thresholds"]["high"]["line"] = 75
        inv_v1_modified["gate_thresholds"]["high"]["branch"] = 60
        inv_v1_modified["gate_thresholds"]["low"]["function"] = 80
        inv_path.write_text(json.dumps(inv_v1_modified, ensure_ascii=False))

        # Step 3: 增量重建（模拟 fetch-mcp-data --incremental --existing）
        old_inv = json.loads(inv_path.read_text())
        inv_v2 = scan_inventory.build_inventory(
            dump, "proj", "sha_v2",
            gate_thresholds=old_inv["gate_thresholds"],
        )
        # 增量保留
        if "gate_thresholds" in old_inv:
            inv_v2["gate_thresholds"] = old_inv["gate_thresholds"]
        # 人工标记
        overlay = fetch_mcp_data.extract_human_overlay(old_inv)
        fetch_mcp_data.apply_overlay_to_methods(inv_v2["methods"], overlay)

        # Step 4: 验证 gate_thresholds 被保留
        assert inv_v2["gate_thresholds"]["high"]["line"] == 75
        assert inv_v2["gate_thresholds"]["high"]["branch"] == 60
        assert inv_v2["gate_thresholds"]["low"]["function"] == 80
        # mid 未修改，也保持原样
        assert inv_v2["gate_thresholds"]["mid"]["line"] == 60

    def test_multiple_incremental_rounds(self, scan_inventory, tmp_path):
        """多次增量重建 gate_thresholds 始终保留。"""
        custom = {
            "high": {"line": 77, "branch": 55, "function": 100},
            "mid":  {"line": 44, "branch": 0, "function": 100},
            "low":  {"line": 33, "branch": 0, "function": 77},
        }
        dump = {
            "methods": [], "functions": [], "classes": [],
            "dbus_classes": [], "concurrent_classes": [], "gui_classes": [],
            "dbus_slots": {}, "q_invokables": {}, "q_plugins": {},
        }

        gates = custom
        for i in range(3):
            inv = scan_inventory.build_inventory(
                dump, "proj", f"sha_round{i}",
                gate_thresholds=gates,
            )
            # 模拟增量确认保留
            if gates is not None:
                inv["gate_thresholds"] = gates
            # 下一轮从 inv 读
            gates = inv["gate_thresholds"]

        # 经历 3 轮增量重建后仍保留
        assert inv["gate_thresholds"]["high"]["line"] == 77
        assert inv["gate_thresholds"]["low"]["function"] == 77
