# 框架搭建

> 前置条件：`environment_check` 已通过（`project_name_in_graph` 已确定（内存变量）），且测试目录（`test_dir`（内存变量，默认 `autotests/`））不存在。

## 概述

在目标项目根目录下创建测试框架骨架（目录名由 `test_dir` 决定，下文以 `{test_dir}` 代指）：目录结构、stub-ext、CMake 工具、测试运行脚本、报告生成器。此阶段只搭建框架，不生成具体测试用例。

## 工作步骤

### 1. 分析项目结构

读根 `CMakeLists.txt`：
- 项目名：`project(...)`
- Qt 版本：`find_package(Qt5/Qt6 ...)`
- C++ 标准：`CMAKE_CXX_STANDARD`（默认 17）
- 第三方依赖：DTK、boost、nlohmann_json、spdlog 等

扫描源码目录：`src/`、`source/`、`lib/`、`libs/`、`application/`、`apps/`、`base/`、`common/`、`components/`、`plugins/`

将 Qt 版本写入内存变量：`qt_version`。

test_dir = test_dir  # "autotests" 或 "tests"

# 创建目录结构
```
{test_dir}/
├── 3rdparty/stub/       # stub-ext（从 templates/stub-ext/ 复制）
├── cmake/               # CMake 工具脚本
├── .gitignore            # 忽略构建产物和临时状态
├── run-ut.sh            # 一键测试运行脚本（编译→运行→覆盖率→汇总）
├── gen-ut-summary.py    # 轻量摘要生成器（解析 gtest XML + lcov summary）
└── lsan_suppressions.txt # LSan 抑制文件（Qt/DTK 框架误报）
```

### 3. 复制 stub-ext

cp -r ${SKILL_DIR}/templates/stub-ext/* ${PROJECT_PATH}/{test_dir}/3rdparty/stub/

> **注意**：禁止从网络下载 stub-ext，只从 `templates/stub-ext/` 复制。

### 4. 生成 CMake 工具脚本

bash ${SKILL_DIR}/scripts/generate-cmake-utils.sh
```

生成 `{test_dir}/cmake/UnitTestUtils.cmake`。脚本通过环境变量 `TEST_DIR` 接收目录名（默认 `autotests`）：

```bash
TEST_DIR=tests bash ${SKILL_DIR}/scripts/generate-cmake-utils.sh
```

### 5. 生成测试运行脚本 + 摘要生成器 + LSan 抑制文件

```bash
TEST_DIR=${test_dir} bash ${SKILL_DIR}/scripts/generate-runner.sh
```

一次性生成三个文件：

| 文件 | 作用 |
|------|------|
| `{test_dir}/run-ut.sh` | 一键脚本：cmake(项目根) → make → 直接执行 gtest 二进制(per-target XML) → lcov 采集 → genhtml → 调 gen-ut-summary.py |
| `{test_dir}/gen-ut-summary.py` | 轻量摘要生成器：解析 gtest XML + lcov summary，输出 `ut-summary.json`（**不重跑测试**） |
| `{test_dir}/lsan_suppressions.txt` | LSan 抑制文件：抑制 Qt6 DBus / 事件循环等框架级误报泄漏 |

脚本通过环境变量 `TEST_DIR` 接收目录名（默认 `autotests`）。

**run-ut.sh 关键设计**：
- **cmake 源指向项目根**（`cmake $PROJECT_ROOT`），而非 `{test_dir}` 目录——确保 `CMAKE_SOURCE_DIR` = 项目根，使 `src/CMakeLists.txt` 中 `${CMAKE_SOURCE_DIR}/src/*.cpp` 正确解析业务源码
- **直接执行 gtest 二进制**（`./binary --gtest_output=xml:`），不依赖 ctest——避免 `gtest_discover_tests` 注册失效时 0 tests 的问题；每个目标独立 `report_<target>.xml`
- **step_6 调 gen-ut-summary.py**（轻量解析），**不**调 `collect-coverage-report.py`——后者会重跑测试 + 重做 lcov，与 step_4/step_5 重复
- **ASAN/LSan**：`ASAN_OPTIONS=detect_leaks=1` + `LSAN_OPTIONS=suppressions=lsan_suppressions.txt`，并收集 `asan*.log`
- **CMAKE_SAFETYTEST_ARG**：传入 `CMAKE_SAFETYTEST_ARG_ON` 满足公司安全测试规范
- **headless**：`QT_QPA_PLATFORM=offscreen` 适配 CI 无显示环境
- **`--from-step`** 断点续跑、`--parallel` 并行编译
- **覆盖率 extract 模式**：从 `.ut-inventory.json` 动态读取业务源码目录（默认 `*/src/*`），每个模式单引号包裹防止 shell glob 展开

### 6. 生成 {test_dir}/CMakeLists.txt

读模板：`templates/cmake-autotests.txt`

替换占位符：
- `{THIRD_PARTY_PACKAGES}` → 检测到的依赖的 `find_package` 命令
- `{ADD_SUBDIRECTORIES}` → `add_subdirectory()` 调用（初始为空，后续阶段会补充）

**覆盖率编译标志**：模板已内置 `-fprofile-arcs -ftest-coverage`（Debug 模式下启用）。
不要删除此标志——`run-ut.sh` 的 step_5 (lcov --capture) 和 Mode 3 `collect-coverage-report.py` 的覆盖率采集都依赖它。
若项目根 CMakeLists.txt 已有覆盖率标志，{test_dir} 的标志不冲突（追加模式）。

### 7. 修改根 CMakeLists.txt

> **注意**：只 APPEND 新行，**绝不**修改或注释已有代码。

用 `edit` 工具精确匹配插入。找到 `add_subdirectory(src)` 行，在其后插入：

```cmake
option(BUILD_TESTS "Build unit tests" ON)
if(BUILD_TESTS)
    add_subdirectory({test_dir})
endif()
```

其中 `{test_dir}` 替换为 `test_dir` 的值（如 `autotests` 或 `tests`）。

**绝不**：
- 注释掉已有 `if()` / `else()` / `endif()` 块
- 修改已有 `find_package()` 调用
- 修改已有变量赋值
- 删除或重命名已有 include

### 8. 生成 {test_dir}/README.md

简洁使用指南（<300 字）：目录结构、构建/运行命令、GTest 依赖说明。

### 9. 生成 {test_dir}/.gitignore

在 `{test_dir}/` 下生成 `.gitignore`：

```gitignore
# Build artifacts
build-*/

# Test run artifacts
.results/
.reports/

# Python cache
__pycache__/
*.pyc

# Coverage
coverage/
```

**绝不**忽略 `{test_dir}/` 本身（测试代码应纳入版本控制），只忽略构建产物和临时状态文件。

### 10. 更新项目 .gitignore

在项目根 `.gitignore` 中追加（若不存在则创建）：

```
# Qt Autotest Generator artifacts
build-{test_dir}/
{test_dir}/.results/
{test_dir}/.reports/
```

其中 `{test_dir}` 替换为 `test_dir` 的值。

> **注意**：`.ut-inventory.json` **不**加入 `.gitignore`，它是项目单元测试状态的真相源，跟随测试代码一起纳入版本控制。

**绝不**忽略 `{test_dir}/` 本身（测试代码应纳入版本控制），只忽略构建产物和临时状态文件。

### 11. 验证框架编译

test_dir = test_dir  # "autotests" 或 "tests"

mkdir -p ${PROJECT_PATH}/build-${test_dir}
cd ${PROJECT_PATH}/build-${test_dir}
cmake .. -DBUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build . -j$(nproc)

**必须使用 `-DCMAKE_BUILD_TYPE=Debug`**：覆盖率编译标志（`-fprofile-arcs -ftest-coverage`）仅在 Debug 模式下启用。
若不传 Debug，编译出的二进制无 gcov 插桩，`run-ut.sh` 的 lcov 步骤和 Mode 3 `collect-coverage-report.py` 的覆盖率采集将全部失效。

若失败 → 分析错误 → 修 CMakeLists → 重试（max 10 loops）。

### 12. 更新内存变量

记录 `qt_version` 到内存变量。

## 关键约束

- 不生成具体测试用例（测试代码由后续阶段负责）
- 不修改已有 CMake 代码，只 APPEND，不改不删不注释已有块
- 不从网络下载 stub-ext
- 空框架必须能编译通过，不跳过框架编译验证
- `{test_dir}/` 已存在时跳过本阶段
- 必须生成 `{test_dir}/.gitignore`（排除构建产物、缓存、覆盖率数据）