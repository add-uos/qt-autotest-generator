# 覆盖率采集与汇总（Mode 3）

> 前置条件：项目已编译且存在 gtest 可执行文件；可选 `.ut-inventory.json`（有则产出分级覆盖率，无则仅总覆盖率）。

## 概述

只采集/统计覆盖率，**不生成测试代码**。一条命令完成：运行测试 → lcov 采集 → genhtml → 分级覆盖率 → 汇总 JSON。

> **与 `run-ut.sh` 的关系**：`run-ut.sh`（框架搭建时由 `scripts/generate-runner.sh` 生成）是用户手动运行的便捷脚本，支持交互式步骤选择；Mode 3 正式采集走 `scripts/coverage-report.py`，支持 `--skip-build`、`--inventory` 等参数化控制。两者功能部分重叠但不冲突——`run-ut.sh` 面向人工操作，`coverage-report.py` 面向 Agent 自动化。

## 适用时机

用户意图为**只看覆盖率/采集报告/统计分级**，不需要生成或修改测试代码。典型触发：

- 采集覆盖率、统计覆盖率、生成覆盖率报告
- 看覆盖率、跑一下看分级覆盖率、出报告
- collect coverage、coverage report、coverage summary

## 主入口

```bash
python3 ${SKILL_DIR}/scripts/coverage-report.py \
  ${PROJECT_PATH} \
  --build-dir build-ut \
  --test-target build-ut/tests/${test_binary} \
  --report-dir build-ut \
  --inventory ${test_dir}/.ut-inventory.json \
  --timeout 300
```

> 多目标项目可改用 `--test-targets build-ut/tests/test_a,build-ut/tests/test_b` 一次采集多个 gtest 二进制的覆盖率。

**参数说明**：

| 参数 | 必选 | 默认值 | 说明 |
|------|------|--------|------|
| `project_dir` | ✓ | — | 项目根目录 |
| `--build-dir` | | 自动探测 | 构建目录名（相对项目根），自动探测 `build-ut` / `build-autotests` / `build` |
| `--test-target` | | 自动探测 | 单个 gtest 可执行文件路径（相对 build-dir），默认自动探测 |
| `--report-dir` | | = build-dir | 报告输出目录名（相对项目根） |
| `--inventory` | | 自动探测 | `.ut-inventory.json` 路径，自动探测 `autotests/.ut-inventory.json` 或 `tests/.ut-inventory.json` |
| `--skip-build` | | false | 跳过编译，直接运行测试采集覆盖率 |
| `--build-type` | | Debug | CMAKE_BUILD_TYPE |
| `--coverage-flags` | | `--coverage` | 覆盖率插桩 flag，注入 CMAKE_C_FLAGS / CMAKE_CXX_FLAGS / CMAKE_EXE_LINKER_FLAGS / CMAKE_SHARED_LINKER_FLAGS；项目 CMake 已自行开启插桩时传空字符串 `''` 关闭 |
| `--cmake-extra` | | [] | 额外传给 cmake 的参数，可多次指定：`--cmake-extra=-DBUILD_TESTS=ON` |
| `--test-targets` | | None | 多个 gtest 可执行文件，逗号分隔（相对 build-dir 或绝对路径） |
| `--timeout` | | 300 | 单个测试目标的超时秒数 |

**最简用法**（自动探测所有路径）：

```bash
python3 ${SKILL_DIR}/scripts/coverage-report.py /path/to/project
```

## 工作步骤

脚本内部按 6 步顺序执行：

### 1. 编译（可选，`--skip-build` 跳过）

```bash
mkdir -p ${BUILD_DIR} && cd ${BUILD_DIR}
cmake ${PROJECT_PATH} -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_FLAGS=--coverage -DCMAKE_CXX_FLAGS=--coverage \
  -DCMAKE_EXE_LINKER_FLAGS=--coverage -DCMAKE_SHARED_LINKER_FLAGS=--coverage
cmake --build . -j$(nproc)
```

编译后脚本会校验 `${BUILD_DIR}` 下存在 `.gcno` 插桩产物，缺失则提前退出（退出码 2），避免跑完测试才发现 SF=0。

### 2. 运行测试 + gtest XML

运行 gtest 二进制，产出 JUnit XML 到 `${REPORT_DIR}/report/report_<target>.xml`。

### 3. lcov 采集

```bash
lcov -d ${BUILD_DIR} -c -o ${BUILD_DIR}/coverage.info
lcov --extract ${BUILD_DIR}/coverage.info '*/src/*' -o ${BUILD_DIR}/coverage.info
lcov --remove ${BUILD_DIR}/coverage.info '*/tests/*' '*/autotests/*' '*/3rdparty/*' -o ${BUILD_DIR}/coverage.info
```

### 4. genhtml

产出 lcov HTML 覆盖率报告到 `${REPORT_DIR}/html/index.html`。

### 5. 分级覆盖率（需 inventory）

调用 `scripts/coverage-report.py`，产出 `${REPORT_DIR}/coverage_by_level.json`。

无 `.ut-inventory.json` 时跳过此步。

### 6. 汇总 JSON

解析 gtest XML + lcov summary + 分级覆盖率，产出 `${REPORT_DIR}/ut-summary.json`。

## 产出目录结构

```
${REPORT_DIR}/
├── report/                   # gtest XML
│   └── report_<target>.xml
├── html/                     # lcov genhtml
│   └── index.html
├── coverage_by_level.json   # 分级覆盖率（需 inventory，否则无此文件）
└── ut-summary.json           # 汇总 JSON
```

## 汇总 JSON 结构

```json
{
  "project": "deepin-image-viewer",
  "build_dir": "build",
  "test_target": "deepin-image-viewer-test",
  "test_cases": {
    "total": 465,
    "passed": 465,
    "failed": 0
  },
  "test_suites": [
    {"suite": "ut_filecontrol", "tests": 44, "failures": 0, "errors": 0, "time": 0.279}
  ],
  "line_coverage": {
    "total": 4721,
    "passed": 3883,
    "failed": 838,
    "coverage": "82.20%"
  },
  "function_coverage": {
    "total": 422,
    "passed": 420,
    "failed": 2,
    "coverage": "99.50%"
  },
  "tiered_coverage": {
    "by_level": {
      "high": {
        "methods": 4,
        "function_coverage": 100.0,
        "lines": 145,
        "line_coverage": 73.1,
        "gate": {"line": 90, "branch": 80, "function": 100},
        "pass": false
      },
      "mid": { "methods": 125, "...": "..." },
      "low": { "methods": 252, "...": "..." }
    },
    "total": {
      "methods": 381,
      "function_coverage": 99.7,
      "lines": 4653,
      "line_coverage": 82.3,
      "pass": false
    },
    "uncovered_functions": ["ImageInfoData::isError"]
  }
}
```

字段说明：

| 字段 | 来源 | 说明 |
|------|------|------|
| `test_cases` | gtest XML | 总/通过/失败用例数 |
| `test_suites` | gtest XML | 每个 suite 的用例数、失败数、耗时 |
| `line_coverage` | `lcov --summary` | 行级覆盖率（总行数/已覆盖/未覆盖/百分比） |
| `function_coverage` | `lcov --summary` | 函数级覆盖率 |
| `tiered_coverage` | `coverage-report.py` | 按 high/mid/low 分级的覆盖率（需 inventory） |
| `tiered_coverage.by_level.<lv>.pass` | 脚本计算 | 函数覆盖率达 `gate.function` 且行覆盖率达 `gate.line` |

无 `.ut-inventory.json` 时，`tiered_coverage` 字段不存在。

## 关键约束

- **不生成测试代码**：只采集和统计，不编译新测试、不修改项目
- **不修改项目源码**
- **lcov 不可用时降级**：跳过 HTML + 分级覆盖率，只输出 gtest XML + 基础 JSON
- **依赖**：cmake, make, lcov, genhtml, c++filt（用于 `coverage-report.py` demangle）
