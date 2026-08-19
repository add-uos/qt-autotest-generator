---
name: qt-autotest-generator
description: "Qt CMake 项目单元测试：函数重要性探测（Mode 1，生成 .ut-inventory.json 分级表）、按分级补全 GTest 用例（Mode 2，编译验证+覆盖率门禁+更新 usecase_count）、覆盖率采集与汇总（Mode 3，一条命令出分级报告）。触发于「扫描函数重要性/生成 inventory/探测分级/项目初始化单测分析」→ Mode 1；「生成单测/补全测试/add gtest/写测试/建测试框架/修测试/重新对账」→ Mode 2；「采集覆盖率/统计覆盖率/生成覆盖率报告/collect coverage/coverage report」→ Mode 3。硬门禁：codebase-memory-mcp 知识图谱（远端优先，本地兜底）。不触发于：非 Qt 或非 CMake 项目、Qt Test/Catch2 框架、仅运行测试/配 CI 不生成测试代码。"
version: "3.1.0"
user-invocable: true
argument-hint: "[项目路径 / 模块路径 / 类名]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
compatibility:
  required_mcp_any_of:
    - name: remote-codebase-memory-mcp
      purpose: "远端代码知识图谱，毫秒级类结构分析与依赖追踪；硬门禁，无图谱不执行"
    - name: codebase-memory-mcp
      min_version: "0.8.0"
      purpose: "本地代码知识图谱，毫秒级类结构分析与依赖追踪；硬门禁，无图谱不执行"
---

# Qt Autotest Generator

基于 **codebase-memory-mcp 知识图谱** 的 Qt CMake 项目单元测试技能。支持三种模式；分步指令在 **`reference/`**，执行前须 **`Read`** 对应文件。

| 模式 | 何时用 | 主入口 |
|------|--------|--------|
| **Mode 1 · 函数重要性探测** | 项目初始化、扫描方法分级、生成 inventory | 下文 + `Read reference/inventory.md` |
| **Mode 2 · 单元测试编写** | 按 inventory 补全 GTest 用例 | 下文 + `Read reference/test_writer.md` |
| **Mode 3 · 覆盖率采集与汇总** | 只采集/统计覆盖率，不生成测试代码 | `Read reference/report_generator.md` + `scripts/collect-coverage-report.py` |

Mode 2 启动时若 `.ut-inventory.json` 不存在 → **自动触发 Mode 1**。

Mode 3 为**只读采集**，不生成/修改测试代码，适合「跑一下看覆盖率」或「出分级覆盖率报告」。

## 环境与约定

- **语言**：默认与用户语种一致；技术术语用行业常用表述。
- **测试框架**：Google Test only，不用 Qt Test / Catch2。
- **测试目录**：优先 `autotests/`；若项目已有 `tests/` 且含 C++ GTest 代码，则沿用 `tests/`。目录在 environment_check 阶段一次性探测确定。
- **知识图谱 MCP 硬门禁**：无图谱索引不执行，不降级到文件扫描/LSP。远端优先，本地兜底，互斥使用其一。详见 `reference/mcp-providers.md`。
- **不修源码**：疑似源码缺陷只标红交还用户。
- **只 APPEND 不改已有**：修改根 CMakeLists.txt 和测试 CMake 时只追加新行。
- **不问用户确认**：直接执行。

## 触发条件

- **Mode 1**：扫描函数重要性、建立分级表、探测分级、生成 inventory、项目初始化单测分析、importance inventory、scan method importance
- **Mode 2**：生成单测、建测试框架、批量生成单测、补全测试、修测试、重新对账、加测试、add gtest、setup unit tests、coverage gap、fix test failures、sync tests、improve coverage
- **Mode 3**：采集覆盖率、统计覆盖率、生成覆盖率报告、collect coverage、coverage report、coverage summary

**不触发于**：非 Qt 或非 CMake 项目、Qt Test/Catch2/doctest 框架、仅运行测试/配 CI/看日志、集成测试/性能测试/UI 自动化

---

## Mode 1 · 函数重要性探测

1. **对账（reconcile）**：`Read` `reference/reconcile-logic.md` → 首次运行（无 inventory）直接进入步骤 2；inventory 已存在且 `base_sha` 漂移时按差异路由后再决定是否全量重扫
2. **`Read`** `reference/environment_check.md` → MCP 门禁
3. **`Read`** `reference/inventory.md` → 全量扫描 → 评分 → 产出 `.ut-inventory.json` + `inventory-summary.md`

Mode 1 **不生成测试代码、不编译、不运行**，只建表。

---

## Mode 3 · 覆盖率采集与汇总

1. **`Read`** `reference/report_generator.md` → 调用 `scripts/collect-coverage-report.py`
2. 脚本一条命令完成：运行测试 → lcov 采集 → genhtml → 分级覆盖率 → 汇总 JSON
3. 产出：`report/`（gtest XML）+ `html/`（lcov HTML）+ `coverage_by_level.json`（分级详情）+ `ut-summary.json`（三合一汇总）

Mode 3 **不生成测试代码、不编译新测试、不修改项目**，只采集和统计。

---

## Mode 2 · 单元测试编写

1. **对账（reconcile）**：`Read` `reference/reconcile-logic.md` → 若 inventory 不存在走首次运行（步骤 3 会触发 Mode 1）；若 `base_sha` 已漂移按差异路由（新增/签名变更/删除/分支切换）后再进入下方主流程
2. **`Read`** `reference/environment_check.md` → MCP 门禁
3. 检查 `{test_dir}/.ut-inventory.json` → 不存在则先执行 Mode 1
4. **`Read`** `reference/test_writer.md` → 逐类闭环 → 编译验证 → 更新 `usecase_count`

Mode 2 的子步骤按需读取：

| 子步骤 | 文件 | 何时读 |
|--------|------|--------|
| 框架搭建 | `reference/framework_builder.md` | `{test_dir}/` 不存在时 |
| 类准备 | `reference/inventory.md` | 方法分级入表，`test_writer` §4 从中提取待测类 |
| 依赖追踪 | `reference/dependency_tracer.md` | 读 inventory 的 is_gui、MCP trace_path 出向、stub 决策、CMake 目录 |
| 测试代码生成 | `reference/test_code_gen.md` | 逐类闭环第 2 步 |
| 编译验证 | `reference/build_verifier.md` | 逐类闭环第 3 步 |
| 自检 | `reference/self_checker.md` | 逐类闭环第 4 步 |
| 增量补全 | `reference/incremental_updater.md` | 覆盖率缺口时 |
| 失败修复 | `reference/failure_repairer.md` | 编译/运行失败时 |
| 代码提交 | `reference/code_committer.md` | 批次自检通过后 |
| 报告生成 | `reference/report_generator.md` | Mode 3 覆盖率采集与汇总 |

---

## Reference 文件映射

### Mode 1

| 步骤 | 文件 | 用途 |
|------|------|------|
| 门禁 | `reference/environment_check.md` | MCP 提供方解析、索引验证 |
| 主流程 | `reference/inventory.md` | 全量扫描 → 评分 → 产出 `.ut-inventory.json`（含 classes/is_gui） |

### Mode 2

| 步骤 | 文件 | 用途 |
|------|------|------|
| 门禁 | `reference/environment_check.md` | MCP 提供方解析、索引验证 |
| 框架搭建 | `reference/framework_builder.md` | `{test_dir}/` 脚手架、CMake、stub、runner |
| 依赖追踪 | `reference/dependency_tracer.md` | 读 inventory 的 is_gui、MCP trace_path、stub 决策、CMake 目录 |
| 测试代码生成 | `reference/test_code_gen.md` | 读模板生成测试代码、AAA、命名 |
| 编译验证 | `reference/build_verifier.md` | 强制编译+运行、错误分类→修复表 |
| 自检 | `reference/self_checker.md` | 覆盖率/命名/SPDX/stub/断言强度/环境隔离 |
| 增量补全 | `reference/incremental_updater.md` | 图谱差集补缺失用例、CMake 智能合并 |
| 失败修复 | `reference/failure_repairer.md` | 失败修复 + 根因分类 + 源码缺陷标红 |
| 代码提交 | `reference/code_committer.md` | 批次增量提交（只 commit 不 push） |
| 报告生成 | `reference/report_generator.md` | Mode 3：覆盖率采集 + 汇总 JSON（含分级） |

---

## 核心原则（Iron Laws）

1. **知识图谱 MCP 硬门禁** —— 无图谱索引不执行
2. **Google Test only** —— 不用 Qt Test / Catch2
3. **函数覆盖率门禁** —— 有 `.ut-inventory.json` 时按方法分级：🌟high 行90%+分支80%+函数100%，⚖mid 行60%+函数100%，💤low 行60%+函数100%（同 mid）
4. **强制编译+运行验证** —— 编译并跑通后才能报完成
5. **内置 stub-ext** —— 从 `templates/stub-ext/` 复制，不从网络下载
6. **逐类闭环** —— 每个类独立走完 依赖追踪→生成→验证→自检；单类失败记录跳过，不阻塞其他类
7. **不修源码** —— 疑似源码缺陷只标红交还用户
8. **只 APPEND 不改已有** —— 不注释/删除/修改已有 CMake 代码
9. **批次提交** —— 只 commit，不 push
10. **全局闭环迭代上限** —— 同一类最多循环 3 轮；3 轮后仍未通过，标记 `failed` + `max_iterations_exceeded` 并跳过
11. **usecase_count 实时更新** —— 每类编译通过后立即更新 `.ut-inventory.json` 的 `usecase_count` 字段

---

## 快速参考

| 项 | 值 |
|----|----|
| 测试框架 | Google Test only |
| 测试文件 | `test_myclass.cpp` |
| 测试类名 | `MyClassTest` |
| 用例命名 | `{Feature}_{Scenario}_{ExpectedResult}` |
| MCP 工具 | `search_graph`, `get_code_snippet`, `trace_path`, `query_graph`, `index_status` |
| Stub 模板 | `templates/stub-patterns.cpp` |
| CMake 模板 | `templates/cmake-*.txt` |
| 编译重试 | per-error 3 次，max 10 loops |
| 函数覆盖率阈值 | 默认 90%，可由用户指定 |
| MCP 提供方指南 | `reference/mcp-providers.md` |
| MCP 使用指南 | `reference/codebase-memory-guide.md` |
| 测试方法论 | `reference/test-types.md` |
| 覆盖率分级 | `reference/coverage-tiers.md` |
| 分级覆盖率采集 | `scripts/collect-coverage-report.py`（Mode 3） |
| Inventory Schema | `reference/inventory-schema.md` |
| 对账逻辑 | `reference/reconcile-logic.md` |

---

## 模板文件（`templates/`）

技能内置的两类资产合并存放于根级 `templates/` 目录：**代码生成模板**（带占位符，读取后替换）和 **stub-ext 库**（vendored 第三方库，整目录原样复制）。

### 代码生成模板（平铺于 `templates/`）

| 文件 | 用途 | 使用阶段 | 占位符 |
|------|------|---------|--------|
| `google-test-base.cpp` | GTest 测试夹具基类骨架：TEST_F 类结构、SetUp/TearDown、stub 声明、SPDX 头 | 测试代码生成（每类） | `{ClassName}` `{header_file}` `{SPDX_YEAR}` `{SetUpTestSuite}` `{TestCases}` 等 |
| `stub-patterns.cpp` | 常用 stub 模式速查：UI 显示/尺寸、信号监听、虚函数、文件 IO、网络、定时器等 19 节模式 | 依赖追踪 + 测试代码生成（参考用，不直接复制） | `{ClassName}` `{MethodName}` `{SignalName}`（示例占位符，参考用） |
| `cmake-autotests.txt` | 测试根 `CMakeLists.txt` 模板：GTest 依赖、覆盖率标志、子目录挂载 | 框架搭建 | `{THIRD_PARTY_PACKAGES}` `{ADD_SUBDIRECTORIES}` |
| `cmake-submodule.txt` | 测试子模块 `CMakeLists.txt` 模板：可执行目标、stub-shadow 链接、Qt 版本、include 路径 | 测试代码生成（每模块） | `{QT_VERSION}` `{PROJECT_LIBRARIES}` `{QT_EXTRA_LIBS}` `{module_name}` `{test_dir}` `{source_module_path}` |

### stub-ext 库（`templates/stub-ext/`）

vendored [stub-ext](https://github.com/guyongling/stub-ext) 库源码，用于运行时函数 stub（替换虚函数/私有方法/系统调用）。框架搭建时**整目录原样复制**到项目 `{test_dir}/3rdparty/stub/`，不从网络下载。

| 文件 | 说明 |
|------|------|
| `stubext.h` | 库主头文件，测试代码 `#include "stubext.h"` 入口 |
| `stub.h` | Stub 核心实现（函数地址替换） |
| `stub-shadow.h` / `stub-shadow.cpp` | Shadow 机制（堆栈上对象 stub），必须编入 test target |
| `addr_any.h` / `addr_pri.h` | 内存地址工具（私有成员访问） |
| `elfio.hpp` | ELF 解析（内联第三方头，用于符号定位） |

---

## 红旗（出现即停）

- 用 Qt Test / Catch2 框架
- codebase-memory 未 ready 就开始生成
- MCP 提供方未解析或混用多个提供方
- 未编译通过就报完成
- 从网络下载 stub-ext
- 修改用户源码
- 单类失败阻塞整批

---

## Agent 自用工作流检查清单

```
□ 已区分 Mode 1（分析）/ Mode 2（编写）/ Mode 3（采集），未混跑
□ 已执行 reconcile（比对 git HEAD 与 inventory.base_sha，按差异路由；首次运行无 inventory 直接进入环境检查）
□ Mode 1：已 Read reference/environment_check.md + reference/inventory.md；产出 .ut-inventory.json
□ Mode 2：已 Read reference/environment_check.md；.ut-inventory.json 存在（不存在则先执行 Mode 1）
□ Mode 2：已按步骤 Read 对应 reference 子步骤文件
□ MCP 提供方已解析（远端优先，本地兜底），互斥使用
□ 逐类闭环：每类走 依赖追踪 → 测试生成 → 编译验证 → 自检（类列表与 level 来自 inventory）
□ 单类失败：已记录 failure_reason + 跳过 + 继续下一个类
□ 每类编译通过后：已更新 .ut-inventory.json 的 usecase_count
□ 批次提交：本批次自检通过后已执行代码提交（只 commit 不 push）
□ 疑似源码缺陷：已标红，未自行修源码
□ 全类完成 + 覆盖达标：已执行报告生成收尾（或 Mode 3 单独采集覆盖率）
```
