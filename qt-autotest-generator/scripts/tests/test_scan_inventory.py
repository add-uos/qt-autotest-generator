"""scan-inventory.py 健壮性测试。

聚焦评分与过滤逻辑的边界场景：glob 转 regex、scope 匹配、score_method
各 factor 组合、build_inventory 对空/缺字段/噪声数据的处理。
"""
import re

import pytest


# ── glob_to_regex ─────────────────────────────────────────────────────

class TestGlobToRegex:
    def test_double_star(self, scan_inventory):
        r = scan_inventory.glob_to_regex("src/**")
        assert re.match(r, "src/a/b/c.cpp")
        assert re.match(r, "src/x.cpp")

    def test_double_star_matches_zero_depth(self, scan_inventory):
        # ** 匹配任意深度（含零）
        r = scan_inventory.glob_to_regex("src/**")
        assert re.match(r, "src/") or re.match(r, "src")

    def test_single_star(self, scan_inventory):
        r = scan_inventory.glob_to_regex("src/*.cpp")
        assert re.match(r, "src/a.cpp")
        assert not re.match(r, "src/sub/a.cpp")  # * 不跨目录

    def test_question_mark(self, scan_inventory):
        r = scan_inventory.glob_to_regex("a?.cpp")
        assert re.match(r, "ab.cpp")
        assert not re.match(r, "abc.cpp")

    def test_literal_special_chars_escaped(self, scan_inventory):
        # . + ^ $ 等需转义
        r = scan_inventory.glob_to_regex("file.cpp")
        assert re.match(r, "file.cpp")
        assert not re.match(r, "fileXcpp")  # . 不匹配任意字符

    def test_empty_pattern(self, scan_inventory):
        r = scan_inventory.glob_to_regex("")
        assert re.match(r, "")

    def test_plain_no_star(self, scan_inventory):
        r = scan_inventory.glob_to_regex("src/foo.cpp")
        assert re.match(r, "src/foo.cpp")
        assert not re.match(r, "src/bar.cpp")


# ── scope_match ───────────────────────────────────────────────────────

class TestScopeMatch:
    def test_none_file_path_testable(self, scan_inventory):
        testable, reason = scan_inventory.scope_match(None, [])
        assert testable is True and reason is None

    def test_no_rules_testable(self, scan_inventory):
        testable, reason = scan_inventory.scope_match("src/a.cpp", [])
        assert testable is True and reason is None

    def test_exempt_match(self, scan_inventory):
        rules = [{"pattern": "tests/**", "scope": "exempt"}]
        testable, reason = scan_inventory.scope_match("tests/x.cpp", rules)
        assert testable is False
        assert "tests/**" in reason

    def test_exempt_no_match(self, scan_inventory):
        rules = [{"pattern": "tests/**", "scope": "exempt"}]
        testable, reason = scan_inventory.scope_match("src/a.cpp", rules)
        assert testable is True and reason is None

    def test_non_exempt_rule_ignored(self, scan_inventory):
        # scope 非 exempt 的规则不阻止测试
        rules = [{"pattern": "src/**", "scope": "include"}]
        testable, reason = scan_inventory.scope_match("src/a.cpp", rules)
        assert testable is True


# ── score_method ──────────────────────────────────────────────────────

class TestScoreMethod:
    def test_empty_factors_low(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", [])
        assert level == "low" and score == 0

    def test_dbus_slot_high(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["dbus_slot"])
        assert level == "high" and score == 3

    def test_q_invokable_high(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["q_invokable"])
        assert level == "high" and score == 3

    def test_complexity_high(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["complexity:20"])
        assert level == "high" and score == 3

    def test_complexity_mid(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["complexity:10"])
        assert level == "mid" and score == 2

    def test_complexity_mid_boundary_8(self, scan_inventory):
        # complexity≥8 也应得 +2
        level, source, score = scan_inventory.score_method("foo", ["complexity:8"])
        assert level == "mid" and score == 2

    def test_complexity_below_8_is_plus1(self, scan_inventory):
        # complexity=7 不够 8，只能 +1
        level, source, score = scan_inventory.score_method("foo", ["complexity:7"])
        assert level == "mid" and score == 1

    def test_complexity_low(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["complexity:4"])
        assert level == "low" and score == 0

    def test_transitive_loop_depth_high(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["transitive_loop_depth:3"])
        assert level == "high" and score == 3

    def test_linear_scan_in_loop_mid(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["linear_scan_in_loop:1"])
        assert level == "mid" and score == 1

    def test_in_degree_alone_mid(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["in_degree:5"])
        assert level == "mid" and score == 1

    def test_destructor_penalty(self, scan_inventory):
        level, source, score = scan_inventory.score_method("~Foo", ["destructor"])
        assert score == -1 and level == "low"

    def test_operator_penalty(self, scan_inventory):
        level, source, score = scan_inventory.score_method("operator+", ["operator"])
        assert score == -1 and level == "low"

    def test_name_pattern_suggested(self, scan_inventory):
        # name_pattern 无分值但触发 suggested
        level, source, score = scan_inventory.score_method("foo", ["name_pattern:bar"])
        assert level == "mid"
        assert source == "suggested"

    def test_cognitive_below_15_no_score(self, scan_inventory):
        # cognitive<15 → 不加分
        level, source, score = scan_inventory.score_method("foo", ["cognitive:14"])
        assert level == "low" and score == 0

    def test_cognitive_boundary_15(self, scan_inventory):
        # cognitive=15 → +1
        level, source, score = scan_inventory.score_method("foo", ["cognitive:15"])
        assert level == "mid" and score == 1

    def test_cognitive_boundary_30(self, scan_inventory):
        # cognitive=30 → +2
        level, source, score = scan_inventory.score_method("foo", ["cognitive:30"])
        assert level == "mid" and score == 2

    def test_lines_below_50_no_score(self, scan_inventory):
        # lines<50 → 不加分
        level, source, score = scan_inventory.score_method("foo", ["lines:49"])
        assert level == "low" and score == 0

    def test_lines_boundary_50(self, scan_inventory):
        # lines=50 → +1
        level, source, score = scan_inventory.score_method("foo", ["lines:50"])
        assert level == "mid" and score == 1

    def test_lines_boundary_150(self, scan_inventory):
        # lines=150 → +1（和 50-149 一样，保守加分）
        level, source, score = scan_inventory.score_method("foo", ["lines:150"])
        assert level == "mid" and score == 1

    def test_loop_count_boundary_5(self, scan_inventory):
        # loop_count=5 → +1
        level, source, score = scan_inventory.score_method("foo", ["loop_count:5"])
        assert level == "mid" and score == 1

    def test_alloc_in_loop_boundary_1(self, scan_inventory):
        # alloc_in_loop=1 → +1
        level, source, score = scan_inventory.score_method("foo", ["alloc_in_loop:1"])
        assert level == "mid" and score == 1

    def test_alloc_in_loop_zero_no_score(self, scan_inventory):
        # alloc_in_loop=0 → 不加分（但 build_inventory 不会生成这个因子）
        level, source, score = scan_inventory.score_method("foo", ["alloc_in_loop:0"])
        assert level == "mid" and score == 1  # score_method 本身不检查值>=1

    def test_complexity_boundary_5(self, scan_inventory):
        # complexity=5 → +1
        level, source, score = scan_inventory.score_method("foo", ["complexity:5"])
        assert level == "mid" and score == 1

    def test_complexity_boundary_20(self, scan_inventory):
        # complexity=20 → +3
        level, source, score = scan_inventory.score_method("foo", ["complexity:20"])
        assert level == "high" and score == 3

    def test_destructor_plus_risk_factors(self, scan_inventory):
        # destructor(-1) + recursive(+1) = 0 → low
        level, source, score = scan_inventory.score_method(
            "~Foo", ["destructor", "recursive"])
        assert score == 0 and level == "low"

    def test_combined_all_risk_factors(self, scan_inventory):
        # loop_count:8(+1) + alloc_in_loop:1(+1) + recursive(+1)
        # + linear_scan_in_loop(+1) + in_degree:5(+1) = 5 → high
        level, source, score = scan_inventory.score_method(
            "foo", ["loop_count:8", "alloc_in_loop:1", "recursive",
                     "linear_scan_in_loop:1", "in_degree:5"])
        assert level == "high" and score == 5

    def test_combined_to_high(self, scan_inventory):
        # in_degree(1) + complexity:10(2) = 3 → high
        level, source, score = scan_inventory.score_method(
            "foo", ["in_degree:5", "complexity:10"])
        assert level == "high" and score == 3

    def test_concurrent_base(self, scan_inventory):
        level, source, score = scan_inventory.score_method("foo", ["concurrent_base:QThread"])
        assert level == "mid" and score == 1

    def test_invalid_complexity_value(self, scan_inventory):
        # 非数字 complexity → int() 抛异常
        with pytest.raises(ValueError):
            scan_inventory.score_method("foo", ["complexity:abc"])

    # ── 新增因子测试 ──

    def test_cognitive_high_alone_mid(self, scan_inventory):
        # cognitive≥30 单独只能到 mid (+2)
        level, source, score = scan_inventory.score_method("foo", ["cognitive:50"])
        assert level == "mid" and score == 2

    def test_cognitive_mid(self, scan_inventory):
        # cognitive 15-29 → +1
        level, source, score = scan_inventory.score_method("foo", ["cognitive:20"])
        assert level == "mid" and score == 1

    def test_cognitive_plus_complexity_high(self, scan_inventory):
        # cognitive:30 (+2) + complexity:5 (+1) = 3 → high
        level, source, score = scan_inventory.score_method(
            "foo", ["cognitive:50", "complexity:5"])
        assert level == "high" and score == 3

    def test_lines_alone_mid(self, scan_inventory):
        # lines≥50 单独只能到 mid (+1)
        level, source, score = scan_inventory.score_method("foo", ["lines:100"])
        assert level == "mid" and score == 1

    def test_lines_large_also_mid(self, scan_inventory):
        # lines≥150 也只 +1（保守加分）
        level, source, score = scan_inventory.score_method("foo", ["lines:200"])
        assert level == "mid" and score == 1

    def test_loop_count(self, scan_inventory):
        # loop_count≥5 → +1
        level, source, score = scan_inventory.score_method("foo", ["loop_count:8"])
        assert level == "mid" and score == 1

    def test_loop_count_below_threshold(self, scan_inventory):
        # loop_count<5 → 不加分
        level, source, score = scan_inventory.score_method("foo", ["loop_count:4"])
        assert level == "low" and score == 0

    def test_alloc_in_loop(self, scan_inventory):
        # alloc_in_loop≥1 → +1
        level, source, score = scan_inventory.score_method("foo", ["alloc_in_loop:3"])
        assert level == "mid" and score == 1

    def test_recursive(self, scan_inventory):
        # recursive → +1
        level, source, score = scan_inventory.score_method("foo", ["recursive"])
        assert level == "mid" and score == 1

    def test_combined_cognitive_lines_complexity(self, scan_inventory):
        # cognitive:30 (+2) + lines:100 (+1) + complexity:5 (+1) = 4 → high
        level, source, score = scan_inventory.score_method(
            "foo", ["cognitive:40", "lines:100", "complexity:6"])
        assert level == "high" and score == 4

    def test_risk_factors_combined(self, scan_inventory):
        # loop_count:8 (+1) + alloc_in_loop:5 (+1) + recursive (+1) = 3 → high
        level, source, score = scan_inventory.score_method(
            "foo", ["loop_count:8", "alloc_in_loop:5", "recursive"])
        assert level == "high" and score == 3


# ── build_inventory ───────────────────────────────────────────────────

class TestBuildInventory:
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

    def test_empty_data(self, scan_inventory):
        inv = scan_inventory.build_inventory(self._minimal_dump(), "proj", "sha123")
        assert inv["project"] == "proj"
        assert inv["base_sha"] == "sha123"
        assert inv["methods"] == []
        assert "scan_stats" in inv

    def test_missing_p75_defaults(self, scan_inventory):
        dump = self._minimal_dump()
        del dump["in_degree_p75_nonzero"]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        # 不应崩溃，p75 回退默认 5
        assert inv["base_sha"] == "sha"

    def test_single_method(self, scan_inventory):
        dump = self._minimal_dump()
        dump["in_degree_p75_nonzero"] = 2  # 让 in_degree:2 >= p75 触发因子
        dump["methods"] = [{
            "qualified_name": "proj.Calc.add",
            "name": "add",
            "class_qn": "proj.Calc",
            "file_path": "src/calc.cpp",
            "in_degree": 2,
            "complexity": 15,
            "signature": "int add(int, int)",
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        assert len(inv["methods"]) == 1
        m = inv["methods"][0]
        # complexity:15 → +2, in_degree:2>=p75 → +1 = 3 → high
        assert m["level"] == "high"
        assert "in_degree:2" in m["factors"]
        assert any(f.startswith("complexity:") for f in m["factors"])

    def test_function_noise_filtered(self, scan_inventory):
        dump = self._minimal_dump()
        dump["functions"] = [
            {"name": "DGUI_USE_NAMESPACE", "qualified_name": "x.DGUI_USE_NAMESPACE"},
            {"name": "Q_OBJECT", "qualified_name": "x.Q_OBJECT"},
            {"name": "main", "qualified_name": "x.main", "complexity": 5},
        ]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        # 噪声被过滤，只留 main
        names = [m["name"] for m in inv["methods"]]
        assert "main" in names
        assert "DGUI_USE_NAMESPACE" not in names
        assert "Q_OBJECT" not in names

    def test_missing_methods_key(self, scan_inventory):
        # 缺 methods 键不应崩溃
        dump = {"project": "p", "functions": []}
        inv = scan_inventory.build_inventory(dump, "p", "sha")
        assert inv["methods"] == []

    def test_stats_consistency(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [
            {"qualified_name": "A", "name": "a", "complexity": 20, "in_degree": 1},
            {"qualified_name": "B", "name": "b", "complexity": 1, "in_degree": 0},
        ]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        stats = inv["scan_stats"]
        # testable + non_testable == filtered_methods（过滤后的总数）
        assert stats["testable"] + stats["non_testable"] == stats["filtered_methods"]

    # ── 新因子提取测试 ──

    def test_cognitive_factor_extraction(self, scan_inventory):
        """build_inventory 从 method 提取 cognitive 因子"""
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.foo", "name": "foo",
            "cognitive": 35, "complexity": 3,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert any(f.startswith("cognitive:") for f in m["factors"])
        # cognitive:35 → +2, complexity:3 → 0 → score=2 → mid
        assert m["level"] == "mid"

    def test_cognitive_below_threshold_no_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.bar", "name": "bar",
            "cognitive": 10,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert not any(f.startswith("cognitive:") for f in m["factors"])

    def test_cognitive_missing_key_no_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{"qualified_name": "A.baz", "name": "baz"}]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert not any(f.startswith("cognitive:") for f in m["factors"])

    def test_cognitive_zero_no_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.qux", "name": "qux", "cognitive": 0,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert not any(f.startswith("cognitive:") for f in m["factors"])

    def test_lines_factor_extraction(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.long_func", "name": "long_func",
            "lines": 80, "complexity": 3,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert any(f.startswith("lines:") for f in m["factors"])
        # lines:80 → +1 → mid
        assert m["level"] == "mid"

    def test_lines_below_50_no_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.short_func", "name": "short_func",
            "lines": 30,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert not any(f.startswith("lines:") for f in m["factors"])

    def test_lines_150_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.huge_func", "name": "huge_func",
            "lines": 200, "complexity": 3,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        # lines>=150 和 >=50 都是 +1
        lines_factors = [f for f in m["factors"] if f.startswith("lines:")]
        assert len(lines_factors) == 1  # 只追加一次

    def test_loop_count_factor_extraction(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.loopy", "name": "loopy",
            "loop_count": 8,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert any(f.startswith("loop_count:") for f in m["factors"])

    def test_loop_count_below_5_no_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.loopy2", "name": "loopy2",
            "loop_count": 3,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert not any(f.startswith("loop_count:") for f in m["factors"])

    def test_alloc_in_loop_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.alloc", "name": "alloc",
            "alloc_in_loop": 2,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert any(f.startswith("alloc_in_loop:") for f in m["factors"])

    def test_alloc_in_loop_zero_no_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.noalloc", "name": "noalloc",
            "alloc_in_loop": 0,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert not any(f.startswith("alloc_in_loop:") for f in m["factors"])

    def test_recursive_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.rec", "name": "rec",
            "recursive": True,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert "recursive" in m["factors"]

    def test_recursive_false_no_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.norec", "name": "norec",
            "recursive": False,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert "recursive" not in m["factors"]

    def test_recursive_missing_key_no_factor(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{"qualified_name": "A.norec2", "name": "norec2"}]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert "recursive" not in m["factors"]

    def test_complexity_threshold_8_in_build(self, scan_inventory):
        """build_inventory 中 complexity>=8 追加因子"""
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.c8", "name": "c8",
            "complexity": 8,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert any(f == "complexity:8" for f in m["factors"])

    def test_complexity_7_in_build(self, scan_inventory):
        """build_inventory 中 complexity=7 只追加 complexity:7 因子（>=5 阈值）"""
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.c7", "name": "c7",
            "complexity": 7,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert any(f == "complexity:7" for f in m["factors"])

    def test_all_new_factors_combined(self, scan_inventory):
        """cognitive + lines + loop_count + alloc_in_loop + recursive + complexity 全叠加"""
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.mega", "name": "mega",
            "cognitive": 40, "lines": 200, "loop_count": 8,
            "alloc_in_loop": 3, "recursive": True, "complexity": 15,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        factor_prefixes = [f.split(":")[0] if ":" in f else f for f in m["factors"]]
        assert "cognitive" in factor_prefixes
        assert "lines" in factor_prefixes
        assert "loop_count" in factor_prefixes
        assert "alloc_in_loop" in factor_prefixes
        assert "recursive" in m["factors"]
        assert "complexity" in factor_prefixes
        # cognitive:40(+2) + lines:200(+1) + loop_count:8(+1)
        # + alloc_in_loop:3(+1) + recursive(+1) + complexity:15(+2) = 8 → high
        assert m["level"] == "high"

    def test_cognitive_none_handled(self, scan_inventory):
        """cognitive=None 应被 or 0 处理"""
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.cn", "name": "cn",
            "cognitive": None,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert not any(f.startswith("cognitive:") for f in m["factors"])

    def test_lines_none_handled(self, scan_inventory):
        dump = self._minimal_dump()
        dump["methods"] = [{
            "qualified_name": "A.ln", "name": "ln",
            "lines": None,
        }]
        inv = scan_inventory.build_inventory(dump, "proj", "sha")
        m = inv["methods"][0]
        assert not any(f.startswith("lines:") for f in m["factors"])


# ── generate_summary ──────────────────────────────────────────────────

class TestGenerateSummary:
    def test_empty_inventory(self, scan_inventory):
        # generate_summary 需要完整 stats 字段；直接用 build_inventory 产出
        inv = scan_inventory.build_inventory(
            {"project": "p", "methods": [], "functions": [],
             "in_degree_p75_nonzero": 5}, "p", "sha")
        summary = scan_inventory.generate_summary(inv)
        assert "p" in summary
        assert isinstance(summary, str)
        assert "函数重要性探测报告" in summary
