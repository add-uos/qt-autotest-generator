#!/bin/bash
# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

################################################################################
# 生成测试运行脚本 + 摘要生成器 + LSan 抑制文件
# 通过环境变量 TEST_DIR 指定测试目录名（默认 autotests）
# 产物：
#   {test_dir}/run-ut.sh             — 一键编译→运行→覆盖率→汇总
#   {test_dir}/gen-ut-summary.py     — 轻量摘要生成器（解析 gtest XML + lcov summary）
#   {test_dir}/lsan_suppressions.txt — LSan 抑制文件（Qt/DTK 框架误报）
# run-ut.sh 内部用 __TEST_DIR__ / __SOURCE_DIRS_PATTERN__ 占位符，生成后 sed 替换
################################################################################

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="${TEST_DIR:-autotests}"
AUTOTEST_ROOT="${AUTOTEST_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)/${TEST_DIR}}"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_step()    { echo -e "${BLUE}[STEP]${NC} $1"; }

# ============================================================
# 1. gen-ut-summary.py — 轻量摘要生成器（不重跑测试）
# ============================================================
generate_summary_script() {
    cat > "${AUTOTEST_ROOT}/gen-ut-summary.py" << 'GENSUMEOF'
#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate ut-summary.json from gtest XML reports and lcov coverage data.

Reads environment variables:
  - projectdir:   project root directory (required)
  - builddir:     build directory name, relative to projectdir (required)
  - reportdir:    report output directory name, relative to projectdir (required)

Optional overrides (take precedence over defaults):
  - GTEST_XML_DIR:  directory containing gtest XML reports
  - COVERAGE_INFO:  path to lcov .info file for coverage parsing

Output: {projectdir}/{reportdir}/ut-summary.json
"""

import json
import xml.etree.ElementTree as ET
import glob
import os
import subprocess
import re
import sys


def parse_gtest_xml(xml_dir):
    """Parse Google Test / JUnit XML output, return (total, passed, failed)."""
    total = passed = failed = 0
    for xml_file in sorted(glob.glob(os.path.join(xml_dir, "*.xml"))):
        try:
            root = ET.parse(xml_file).getroot()
            t = int(root.get("tests", 0))
            f_count = int(root.get("failures", 0))
            err_count = int(root.get("errors", 0))
            total += t
            failed += f_count + err_count
            passed += t - f_count - err_count
        except Exception as e:
            print(f"Warning: failed to parse {xml_file}: {e}", file=sys.stderr)
    return total, passed, failed


def parse_lcov_summary(coverage_info):
    """Parse lcov --summary output, return coverage dict."""
    result = {}
    if not os.path.exists(coverage_info):
        print(f"Warning: coverage info file not found: {coverage_info}", file=sys.stderr)
        return result

    lcov_out = subprocess.run(
        ["lcov", "--summary", coverage_info, "--rc", "lcov_branch_coverage=1"],
        capture_output=True, text=True
    )
    summary_text = lcov_out.stdout + lcov_out.stderr

    m_lines = re.search(r'lines.*?:\s*([\d.]+)%\s*\((\d+)\s+of\s+(\d+)\s+\w+\)', summary_text)
    if m_lines:
        pct, hit, total = m_lines.groups()
        result["line_coverage"] = {
            "total": int(total),
            "passed": int(hit),
            "failed": int(total) - int(hit),
            "coverage": f"{float(pct):.2f}%"
        }

    m_func = re.search(r'functions.*?:\s*([\d.]+)%\s*\((\d+)\s+of\s+(\d+)\s+\w+\)', summary_text)
    if m_func:
        pct, hit, total = m_func.groups()
        result["function_coverage"] = {
            "total": int(total),
            "passed": int(hit),
            "failed": int(total) - int(hit),
            "coverage": f"{float(pct):.2f}%"
        }

    return result


def main():
    projectdir = os.environ.get("projectdir")
    builddir = os.environ.get("builddir")
    reportdir = os.environ.get("reportdir")

    if not all([projectdir, builddir, reportdir]):
        print("Error: environment variables projectdir, builddir, reportdir are required", file=sys.stderr)
        sys.exit(1)

    gtest_dir = os.environ.get("GTEST_XML_DIR",
                                os.path.join(projectdir, builddir, "report"))
    coverage_info = os.environ.get("COVERAGE_INFO",
                                    os.path.join(projectdir, builddir, "coverage.info"))

    total, passed, failed = parse_gtest_xml(gtest_dir)

    result = {
        "test_cases": {
            "total": total,
            "passed": passed,
            "failed": failed
        }
    }

    coverage_data = parse_lcov_summary(coverage_info)
    result.update(coverage_data)

    output_path = os.path.join(projectdir, reportdir, "ut-summary.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
GENSUMEOF
    chmod +x "${AUTOTEST_ROOT}/gen-ut-summary.py"
    print_success "Generated ${TEST_DIR}/gen-ut-summary.py"
}

# ============================================================
# 2. lsan_suppressions.txt — LSan 抑制文件
# ============================================================
generate_lsan_suppressions() {
    cat > "${AUTOTEST_ROOT}/lsan_suppressions.txt" << 'LSANEOF'
# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later
#
# LSan suppressions for unit tests
# Suppress known false-positive leaks from Qt/DTK frameworks.
# Add project-specific suppressions below.
#
# Syntax: leak:<function_name_pattern>
#
# Qt6 DBus / event-loop deferred cleanup (freed at process exit, reported as leak)
leak:QDBusConnectionManager
leak:QDBusServiceWatcher
leak:QMetaType
leak:QFactoryLoader
LSANEOF
    print_success "Generated ${TEST_DIR}/lsan_suppressions.txt"
}

# ============================================================
# 3. run-ut.sh — 一键测试运行脚本
# ============================================================
generate_test_runner_script() {
    cat > "${AUTOTEST_ROOT}/run-ut.sh" << 'RUNUTEOF'
#!/bin/bash

################################################################################
# Unit Test Runner Script
# 一键流程：编译 → 直接运行 gtest 二进制(per-target XML) → lcov 覆盖率 → genhtml → ut-summary.json
# 特性：ASAN/LSan 泄漏检测、CMAKE_SAFETYTEST_ARG、headless(Qt offscreen)、--from-step 断点续跑
################################################################################

set -e

# 测试目录名（框架搭建时确定）
TEST_DIR="__TEST_DIR__"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_step()    { echo -e "${BLUE}[STEP $1]${NC} $2"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }

show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --from-step <N>   Start from step N (1-6)"
    echo "  --parallel <N>    Parallel build jobs (default: $(nproc))"
    echo "  -h, --help        Show this help"
    echo ""
    echo "Steps:"
    echo "  1. Prepare build env"
    echo "  2. Configure CMake"
    echo "  3. Compile tests"
    echo "  4. Run unit tests (per-target gtest XML)"
    echo "  5. Generate coverage report (lcov + genhtml)"
    echo "  6. Generate ut-summary.json"
    echo ""
    echo "Examples:"
    echo "  $0                 # Run all steps"
    echo "  $0 --from-step 4   # Skip build, run tests + coverage"
    echo "  $0 --from-step 6   # Only regenerate summary from existing data"
}

# Parse args
START_STEP=1
PARALLEL_JOBS=$(nproc)
while [[ $# -gt 0 ]]; do
    case $1 in
        --from-step)
            START_STEP="$2"
            if ! [[ "$START_STEP" =~ ^[1-6]$ ]]; then
                print_error "Invalid step: $START_STEP (must be 1-6)"
                exit 1
            fi
            shift 2 ;;
        --parallel)
            PARALLEL_JOBS="$2"
            if ! [[ "$PARALLEL_JOBS" =~ ^[1-9][0-9]*$ ]]; then
                print_error "Invalid parallel: $PARALLEL_JOBS (positive integer)"
                exit 1
            fi
            shift 2 ;;
        -h|--help) show_usage; exit 0 ;;
        *) print_error "Unknown option: $1"; show_usage; exit 1 ;;
    esac
done

# Directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$PROJECT_ROOT/build-${TEST_DIR}"
REPORT_DIR="$BUILD_DIR/test-reports"
GTEST_XML_DIR="$BUILD_DIR/report"

# Headless Qt (CI 友好)
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}

# ASAN/LSan：启用泄漏检测 + 抑制 Qt/DTK 框架误报
export ASAN_OPTIONS=${ASAN_OPTIONS:-detect_leaks=1}
export LSAN_OPTIONS=suppressions="$SCRIPT_DIR/lsan_suppressions.txt"

# State
TEST_PASSED=false
TEST_EXIT_CODE=0
COVERAGE_SUCCESS=false

echo "========================================"
echo "  Unit Test Runner"
echo "========================================"
echo "Project root:  $PROJECT_ROOT"
echo "Build dir:     $BUILD_DIR"
echo "Reports:       $REPORT_DIR"
echo "Parallel jobs: $PARALLEL_JOBS"
[ "$START_STEP" -gt 1 ] && print_info "Starting from step $START_STEP"
echo ""

step_1_prepare_build_env() {
    print_step 1 "Preparing build environment..."
    rm -rf "$BUILD_DIR"
    mkdir -p "$BUILD_DIR" "$REPORT_DIR"
    print_success "Build environment prepared"
}

step_2_configure_cmake() {
    print_step 2 "Configuring CMake..."
    mkdir -p "$BUILD_DIR"
    cd "$BUILD_DIR"
    # 注意：cmake 源指向项目根（CMAKE_SOURCE_DIR=项目根），而非 autotests 目录
    cmake "$PROJECT_ROOT" \
        -DCMAKE_BUILD_TYPE=Debug \
        -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
        -DBUILD_TESTS=ON \
        -DCMAKE_SAFETYTEST_ARG="CMAKE_SAFETYTEST_ARG_ON"
    print_success "CMake configuration completed"
}

step_3_compile_tests() {
    print_step 3 "Compiling tests..."
    cd "$BUILD_DIR"
    cmake --build . -j "$PARALLEL_JOBS"
    print_success "Compilation completed"
}

# 发现 gtest 二进制：优先 {test_dir}/src，回退到 build 目录递归
discover_test_binaries() {
    local found
    found=$(find "$BUILD_DIR/$TEST_DIR/src" -maxdepth 1 -type f -executable -name "test_*" 2>/dev/null | sort)
    if [ -z "$found" ]; then
        found=$(find "$BUILD_DIR" -maxdepth 4 -type f -executable -name "test_*" -not -name "*.so" -not -name "*.a" -not -name "*.cmake" 2>/dev/null | sort)
    fi
    echo "$found"
}

step_4_run_tests() {
    print_step 4 "Running unit tests..."
    mkdir -p "$GTEST_XML_DIR"
    TEST_START_TIME=$(date +%s)
    TEST_PASSED=true
    TEST_EXIT_CODE=0

    local binaries
    binaries=$(discover_test_binaries)
    if [ -z "$binaries" ]; then
        print_error "No test binaries found under $BUILD_DIR (did you compile?)"
        TEST_PASSED=false
        TEST_EXIT_CODE=1
        return
    fi

    while IFS= read -r binary; do
        [ -z "$binary" ] && continue
        local target
        target=$(basename "$binary")
        echo "==> Running $target ..."
        set +e
        "$binary" --gtest_output="xml:$GTEST_XML_DIR/report_${target}.xml"
        local ec=$?
        set -e
        if [ $ec -ne 0 ]; then
            print_error "$target FAILED (exit $ec)"
            TEST_PASSED=false
            TEST_EXIT_CODE=$ec
        else
            print_success "$target PASSED"
        fi
    done <<< "$binaries"

    TEST_END_TIME=$(date +%s)
    print_info "Test execution completed in $((TEST_END_TIME - TEST_START_TIME))s"
}

step_5_generate_coverage() {
    print_step 5 "Generating coverage report..."
    if ! command -v lcov &> /dev/null; then
        print_warning "lcov not installed, skipping coverage"
        COVERAGE_SUCCESS=false
        return
    fi

    set +e
    mkdir -p "$BUILD_DIR/html"
    # 采集 → 仅保留业务源码 → 排除测试/第三方 → genhtml
    lcov -d "$BUILD_DIR" -c -o "$BUILD_DIR/coverage.info" > "$REPORT_DIR/coverage_output.log" 2>&1 || true
    lcov --extract "$BUILD_DIR/coverage.info" __SOURCE_DIRS_PATTERN__ -o "$BUILD_DIR/coverage.info" >> "$REPORT_DIR/coverage_output.log" 2>&1 || true
    lcov --remove "$BUILD_DIR/coverage.info" '*/test*' '*/'"${TEST_DIR}"'/*' '*/3rdparty/*' -o "$BUILD_DIR/coverage.info" >> "$REPORT_DIR/coverage_output.log" 2>&1 || true
    genhtml -o "$BUILD_DIR/html" --title "Coverage Report" --show-details --legend "$BUILD_DIR/coverage.info" >> "$REPORT_DIR/coverage_output.log" 2>&1
    local genhtml_rc=$?
    if [ $genhtml_rc -eq 0 ] && [ -s "$BUILD_DIR/coverage.info" ]; then
        print_success "Coverage report generated"
        COVERAGE_SUCCESS=true
        print_success "Coverage HTML: file://$BUILD_DIR/html/index.html"
    else
        print_warning "Coverage report generation failed (see $REPORT_DIR/coverage_output.log)"
        COVERAGE_SUCCESS=false
    fi
    set -e
}

step_6_generate_summary() {
    print_step 6 "Generating ut-summary.json..."
    if ! command -v python3 &> /dev/null; then
        print_warning "Python 3 not installed, skipping summary"
        return
    fi

    # 轻量解析：从已有 gtest XML + lcov summary 生成 JSON，不重跑测试
    export projectdir="$PROJECT_ROOT"
    export builddir="build-${TEST_DIR}"
    export reportdir="build-${TEST_DIR}"
    export GTEST_XML_DIR="$GTEST_XML_DIR"
    export COVERAGE_INFO="$BUILD_DIR/coverage.info"

    if python3 "$SCRIPT_DIR/gen-ut-summary.py"; then
        print_success "Summary: file://$BUILD_DIR/ut-summary.json"
        # 若从 step 6 起，依据 summary 推断测试是否通过
        if [ "$START_STEP" -eq 6 ]; then
            local failed
            failed=$(python3 -c "import json;print(json.load(open('$BUILD_DIR/ut-summary.json'))['test_cases']['failed'])" 2>/dev/null || echo 1)
            [ "$failed" = "0" ] && TEST_PASSED=true || TEST_PASSED=false
        fi
    else
        print_warning "Summary generation failed"
    fi
}

# Execute steps based on START_STEP
case $START_STEP in
    1) step_1_prepare_build_env; step_2_configure_cmake; step_3_compile_tests; step_4_run_tests; step_5_generate_coverage; step_6_generate_summary ;;
    2) mkdir -p "$BUILD_DIR" "$REPORT_DIR"; step_2_configure_cmake; step_3_compile_tests; step_4_run_tests; step_5_generate_coverage; step_6_generate_summary ;;
    3) mkdir -p "$BUILD_DIR" "$REPORT_DIR"; step_3_compile_tests; step_4_run_tests; step_5_generate_coverage; step_6_generate_summary ;;
    4) mkdir -p "$REPORT_DIR"; step_4_run_tests; step_5_generate_coverage; step_6_generate_summary ;;
    5) mkdir -p "$REPORT_DIR"; step_5_generate_coverage; step_6_generate_summary ;;
    6) mkdir -p "$REPORT_DIR"; step_6_generate_summary ;;
esac

# 收集 ASAN 日志（若存在；无崩溃时不产生）
cp "$BUILD_DIR"/asan*.log* "$REPORT_DIR/asan.log" 2>/dev/null || true

echo ""
echo "========================================"
if [ "$TEST_PASSED" = true ]; then
    print_success "Unit test execution completed!"
else
    print_error "Unit tests have failures"
fi
echo ""
echo "Generated artifacts:"
echo "  gtest XML:     $GTEST_XML_DIR/"
echo "  coverage HTML: $BUILD_DIR/html/index.html"
echo "  summary JSON:  $BUILD_DIR/ut-summary.json"
echo "  logs:          $REPORT_DIR/"
echo "========================================"

if [ "$TEST_PASSED" != true ]; then
    exit 1
fi
RUNUTEOF

    # Replace __TEST_DIR__ placeholder
    sed -i "s|__TEST_DIR__|${TEST_DIR}|g" "${AUTOTEST_ROOT}/run-ut.sh"

    # Replace SOURCE_DIRS pattern for coverage extraction.
    # If inventory exists, read source dirs from it; otherwise default to */src/*.
    # Each pattern is single-quoted so the shell does not glob-expand it.
    INVENTORY_FILE="${AUTOTEST_ROOT}/.ut-inventory.json"
    if [ -f "${INVENTORY_FILE}" ]; then
        SOURCE_DIRS_PATTERN=$(python3 -c "import json; s=json.load(open('${INVENTORY_FILE}')); dirs=set(); [dirs.add(m.get('file_path','').split('/')[0]) for m in s.get('methods',[]) if m.get('file_path')]; print(' '.join(f\"'*/{d}/*'\" for d in sorted(dirs) if d))" 2>/dev/null || echo "'*/src/*'")
    else
        SOURCE_DIRS_PATTERN="'*/src/*'"
    fi
    sed -i "s|__SOURCE_DIRS_PATTERN__|${SOURCE_DIRS_PATTERN}|g" "${AUTOTEST_ROOT}/run-ut.sh"

    chmod +x "${AUTOTEST_ROOT}/run-ut.sh"
    print_success "Generated ${TEST_DIR}/run-ut.sh"
}

generate_summary_script
generate_lsan_suppressions
generate_test_runner_script
