"""plan-test-classes.py 单元测试。

覆盖 test-writer.md §4 固化的数据变换：分组、类 level 取最高、排序、
is_gui 匹配、自由函数按文件归组、双 schema 字段兼容（schema 文档字段 vs
存量数据字段 qn/file/短名 class_qn——deepin-image-viewer 实测形态）。
端到端验证见 doc/mode2-script-offload-design.md §2.1。
"""
import json

import pytest


def _m(name, cls=None, level="low", testable=True, file="src/x.cpp",
       qn=None, node_type="Method", **extra):
    """构造一个方法条目（默认 schema 字段形态）。"""
    e = {
        "qualified_name": qn or f"proj.src.{cls}.{name}" if cls else f"proj.src.{name}",
        "name": name,
        "class_qn": cls,
        "file_path": file,
        "level": level,
        "testable": testable,
        "node_type": node_type,
    }
    e.update(extra)
    return e


# ── 分组与类 level ───────────────────────────────────────────────────

class TestGrouping:
    def test_group_by_class(self, plan_test_classes):
        inv = {"methods": [
            _m("a", cls="proj.src.A", level="mid"),
            _m("b", cls="proj.src.A", level="low"),
            _m("c", cls="proj.src.B", level="low"),
        ]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert len(classes) == 2
        a = next(c for c in classes if c["name"] == "A")
        assert a["method_count"] == 2
        assert [m["name"] for m in a["methods"]] == ["a", "b"]

    def test_class_level_is_max_of_methods(self, plan_test_classes):
        # 类 level = 其方法最高级：一个 high 方法就把类抬到 high
        inv = {"methods": [
            _m("critical", cls="proj.src.Mixed", level="high"),
            _m("trivial", cls="proj.src.Mixed", level="low"),
            _m("normal", cls="proj.src.OnlyLow", level="low"),
        ]}
        classes, _ = plan_test_classes.build_plan(inv)
        mixed = next(c for c in classes if c["name"] == "Mixed")
        assert mixed["level"] == "high"
        assert next(c for c in classes if c["name"] == "OnlyLow")["level"] == "low"

    def test_non_testable_filtered(self, plan_test_classes):
        inv = {"methods": [
            _m("a", cls="proj.src.A", level="high", testable=False),
            _m("b", cls="proj.src.A", level="low"),
        ]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert len(classes) == 1
        assert classes[0]["method_count"] == 1

    def test_exempt_level_null_not_testable(self, plan_test_classes):
        # scope=exempt → testable=false + level=null，不得进入任何组
        inv = {"methods": [_m("gen", cls=None, level=None, testable=False,
                              node_type="Function")]}
        classes, free = plan_test_classes.build_plan(inv)
        assert classes == [] and free == []

    def test_methods_sorted_by_level_desc_within_class(self, plan_test_classes):
        inv = {"methods": [
            _m("low1", cls="proj.src.C", level="low"),
            _m("high1", cls="proj.src.C", level="high"),
            _m("mid1", cls="proj.src.C", level="mid"),
        ]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert [m["level"] for m in classes[0]["methods"]] == ["high", "mid", "low"]

    def test_class_sort_high_mid_low(self, plan_test_classes):
        inv = {"methods": [
            _m("x", cls="proj.src.LowCls", level="low"),
            _m("x", cls="proj.src.HighCls", level="high"),
            _m("x", cls="proj.src.MidCls", level="mid"),
        ]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert [c["name"] for c in classes] == ["HighCls", "MidCls", "LowCls"]

    def test_same_level_stable_inventory_order(self, plan_test_classes):
        # 同级保持 inventory 出现顺序（稳定排序）
        inv = {"methods": [
            _m("x", cls="proj.src.Second", level="mid"),
            _m("x", cls="proj.src.First", level="mid"),
        ]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert [c["name"] for c in classes] == ["Second", "First"]

    def test_empty_inventory(self, plan_test_classes):
        classes, free = plan_test_classes.build_plan({"methods": []})
        assert classes == [] and free == []

    def test_missing_level_defaults_low(self, plan_test_classes):
        # testable 但 level 缺失/非法 → _level() 归一化返回 low，排序/类 level 正确
        inv = {"methods": [
            _m("weird", cls="proj.src.A", level=None),
            _m("odd", cls="proj.src.A", level="bogus"),
        ]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert classes[0]["level"] == "low"
        # _level 只影响排序和类 level 归算，方法 entry 的 level 字段保持原样
        assert any(m["level"] is None or m["level"] == "bogus" for m in classes[0]["methods"])


# ── 双 schema 字段兼容（实测存量数据形态） ──────────────────────────

class TestSchemaCompat:
    def test_legacy_qn_file_short_class_qn(self, plan_test_classes):
        # deepin-image-viewer 实测形态：qn/file/短 class_qn
        inv = {"methods": [{
            "qn": "home-uos-repos-div.src.dbus.ApplicationAdaptor.foo",
            "name": "foo",
            "class_qn": "ApplicationAdaptor",   # 短名
            "file": "src/dbus/applicationadpator.cpp",
            "level": "high",
            "testable": True,
            "node_type": "Method",
        }]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert len(classes) == 1
        c = classes[0]
        assert c["name"] == "ApplicationAdaptor"
        # 类全名从方法 qn 剥最后一节推导
        assert c["qualified_name"] == "home-uos-repos-div.src.dbus.ApplicationAdaptor"
        assert c["file_path"] == "src/dbus/applicationadpator.cpp"

    def test_full_class_qn_kept_as_is(self, plan_test_classes):
        inv = {"methods": [_m("foo", cls="proj.src.Calculator", level="mid")]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert classes[0]["qualified_name"] == "proj.src.Calculator"

    def test_short_class_qn_unstrippable_falls_back(self, plan_test_classes):
        # 短名 + 方法 qn 与方法名对不上 → 类全名退化为短名，不崩溃
        inv = {"methods": [{
            "qn": "some.weird.shape",
            "name": "foo",
            "class_qn": "Bar",
            "file": "src/bar.cpp",
            "level": "low",
            "testable": True,
        }]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert classes[0]["qualified_name"] == "Bar"


# ── is_gui 匹配 ──────────────────────────────────────────────────────

class TestGuiMatching:
    def test_gui_matched_by_short_name(self, plan_test_classes):
        # inventory-schema：Mode 2 用 classes[].name（短名）匹配，
        # methods[].class_qn 无论是短名还是全名都要能对上
        inv = {
            "classes": [{"name": "FileView", "is_gui": True}],
            "methods": [
                _m("x", cls="proj.src.FileView", level="mid"),
                _m("y", cls="CoreThing", level="mid"),
            ],
        }
        classes, _ = plan_test_classes.build_plan(inv)
        fv = next(c for c in classes if c["name"] == "FileView")
        ct = next(c for c in classes if c["name"] == "CoreThing")
        assert fv["is_gui"] is True and ct["is_gui"] is False

    def test_gui_matched_when_class_qn_already_short(self, plan_test_classes):
        inv = {
            "classes": [{"name": "FileView", "is_gui": True}],
            "methods": [_m("x", cls="FileView", level="mid")],
        }
        classes, _ = plan_test_classes.build_plan(inv)
        assert classes[0]["is_gui"] is True

    def test_empty_classes_no_gui(self, plan_test_classes):
        # 真实存量数据 classes 常为空 → 全部 is_gui=false，不崩溃
        inv = {"classes": [], "methods": [_m("x", cls="proj.src.A")]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert classes[0]["is_gui"] is False

    def test_non_gui_class_entries_ignored(self, plan_test_classes):
        inv = {
            "classes": [{"name": "Plain", "is_gui": False}],
            "methods": [_m("x", cls="proj.src.Plain")],
        }
        classes, _ = plan_test_classes.build_plan(inv)
        assert classes[0]["is_gui"] is False


# ── 自由函数归组 ─────────────────────────────────────────────────────

class TestFreeFunctions:
    def test_group_by_file(self, plan_test_classes):
        inv = {"methods": [
            _m("f1", cls=None, level="low", file="src/utils.cpp", node_type="Function"),
            _m("f2", cls=None, level="mid", file="src/utils.cpp", node_type="Function"),
            _m("g1", cls=None, level="low", file="src/io/helper.cpp", node_type="Function"),
        ]}
        _, free = plan_test_classes.build_plan(inv)
        assert len(free) == 2
        # module = 最后一段目录：src/utils.cpp → src; src/io/helper.cpp → io
        src_g = next(g for g in free if g["module"] == "src")
        assert src_g["function_count"] == 2
        io_g = next(g for g in free if g["module"] == "io")
        assert io_g["function_count"] == 1

    def test_functions_sorted_by_level_desc(self, plan_test_classes):
        inv = {"methods": [
            _m("lo", cls=None, level="low", file="src/u.cpp", node_type="Function"),
            _m("hi", cls=None, level="high", file="src/u.cpp", node_type="Function"),
        ]}
        _, free = plan_test_classes.build_plan(inv)
        assert [f["level"] for f in free[0]["functions"]] == ["high", "low"]

    def test_groups_sorted_by_file_path(self, plan_test_classes):
        inv = {"methods": [
            _m("z", cls=None, file="src/b.cpp", node_type="Function"),
            _m("a", cls=None, file="src/a.cpp", node_type="Function"),
        ]}
        _, free = plan_test_classes.build_plan(inv)
        assert [g["file_path"] for g in free] == ["src/a.cpp", "src/b.cpp"]

    def test_missing_class_qn_method_treated_as_free(self, plan_test_classes):
        # test-writer §4 伪代码：class_qn 为空即入自由函数收集，不看 node_type
        inv = {"methods": [_m("orphan", cls=None, node_type="Method")]}
        classes, free = plan_test_classes.build_plan(inv)
        assert classes == []
        assert free[0]["function_count"] == 1


# ── module 推导与同名类消歧 ──────────────────────────────────────────

class TestModuleDisambiguation:
    @pytest.mark.parametrize("file_path,module", [
        ("src/lib/ui/fileview.cpp", "ui"),
        ("src/dbus/manager.cpp", "dbus"),
        ("deep/nested/path/thing.cpp", "path"),
        ("utils.cpp", "common"),          # 无目录段 → common
        ("", "common"),                    # 空 → common
        (None, "common"),                  # None → common
    ])
    def test_module_of(self, plan_test_classes, file_path, module):
        assert plan_test_classes._module_of(file_path) == module

    def test_same_name_classes_different_modules_not_merged(self, plan_test_classes):
        # A/Manager.h 与 B/Manager.h：同名类按模块拆分，不合并（test-code-gen §3）
        inv = {"methods": [
            _m("run", cls="proj.a.Manager", file="a/manager.cpp", level="mid"),
            _m("run", cls="proj.b.Manager", file="b/manager.cpp", level="high"),
        ]}
        classes, _ = plan_test_classes.build_plan(inv)
        assert len(classes) == 2
        mods = {c["module"] for c in classes}
        assert mods == {"a", "b"}
        # 两同名类各自保留独立 file_path
        files = {c["file_path"] for c in classes}
        assert files == {"a/manager.cpp", "b/manager.cpp"}


# ── 输出与 CLI ───────────────────────────────────────────────────────

class TestOutputAndCli:
    def test_plan_structure(self, plan_test_classes):
        inv = {"methods": [
            _m("a", cls="proj.src.A", level="high"),
            _m("f", cls=None, file="src/u.cpp", node_type="Function"),
        ]}
        classes, free = plan_test_classes.build_plan(inv)
        plan = {"classes": classes, "free_function_groups": free}
        blob = json.dumps(plan, ensure_ascii=False)  # 可序列化
        assert "classes" in blob and "free_function_groups" in blob

    def test_cli_stdout_no_file(self, plan_test_classes, tmp_path, capsys):
        inv = tmp_path / ".ut-inventory.json"
        inv.write_text(json.dumps({"methods": [
            _m("a", cls="proj.src.A", level="high")]}), encoding="utf-8")
        rc = plan_test_classes.plan_main_no_exit([
            "--inventory", str(inv), "--stdout"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "[PLAN] classes: 1" in out
        assert '"name": "A"' in out
        assert not (tmp_path / ".reports").exists()  # 未落盘

    def test_cli_writes_default_report(self, plan_test_classes, tmp_path, capsys):
        inv = tmp_path / ".ut-inventory.json"
        inv.write_text(json.dumps({"methods": [
            _m("a", cls="proj.src.A", level="mid"),
            _m("b", cls=None, file="src/u.cpp", node_type="Function"),
        ]}), encoding="utf-8")
        rc = plan_test_classes.plan_main_no_exit([
            "--inventory", str(inv)])
        out = capsys.readouterr().out
        assert rc == 0
        report = tmp_path / ".reports" / "testable-classes.json"
        assert report.exists()
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["class_count"] == 1
        assert len(data["free_function_groups"]) == 1
        assert "[PLAN] plan written:" in out

    def test_cli_explicit_output(self, plan_test_classes, tmp_path):
        inv = tmp_path / ".ut-inventory.json"
        inv.write_text(json.dumps({"methods": []}), encoding="utf-8")
        out = tmp_path / "plan.json"
        rc = plan_test_classes.plan_main_no_exit([
            "--inventory", str(inv), "--output", str(out)])
        assert rc == 0 and out.exists()

    def test_cli_missing_inventory(self, plan_test_classes, tmp_path, capsys):
        rc = plan_test_classes.plan_main_no_exit([
            "--inventory", str(tmp_path / "nope.json")])
        assert rc == 2
        assert "not found" in capsys.readouterr().out

    def test_cli_invalid_json(self, plan_test_classes, tmp_path, capsys):
        bad = tmp_path / ".ut-inventory.json"
        bad.write_text("{broken", encoding="utf-8")
        rc = plan_test_classes.plan_main_no_exit(["--inventory", str(bad)])
        assert rc == 2
        assert "invalid JSON" in capsys.readouterr().out
