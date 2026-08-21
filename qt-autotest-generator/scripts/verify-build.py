#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 Uniontech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
verify-build.py — Mode 2 编译验证·执行层固化脚本

固化 build-verifier.md §1–§4 的「执行」部分：cmake configure → 编译目标 → 运行测试，
按错误分类表正则预分类，输出 ~10-20 行结构化摘要。模型只消费摘要做修复决策，
不再读完整 build log（原始 log 落盘 .results/ 供定向回读）。

模型保留职责（本脚本不做）：
  - 修复决策（错误 → 改哪里）
  - 迭代计数 / 3 轮上限（Iron Law #10，调度语义在模型侧）
  - 源码缺陷判定（source_defect_*，需 get_code_snippet 确认）

用法:
  python3 verify-build.py --project /path/to/project --class Calculator [--module core]
  python3 verify-build.py --project . --class FileManager --module io --test-dir tests --timeout 60
  python3 verify-build.py --project . --target test_core --class Calculator   # 显式指定 target

输出摘要格式（stdout，退出码 0=全通过 / 1=失败）:
  [VERIFY] Calculator | build: FAIL | run: skipped
  errors:
    E1 undefined_reference | 'Calculator::extra() const' | test_calculator.cpp:42
    E2 no_such_file | 'config.h' not found | test_calculator.cpp:8
  gtest: n/a
  hint: E1 → trace_path 重查传递依赖或补 target_link_libraries；E2 → 补 target_include_directories
  log: autotests/.results/build-calculator.log

  [VERIFY] Calculator | build: PASS | run: PASS
  errors: (none)
  gtest: 15 tests, 15 pass, 0 fail (0.31s)
  xml: autotests/.results/test-calculator.xml
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# 错误分类表（顺序即优先级：特殊模式在前，通用 undefined_reference 兜底）
# 与 build-verifier.md §2 错误分类表一一对应
# ---------------------------------------------------------------------------

ERROR_PATTERNS = [
    # undefined reference to `stub_ext::freeWrapper' → stub-shadow.cpp 未编入
    ("stub_ext_freewrapper",
     re.compile(r"undefined reference to [`'\"]?stub_ext::freeWrapper"),
     "确认 templates/stub-ext/stub-shadow.cpp 已编入 test target"),
    # undefined reference to `vtable for XXX' → 缺 Q_OBJECT / MOC
    ("vtable",
     re.compile(r"undefined reference to [`'\"]?vtable for (\w+)"),
     "检查 Q_OBJECT 宏与 MOC 处理"),
    # undefined reference to `X' → 链接缺依赖
    ("undefined_reference",
     re.compile(r"undefined reference to [`'\"]([^`'\"]+)[`'\"]"),
     "trace_path 重查传递依赖或补 target_link_libraries"),
    # fatal error: xxx.h: No such file or directory → include 路径缺失
    ("no_such_file",
     re.compile(r"fatal error:\s*([^:]+):\s*No such file or directory"),
     "补 target_include_directories"),
    # stub.set_lamda 签名不匹配
    ("stub_signature",
     re.compile(r"no matching function for call to [`'\"]?\w*\.?\s*set_lamda"),
     "get_code_snippet 重读方法签名，修正 stub"),
    # expected primary-expression → 重载/类型问题
    ("primary_expression",
     re.compile(r"expected primary-expression before ['\"]?(\w+)"),
     "检查返回/参数类型，用 static_cast 修正重载"),
    # CMake Error → CMakeLists 语法/配置
    ("cmake_error",
     re.compile(r"CMake Error(?:\.[a-z]+)? (?:at )?([^\s:(]+)"),
     "修 CMakeLists.txt 语法"),
    # 通用兑底：任何 error: 行（分类表未覆盖的编译错误，如 'has no member named'）
    ("compile_error",
     re.compile(r"\berror:\s*(.+?)\s*(?:\[-W|;)?$"),
     "读 log 定位错误，按 build-verifier.md §2 分类处理"),
]

LOCATION_PREFIX = re.compile(r"^([^:\s]+\.(?:cpp|h|hpp|cc|cxx|txt)):(\d+)(?::\d+)?:")

MAX_SUMMARY_ERRORS = 8


def run_cmd(cmd, cwd, timeout):
    """执行命令，返回 (returncode, stdout+stderr 合并文本)。

    注入 LC_ALL=C：gcc/ld 错误文本会被本地化（如"没有那个文件或目录"），
    强制 C locale 保证错误分类正则稳定匹配英文原文。
    """
    env = dict(os.environ, LC_ALL="C", LANG="C", LANGUAGE="C")
    try:
        p = subprocess.run(
            cmd, cwd=cwd, timeout=timeout, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )
        return p.returncode, p.stdout or ""
    except subprocess.TimeoutExpired:
        return 124, ""
    except FileNotFoundError as e:
        return 127, str(e)


def classify_errors(log_text):
    """按分类表提取错误，去重，返回 [(kind, detail, location), ...]。"""
    errors = []
    seen = set()
    lines = log_text.splitlines()
    for i, line in enumerate(lines):
        # 跳过 make/ninja/collect2 的命令回显与噪声行，减少误报
        if line.startswith(("make[", "ninja:", "[ ", "cd ", "collect2:", "gmake")):
            continue
        for kind, pat, _hint in ERROR_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            # 提取错误细节（捕获组若有则用之，否则整行截断）
            detail = (m.group(1) if m.groups() else "").strip()
            if not detail:
                detail = line.strip()[:100]
            # 定位 file:line —— 优先同行前缀，其次向上找最近的位置前缀
            location = ""
            lm = LOCATION_PREFIX.search(line)
            if lm:
                location = f"{os.path.basename(lm.group(1))}:{lm.group(2)}"
            else:
                for j in range(i - 1, max(i - 6, -1), -1):
                    pm = LOCATION_PREFIX.search(lines[j])
                    if pm:
                        location = f"{os.path.basename(pm.group(1))}:{pm.group(2)}"
                        break
            key = (kind, detail, location)
            if key in seen:
                break
            seen.add(key)
            errors.append(key)
            break  # 一行只归一个类
        if len(errors) >= MAX_SUMMARY_ERRORS:
            break
    return errors


def find_target(build_dir, class_target, module_target):
    """用 `cmake --build --target help` 解析可用 target：类 target 优先，模块 target 兜底。"""
    code, out = run_cmd(
        ["cmake", "--build", ".", "--target", "help"], build_dir, 60)
    if code != 0:
        # help target 不可用（生成器不支持），按声明顺序返回候选，由调用方尝试
        return [t for t in (class_target, module_target) if t]
    candidates = []
    for line in out.splitlines():
        # make 风格 "... test_calculator"（前导省略号）/ ninja 风格 "test_core: ..."
        stripped = line.strip(". \t")
        if not stripped:
            continue
        token = stripped.split(":")[0].split()[0] if stripped.split(":")[0].split() else ""
        if not token or token.startswith((".", "_")):
            continue
        if token in (class_target, module_target) and token not in candidates:
            candidates.append(token)
    # 排序：类 target 在前
    candidates.sort(key=lambda t: (t != class_target,))
    return candidates


def find_binary(build_dir, test_dir, target):
    """定位测试可执行文件：build_dir/{test_dir}/**/{target}，glob 兜底。"""
    direct = os.path.join(build_dir, test_dir)
    for path in [os.path.join(direct, "**", target), os.path.join(build_dir, "**", target)]:
        hits = [h for h in glob.glob(path, recursive=True)
                if os.path.isfile(h) and os.access(h, os.X_OK)]
        if hits:
            return sorted(hits, key=len)[0]  # 最浅路径优先
    return None


def _failure_brief(message):
    """从 gtest failure message 提取 'file.cpp:42 | Expected ...' 简报，去绝对路径。

    兼容两种首行格式：'path:line: Failure' 与裸 'path:line'。
    """
    lines = [l.strip() for l in (message or "").splitlines() if l.strip()]
    for i, line in enumerate(lines):
        head = line[: -len(": Failure")] if line.endswith(": Failure") else line
        m = re.match(r"^(.+?):(\d+)$", head)
        if not m:
            continue
        loc = f"{os.path.basename(m.group(1))}:{m.group(2)}"
        info = lines[i + 1][:60] if i + 1 < len(lines) else ""
        return f"{loc} | {info}" if info else loc
    return lines[0][:80] if lines else "failure"


def parse_gtest_xml(xml_path):
    """解析 gtest XML → (总用例数, 失败数, 失败清单[(name, 首行失败信息)], 耗时秒)。"""
    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, OSError):
        return None
    root = tree.getroot()
    total = 0
    failed = []
    time_sec = 0.0
    suites = root.iter("testsuite")
    for suite in suites:
        total += int(suite.get("tests", "0"))
        time_sec += float(suite.get("time", "0")[:-1] if suite.get("time", "").endswith("s")
                          else suite.get("time", "0") or 0)
        for case in suite.iter("testcase"):
            for fail in case.findall("failure"):
                failed.append((case.get("name", "?"), _failure_brief(fail.get("message"))))
                break
    return total, len(failed), failed, time_sec


def main():
    ap = argparse.ArgumentParser(
        description="Mode 2 编译验证·执行层固化：configure + build + run，输出预分类摘要")
    ap.add_argument("--project", required=True, help="项目根路径")
    ap.add_argument("--class", dest="classname", required=True,
                    help="类名（任意大小写），target 默认 test_<小写类名>")
    ap.add_argument("--module", default=None,
                    help="模块名（test_dir 下的子目录）；类 target 不存在时回退 test_<module>")
    ap.add_argument("--target", default=None,
                    help="显式指定 CMake target（优先于自动推导）")
    ap.add_argument("--test-dir", default=None,
                    help="测试目录名（autotests/tests），默认自动探测")
    ap.add_argument("--build-dir", default=None,
                    help="构建目录，默认 {project}/build-{test_dir}")
    ap.add_argument("--timeout", type=int, default=120,
                    help="单类测试运行超时秒数（默认 120，同 build-verifier.md §3）")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4,
                    help="并行编译任务数")
    ap.add_argument("--skip-run", action="store_true",
                    help="只编译不运行（框架搭建阶段的快速检查）")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    if not os.path.isdir(project):
        print(f"[VERIFY] error: project not found: {project}")
        return 2

    # -- 探测 test_dir --
    test_dir = args.test_dir
    if not test_dir:
        for cand in ("autotests", "tests"):
            if os.path.isdir(os.path.join(project, cand)):
                test_dir = cand
                break
        else:
            print("[VERIFY] error: test dir not found (autotests/ or tests/), use --test-dir")
            return 2

    build_dir = os.path.abspath(args.build_dir or os.path.join(project, f"build-{test_dir}"))
    os.makedirs(build_dir, exist_ok=True)

    class_lower = re.sub(r"(?<!^)(?=[A-Z])", "_", args.classname).lower() \
        if any(c.isupper() for c in args.classname[1:]) else args.classname.lower()
    class_target = f"test_{class_lower}"
    module_target = f"test_{args.module}" if args.module else None
    primary_target = args.target or class_target

    results_dir = os.path.join(project, test_dir, ".results")
    os.makedirs(results_dir, exist_ok=True)
    build_log_path = os.path.join(results_dir, f"build-{class_lower}.log")
    xml_path = os.path.join(results_dir, f"test-{class_lower}.xml")

    log_parts = []
    summary_errors = []
    hints = []
    build_status = "FAIL"
    run_status = "skipped"
    gtest_line = "gtest: n/a"

    # -- 1. configure --
    # source 用项目绝对路径而非 ".."：--build-dir 可能在项目外（如 /tmp），".." 会指错源
    # -DBUILD_TESTS：新建框架（sample-qt-project 风格）的开关；
    # -DCMAKE_BUILD_TYPE=Debug：存量项目（如 deepin-image-viewer）用 Debug 作为
    #   add_subdirectory(tests) 开关，且覆盖率标志仅 Debug 启用（framework-builder.md）
    code, out = run_cmd(
        ["cmake", project, "-DBUILD_TESTS=ON", "-DCMAKE_BUILD_TYPE=Debug"],
        build_dir, 300)
    log_parts.append(
        f"$ cmake <project> -DBUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Debug  (rc={code})\n{out}")
    if code != 0:
        summary_errors = classify_errors(out) or [("configure_failed", "cmake configure rc!=0", "")]
        build_status = "FAIL (configure)"

    # -- 2. build --
    if build_status == "FAIL" and build_status.endswith("(configure)"):
        pass  # configure 已失败，跳过编译
    else:
        targets = ([args.target] if args.target
                   else find_target(build_dir, class_target, module_target))
        if not targets:
            summary_errors = [("no_target",
                               f"target {primary_target}"
                               + (f" / {module_target}" if module_target else "")
                               + " not found in CMake", "")]
            hints.append(f"E? → 确认 {test_dir}/CMakeLists.txt 已 add_subdirectory 且目标名正确")
        else:
            build_ok = False
            for tgt in targets:
                code, out = run_cmd(
                    ["cmake", "--build", ".", "-j", str(args.jobs), "--target", tgt],
                    build_dir, 1800)
                log_parts.append(f"$ cmake --build . --target {tgt}  (rc={code})\n{out}")
                if code == 0:
                    build_ok = True
                    primary_target = tgt
                    break
                errs = classify_errors(out)
                if errs:
                    summary_errors = errs
                    break  # 已分类的编译错误无需再试下一个 target
                # rc!=0 但无已识别错误（如 unknown target）→ 尝试下一候选
            build_status = "PASS" if build_ok else "FAIL"

    # -- 3. run --
    if build_status == "PASS" and not args.skip_run:
        binary = find_binary(build_dir, test_dir, primary_target)
        if not binary:
            summary_errors = [("binary_not_found", f"{primary_target} built but executable not located", "")]
            run_status = "not_run"
        else:
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
            try:
                p = subprocess.run(
                    [binary, f"--gtest_output=xml:{xml_path}"],
                    cwd=build_dir, timeout=args.timeout, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, errors="replace")
                code, out = p.returncode, p.stdout or ""
            except subprocess.TimeoutExpired:
                code, out = 124, ""
            log_parts.append(f"$ {binary} --gtest_output=xml  (rc={code})\n{out}")

            parsed = parse_gtest_xml(xml_path)
            if code == 124:
                run_status = "FAIL (timeout)"
                summary_errors.append(("runtime_timeout",
                                       f"exceeded {args.timeout}s — 可能死循环或 stub 缺失导致真实 IO", ""))
                hints.append("E? → 补 stub 后重试；仍超时按 build-verifier.md §4 判定")
            elif code < 0 or code in (134, 139, 136):
                run_status = f"FAIL (crash rc={code})"
                summary_errors.append(("runtime_crash",
                                       f"signal/abort rc={code} — stub 不全或源码缺陷", ""))
                hints.append("E? → 先补 stub；仍崩溃 → get_code_snippet 确认后标红")
            elif parsed is None:
                run_status = "FAIL" if code != 0 else "PASS (no xml)"
            else:
                total, nfail, failures, t = parsed
                run_status = "PASS" if (nfail == 0 and code == 0) else "FAIL"
                gtest_line = f"gtest: {total} tests, {total - nfail} pass, {nfail} fail ({t:.2f}s)"
                for name, msg in failures[:MAX_SUMMARY_ERRORS]:
                    summary_errors.append(("assert_failure", f"{name} | {msg}", ""))
                if nfail:
                    hints.append("E? → 检查测试逻辑；逻辑正确而断言恒失败 → 疑似源码缺陷")

    # -- 4. 写 log + 输出摘要 --
    with open(build_log_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(log_parts))

    hints_line = ""
    if summary_errors and not any(h.startswith("E?") for h in hints):
        # 注意：no_target / assert_failure / runtime_* 等运行期类别不在 ERROR_PATTERNS 表内，
        # 查不到时用通用提示，不得抛 StopIteration
        def _hint_for(kind):
            return next((h for k, _, h in ERROR_PATTERNS if k == kind),
                        "读 log 定位错误，按 build-verifier.md 处理")

        hints_line = "hint: " + "；".join(
            f"E{i+1} → {_hint_for(kind)}"
            for i, (kind, _, _) in enumerate(summary_errors))
    elif hints:
        hints_line = "hint: " + "；".join(hints)

    print(f"[VERIFY] {args.classname} | build: {build_status} | run: {run_status}")
    if summary_errors:
        print("errors:")
        for i, (kind, detail, loc) in enumerate(summary_errors):
            print(f"  E{i+1} {kind} | {detail}" + (f" | {loc}" if loc else ""))
    else:
        print("errors: (none)")
    print(gtest_line)
    if os.path.exists(xml_path):
        print(f"xml: {os.path.relpath(xml_path, project)}")
    if hints_line:
        print(hints_line)
    print(f"log: {os.path.relpath(build_log_path, project)}")

    ok = build_status == "PASS" and run_status in ("PASS", "PASS (no xml)", "skipped", "not_run")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
