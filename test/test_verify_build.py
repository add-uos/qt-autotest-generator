"""verify-build.py 单元测试。

聚焦错误分类表、gtest XML 解析、失败简报提取、target/二进制定位、
命令执行（LC_ALL 注入/超时）的边界场景。端到端编译验证已在
sample-qt-project 上人工实测（见 doc/mode2-script-offload-design.md §2.2），
此处只测纯函数与可 monkeypatch 的薄封装。
"""
import os
import textwrap

import pytest


# ── classify_errors：错误分类表 ───────────────────────────────────────

class TestClassifyErrors:
    def test_undefined_reference(self, verify_build):
        log = "test.cpp:42: undefined reference to `Calculator::extra() const'"
        errs = verify_build.classify_errors(log)
        assert errs == [("undefined_reference", "Calculator::extra() const",
                         "test.cpp:42")]

    def test_vtable_priority_over_undefined_reference(self, verify_build):
        # vtable 本身也是 undefined reference，特殊模式必须先命中且只报一个
        log = "manager.cpp:10: undefined reference to `vtable for FileManager'"
        errs = verify_build.classify_errors(log)
        assert errs == [("vtable", "FileManager", "manager.cpp:10")]

    def test_stub_ext_freewrapper(self, verify_build):
        log = "stub-shadow.o: undefined reference to `stub_ext::freeWrapper(void*)'"
        errs = verify_build.classify_errors(log)
        assert errs[0][0] == "stub_ext_freewrapper"

    def test_no_such_file_with_same_line_location(self, verify_build):
        log = ("test_calculator.cpp:7:10: fatal error: no_such_header.h: "
               "No such file or directory")
        errs = verify_build.classify_errors(log)
        assert errs == [("no_such_file", "no_such_header.h",
                         "test_calculator.cpp:7")]

    def test_stub_signature(self, verify_build):
        log = ("test_x.cpp:20:15: error: no matching function for call to "
               "'stub.set_lamda<int(int)>'")
        errs = verify_build.classify_errors(log)
        assert errs[0][0] == "stub_signature"

    def test_primary_expression(self, verify_build):
        log = "test_x.cpp:31:22: error: expected primary-expression before 'int'"
        errs = verify_build.classify_errors(log)
        assert errs[0][0] == "primary_expression"
        assert errs[0][1] == "int"

    def test_cmake_error(self, verify_build):
        log = "CMake Error at autotests/CMakeLists.txt:12 (target_link_libraries):"
        errs = verify_build.classify_errors(log)
        assert errs[0][0] == "cmake_error"
        assert errs[0][1] == "autotests/CMakeLists.txt"

    def test_compile_error_fallback(self, verify_build):
        # 分类表外的编译错误走兜底，不得静默丢弃
        log = ("test_calculator.cpp:33:10: error: 'class Calculator' has no "
               "member named 'nosuchmethod'")
        errs = verify_build.classify_errors(log)
        assert errs[0][0] == "compile_error"
        assert "nosuchmethod" in errs[0][1]
        assert errs[0][2] == "test_calculator.cpp:33"

    def test_make_noise_lines_filtered(self, verify_build):
        # make/gmake/collect2 噪声行不得产生 compile_error 误报
        log = "\n".join([
            "gmake[3]: *** [CMakeFiles/test_core.dir/build.make:79: Error] 1",
            "make[1]: *** [Makefile:147: test_calculator] Error 2",
            "collect2: error: ld returned 1 exit status",
            "ninja: build stopped: subcommand failed.",
        ])
        assert verify_build.classify_errors(log) == []

    def test_dedup_same_error(self, verify_build):
        line = ("test.cpp:7:10: fatal error: config.h: "
                "No such file or directory")
        errs = verify_build.classify_errors(line + "\n" + line)
        assert len(errs) == 1

    def test_location_back_search(self, verify_build):
        # 错误行本身无位置前缀时，向上找最近的位置前缀
        log = "\n".join([
            "In file included from test_x.cpp:2:",
            "from /usr/include/x.h:4,",
            "test_x.cpp:88:31: error: 'class Foo' has no member named 'bar'",
        ])
        errs = verify_build.classify_errors(log)
        assert errs[0][2] == "test_x.cpp:88"

    def test_location_back_search_bounded(self, verify_build):
        # 位置前缀距离超过 5 行 → 放弃定位，不误挂
        lines = ["src/a.cpp:1:1: note: far away"] + ["filler"] * 7
        lines.append("error: 'x' does not name a type")
        errs = verify_build.classify_errors("\n".join(lines))
        assert errs[0][2] == ""

    def test_max_summary_errors_cap(self, verify_build):
        lines = [
            f"test.cpp:{i}: error: 'class T' has no member named 'm{i}'"
            for i in range(20)
        ]
        errs = verify_build.classify_errors("\n".join(lines))
        assert len(errs) == verify_build.MAX_SUMMARY_ERRORS

    def test_one_error_per_line(self, verify_build):
        # 同一行命中多个模式时只归一类（第一个命中的）
        log = ("test.cpp:1:1: fatal error: a.h: No such file or directory "
               "undefined reference to `f()'")
        errs = verify_build.classify_errors(log)
        assert len(errs) == 1

    def test_empty_log(self, verify_build):
        assert verify_build.classify_errors("") == []


# ── _failure_brief：gtest 失败信息提取 ────────────────────────────────

class TestFailureBrief:
    def test_with_failure_suffix(self, verify_build):
        msg = ("/home/u/proj/autotests/core/test_calc.cpp:42: Failure\n"
               "Expected equality of these values:\n  9\n  8")
        assert verify_build._failure_brief(msg) == (
            "test_calc.cpp:42 | Expected equality of these values:")

    def test_bare_path_line(self, verify_build):
        # 新版 gtest message 首行不带 ": Failure" 后缀
        msg = ("/home/u/proj/autotests/core/test_calc.cpp:35\n"
               "Expected equality of these values:\n")
        assert verify_build._failure_brief(msg) == (
            "test_calc.cpp:35 | Expected equality of these values:")

    def test_absolute_path_stripped(self, verify_build):
        msg = "/very/long/abs/path/test_x.cpp:9: Failure\nmsg"
        assert "/very" not in verify_build._failure_brief(msg)

    def test_no_location_falls_back_to_first_line(self, verify_build):
        assert verify_build._failure_brief("some random failure text") == \
            "some random failure text"

    def test_empty_message(self, verify_build):
        assert verify_build._failure_brief("") == "failure"
        assert verify_build._failure_brief(None) == "failure"

    def test_multiline_only_location(self, verify_build):
        # 只有位置行、无后续信息行 → 只返回位置
        assert verify_build._failure_brief("/a/b/c.cpp:7") == "c.cpp:7"


# ── parse_gtest_xml ───────────────────────────────────────────────────

class TestParseGtestXml:
    def _write(self, path, content):
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(path)

    def test_pass_only(self, verify_build, tmp_path):
        xml = self._write(tmp_path / "ok.xml", """\
            <?xml version="1.0"?>
            <testsuites>
              <testsuite name="CalcTest" tests="2" time="0.05">
                <testcase name="Add_Basic" status="run" time="0.02"/>
                <testcase name="Add_Zero" status="run" time="0.03"/>
              </testsuite>
            </testsuites>""")
        total, nfail, failures, t = verify_build.parse_gtest_xml(xml)
        assert total == 2 and nfail == 0 and failures == []
        assert t == pytest.approx(0.05)

    def test_with_failures(self, verify_build, tmp_path):
        xml = self._write(tmp_path / "fail.xml", """\
            <?xml version="1.0"?>
            <testsuites>
              <testsuite name="T1" tests="2" time="1.0s">
                <testcase name="A" status="run" time="0.1"/>
                <testcase name="B" status="run" time="0.1">
                  <failure message="/p/test_x.cpp:9&#10;Expected: 8&#10;Actual: 9" type=""/>
                </testcase>
              </testsuite>
            </testsuites>""")
        total, nfail, failures, t = verify_build.parse_gtest_xml(xml)
        assert total == 2 and nfail == 1
        assert failures[0][0] == "B"
        assert failures[0][1] == "test_x.cpp:9 | Expected: 8"
        assert t == pytest.approx(1.0)  # "1.0s" 后缀被剥

    def test_multiple_suites_summed(self, verify_build, tmp_path):
        xml = self._write(tmp_path / "multi.xml", """\
            <?xml version="1.0"?>
            <testsuites>
              <testsuite name="T1" tests="3" time="0.1"/>
              <testsuite name="T2" tests="4" time="0.2"/>
            </testsuites>""")
        total, _, _, t = verify_build.parse_gtest_xml(xml)
        assert total == 7 and t == pytest.approx(0.3)

    def test_missing_file(self, verify_build, tmp_path):
        assert verify_build.parse_gtest_xml(str(tmp_path / "nope.xml")) is None

    def test_malformed_xml(self, verify_build, tmp_path):
        p = tmp_path / "bad.xml"
        p.write_text("<testsuites><testsuite", encoding="utf-8")
        assert verify_build.parse_gtest_xml(str(p)) is None


# ── find_binary：可执行文件定位 ────────────────────────────────────────

class TestFindBinary:
    def _make_exec(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_direct_layout(self, verify_build, tmp_path):
        exe = self._make_exec(tmp_path / "autotests" / "core" / "test_core")
        found = verify_build.find_binary(str(tmp_path), "autotests", "test_core")
        assert found == str(exe)

    def test_deep_layout(self, verify_build, tmp_path):
        exe = self._make_exec(tmp_path / "autotests" / "a" / "b" / "test_calc")
        found = verify_build.find_binary(str(tmp_path), "autotests", "test_calc")
        assert found == str(exe)

    def test_not_executable_skipped(self, verify_build, tmp_path):
        p = tmp_path / "autotests" / "test_core"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not executable", encoding="utf-8")
        p.chmod(0o644)
        assert verify_build.find_binary(str(tmp_path), "autotests", "test_core") is None

    def test_shallowest_path_wins(self, verify_build, tmp_path):
        self._make_exec(tmp_path / "deep" / "a" / "b" / "c" / "test_x")
        shallow = self._make_exec(tmp_path / "test_x")
        found = verify_build.find_binary(str(tmp_path), "autotests", "test_x")
        assert found == str(shallow)

    def test_not_found(self, verify_build, tmp_path):
        assert verify_build.find_binary(str(tmp_path), "autotests", "test_x") is None


# ── find_target：cmake help 输出解析 ──────────────────────────────────

class TestFindTarget:
    def _patch(self, verify_build, monkeypatch, rc, out):
        monkeypatch.setattr(verify_build, "run_cmd", lambda *a, **k: (rc, out))

    def test_make_style_with_ellipsis(self, verify_build, monkeypatch):
        out = ("The following are some of the valid targets for this Makefile:\n"
               "... all (the default if no target is provided)\n"
               "... test_calculator\n"
               "... sample-qt-project\n")
        self._patch(verify_build, monkeypatch, 0, out)
        assert verify_build.find_target("/b", "test_calculator", None) == \
            ["test_calculator"]

    def test_ninja_style(self, verify_build, monkeypatch):
        out = ("test_core: phony cmake_object_order_depends_target_test_core\n"
               "all: phony test_core\n")
        self._patch(verify_build, monkeypatch, 0, out)
        assert verify_build.find_target("/b", "test_core", None) == ["test_core"]

    def test_class_target_sorted_first(self, verify_build, monkeypatch):
        out = "... test_core\n... test_filemanager\n"
        self._patch(verify_build, monkeypatch, 0, out)
        assert verify_build.find_target("/b", "test_filemanager", "test_core") == \
            ["test_filemanager", "test_core"]

    def test_help_unavailable_returns_declaration_order(self, verify_build, monkeypatch):
        # 生成器不支持 help target → 按声明顺序返回候选，由调用方尝试
        self._patch(verify_build, monkeypatch, 1, "")
        assert verify_build.find_target("/b", "test_a", "test_b") == \
            ["test_a", "test_b"]

    def test_no_candidates(self, verify_build, monkeypatch):
        self._patch(verify_build, monkeypatch, 0, "... all\n... other\n")
        assert verify_build.find_target("/b", "test_x", None) == []


# ── run_cmd：命令执行封装 ─────────────────────────────────────────────

class TestRunCmd:
    def test_locale_forced_to_c(self, verify_build, tmp_path):
        # gcc/ld 错误文本本地化会破坏分类正则，必须强制 C locale
        _, out = verify_build.run_cmd(
            ["sh", "-c", "echo $LC_ALL $LANG"], str(tmp_path), 10)
        assert out.strip() == "C C"

    def test_cwd_respected(self, verify_build, tmp_path):
        _, out = verify_build.run_cmd(["pwd"], str(tmp_path), 10)
        assert os.path.realpath(out.strip()) == os.path.realpath(str(tmp_path))

    def test_timeout_returns_124(self, verify_build, tmp_path):
        rc, out = verify_build.run_cmd(["sleep", "5"], str(tmp_path), 0.2)
        assert rc == 124

    def test_missing_command_returns_127(self, verify_build, tmp_path):
        rc, _ = verify_build.run_cmd(["no-such-cmd-xyz"], str(tmp_path), 10)
        assert rc == 127

    def test_stderr_merged(self, verify_build, tmp_path):
        rc, out = verify_build.run_cmd(
            ["sh", "-c", "echo err >&2; true"], str(tmp_path), 10)
        assert rc == 0 and "err" in out

    def test_returncode_propagated(self, verify_build, tmp_path):
        rc, _ = verify_build.run_cmd(["sh", "-c", "exit 2"], str(tmp_path), 10)
        assert rc == 2


# ── 类名 → target 名推导（main 的内联逻辑回归） ──────────────────────

class TestClassNameToTarget:
    def _to_target(self, verify_build, classname):
        s = classname
        return "test_" + (verify_build.re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()
                          if any(c.isupper() for c in s[1:]) else s.lower())

    @pytest.mark.parametrize("classname,target", [
        ("Calculator", "test_calculator"),       # 全小写 → 原样
        ("FileManager", "test_file_manager"),    # 驼峰 → snake
        ("DBusService", "test_d_bus_service"),   # 连续大写 → 逐字符拆
        ("fileview", "test_fileview"),           # 无大写 → 原样
    ])
    def test_conversion(self, verify_build, classname, target):
        assert self._to_target(verify_build, classname) == target
