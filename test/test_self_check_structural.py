"""self-check-structural.py 单元测试。

覆盖 6 类纯文件正则检查 + 块切分 + CLI。全部用合成字符串构造测试内容，
不依赖外部文件，保证行号/规则可预测。端到端验证见设计文档 §2.3。
"""
import json

import pytest

# ── 测试夹具：构造最小合规测试文件 ────────────────────────────────────

GOOD_HEADER = (
    "// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.\n"
    "// SPDX-License-Identifier: GPL-3.0-or-later\n"
)

GOOD_FIXTURE = (
    "class CalculatorTest : public ::testing::Test {\n"
    "protected:\n"
    "    void SetUp() override { stub.clear(); obj = new Calculator(); }\n"
    "    void TearDown() override { delete obj; stub.clear(); }\n"
    "    stub_ext::StubExt stub;\n"
    "    Calculator *obj = nullptr;\n"
    "};\n"
)


def _file(cases, header=GOOD_HEADER, fixture=GOOD_FIXTURE, with_aaa=True):
    """拼一个测试文件：header + fixture + 若干 TEST_F 块。cases=[(case_name, body)]。
    with_aaa=True 时在 body 前插入 // Arrange / // Act / // Assert 注释框架。
    """
    parts = [header, fixture]
    for name, body in cases:
        if with_aaa:
            # 把 body 包在 // Act 段，补 // Arrange 和 // Assert 空框架
            parts.append(
                f"TEST_F(CalculatorTest, {name}) {{\n"
                f"    // Arrange\n"
                f"    // Act\n{body}\n"
                f"    // Assert\n"
                f"}}\n")
        else:
            parts.append(f"TEST_F(CalculatorTest, {name}) {{\n{body}\n}}\n")
    return "\n".join(parts)


# ── 块切分 ────────────────────────────────────────────────────────────

class TestSplitBlocks:
    def test_basic_one_block(self, self_check_structural):
        c = "TEST_F(ATest, Foo_Bar_Baz) {\n    EXPECT_EQ(1, 1);\n}\n"
        blocks = self_check_structural.split_test_blocks(c)
        assert len(blocks) == 1
        assert blocks[0]["fixture"] == "ATest"
        assert blocks[0]["case"] == "Foo_Bar_Baz"
        assert blocks[0]["start_line"] == 1

    def test_test_p_block(self, self_check_structural):
        c = "TEST_P(PT, Case_X_Y) {\n    EXPECT_EQ(1, 1);\n}\n"
        blocks = self_check_structural.split_test_blocks(c)
        assert len(blocks) == 1
        assert blocks[0]["case"] == "Case_X_Y"

    def test_nested_braces_in_block(self, self_check_structural):
        # 块内含嵌套大括号（lambda/初始化列表），深度须正确归零
        c = ("TEST_F(ATest, Foo_Bar_Baz) {\n"
             "    auto f = []() { return 1; };\n"
             "    QList<int> v = {1, 2};\n"
             "    EXPECT_EQ(f(), 1);\n"
             "    EXPECT_EQ(v.size(), 2);\n"
             "}\n")
        blocks = self_check_structural.split_test_blocks(c)
        assert len(blocks) == 1
        assert "EXPECT_EQ(v.size(), 2)" in blocks[0]["body"]

    def test_multiple_blocks(self, self_check_structural):
        c = ("TEST_F(ATest, A_B_C) {\n    EXPECT_EQ(1, 1);\n    EXPECT_EQ(2, 2);\n}\n"
             "TEST_F(ATest, D_E_F) {\n    EXPECT_EQ(3, 3);\n    EXPECT_EQ(4, 4);\n}\n")
        blocks = self_check_structural.split_test_blocks(c)
        assert len(blocks) == 2
        assert [b["case"] for b in blocks] == ["A_B_C", "D_E_F"]

    def test_no_blocks(self, self_check_structural):
        assert self_check_structural.split_test_blocks("// just a comment\n") == []

    def test_start_line_offset(self, self_check_structural):
        # TEST_F 不在第 1 行
        c = "\n\nTEST_F(ATest, A_B_C) {\n    EXPECT_EQ(1, 1);\n    EXPECT_EQ(2, 2);\n}\n"
        blocks = self_check_structural.split_test_blocks(c)
        assert blocks[0]["start_line"] == 3


class TestExtractTestedNames:
    def test_first_segment_pascalcase(self, self_check_structural):
        c = ("TEST_F(ATest, Add_Positive_ReturnsSum) {\n}\n"
             "TEST_F(ATest, Subtract_Negative_ReturnsDiff) {\n}\n")
        names = self_check_structural.extract_tested_names(c)
        assert names == {"Add", "Subtract"}


# ── SPDX ──────────────────────────────────────────────────────────────

class TestSpdx:
    def test_present(self, self_check_structural):
        assert self_check_structural.check_spdx(GOOD_HEADER) == []

    def test_missing_copyright(self, self_check_structural):
        v = self_check_structural.check_spdx("// SPDX-License-Identifier: GPL-3.0-or-later\n")
        assert len(v) == 1 and v[0]["check"] == "spdx"

    def test_missing_license(self, self_check_structural):
        v = self_check_structural.check_spdx("// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.\n")
        assert len(v) == 1 and "License" in v[0]["message"]

    def test_wrong_license(self, self_check_structural):
        c = ("// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.\n"
             "// SPDX-License-Identifier: MIT\n")
        v = self_check_structural.check_spdx(c)
        assert len(v) == 1

    def test_completely_missing(self, self_check_structural):
        assert len(self_check_structural.check_spdx("#include <gtest/gtest.h>\n")) == 2


# ── 命名规范 ──────────────────────────────────────────────────────────

class TestNaming:
    def _blk(self, case):
        return [{"case": case, "start_line": 1, "body": "", "fixture": "T"}]

    def test_good_name(self, self_check_structural):
        assert self_check_structural.check_naming(self._blk("Add_Positive_ReturnsSum")) == []

    def test_too_few_segments(self, self_check_structural):
        v = self_check_structural.check_naming(self._blk("AddPositive"))
        assert len(v) == 1 and v[0]["rule"] == "TOO_FEW_SEGMENTS"

    def test_two_segments_still_too_few(self, self_check_structural):
        v = self_check_structural.check_naming(self._blk("Add_Positive"))
        assert len(v) == 1 and v[0]["rule"] == "TOO_FEW_SEGMENTS"

    def test_round_batch_in_name(self, self_check_structural):
        for bad in ["Add_R18_ReturnsSum", "Foo_Round2_Bar", "X_Batch3_Y_Z"]:
            v = self_check_structural.check_naming(self._blk(bad))
            assert len(v) == 1 and v[0]["rule"] == "ROUND_BATCH", bad

    def test_meaningless_name(self, self_check_structural):
        v = self_check_structural.check_naming(self._blk("Test1"))
        # Test1 既无下划线分段又匹配无意义名；ROUND_BATCH 先判（不匹配），MEANINGLESS 命中
        assert len(v) >= 1
        rules = {x["rule"] for x in v}
        assert "MEANINGLESS" in rules or "TOO_FEW_SEGMENTS" in rules


# ── 断言强度 ──────────────────────────────────────────────────────────

class TestAssertion:
    def _blk(self, body):
        return [{"case": "Foo_Bar_Baz", "start_line": 1, "body": body, "fixture": "T"}]

    def test_pass_two_valid_asserts(self, self_check_structural):
        body = "    EXPECT_EQ(1, 1);\n    EXPECT_EQ(2, 2);\n"
        assert self_check_structural.check_assertion(self._blk(body)) == []

    def test_empty_assert(self, self_check_structural):
        body = "    obj->foo();\n    // no assert\n"
        v = self_check_structural.check_assertion(self._blk(body))
        assert len(v) == 1 and v[0]["rule"] == "EMPTY_ASSERT"

    def test_sole_no_fatal(self, self_check_structural):
        body = "    EXPECT_NO_FATAL_FAILURE(obj->foo());\n"
        v = self_check_structural.check_assertion(self._blk(body))
        assert v[0]["rule"] == "SOLE_NO_FATAL"

    def test_sole_gmock_expect(self, self_check_structural):
        body = "    EXPECT_CALL(mock, foo());\n"
        v = self_check_structural.check_assertion(self._blk(body))
        assert v[0]["rule"] == "SOLE_GMOCK_EXPECT"

    def test_low_assert_one_valid(self, self_check_structural):
        body = "    EXPECT_EQ(1, 1);\n"
        v = self_check_structural.check_assertion(self._blk(body))
        assert v[0]["rule"] == "LOW_ASSERT"

    def test_no_throw_not_counted(self, self_check_structural):
        # EXPECT_NO_THROW 不计入有效断言；只有它 → 空/低断言
        body = "    EXPECT_NO_THROW(obj->foo());\n"
        v = self_check_structural.check_assertion(self._blk(body))
        # 无有效断言、无 nofatal、无 gmock → EMPTY_ASSERT
        assert v[0]["rule"] == "EMPTY_ASSERT"

    def test_sole_bool_warning(self, self_check_structural):
        body = "    EXPECT_TRUE(obj->isOk());\n"
        v = self_check_structural.check_assertion(self._blk(body))
        rules = {x["rule"] for x in v}
        assert "LOW_ASSERT" in rules and "SOLE_BOOL_ASSERT" in rules
        bool_v = next(x for x in v if x["rule"] == "SOLE_BOOL_ASSERT")
        assert bool_v["severity"] == "warning"

    def test_expect_call_plus_valid_passes(self, self_check_structural):
        # EXPECT_CALL + 2 个有效断言 → 通过（CALL 不计入但有效断言够）
        body = "    EXPECT_CALL(mock, foo());\n    EXPECT_EQ(1, 1);\n    EXPECT_EQ(2, 2);\n"
        assert self_check_structural.check_assertion(self._blk(body)) == []


# ── 结构 ──────────────────────────────────────────────────────────────

class TestStructure:
    def test_good(self, self_check_structural):
        c = "class FooTest : public ::testing::Test {\nvoid SetUp() {}\nvoid TearDown() {}\n};\n"
        assert self_check_structural.check_structure(c) == []

    def test_missing_inheritance(self, self_check_structural):
        c = "class FooTest {\nvoid SetUp() {}\nvoid TearDown() {}\n};\n"
        v = self_check_structural.check_structure(c)
        assert len(v) == 1 and "继承" in v[0]["message"]

    def test_missing_setup(self, self_check_structural):
        c = "class FooTest : public ::testing::Test {\nvoid TearDown() {}\n};\n"
        v = self_check_structural.check_structure(c)
        assert len(v) == 1 and "SetUp" in v[0]["message"]

    def test_missing_teardown(self, self_check_structural):
        c = "class FooTest : public ::testing::Test {\nvoid SetUp() {}\n};\n"
        v = self_check_structural.check_structure(c)
        assert len(v) == 1 and "TearDown" in v[0]["message"]


# ── stub ──────────────────────────────────────────────────────────────

class TestStub:
    def test_no_stub_no_violation(self, self_check_structural):
        c = "void TearDown() override { delete obj; }\n"
        assert self_check_structural.check_stub(c) == []

    def test_set_without_clear(self, self_check_structural):
        c = ("void SetUp() override { stub.set_lamda(&A::f, [](){return 1;}); }\n"
             "void TearDown() override { delete obj; }\n")
        v = self_check_structural.check_stub(c)
        assert any(x["rule"] == "STUB_NOT_CLEARED" for x in v)

    def test_clear_outside_teardown(self, self_check_structural):
        c = ("void SetUp() override { stub.set_lamda(&A::f, [](){return 1;}); stub.clear(); }\n"
             "void TearDown() override { delete obj; }\n")
        v = self_check_structural.check_stub(c)
        assert any(x["rule"] == "STUB_CLEAR_NOT_IN_TEARDOWN" for x in v)

    def test_clear_in_teardown_ok(self, self_check_structural):
        c = ("void SetUp() override { stub.set_lamda(&A::f, [](){return 1;}); }\n"
             "void TearDown() override { stub.clear(); delete obj; }\n")
        assert self_check_structural.check_stub(c) == []


# ── 环境隔离 ──────────────────────────────────────────────────────────

class TestEnv:
    def test_clean(self, self_check_structural):
        c = ('    QString p = QDir::tempPath() + "/x";\n    EXPECT_EQ(1, 1);\n')
        assert self_check_structural.check_env(c) == []

    def test_hardcoded_path(self, self_check_structural):
        c = '    QFile f("/home/user/data.txt");\n'
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "HARDCODED_PATH" for x in v)

    def test_temp_path_excluded(self, self_check_structural):
        c = ('    QTemporaryDir d("/tmp/mytest");\n'   # 命中 /tmp/ 但有 QTemporaryDir → 排除
             '    QString p = QDir::temp() + "/y";\n')
        v = self_check_structural.check_env(c)
        assert not any(x["rule"] == "HARDCODED_PATH" for x in v)

    def test_env_unbalanced(self, self_check_structural):
        c = "    qputenv(\"X\", \"1\");\n"   # 有 put 无 unset
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "ENV_UNBALANCED" for x in v)

    def test_env_balanced_ok(self, self_check_structural):
        c = "    qputenv(\"X\", \"1\");\n    qunsetenv(\"X\");\n"
        assert not any(x["rule"] == "ENV_UNBALANCED"
                       for x in self_check_structural.check_env(c))

    def test_real_external_call(self, self_check_structural):
        c = "    QProcess::start(\"cmd\");\n"
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "REAL_EXTERNAL_CALL" for x in v)

    def test_real_time_dependency(self, self_check_structural):
        # §5b 真实时间依赖：QDateTime::currentDateTime → 违规
        c = "    QDateTime::currentDateTime();\n"
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "REAL_EXTERNAL_CALL" for x in v)

    def test_qrandom_system(self, self_check_structural):
        c = "    QRandomGenerator::system();\n"
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "REAL_EXTERNAL_CALL" for x in v)

    def test_srand_detected(self, self_check_structural):
        c = "    srand(42);\n"
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "REAL_EXTERNAL_CALL" for x in v)

    def test_stub_excluded_from_external(self, self_check_structural):
        # stub.set_lamda(&QProcess::start, ...) 不算真实调用
        c = "    stub.set_lamda(&QProcess::start, [](){return 0;});\n"
        v = self_check_structural.check_env(c)
        assert not any(x["rule"] == "REAL_EXTERNAL_CALL" for x in v)

    def test_stub_set_not_excluded(self, self_check_structural):
        # stub.set(...)（非 set_lamda）不往排除名单里——与 reference grep 一致
        # grep 仅排除 stub.set_lamda|__DBG_STUB_INVOKE__，stub.set 会被报为 REAL_EXTERNAL_CALL
        c = "    stub.set(&QProcess::start, [](){return 0;});\n"
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "REAL_EXTERNAL_CALL" for x in v)

    def test_stub_in_comment_does_not_suppress(self, self_check_structural):
        # 行内仅注释含 'stub' 但实际调 QProcess::start——收窄后不再被吞
        c = '    QProcess::start("cmd"); // TODO stub this\n'
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "REAL_EXTERNAL_CALL" for x in v)

    def test_home_path_access_warning(self, self_check_structural):
        # §5b QDir::homePath() → warning（可能合法，需复核）
        c = "    QString home = QDir::homePath();\n"
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "HOME_PATH_ACCESS" for x in v)
        hv = next(x for x in v if x["rule"] == "HOME_PATH_ACCESS")
        assert hv["severity"] == "warning"  # 不是 error，是 warning


# ── AAA 结构检查 ────────────────────────────────────────────────────

class TestAAA:
    def test_full_aaa_pass(self, self_check_structural):
        c = ("TEST_F(T, Add_Pos_ReturnsSum) {\n"
             "    // Arrange\n"
             "    int a = 1, b = 2;\n"
             "    // Act\n"
             "    int r = obj->add(a, b);\n"
             "    // Assert\n"
             "    EXPECT_EQ(r, 3);\n"
             "}\n")
        blocks = self_check_structural.split_test_blocks(c)
        v = self_check_structural.check_aaa(blocks)
        assert v == []

    def test_missing_arrange(self, self_check_structural):
        c = ("TEST_F(T, Add_Pos_ReturnsSum) {\n"
             "    // Act\n"
             "    int r = obj->add(1, 2);\n"
             "    // Assert\n"
             "    EXPECT_EQ(r, 3);\n"
             "}\n")
        blocks = self_check_structural.split_test_blocks(c)
        v = self_check_structural.check_aaa(blocks)
        assert any(x["rule"] == "MISSING_AAA" for x in v)
        mv = next(x for x in v if x["rule"] == "MISSING_AAA")
        assert "Arrange" in mv["message"]

    def test_missing_act(self, self_check_structural):
        c = ("TEST_F(T, Add_Pos_ReturnsSum) {\n"
             "    // Arrange\n"
             "    int a = 1;\n"
             "    // Assert\n"
             "    EXPECT_EQ(obj->add(a, 2), 3);\n"
             "}\n")
        blocks = self_check_structural.split_test_blocks(c)
        v = self_check_structural.check_aaa(blocks)
        assert any(x["rule"] == "MISSING_AAA" for x in v)
        mv = next(x for x in v if x["rule"] == "MISSING_AAA")
        assert "Act" in mv["message"]

    def test_missing_assert_comment(self, self_check_structural):
        c = ("TEST_F(T, Add_Pos_ReturnsSum) {\n"
             "    // Arrange\n"
             "    int a = 1;\n"
             "    // Act\n"
             "    EXPECT_EQ(obj->add(a, 2), 3);\n"
             "}\n")
        blocks = self_check_structural.split_test_blocks(c)
        v = self_check_structural.check_aaa(blocks)
        assert any(x["rule"] == "MISSING_AAA" for x in v)

    def test_no_aaa_at_all(self, self_check_structural):
        c = ("TEST_F(T, Add_Pos_ReturnsSum) {\n"
             "    EXPECT_EQ(obj->add(1,2), 3);\n"
             "}\n")
        blocks = self_check_structural.split_test_blocks(c)
        v = self_check_structural.check_aaa(blocks)
        assert any(x["rule"] == "MISSING_AAA" for x in v)
        mv = next(x for x in v if x["rule"] == "MISSING_AAA")
        assert "Arrange" in mv["message"] and "Act" in mv["message"] and "Assert" in mv["message"]

    def test_empty_arrange_warning(self, self_check_structural):
        c = ("TEST_F(T, Add_Pos_ReturnsSum) {\n"
             "    // Arrange\n"
             "    // Act\n"
             "    int r = obj->add(1, 2);\n"
             "    // Assert\n"
             "    EXPECT_EQ(r, 3);\n"
             "}\n")
        blocks = self_check_structural.split_test_blocks(c)
        v = self_check_structural.check_aaa(blocks)
        assert any(x["rule"] == "EMPTY_AAA" for x in v)
        ev = next(x for x in v if x["rule"] == "EMPTY_AAA")
        assert ev["severity"] == "warning"
        assert "Arrange" in ev["message"]


# ── 用例计数声明检查 ────────────────────────────────────────────────

class TestUsecaseDecl:
    def test_missing_decl_warning(self, self_check_structural):
        # 无声明表格 → MISSING_DECL warning
        c = "// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.\n"
        v = self_check_structural.check_usecase_decl(c)
        assert any(x["rule"] == "MISSING_DECL" for x in v)
        dv = next(x for x in v if x["rule"] == "MISSING_DECL")
        assert dv["severity"] == "warning"

    def test_decl_met_below_min(self, self_check_structural):
        # actual < min → BELOW_MIN_CASES error
        c = ("// | method | level | factors | min | actual |\n"
             "// | add | high | complexity_ge_20 | 3 | 1 |\n")
        v = self_check_structural.check_usecase_decl(c)
        assert any(x["rule"] == "BELOW_MIN_CASES" for x in v)
        bv = next(x for x in v if x["rule"] == "BELOW_MIN_CASES")
        assert bv["severity"] == "error"
        assert "actual=1 < min=3" in bv["message"]

    def test_decl_met_equal(self, self_check_structural):
        # actual == min → 无违规
        c = ("// | method | level | factors | min | actual |\n"
             "// | add | high | complexity_ge_20 | 3 | 3 |\n")
        v = self_check_structural.check_usecase_decl(c)
        assert not any(x["rule"] == "BELOW_MIN_CASES" for x in v)

    def test_decl_met_above_min(self, self_check_structural):
        # actual > min → 无违规（下限不是上限）
        c = ("// | method | level | factors | min | actual |\n"
             "// | add | high | complexity_ge_20 | 3 | 5 |\n")
        v = self_check_structural.check_usecase_decl(c)
        assert not any(x["rule"] == "BELOW_MIN_CASES" for x in v)

    def test_decl_multiple_rows(self, self_check_structural):
        c = ("// | method | level | factors | min | actual |\n"
             "// | add | high | complexity_ge_20 | 3 | 3 |\n"
             "// | sub | mid | - | 2 | 1 |\n")
        v = self_check_structural.check_usecase_decl(c)
        below = [x for x in v if x["rule"] == "BELOW_MIN_CASES"]
        assert len(below) == 1  # 只有 sub 不达标
        assert "actual=1 < min=2" in below[0]["message"]

    def test_writable_location_warning(self, self_check_structural):
        c = "    QString loc = QStandardPaths::writableLocation(QStandardPaths::HomeLocation);\n"
        v = self_check_structural.check_env(c)
        assert any(x["rule"] == "HOME_PATH_ACCESS" for x in v)

    def test_home_path_not_flagged_when_stubbed(self, self_check_structural):
        # &QDir::homePath 是函数指针（不是调用），不触发 HOME_PATH_ACCESS
        c = '    stub.set_lamda(&QDir::homePath, [](){return QString();});\n'
        v = self_check_structural.check_env(c)
        assert not any(x["rule"] == "HOME_PATH_ACCESS" for x in v)
        assert not any(x["rule"] == "REAL_EXTERNAL_CALL" for x in v)
        assert not any(x["rule"] == "HARDCODED_PATH" for x in v)


# ── 汇总与 summary ────────────────────────────────────────────────────

class TestRunAll:
    def test_clean_file_all_pass(self, self_check_structural):
        c = _file([("Add_Pos_ReturnsSum",
                    "    EXPECT_EQ(obj->add(1,2), 3);\n    EXPECT_EQ(obj->add(0,0), 0);\n")])
        v, summary, blocks = self_check_structural.run_all_checks(c)
        errors = [x for x in v if x["severity"] == "error"]
        assert errors == [], f"unexpected errors: {errors}"
        # warning 可能存在（EMPTY_AAA/MISSING_DECL），不阻塞
        assert all(summary[n] != "fail" for n in self_check_structural.CHECK_NAMES)
        assert len(blocks) == 1

    def test_summary_fail_when_error(self, self_check_structural):
        c = _file([("Bad", "    EXPECT_EQ(1, 1);\n")])  # 分段不足 + 低断言
        _, summary, _ = self_check_structural.run_all_checks(c)
        assert summary["naming"] == "fail"
        assert summary["assertion"] == "fail"
        assert summary["spdx"] == "pass"

    def test_summary_warn_when_only_warning(self, self_check_structural):
        # 唯一布尔断言 → LOW_ASSERT(error) + SOLE_BOOL(warning)；构造纯 warning 较难，
        # 此处验证 SOLE_BOOL 的 warning 确实出现在 violations
        c = _file([("IsOk_Init_ReturnsTrue", "    EXPECT_TRUE(obj->isOk());\n")])
        v, summary, _ = self_check_structural.run_all_checks(c)
        assert summary["assertion"] == "fail"  # LOW_ASSERT 是 error
        assert any(x["severity"] == "warning" for x in v)


# ── CLI ───────────────────────────────────────────────────────────────

class TestCli:
    def test_clean_file_exit_zero(self, self_check_structural, tmp_path, capsys):
        f = tmp_path / "t.cpp"
        f.write_text(_file([("Add_Pos_ReturnsSum",
                            "    EXPECT_EQ(obj->add(1,2), 3);\n    EXPECT_EQ(obj->add(0,0), 0);\n")]),
                     encoding="utf-8")
        # 无 error 时退出码 0（warnings 不阻塞）
        rc = self_check_structural.main_no_exit(["--file", str(f)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "pass" in out

    def test_violations_exit_one(self, self_check_structural, tmp_path, capsys):
        f = tmp_path / "t.cpp"
        f.write_text(_file([("Bad", "    EXPECT_EQ(1, 1);\n")]), encoding="utf-8")
        rc = self_check_structural.main_no_exit(["--file", str(f)])
        assert rc == 1
        assert "naming:fail" in capsys.readouterr().out

    def test_json_output(self, self_check_structural, tmp_path, capsys):
        f = tmp_path / "t.cpp"
        f.write_text(_file([("Add_Pos_ReturnsSum",
                             "    EXPECT_EQ(obj->add(1,2), 3);\n    EXPECT_EQ(obj->add(0,0), 0);\n")]),
                      encoding="utf-8")
        self_check_structural.main_no_exit(["--file", str(f), "--json"])
        out = capsys.readouterr().out
        # JSON 部分在摘要后
        json_part = out[out.index("{"):]
        data = json.loads(json_part)
        assert "violations" in data and "summary" in data
        assert data["test_case_count"] == 1
        assert "Add" in data["tested_names"]

    def test_write_output_file(self, self_check_structural, tmp_path):
        f = tmp_path / "t.cpp"
        f.write_text(_file([("Bad", "    EXPECT_EQ(1, 1);\n")]), encoding="utf-8")
        out = tmp_path / "report.json"
        self_check_structural.main_no_exit(["--file", str(f), "--output", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["violations"]) > 0
        assert data["summary"]["naming"] == "fail"

    def test_missing_file(self, self_check_structural, tmp_path, capsys):
        rc = self_check_structural.main_no_exit(["--file", str(tmp_path / "nope.cpp")])
        assert rc == 2
        assert "not found" in capsys.readouterr().out
