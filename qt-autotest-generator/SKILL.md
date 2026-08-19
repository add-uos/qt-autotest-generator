---
name: qt-autotest-generator
description: "用于为 Qt CMake 项目生成与维护 Google Test 单元测试：搭建 autotests/ 或 tests/ 框架、逐类生成 GTest 用例、补全覆盖率缺口、修复失败用例、源码变更后重新对账。触发于「生成单测/建测试框架/批量生成测试/补全测试/修测试/重新对账/加测试」等表述，以及 'add gtest'、'setup unit tests'、'coverage gap'、'fix test failures'、'sync tests' 等英文触发语，针对 Qt + CMake 的 C++ 项目；即使用户只说 add tests 或提高覆盖率也应触发。硬门禁依赖 codebase-memory-mcp 知识图谱（远端优先、本地兜底），强制编译+运行验证，函数覆盖率默认门禁 90%。不触发于：非 Qt 或非 CMake 项目、Qt Test/Catch2 框架、仅运行测试/CI 而不生成测试的任务。"
version: "2.0.0"
user-invocable: true
argument-hint: "[项目路径 / 模块路径 / 类名 / repo_url 分支名]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
compatibility:
  # 知识图谱 MCP：远端优先，本地兜底（互斥使用其一）。
  # 详见 resources/references/mcp-providers.md
  required_mcp_any_of:
    - name: remote-codebase-memory-mcp
      purpose: "远端代码知识图谱，毫秒级类结构分析与依赖追踪；硬门禁，无图谱不执行"
    - name: codebase-memory-mcp
      min_version: "0.8.0"
      purpose: "本地代码知识图谱，毫秒级类结构分析与依赖追踪；硬门禁，无图谱不执行"
---

# Qt Autotest Generator

基于 **codebase-memory-mcp 知识图谱** 的 Qt CMake 项目单元测试自动生成技能。按顺序工作流执行：环境门禁 → 框架搭建 → 逐类闭环（分析→追踪→生成→验证→自检）→ 批次提交 → 报告收尾。各 phase 的详细步骤见 `phases/*.md`，全流程状态持久化在 `{test_dir}/.ut-session.json`。

## 核心原则（Iron Laws）

1. **知识图谱 MCP 硬门禁** —— 无图谱索引不执行，不降级到文件扫描/LSP；LSP 未集成，图谱是唯一代码分析来源。提供方在环境检查阶段一次性解析（**远端优先，本地兜底，互斥使用其一**），全流程通过 `session.mcp_provider` 调用，详见 `resources/references/mcp-providers.md`
2. **测试目录** —— 优先 `autotests/`；若项目已有 `tests/` 且含 C++ 测试代码，则沿用 `tests/`，不强制迁移。目录在 environment_check 阶段一次性探测确定，写入 `session.test_dir`，全流程统一读取（下文以 `{test_dir}` 代指）
3. **Google Test only** —— 固定框架，不用 Qt Test / Catch2
4. **函数覆盖率门禁** —— lcov 函数覆盖率不低于阈值（默认 90%，可由用户指定）；每个公开/受保护方法至少 1 个用例；低于阈值触发增量补全。有 `.ut-inventory.json` 时按方法分级：🌟high 行90%+分支80%+函数100%，⚖mid 行60%+函数100%，💤low 无硬性门禁（详见 `resources/references/coverage-tiers.md`）。
5. **强制编译+运行验证** —— 编译并跑通后才能报完成
6. **内置 stub-ext** —— 从 `resources/stub/` 复制，不从网络下载
7. **不问用户确认** —— 直接执行
8. **逐类闭环** —— 每个类独立走完 分析→追踪→生成→验证→自检；单类失败记录跳过，不阻塞其他类
9. **不修源码** —— 疑似源码缺陷只标红交还用户，技能只负责测试
10. **只 APPEND 不改已有** —— 修改根 CMakeLists.txt 和测试 CMake 时只追加新行，不注释/删除/修改已有代码
11. **批次提交** —— 每批次（一轮批量生成/增量补全/失败修复）所有类自检通过后即提交本批次完成类的测试代码（提交信息复用 `git-commit-workflow` 技能格式，自动化提交跳过人工确认）；**只 commit，不 push**。最终报告收尾时测试代码已全部入库，不再触发提交
12. **状态持久化** —— 一切状态写 `{test_dir}/.ut-session.json`，跨 phase 传递
13. **全局闭环迭代上限** —— 同一类在逐类闭环内最多循环 3 轮（分析→追踪→生成→验证→自检为 1 轮）；3 轮后仍未通过，标记 `failed` + `failure_reason: "max_iterations_exceeded"` 并跳过

---

## 触发条件

- **拉取项目生成单测**："拉取 https://github.com/foo/bar 的 dev 分支生成单测"、"clone 项目建测试" → 用户提供仓库地址 + 分支名，先执行项目准备
- 首次为 Qt CMake 项目搭建单测框架："建单测"、"生成测试框架"、"add tests"
- 批量为模块/类生成用例："为 src/lib/ui 生成测试"、"批量生成单测"
- **函数重要性探测**："探测分级函数"、"扫描方法重要性"、"生成重要性清单"、"importance inventory"、"scan method importance" → 执行 Mode 1（`phases/importance_inventory.md`），产出 `.ut-inventory.json`
- 修复失败用例："测试编译失败"、"修测试"、"fix test failures"
- 源码变更后对账："代码改了重新检查"、"重新对账"、"sync tests"
- **指定覆盖率阈值**："函数覆盖率 90%"、"覆盖率不低于 95%"、"coverage threshold 85%" → 写入 `session.coverage_threshold`，默认 90

**不触发于**（交还给通用工具或其它技能）：
- 非 Qt 或非 CMake 的项目（纯 C、Python、Go、前端等）
- 明确要求用 Qt Test / Catch2 / doctest 等其它测试框架
- 仅运行已有测试 / 配 CI / 看测试日志，不涉及「生成 / 补全 / 修复测试代码」
- 单测之外的测试类型：集成测试、性能基准、UI 自动化、契约测试

---

## 工作流总览

每次触发后先执行 **reconcile（对账）** 判断源码是否变更，再决定执行哪个 phase。各 phase 的详细步骤见对应文件，按需读取。

### reconcile（对账）逻辑 —— 每次触发必先执行

> 详见 `resources/references/reconcile-logic.md`，包含完整伪代码、索引超时处理、分支切换处理。

### 意图识别与流程路由

| 用户意图 | session 状态 | 执行流程 |
|---------|-------------|--------|
| 用户提供 repo_url + branch 拉取项目 | 无 session | 项目准备 → 环境检查 → 框架搭建 |
| 用户提供本地 project_path | 无 session | 环境检查 → 框架搭建 |
| 首次搭建 | 无 session | 环境检查 → 框架搭建 |
| 批量生成 | 框架就绪 + 有未完成类 | 类分析 → 依赖追踪 → 测试生成 → 编译验证 → 自检（逐类循环）→ **批次内全类自检通过** → 代码提交 |
| 增量补全 | 全类完成 + 覆盖有缺口 | 增量补全 → 编译验证 → 自检 → **本批次自检通过** → 代码提交 |
| 修复失败 | 有 failed 类 | 失败修复 → 编译验证 → 自检 → **本批次自检通过** → 代码提交 |
| 源码变更对账 | baseline 漂移 | reconcile → 按差异路由 → **本批次自检通过** → 代码提交 |
| 全部完成 | 全类完成 + 覆盖达标 | 报告生成（固定收尾；测试代码已在各批次提交中入库，不再提交） |

### 逐类闭环流程

对每个目标类（session.classes 中 status != "done"），依次执行：

```
1. 类分析（单类：拉方法、GUI 识别、用例规划）         — phases/class_analyzer.md
2. 依赖追踪（单类：trace_path 出向、stub 决策、CMake 目录）  — phases/dependency_tracer.md
3. 测试生成（单类：读模板生成测试代码）               — phases/test_writer.md
4. 编译验证（单类：编译+运行+错误分类+重试）           — phases/build_verifier.md
5. 自检（单类：覆盖率/命名/SPDX/stub 自检）           — phases/self_checker.md

自检通过 → 标记 done → 下一类
自检不过（覆盖率缺口 / 函数覆盖率 < 阈值 / 命名 / SPDX / stub）→ 增量补全或测试生成修正
失败修复耗尽 → 标记 failed + failure_reason → 跳过 → 下一类

本批次所有目标类自检处理完毕（含 done / failed / skipped）→ 按核心原则 11 执行批次提交（代码提交；详见原则 11 与 phases/code_committer.md）

全部类 done → 检查覆盖率缺口（方法名差集 + lcov 函数覆盖率是否达标）→ 有缺口则增量补全 → 无则报告生成
```

### 并行处理策略

当目标类数量 >= 5 时，可并行处理多个类的闭环链。详见 `resources/references/parallel-strategy.md`。

要点：
- **并行粒度**：每个类独立走完全链
- **并行上限**：`min(类数, 4)` 个类
- **状态隔离**：分片 session，不并发写主 session
- **收尾同步**：合并分片后统一检查覆盖率缺口，执行批次提交

### 迭代双信号触发

编译验证跑完后，读结果判断下一步：

| 信号 | 来源 | 触发 |
|------|------|------|
| 编译/运行失败 | 编译验证 | 失败修复 |
| 全过 + 方法名差集非空 | 自检覆盖率差集 | 增量补全 |
| 全过 + lcov 函数覆盖率 < 阈值 | 自检函数覆盖率门禁 | 增量补全（传入未覆盖函数清单） |
| 全过 + 覆盖达标 | 编译验证 + 自检 | 下一类 or 报告生成 |

- **自动检测**：编译验证/自检结束后自动判断
- **显式触发**：用户说"补全"/"修复"直接进对应 phase

---

## Phase 文件映射

| Phase | 文件 | 职责 |
|-------|------|------|
| 项目准备 | `phases/project_preparer.md` | 拉取代码、校验基线、安装依赖、验证构建环境；用户提供 repo_url 时第一道前置 |
| 环境门禁 | `phases/environment_check.md` | MCP 提供方解析（远端优先，本地兜底）、索引、验证；失败硬终止 |
| **函数重要性探测** | `phases/importance_inventory.md` | **Mode 1**：扫描知识图谱，为每个方法评分分级（🌟high/⚖mid/💤low），产出 `.ut-inventory.json`，按分级差异化设定覆盖率门禁；可用 `resources/scripts/ut-inventory-editor/` 可视化编辑 |
| 框架搭建 | `phases/framework_builder.md` | {test_dir}/ 脚手架、CMake、stub、runner、report_generator |
| 类分析 | `phases/class_analyzer.md` | MCP 拉类+方法、GUI 识别、按复杂度规划用例数 |
| 依赖追踪 | `phases/dependency_tracer.md` | MCP trace_path 出向、stub 决策矩阵、收集源码目录 |
| 测试生成 | `phases/test_writer.md` | 读模板生成测试代码、AAA、命名、protected 暴露 |
| 编译验证 | `phases/build_verifier.md` | 强制编译+运行、错误分类→修复表、重试预算 |
| 报告生成 | `phases/report_generator.md` | 固定收尾出 HTML/CSV 报告（含源码缺陷清单） |
| 代码提交 | `phases/code_committer.md` | 每批次自检通过后增量提交本批次完成类的测试代码到 git（只 commit，不 push）；防重提；提交信息复用 `git-commit-workflow` 技能格式（自动化提交跳过人工确认） |
| 自检 | `phases/self_checker.md` | 覆盖率完整性/命名规范/SPDX 头/stub 正确性/结构（单类自检，内部执行不产出交付文件） |
| 增量补全 | `phases/incremental_updater.md` | 图谱差集补缺失用例、CMake 智能合并 |
| 失败修复 | `phases/failure_repairer.md` | 失败修复 + 根因分类 + 源码缺陷标红 |

---

## 状态文件：`{test_dir}/.ut-session.json`

跨 phase 唯一的状态传递媒介。完整结构详见 `resources/references/session-schema.md`。

关键字段速查：
- `test_dir`：测试目录名（`"autotests"` 或 `"tests"`），由 environment_check 阶段探测确定
- `mcp_provider` / `mcp_provider_type`：解析到的知识图谱 MCP 提供方
- `status`: `pending` / `in_progress` / `done` / `failed` / `skipped` / `stale`
- `failure_reason`: `null` / `compile_error` / `runtime_crash` / `stub_incomplete` / `source_defect_compile` / `source_defect_runtime` / `source_defect_logic` / `needs_manual` / `max_iterations_exceeded`
- `coverage_threshold`: 函数覆盖率门禁阈值，默认 90（旧格式，向后兼容；新格式见 `inventory_path` 中的分级门禁）
- `inventory_path`: Mode 1 产出的 `.ut-inventory.json` 路径；存在时按方法分级设覆盖率门禁（🌟high: 行90%+分支80%+函数100%，⚖mid: 行60%+函数100%，💤low: 无硬性门禁）
- `iteration_count`：逐类闭环迭代轮数（1-3），达到 3 时强制标 `failed` + `max_iterations_exceeded`（Iron Law #13）
- `committed_classes` / `commit_history`：批次提交审计

---

## 快速参考

| 项 | 值 |
|----|----|
| 测试框架 | Google Test only |
| 测试文件 | `test_myclass.cpp` |
| 测试类名 | `MyClassTest`（禁止携带轮数/批次号如 `R18`） |
| 用例命名 | `{Feature}_{Scenario}_{ExpectedResult}`（禁止携带轮数/批次号如 `R18`） |
| MCP 工具 | 通过 `session.mcp_provider` 调用：`search_graph`, `get_code_snippet`, `trace_path`, `query_graph`, `index_status`；本地提供方额外支持 `index_repository` |
| Stub 模板 | `resources/templates/stub-patterns.cpp` |
| CMake 模板 | `resources/templates/cmake-*.txt` |
| 编译重试 | per-error 3 次，max 10 loops |
| 函数覆盖率阈值 | 默认 90%，可由用户指定；低于阈值触发增量补全 |
| 分级覆盖率门禁 | Mode 1 产出 `.ut-inventory.json` 后按方法分级：🌟high 行90%+分支80%+函数100%；⚖mid 行60%+函数100%；💤low 无硬性门禁 |
| MCP 提供方指南 | `resources/references/mcp-providers.md` |
| MCP 使用指南 | `resources/references/codebase-memory-guide.md` |
| 测试方法论 | `resources/references/test-types.md` |
| 覆盖率分级 | `resources/references/coverage-tiers.md` |

---

## 红旗（出现即停）

- 测试目录未经 environment_check 确认就随意选择（未记录在 session.test_dir）
- 用 Qt Test / Catch2 框架
- codebase-memory 未 ready 就开始生成
- MCP 提供方未解析或混用多个提供方（必须互斥）
- 未编译通过就报完成
- 从网络下载 stub-ext
- 修改用户源码（只标红不修）
- 单类失败阻塞整批

---

## 执行检查清单

```
□ 已执行 reconcile：读 {test_dir}/.ut-session.json，比对 git HEAD，判断源码是否变更
□ 首次运行且用户提供了 repo_url：已执行项目准备（拉取代码+搭建环境）
□ 首次运行且用户提供本地路径：已执行环境检查 → 框架搭建
□ MCP 提供方已解析（远端优先，本地兜底），session.mcp_provider 已记录
□ codebase-memory 索引 ready 后才执行后续 phase
□ 逐类闭环：每类走 类分析 → 依赖追踪 → 测试生成 → 编译验证 → 自检
□ 单类失败：已记录 failure_reason + 跳过 + 继续下一个类
□ 编译验证后读双信号：失败→失败修复；方法名差集→增量补全；函数覆盖率<阈值→增量补全
□ 批次提交：本批次所有目标类自检处理完毕（done/failed/skipped）后，已执行代码提交本批次完成类（跳过 committed_classes 中已记录的类）
□ 全类完成 + 覆盖达标：已执行报告生成收尾（测试代码已在各批次提交中入库，不再提交）
□ 疑似源码缺陷：已标 failure_reason，报告里标红，未自行修源码
□ 源码缺陷通知：报告生成返回 source_defect_count > 0 时，已向用户输出醒目提示
□ 提交边界：所有提交仅 commit 未 push；未提交源码修改、构建产物、session 文件、缓存；{test_dir}/.gitignore 已 staging
```
