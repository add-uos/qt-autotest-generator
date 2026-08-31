"""update-usecase-count.py 单元测试。

覆盖 test-writer.md §6 固化：TEST_F 计数、方法名匹配（PascalCase vs camelCase
小写归一化）、增量回写、失败安全（匹配不到不动）、双 schema 字段兼容。
"""
import json

import pytest


def _inventory(methods):
    """构造 inventory（schema 字段形态）。methods=[(name, class_qn, testable)]。"""
    return {"methods": [
        {"qualified_name": f"p.{cq}.{n}", "name": n, "class_qn": cq,
         "file_path": "src/x.cpp", "testable": t, "usecase_count": 0}
        for n, cq, t in methods
    ]}


def _legacy_inventory(methods):
    """存量字段形态（qn/file/短名 class_qn，如 deepin-image-viewer）。"""
    return {"methods": [
        {"qn": f"home.repos.{cq}.{n}", "name": n, "class_qn": cq,
         "file": "src/x.cpp", "testable": t, "usecase_count": 0}
        for n, cq, t in methods
    ]}


TEST_FILE = (
    "TEST_F(CalculatorTest, Add_Positive_ReturnsSum) {}\n"
    "TEST_F(CalculatorTest, Add_Negative_ReturnsSum) {}\n"
    "TEST_F(CalculatorTest, Add_Zero_ReturnsZero) {}\n"
    "TEST_F(CalculatorTest, Subtract_Pos_ReturnsDiff) {}\n"
    "TEST_F(CalculatorTest, Multiply_Pos_ReturnsProduct) {}\n"
)


# ── 计数纯函数 ────────────────────────────────────────────────────────

class TestCountCases:
    def test_match_by_method_name(self, update_usecase_count):
        # add → 3 个用例（首段 Add 小写 == add）
        assert update_usecase_count.count_cases_for_method(TEST_FILE, "add") == 3
        assert update_usecase_count.count_cases_for_method(TEST_FILE, "subtract") == 1
        assert update_usecase_count.count_cases_for_method(TEST_FILE, "multiply") == 1

    def test_no_match_returns_zero(self, update_usecase_count):
        assert update_usecase_count.count_cases_for_method(TEST_FILE, "nonexistent") == 0

    def test_total_cases(self, update_usecase_count):
        assert update_usecase_count.count_total_cases(TEST_FILE) == 5

    def test_test_p_counted(self, update_usecase_count):
        c = "TEST_P(PT, Foo_Bar_Baz) {}\nTEST_F(FT, Foo_Other_X) {}\n"
        assert update_usecase_count.count_total_cases(c) == 2
        assert update_usecase_count.count_cases_for_method(c, "foo") == 2


# ── 方法匹配 ──────────────────────────────────────────────────────────

class TestMethodMatch:
    def test_short_class_qn(self, update_usecase_count):
        inv = _inventory([("add", "Calculator", True), ("foo", "Other", True)])
        m = inv["methods"][0]
        assert update_usecase_count._method_matches(m, "Calculator", None) is True
        assert update_usecase_count._method_matches(m, "Other", None) is False

    def test_full_class_qn(self, update_usecase_count):
        inv = _inventory([("add", "proj.src.Calculator", True)])
        m = inv["methods"][0]
        # 短名匹配：全名 class_qn 取最后一段 == Calculator
        assert update_usecase_count._method_matches(m, "Calculator", None) is True

    def test_class_qn_exact(self, update_usecase_count):
        inv = _inventory([("add", "proj.src.Calculator", True)])
        m = inv["methods"][0]
        assert update_usecase_count._method_matches(m, "Calculator", "proj.src.Calculator") is True
        assert update_usecase_count._method_matches(m, "Calculator", "other.Calculator") is False

    def test_non_testable_excluded(self, update_usecase_count):
        m = {"class_qn": "Calculator", "name": "add", "testable": False}
        assert update_usecase_count._method_matches(m, "Calculator", None) is False

    def test_free_function_excluded(self, update_usecase_count):
        m = {"class_qn": None, "name": "freefn", "testable": True}
        assert update_usecase_count._method_matches(m, "Calculator", None) is False


# ── 增量更新 ──────────────────────────────────────────────────────────

class TestUpdateInventory:
    def test_updates_matching_methods_only(self, update_usecase_count):
        inv = _inventory([
            ("add", "Calculator", True),
            ("subtract", "Calculator", True),
            ("other", "NotMatched", True),   # 不同类，不动
        ])
        updated = update_usecase_count.update_inventory(inv, TEST_FILE, "Calculator", None)
        names = {u["name"] for u in updated}
        assert names == {"add", "subtract"}
        assert inv["methods"][0]["usecase_count"] == 3  # add: 3 cases
        assert inv["methods"][1]["usecase_count"] == 1  # subtract: 1
        assert inv["methods"][2]["usecase_count"] == 0  # 不动

    def test_no_match_keeps_original(self, update_usecase_count):
        inv = _inventory([("add", "Calculator", True)])
        inv["methods"][0]["usecase_count"] = 99
        # 用不含 add 用例的测试文件
        c = "TEST_F(X, Foo_Bar_Baz) {}\n"
        update_usecase_count.update_inventory(inv, c, "Calculator", None)
        # add 匹配到类但 count=0，会更新为 0（testable 方法在类内就更新）
        assert inv["methods"][0]["usecase_count"] == 0

    def test_legacy_schema(self, update_usecase_count):
        # 存量字段 qn/file/短名 class_qn
        inv = _legacy_inventory([("add", "Calculator", True)])
        updated = update_usecase_count.update_inventory(inv, TEST_FILE, "Calculator", None)
        assert len(updated) == 1
        assert inv["methods"][0]["usecase_count"] == 3

    def test_does_not_touch_other_classes(self, update_usecase_count):
        inv = _inventory([
            ("add", "Calculator", True),
            ("add", "OtherCls", True),   # 同名方法不同类
        ])
        inv["methods"][1]["usecase_count"] = 77
        update_usecase_count.update_inventory(inv, TEST_FILE, "Calculator", None)
        assert inv["methods"][1]["usecase_count"] == 77  # OtherCls 不动


# ── CLI ───────────────────────────────────────────────────────────────

class TestAmbiguity:
    def test_detect_single_class(self, update_usecase_count):
        inv = _inventory([("add", "Calculator", True), ("sub", "Calculator", True)])
        qns = update_usecase_count.detect_ambiguous_class(inv, "Calculator")
        assert qns == {"Calculator"}

    def test_detect_multiple_same_name(self, update_usecase_count):
        # 两个不同全名但同名短名
        inv = _inventory([
            ("add", "proj.src.Calculator", True),
            ("add", "proj.other.Calculator", True),
        ])
        qns = update_usecase_count.detect_ambiguous_class(inv, "Calculator")
        assert len(qns) == 2
        assert "proj.src.Calculator" in qns
        assert "proj.other.Calculator" in qns

    def test_no_ambiguity_with_class_qn_exact(self, update_usecase_count, tmp_path, capsys):
        # --class-qn 精确匹配时不触发警告
        inv = _inventory([
            ("add", "proj.src.Calculator", True),
            ("add", "proj.other.Calculator", True),
        ])
        f = tmp_path / ".ut-inventory.json"
        f.write_text(json.dumps(inv), encoding="utf-8")
        tf = tmp_path / "t.cpp"
        tf.write_text(TEST_FILE, encoding="utf-8")
        update_usecase_count.usecase_main_no_exit([
            "--test-file", str(tf), "--inventory", str(f),
            "--class-qn", "proj.src.Calculator"])
        out = capsys.readouterr().out
        assert "WARNING" not in out
        # 只改了 proj.src.Calculator 的方法
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["methods"][0]["usecase_count"] == 3  # proj.src
        assert data["methods"][1]["usecase_count"] == 0  # proj.other 未改

    def test_warns_on_ambiguous_class(self, update_usecase_count, tmp_path, capsys):
        inv = _inventory([
            ("add", "proj.src.Calculator", True),
            ("add", "proj.other.Calculator", True),
        ])
        f = tmp_path / ".ut-inventory.json"
        f.write_text(json.dumps(inv), encoding="utf-8")
        tf = tmp_path / "t.cpp"
        tf.write_text(TEST_FILE, encoding="utf-8")
        update_usecase_count.usecase_main_no_exit([
            "--test-file", str(tf), "--inventory", str(f),
            "--class", "Calculator", "--dry-run"])
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "--class-qn" in out


class TestCli:
    def _setup(self, tmp_path, methods=None):
        methods = methods or [("add", "Calculator", True), ("subtract", "Calculator", True)]
        inv = tmp_path / ".ut-inventory.json"
        inv.write_text(json.dumps(_inventory(methods)), encoding="utf-8")
        tf = tmp_path / "test_calc.cpp"
        tf.write_text(TEST_FILE, encoding="utf-8")
        return tf, inv

    def test_writes_inventory(self, update_usecase_count, tmp_path, capsys):
        tf, inv = self._setup(tmp_path)
        rc = update_usecase_count.usecase_main_no_exit([
            "--test-file", str(tf), "--inventory", str(inv), "--class", "Calculator"])
        assert rc == 0
        data = json.loads(inv.read_text(encoding="utf-8"))
        assert data["methods"][0]["usecase_count"] == 3
        assert "(written)" in capsys.readouterr().out

    def test_dry_run_no_write(self, update_usecase_count, tmp_path, capsys):
        tf, inv = self._setup(tmp_path)
        rc = update_usecase_count.usecase_main_no_exit([
            "--test-file", str(tf), "--inventory", str(inv),
            "--class", "Calculator", "--dry-run"])
        assert rc == 0
        data = json.loads(inv.read_text(encoding="utf-8"))
        assert data["methods"][0]["usecase_count"] == 0  # 未写回
        assert "dry-run" in capsys.readouterr().out

    def test_class_qn_exact(self, update_usecase_count, tmp_path):
        tf, inv = self._setup(tmp_path, [("add", "proj.src.Calculator", True)])
        rc = update_usecase_count.usecase_main_no_exit([
            "--test-file", str(tf), "--inventory", str(inv),
            "--class-qn", "proj.src.Calculator"])
        assert rc == 0
        assert json.loads(inv.read_text())["methods"][0]["usecase_count"] == 3

    def test_missing_class_arg(self, update_usecase_count, tmp_path):
        tf, inv = self._setup(tmp_path)
        rc = update_usecase_count.usecase_main_no_exit([
            "--test-file", str(tf), "--inventory", str(inv)])
        assert rc == 2

    def test_missing_files(self, update_usecase_count, tmp_path, capsys):
        rc = update_usecase_count.usecase_main_no_exit([
            "--test-file", str(tmp_path / "nope.cpp"),
            "--inventory", str(tmp_path / "nope.json"), "--class", "X"])
        assert rc == 2
        assert "not found" in capsys.readouterr().out

    def test_invalid_json(self, update_usecase_count, tmp_path, capsys):
        bad = tmp_path / ".ut-inventory.json"
        bad.write_text("{broken", encoding="utf-8")
        tf = tmp_path / "t.cpp"
        tf.write_text("TEST_F(X, A_B_C) {}", encoding="utf-8")
        rc = update_usecase_count.usecase_main_no_exit([
            "--test-file", str(tf), "--inventory", str(bad), "--class", "X"])
        assert rc == 2
        assert "invalid" in capsys.readouterr().out
