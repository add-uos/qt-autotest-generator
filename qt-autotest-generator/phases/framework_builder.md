# 框架搭建

> 前置条件：`environment_check` 已通过（session 中 `project_name_in_graph` 已记录），且 `autotests/` 目录不存在。

## 概述

在目标项目根目录下创建 `autotests/` 测试框架骨架：目录结构、stub-ext、CMake 工具、测试运行脚本、报告生成器。此阶段只搭建框架，不生成具体测试用例。

## 工作步骤

### 1. 分析项目结构

读根 `CMakeLists.txt`：
- 项目名：`project(...)`
- Qt 版本：`find_package(Qt5/Qt6 ...)`
- C++ 标准：`CMAKE_CXX_STANDARD`（默认 17）
- 第三方依赖：DTK、boost、nlohmann_json、spdlog 等

扫描源码目录：`src/`、`source/`、`lib/`、`libs/`、`application/`、`apps/`、`base/`、`common/`、`components/`、`plugins/`

将 Qt 版本写入 session：`qt_version` 字段。

### 2. 创建目录结构

```
autotests/
├── 3rdparty/stub/       # stub-ext（从 resources/stub/ 复制）
├── cmake/               # CMake 工具脚本
├── run-ut.sh            # 测试运行脚本
└── report_generator/    # 报告生成器（从 resources/report_generator/ 复制）
```

### 3. 复制 stub-ext

```bash
cp -r ${SKILL_DIR}/resources/stub/* ${PROJECT_PATH}/autotests/3rdparty/stub/
```

> **注意**：禁止从网络下载 stub-ext，只从 `resources/stub/` 复制。

### 4. 生成 CMake 工具脚本

```bash
bash ${SKILL_DIR}/resources/scripts/generate-cmake-utils.sh
```

生成 `autotests/cmake/UnitTestUtils.cmake`。

### 5. 生成测试运行脚本 + 报告生成器

```bash
bash ${SKILL_DIR}/resources/scripts/generate-runner.sh
```

生成 `autotests/run-ut.sh` 并复制 `report_generator/`。

### 6. 生成 autotests/CMakeLists.txt

读模板：`resources/templates/cmake-autotests.txt`

替换占位符：
- `{QT_VERSION}` → 5 或 6
- `{THIRD_PARTY_PACKAGES}` → 检测到的依赖的 `find_package` 命令
- `{ADD_SUBDIRECTORIES}` → `add_subdirectory()` 调用（初始为空，后续阶段会补充）

**覆盖率编译标志**：模板已内置 `-fprofile-arcs -ftest-coverage`（Debug 模式下启用）。
不要删除此标志——`run-ut.sh` 的 step_5 (lcov --capture) 和 `report_generator` 的覆盖率解析都依赖它。
若项目根 CMakeLists.txt 已有覆盖率标志，autotests 的标志不冲突（追加模式）。

### 7. 修改根 CMakeLists.txt

> **注意**：只 APPEND 新行，**绝不**修改或注释已有代码。

用 `edit` 工具精确匹配插入。找到 `add_subdirectory(src)` 行，在其后插入：

```cmake
option(BUILD_TESTS "Build unit tests" ON)
if(BUILD_TESTS)
    add_subdirectory(autotests)
endif()
```

**绝不**：
- 注释掉已有 `if()` / `else()` / `endif()` 块
- 修改已有 `find_package()` 调用
- 修改已有变量赋值
- 删除或重命名已有 include

### 8. 生成 autotests/README.md

简洁使用指南（<300 字）：目录结构、构建/运行命令、GTest 依赖说明。

### 9. 更新项目 .gitignore

在项目根 `.gitignore` 中追加（若不存在则创建）：

```
# Qt Autotest Generator artifacts
build-autotests/
autotests/.results/
autotests/.reports/
autotests/.ut-session.json
```

**绝不**忽略 `autotests/` 本身（测试代码应纳入版本控制），只忽略构建产物和临时状态文件。

### 10. 验证框架编译

```bash
mkdir -p ${PROJECT_PATH}/build-autotests
cd ${PROJECT_PATH}/build-autotests
cmake .. -DBUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build . -j$(nproc)
```

**必须使用 `-DCMAKE_BUILD_TYPE=Debug`**：覆盖率编译标志（`-fprofile-arcs -ftest-coverage`）仅在 Debug 模式下启用。
若不传 Debug，编译出的二进制无 gcov 插桩，`run-ut.sh` 的 lcov 步骤和 `report_generator` 的覆盖率解析将全部失效。

若失败 → 分析错误 → 修 CMakeLists → 重试（max 10 loops）。

### 11. 更新 session

```json
{
  "last_phase": "framework_builder",
  "overall_status": "incomplete",
  "qt_version": 5
}
```

## 关键约束

- 不生成具体测试用例（测试代码由后续阶段负责）
- 不修改已有 CMake 代码，只 APPEND，不改不删不注释已有块
- 不从网络下载 stub-ext
- 空框架必须能编译通过，不跳过框架编译验证
- `autotests/` 已存在时跳过本阶段