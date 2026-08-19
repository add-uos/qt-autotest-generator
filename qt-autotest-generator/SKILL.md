---
name: qt-autotest-generator
description: "Qt CMake 项目单元测试：函数重要性探测（Mode 1，生成 .ut-inventory.json 分级表）或按分级补全 GTest 用例（Mode 2，编译验证+覆盖率门禁+更新 usecase_count）。触发于「扫描函数重要性/生成 inventory/探测分级/项目初始化单测分析」→ Mode 1；「生成单测/补全测试/add gtest/写测试/建测试框架/修测试/重新对账」→ Mode 2。硬门禁：codebase-memory-mcp 知识图谱（远端优先，本地兜底）。不触发于：非 Qt 或非 CMake 项目、Qt Test/Catch2 框架、仅运行测试/配 CI 不生成测试代码。"
version: "3.0.0"
user-invocable: true
argument-hint: "[项目路径 / 模块路径 / 类名 / repo_url 分支名]"
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

基于 **codebase-memory-mcp 知识图谱** 的 Qt CMake 项目单元测试技能。支持两种模式；分步指令在 **`prompts/`**，执行前须 **`Read`** 对应文件。

| 模式 | 何时用 | 主入口 |
|------|--------|--------|
| **Mode 1 · 函数重要性探测** | 项目初始化、扫描方法分级、生成 inventory | 下文 + `Read prompts/inventory.md` |
| **Mode 2 · 单元测试编写** | 按 inventory 补全 GTest 用例 | 下文 + `Read prompts/test_writer.md` |

Mode 2 启动时若 `.ut-inventory.json` 不存在 → **自动触发 Mode 1**。

## 环境与约定

- **语言**：默认与用户语种一致；技术术语用行业常用表述。
- **测试框架**：Google Test only，不用 Qt Test / Catch2。
- **测试目录**：优先 `autotests/`；若项目已有 `tests/` 且含 C++ GTest 代码，则沿用 `tests/`。目录在 environment_check 阶段一次性探测确定。
- **知识图谱 MCP 硬门禁**：无图谱索引不执行，不降级到文件扫描/LSP。远端优先，本地兜底，互斥使用其一。详见 `resources/references/mcp-providers.md`。
- **不修源码**：疑似源码缺陷只标红交还用户。
- **只 APPEND 不改已有**：修改根 CMakeLists.txt 和测试 CMake 时只追加新行。
- **不问用户确认**：直接执行。

## 触发条件

- **Mode 1**：扫描函数重要性、建立分级表、探测分级、生成 inventory、项目初始化单测分析、importance inventory、scan method importance
- **Mode 2**：生成单测、建测试框架、批量生成单测、补全测试、修测试、重新对账、加测试、add gtest、setup unit tests、coverage gap、fix test failures、sync tests、improve coverage
- **指定覆盖率阈值**：函数覆盖率 90%、覆盖率不低于 95%、coverage threshold 85% → 写入内存变量，默认 90

**不触发于**：非 Qt 或非 CMake 项目、Qt Test/Catch2/doctest 框架、仅运行测试/配 CI/看日志、集成测试/性能测试/UI 自动化

---

## Mode 1 · 函数重要性探测

1. **`Read`** `prompts/environment_check.md` → MCP 门禁
2. **`Read`** `prompts/inventory.md` → 全量扫描 → 评分 → 产出 `.ut-inventory.json` + `inventory-summary.md`

Mode 1 **不生成测试代码、不编译、不运行**，只建表。

---

## Mode 2 · 单元测试编写

1. **`Read`** `prompts/environment_check.md` → MCP 门禁
2. 检查 `{test_dir}/.ut-inventory.json` → 不存在则先执行 Mode 1
3. **`Read`** `prompts/test_writer.md` → 逐类闭环 → 编译验证 → 更新 `usecase_count`

Mode 2 的子步骤按需读取：

| 子步骤 | 文件 | 何时读 |
|--------|------|--------|
| 项目准备 | `prompts/project_preparer.md` | 用户提供 repo_url 时 |
| 框架搭建 | `prompts/framework_builder.md` | `{test_dir}/` 不存在时 |
| 类准备 | `prompts/inventory.md` | 方法分级入表，`test_writer` §4 从中提取待测类 |
| 依赖追踪 | `prompts/dependency_tracer.md` | 读 inventory 的 is_gui、MCP trace_path 出向、stub 决策、CMake 目录 |
| 测试代码生成 | `prompts/test_code_gen.md` | 逐类闭环第 2 步 |
| 编译验证 | `prompts/build_verifier.md` | 逐类闭环第 3 步 |
| 自检 | `prompts/self_checker.md` | 逐类闭环第 4 步 |
| 增量补全 | `prompts/incremental_updater.md` | 覆盖率缺口时 |
| 失败修复 | `prompts/failure_repairer.md` | 编译/运行失败时 |
| 代码提交 | `prompts/code_committer.md` | 批次自检通过后 |
| 报告生成 | `prompts/report_generator.md` | 全类完成收尾 |

---

## Prompt 文件映射

### Mode 1

| 步骤 | 文件 | 用途 |
|------|------|------|
| 门禁 | `prompts/environment_check.md` | MCP 提供方解析、索引验证 |
| 主流程 | `prompts/inventory.md` | 全量扫描 → 评分 → 产出 `.ut-inventory.json`（含 classes/is_gui） |

### Mode 2

| 步骤 | 文件 | 用途 |
|------|------|------|
| 门禁 | `prompts/environment_check.md` | MCP 提供方解析、索引验证 |
| 项目准备 | `prompts/project_preparer.md` | 拉取代码、校验基线、安装依赖 |
| 框架搭建 | `prompts/framework_builder.md` | `{test_dir}/` 脚手架、CMake、stub、runner |
| 依赖追踪 | `prompts/dependency_tracer.md` | 读 inventory 的 is_gui、MCP trace_path、stub 决策、CMake 目录 |
| 测试代码生成 | `prompts/test_code_gen.md` | 读模板生成测试代码、AAA、命名 |
| 编译验证 | `prompts/build_verifier.md` | 强制编译+运行、错误分类→修复表 |
| 自检 | `prompts/self_checker.md` | 覆盖率/命名/SPDX/stub/断言强度/环境隔离 |
| 增量补全 | `prompts/incremental_updater.md` | 图谱差集补缺失用例、CMake 智能合并 |
| 失败修复 | `prompts/failure_repairer.md` | 失败修复 + 根因分类 + 源码缺陷标红 |
| 代码提交 | `prompts/code_committer.md` | 批次增量提交（只 commit 不 push） |
| 报告生成 | `prompts/report_generator.md` | HTML/CSV 报告（含源码缺陷清单） |

---

## 核心原则（Iron Laws）

1. **知识图谱 MCP 硬门禁** —— 无图谱索引不执行
2. **Google Test only** —— 不用 Qt Test / Catch2
3. **函数覆盖率门禁** —— 有 `.ut-inventory.json` 时按方法分级：🌟high 行90%+分支80%+函数100%，⚖mid 行60%+函数100%，💤low 无硬性门禁
4. **强制编译+运行验证** —— 编译并跑通后才能报完成
5. **内置 stub-ext** —— 从 `resources/stub/` 复制，不从网络下载
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
| Stub 模板 | `resources/templates/stub-patterns.cpp` |
| CMake 模板 | `resources/templates/cmake-*.txt` |
| 编译重试 | per-error 3 次，max 10 loops |
| 函数覆盖率阈值 | 默认 90%，可由用户指定 |
| MCP 提供方指南 | `resources/references/mcp-providers.md` |
| MCP 使用指南 | `resources/references/codebase-memory-guide.md` |
| 测试方法论 | `resources/references/test-types.md` |
| 覆盖率分级 | `resources/references/coverage-tiers.md` |
| Inventory Schema | `resources/references/inventory-schema.md` |
| 对账逻辑 | `resources/references/reconcile-logic.md` |

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
□ 已区分 Mode 1（分析）/ Mode 2（编写），未混跑
□ Mode 1：已 Read prompts/environment_check.md + prompts/inventory.md；产出 .ut-inventory.json
□ Mode 2：已 Read prompts/environment_check.md；.ut-inventory.json 存在（不存在则先执行 Mode 1）
□ Mode 2：已按步骤 Read 对应 prompts 子步骤文件
□ MCP 提供方已解析（远端优先，本地兜底），互斥使用
□ 逐类闭环：每类走 依赖追踪 → 测试生成 → 编译验证 → 自检（类列表与 level 来自 inventory）
□ 单类失败：已记录 failure_reason + 跳过 + 继续下一个类
□ 每类编译通过后：已更新 .ut-inventory.json 的 usecase_count
□ 批次提交：本批次自检通过后已执行代码提交（只 commit 不 push）
□ 疑似源码缺陷：已标红，未自行修源码
□ 全类完成 + 覆盖达标：已执行报告生成收尾
```
