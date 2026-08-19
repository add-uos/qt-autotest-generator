#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
collect-coverage-report.py — Mode 3 覆盖率采集与汇总

一条命令完成：编译 → 运行测试 → lcov 采集 → genhtml → 分级覆盖率 → 汇总 JSON。

产出目录结构（${REPORT_DIR}）:
  ├── report/                   # gtest XML
  │   └── report_<target>.xml
  ├── html/                     # lcov genhtml
  │   └── cov_<target>.html
  ├── coverage_by_level.json   # 分级覆盖率（来自 coverage_by_level.py）
  └── ut-summary.json          # 汇总 JSON（测试结果 + 总覆盖率 + 分级覆盖率）

用法:
  # 最简（自动探测 test target / build 目录）
  python3 scripts/collect-coverage-report.py /path/to/project

  # 指定更多参数
  python3 scripts/collect-coverage-report.py /path/to/project \\
      --build-dir build-ut --test-target my-test-binary \\
      --report-dir build-ut --inventory autotests/.ut-inventory.json

  # 跳过编译（仅重新采集覆盖率）
  python3 scripts/collect-coverage-report.py /path/to/project --skip-build

依赖: cmake, make, lcov, genhtml, c++filt
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


# ───────────────────────── helpers ─────────────────────────

def run(cmd, **kw):
    """Run subprocess, raise on failure."""
    print(f"  $ {cmd}" if isinstance(cmd, str) else f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, shell=isinstance(cmd, str), **kw)


def run_capture(cmd, **kw):
    """Run subprocess, capture output, don't raise."""
    return subprocess.run(cmd, capture_output=True, text=True, shell=isinstance(cmd, str), **kw)


def find_test_target(build_dir):
    """Find the gtest binary under build_dir (common patterns)."""
    candidates = list(Path(build_dir).glob("tests/*test*"))
    candidates += list(Path(build_dir).glob("autotests/*test*"))
    # Also look for a single test binary directly
    for p in sorted(Path(build_dir).rglob("*-test")):
        if p.is_file() and os.access(p, os.X_OK):
            candidates.append(p)
    for p in sorted(Path(build_dir).rglob("test_*")):
        if p.is_file() and os.access(p, os.X_OK):
            candidates.append(p)
    # Filter: executable, not directory
    candidates = [c for c in candidates if c.is_file() and os.access(c, os.X_OK)]
    if candidates:
        return str(candidates[0])
    return None


def parse_gtest_xml(xml_path):
    """Parse gtest JUnit XML → (total, passed, failed)."""
    total = passed = failed = 0
    try:
        root = ET.parse(xml_path).getroot()
        t = int(root.get("tests", 0))
        f = int(root.get("failures", 0))
        e = int(root.get("errors", 0))
        total += t
        failed += f + e
        passed += t - f - e
    except Exception as exc:
        print(f"Warning: failed to parse {xml_path}: {exc}", file=sys.stderr)
    return total, passed, failed


def parse_gtest_xml_suites(xml_path):
    """Parse gtest XML → list of {suite, tests, failures, errors, time}."""
    suites = []
    try:
        root = ET.parse(xml_path).getroot()
        for ts in root.iter("testsuite"):
            suites.append({
                "suite": ts.get("name", ""),
                "tests": int(ts.get("tests", 0)),
                "failures": int(ts.get("failures", 0)),
                "errors": int(ts.get("errors", 0)),
                "time": float(ts.get("time", 0)),
            })
    except Exception:
        pass
    return suites


def parse_lcov_summary(coverage_info):
    """Parse lcov --summary → {line_coverage, function_coverage}."""
    result = {}
    if not os.path.exists(coverage_info):
        return result
    p = run_capture(
        ["lcov", "--summary", coverage_info, "--rc", "lcov_branch_coverage=1"],
    )
    txt = p.stdout + p.stderr
    m = re.search(r"lines.*?:\s*([\d.]+)%\s*\((\d+)\s+of\s+(\d+)", txt)
    if m:
        pct, hit, tot = m.groups()
        result["line_coverage"] = {
            "total": int(tot), "passed": int(hit),
            "failed": int(tot) - int(hit), "coverage": f"{float(pct):.2f}%",
        }
    m = re.search(r"functions.*?:\s*([\d.]+)%\s*\((\d+)\s+of\s+(\d+)", txt)
    if m:
        pct, hit, tot = m.groups()
        result["function_coverage"] = {
            "total": int(tot), "passed": int(hit),
            "failed": int(tot) - int(hit), "coverage": f"{float(pct):.2f}%",
        }
    return result


def run_coverage_by_level(inventory, coverage_info, output_json):
    """Invoke coverage_by_level.py and return parsed JSON."""
    script = SCRIPT_DIR / "coverage_by_level.py"
    if not script.exists():
        print("Warning: coverage_by_level.py not found, skipping tiered coverage", file=sys.stderr)
        return None
    cmd = ["python3", str(script), "-i", inventory, "-c", coverage_info, "--json", "-o", output_json]
    run(cmd)
    if os.path.exists(output_json):
        return json.loads(open(output_json, encoding="utf-8").read())
    return None


# ───────────────────────── main ─────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Mode 3: 覆盖率采集与汇总（编译 → 运行 → lcov → 分级 → 汇总 JSON）"
    )
    ap.add_argument("project_dir", help="项目根目录绝对路径")
    ap.add_argument("--build-dir", default=None,
                    help="构建目录名（相对项目根），默认自动探测 build-ut / build-autotests / build")
    ap.add_argument("--test-target", default=None,
                    help="gtest 可执行文件路径（相对 build-dir），默认自动探测")
    ap.add_argument("--report-dir", default=None,
                    help="报告输出目录名（相对项目根），默认与 build-dir 相同")
    ap.add_argument("--inventory", default=None,
                    help=".ut-inventory.json 路径，默认自动探测 autotests/.ut-inventory.json 或 tests/.ut-inventory.json")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过编译（假定已编译），直接运行测试采集覆盖率")
    ap.add_argument("--build-type", default="Debug",
                    help="CMAKE_BUILD_TYPE（默认 Debug，启用覆盖率插桩）")
    ap.add_argument("--cmake-extra", nargs="*", default=[],
                    help="额外传给 cmake 的参数，如 -DBUILD_TESTS=ON")
    args = ap.parse_args()

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        ap.error(f"项目目录不存在: {project}")

    # ── 自动探测 build-dir / report-dir / test-target / inventory ──
    build_dir_name = args.build_dir
    if not build_dir_name:
        for candidate in ("build-ut", "build-autotests", "build"):
            if (project / candidate).is_dir():
                build_dir_name = candidate
                break
        if not build_dir_name:
            build_dir_name = "build-ut"

    build_dir = project / build_dir_name
    report_dir_name = args.report_dir or build_dir_name
    report_dir = project / report_dir_name

    inventory_path = args.inventory
    if not inventory_path:
        for candidate in ("autotests/.ut-inventory.json", "tests/.ut-inventory.json"):
            if (project / candidate).exists():
                inventory_path = str(project / candidate)
                break

    test_target = args.test_target
    if not test_target:
        found = find_test_target(str(build_dir))
        if found:
            test_target = found
        else:
            ap.error(f"未找到 gtest 可执行文件于 {build_dir}，请用 --test-target 指定")

    target_name = Path(test_target).name
    # Resolve: if not absolute, treat as relative to project root
    if not Path(test_target).is_absolute():
        test_target_abs = str(project / test_target)
    else:
        test_target_abs = test_target
    if not os.path.isfile(test_target_abs):
        ap.error(f"测试目标不存在: {test_target_abs}")

    print(f"\n{'='*60}")
    print(f"Mode 3 · 覆盖率采集与汇总")
    print(f"{'='*60}")
    print(f"  项目:      {project}")
    print(f"  构建目录:  {build_dir}")
    print(f"  报告目录:  {report_dir}")
    print(f"  测试目标:  {test_target_abs}")
    print(f"  inventory: {inventory_path or '(无)'}")

    # ── Step 1: 编译 ──
    if not args.skip_build:
        print(f"\n── Step 1: 编译 (Debug, 覆盖率插桩) ──")
        build_dir.mkdir(parents=True, exist_ok=True)
        cmake_args = [
            "cmake", str(project),
            f"-DCMAKE_BUILD_TYPE={args.build_type}",
            "-DCMAKE_SAFETYTEST_ARG=CMAKE_SAFETYTEST_ARG_ON",
        ] + args.cmake_extra
        run(cmake_args, cwd=str(build_dir))
        run(["cmake", "--build", str(build_dir), "-j", str(os.cpu_count() or 4)])
    else:
        print(f"\n── Step 1: 跳过编译 (--skip-build) ──")

    # ── Step 2: 运行测试 + gtest XML ──
    print(f"\n── Step 2: 运行测试 ──")
    xml_dir = report_dir / "report"
    xml_dir.mkdir(parents=True, exist_ok=True)
    xml_path = xml_dir / f"report_{target_name}.xml"

    env = os.environ.copy()
    env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "detect_leaks=1")

    p = run_capture(
        [test_target_abs, f"--gtest_output=xml:{xml_path}"],
        timeout=300, env=env,
    )
    test_exit_code = p.returncode
    if test_exit_code != 0:
        print(f"  ⚠ 测试退出码 {test_exit_code}（部分用例失败）")

    # ── Step 3: lcov 采集 ──
    print(f"\n── Step 3: lcov 采集 ──")
    coverage_info = build_dir / "coverage.info"
    filtered_info = build_dir / "coverage" / "filtered.info"

    run(["lcov", "-d", str(build_dir), "-c", "-o", str(coverage_info)])

    # Extract src, remove tests/autotests
    run([
        "lcov", "--extract", str(coverage_info), "*/src/*",
        "-o", str(coverage_info),
    ])
    run([
        "lcov", "--remove", str(coverage_info),
        "*/tests/*", "*/autotests/*", "*/3rdparty/*",
        "-o", str(coverage_info),
    ])

    # Keep a filtered copy for coverage_by_level.py (which expects a .info)
    (build_dir / "coverage").mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(coverage_info), str(filtered_info))

    # ── Step 4: genhtml ──
    print(f"\n── Step 4: genhtml ──")
    html_dir = report_dir / "html"
    run(["genhtml", "-o", str(html_dir), str(coverage_info)])

    # Rename index.html → cov_<target>.html (consistent naming)
    index_html = html_dir / "index.html"
    cov_html = html_dir / f"cov_{target_name}.html"
    if index_html.exists() and not cov_html.exists():
        shutil.move(str(index_html), str(cov_html))

    # ── Step 5: 分级覆盖率 ──
    print(f"\n── Step 5: 分级覆盖率 ──")
    tiered_json_path = report_dir / "coverage_by_level.json"
    tiered_data = None
    if inventory_path and os.path.exists(inventory_path):
        tiered_data = run_coverage_by_level(
            inventory_path, str(filtered_info), str(tiered_json_path),
        )
    else:
        print("  跳过（无 .ut-inventory.json）")

    # ── Step 6: 汇总 JSON ──
    print(f"\n── Step 6: 生成 ut-summary.json ──")

    # 6a. gtest 结果
    total, passed, failed = 0, 0, 0
    suites = []
    for xf in sorted(glob.glob(str(xml_dir / "*.xml"))):
        t, p, f = parse_gtest_xml(xf)
        total += t; passed += p; failed += f
        suites.extend(parse_gtest_xml_suites(xf))

    # 6b. lcov 总覆盖率
    lcov_data = parse_lcov_summary(str(coverage_info))

    # 6c. 组装
    summary = {
        "project": project.name,
        "build_dir": build_dir_name,
        "test_target": target_name,
        "test_cases": {
            "total": total,
            "passed": passed,
            "failed": failed,
        },
        "test_suites": suites,
    }
    summary.update(lcov_data)

    # 6d. 合并分级覆盖率（来自 coverage_by_level.py）
    if tiered_data:
        summary["tiered_coverage"] = {
            "by_level": tiered_data.get("by_level", {}),
            "total": tiered_data.get("total", {}),
            "uncovered_functions": tiered_data.get("uncovered_functions", []),
        }

    # Write
    summary_path = report_dir / "ut-summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"✅ 完成! 报告已写入 {report_dir}")
    print(f"{'='*60}")
    print(f"  gtest XML:     {xml_path}")
    print(f"  lcov HTML:     {cov_html}")
    print(f"  汇总 JSON:     {summary_path}")
    if tiered_data:
        print(f"  分级覆盖率:    {tiered_json_path}")

    # Print summary
    tc = summary["test_cases"]
    print(f"\n  用例: {tc['total']} 总 / {tc['passed']} 通过 / {tc['failed']} 失败")
    if "line_coverage" in summary:
        print(f"  行覆盖率:     {summary['line_coverage']['coverage']}")
    if "function_coverage" in summary:
        print(f"  函数覆盖率:    {summary['function_coverage']['coverage']}")
    if tiered_data:
        t = tiered_data.get("total", {})
        print(f"  分级(总):      函数 {t.get('function_coverage',0)}%  行 {t.get('line_coverage',0)}%")
        for lv in ("high", "mid", "low"):
            bl = tiered_data.get("by_level", {}).get(lv, {})
            gate = "✓" if bl.get("pass") else "✗"
            print(f"    {lv:<5}: 函数 {bl.get('function_coverage',0):5.1f}%  行 {bl.get('line_coverage',0):5.1f}%  {gate}")

    sys.exit(test_exit_code)


if __name__ == "__main__":
    main()
