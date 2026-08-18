---
name: qt-autotest-generator
description: "用于为 Qt CMake 项目生成与维护 Google Test 单元测试：搭建 autotests/ 框架、逐类生成 GTest 用例、补全覆盖率缺口、修复失败用例、源码变更后重新对账。触发于「生成单测/建测试框架/批量生成测试/补全测试/修测试/重新对账/加测试」等表述，针对 Qt + CMake 的 C++ 项目；即使用户只说 add tests 或提高覆盖率也应触发。硬门禁依赖 codebase-memory-mcp 知识图谱（远端优先、本地兜底），强制编译+运行验证，函数覆盖率默认门禁 80%。不触发于：非 Qt 或非 CMake 项目、Qt Test/Catch2 框架、仅运行测试/CI 而不生成测试的任务。"
version: "1.1.0"
user-invocable: true
argument-hint: "[项目路径 / 模块路径 / 类名]"
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

基于 **codebase-memory-mcp 知识图谱** 的 Qt CMake 项目单元测试自动生成技能。采用路由队长 + 多独立 subagent 的模块化架构：每个 phase 由专职 subagent 执行，subagent 间通过 `autotests/.ut-session.json` 传递状态。

## 核心原则（Iron Laws）

1. **知识图谱 MCP 硬门禁** —— 无图谱索引不执行，不降级 LSP；LSP 仅在图谱 ready 后做精确签名补充。提供方在 `environment_check` 阶段一次性解析（**远端优先，本地兜底，互斥使用其一**），全流程通过 `session.mcp_provider` 调用，详见 `resources/references/mcp-providers.md`
2. **`autotests/` 目录** —— 固定目录名，不用 `tests/`
3. **Google Test only** —— 固定框架，不用 Qt Test / Catch2
4. **函数覆盖率门禁** —— lcov 函数覆盖率不低于阈值（默认 80%，可由用户指定）；每个公开/受保护方法至少 1 个用例；低于阈值触发 `incremental_updater` 补全。
5. **强制编译+运行验证** —— 编译并跑通后才能报完成
6. **内置 stub-ext** —— 从 `resources/stub/` 复制，不从网络下载
7. **不问用户确认** —— 直接执行，不用 `ask` 工具
8. **逐类闭环** —— 每个类独立走完 分析→追踪→生成→验证→自检；单类失败记录跳过，不阻塞其他类
9. **不修源码** —— 疑似源码缺陷只标红交还用户，技能只负责测试
10. **只 APPEND 不改已有** —— 修改根 CMakeLists.txt 和测试 CMake 时只追加新行，不注释/删除/修改已有代码
11. **批次提交 + 提交规范自检** —— 每批次（一轮批量生成/增量补全/失败修复）所有类 self_checker 通过后立即派发 `code_committer` 增量提交本批次完成类，并派发 `self_checker(commit_check=true)` 做提交规范自检；**只 commit，不 push**。最终 `report_generator` 收尾时测试代码已全部入库，不再触发 `code_committer`
12. **subagent 间不靠内存传状态** —— 一切状态写 `autotests/.ut-session.json`

---

## 触发条件

- **拉取项目生成单测**："拉取 https://github.com/foo/bar 的 dev 分支生成单测"、"clone 项目建测试" → 用户提供仓库地址 + 分支名，先派发 `project_preparer`
- 首次为 Qt CMake 项目搭建单测框架："建单测"、"生成测试框架"、"add tests"
- 批量为模块/类生成用例："为 src/lib/ui 生成测试"、"批量生成单测"
- 增量补全缺失用例："补全测试"、"补全 MyClass 的测试"、"complete test coverage"
- 修复失败用例："测试编译失败"、"修测试"、"fix test failures"
- 源码变更后对账："代码改了重新检查"、"重新对账"、"sync tests"
- **指定覆盖率阈值**："函数覆盖率 80%"、"覆盖率不低于 90%"、"coverage threshold 75%" → 写入 `session.coverage_threshold`，默认 80

**不触发于**（交还给通用工具或其它技能）：
- 非 Qt 或非 CMake 的项目（纯 C、Python、Go、前端等）
- 明确要求用 Qt Test / Catch2 / doctest 等其它测试框架
- 仅运行已有测试 / 配 CI / 看测试日志，不涉及「生成 / 补全 / 修复测试代码」
- 单测之外的测试类型：集成测试、性能基准、UI 自动化、契约测试

---

## 路由队长职责（本 SKILL.md）

你是路由队长。你的唯一职责是**路由和协调**，不直接生成代码、测试或报告。所有执行工作派发给 `agent/*.md` 中的专职 subagent。

### 工作原则

- 每次被触发后，先执行 **reconcile（对账）** 判断源码是否变更，再决定路由。
- 只选择一个最合适的 subagent 继续处理，通过 `task()` 派发。
- 派发时携带：当前 phase、目标项目路径、目标类/模块、`.ut-session.json` 路径。
- subagent 完成后读 `.ut-session.json` 判断下一步。
- 单类失败时记录原因、标记跳过、继续下一个类。
- **批次提交时机**：每批次（一轮批量生成/增量补全/失败修复）所有目标类 self_checker 全部通过后，立即派发 `code_committer` 增量提交本批次完成类的测试代码；提交完成后再派发 `self_checker(commit_check=true)` 做提交规范自检（已提交完整性 / 未误提交源码 / 未误提交构建产物 / 提交信息格式规范），不通过则派发 `code_committer` 修正。
- 全部类完成且覆盖率达标后，派发 `report_generator` 收尾（此时测试代码已在各批次提交中入库，`report_generator` 之后不再派发 `code_committer`）

### reconcile（对账）逻辑 —— 每次触发必先执行

```
1. 读 autotests/.ut-session.json（不存在 → 首次运行）
2. 首次运行：
   a. 用户提供了 repo_url + branch → 派发 project_preparer（拉取代码+搭建环境）
      project_preparer 完成后 → 派发 environment_check → framework_builder
   b. 用户提供了本地 project_path → 派发 environment_check → framework_builder
3. 有 session：
   a. 重新校验 MCP 提供方：读 `session.mcp_provider`，确认该提供方仍可用（`list_projects()` 可调通）且目标项目仍索引 ready。若提供方已失联（如远端断开），重新走 `environment_check` 解析
   b. git rev-parse HEAD → 当前 commit
   c. 与 session.baseline_commit 比较
    d. 不同 → 源码已变更：
       - index_status(project) 若 "indexing" → 等待到 "ready"
       - 长时间不 ready：
         · 本地提供方 → index_repository(mode="fast") 推一下
         · 远端提供方 → 等待远端 watcher 自动同步；超时则向用户提醒「远端索引未同步，请在远端手动刷新」
       - 索引 ready 后验证新鲜度：query_graph 查一个已知类，
         若返回的 file_path 对应的 git log 与当前 HEAD 一致则索引已同步；
         若不一致 → 同上按提供方类型处理（本地可 index_repository 刷新，远端只能等待/提醒）
       - 派发 class_analyzer(mode="diff") → 与 session 记录做方法级 diff
       - 新增方法 → incremental_updater
       - 签名/体变更 → test_writer（重新生成该类）→ build_verifier → self_checker
       - 方法删除 → failure_repairer（清理引用已删方法的测试）
       - 更新 session.baseline_commit
    e. 相同 → 看 session 状态决定下一步
    f. 分支切换检测：git branch --show-current 与 session 记录的分支比较，
       若不同 → 强制刷新索引后重新对账：
         · 本地提供方 → index_repository(mode="fast")
         · 远端提供方 → 等待远端 watcher 同步；超时则向用户提醒
```

### 意图识别与路由表

| 用户意图 | session 状态 | 路由到 |
|---------|-------------|--------|
| 用户提供 repo_url + branch 拉取项目 | 无 session | `project_preparer` → `environment_check` → `framework_builder` |
| 用户提供本地 project_path | 无 session | `environment_check` → `framework_builder` |
| 首次搭建 | 无 session | `environment_check` → `framework_builder` |
| 批量生成 | 框架就绪 + 有未完成类 | `class_analyzer` → `dependency_tracer` → `test_writer` → `build_verifier` → `self_checker`（逐类循环）→ **批次内全类 self_checker 通过** → `code_committer` → `self_checker(commit_check=true)` |
| 增量补全 | 全类完成 + 覆盖有缺口 | `incremental_updater` → `build_verifier` → `self_checker` → **本批次 self_checker 通过** → `code_committer` → `self_checker(commit_check=true)` |
| 修复失败 | 有 failed 类 | `failure_repairer` → `build_verifier` → `self_checker` → **本批次 self_checker 通过** → `code_committer` → `self_checker(commit_check=true)` |
| 源码变更对账 | baseline 漂移 | reconcile → 按差异路由 → **本批次 self_checker 通过** → `code_committer` → `self_checker(commit_check=true)` |
| 全部完成 | 全类完成 + 覆盖达标 | `report_generator`（固定收尾；测试代码已在各批次提交中入库，不再 `code_committer`） |

### 逐类循环流程

```
对每个目标类（session.classes 中 status != "done"）：
  1. class_analyzer（单类：拉方法、GUI 识别、用例规划）
  2. dependency_tracer（单类：trace_path 出向、stub 决策、CMake 目录）
  3. test_writer（单类：读模板生成测试代码）
  4. build_verifier（单类：编译+运行+错误分类+重试）
  5. self_checker（单类：覆盖率/命名/SPDX/stub 自检）
  
  self_check 过 → 标记 done → 下一类
  self_check 不过（覆盖率缺口 / 函数覆盖率 < 阈值 / 命名 / SPDX / stub）→ incremental_updater 或 test_writer 修正
  failure_repairer 耗尽 → 标记 failed + failure_reason → 跳过 → 下一类

本批次所有目标类 self_checker 处理完毕（含 done / failed / skipped）→ 按核心原则 11 执行批次提交闭环（`code_committer` → `self_checker(commit_check=true)`；自检不过则 `code_committer` 修正后重验，详见原则 11 与 `agent/code_committer.md` / `agent/self_checker.md`）

全部类 done → 检查覆盖率缺口（方法名差集 + lcov 函数覆盖率是否达标）→ 有缺口则 incremental_updater → 无则 report_generator
```

### 并行处理策略

当目标类数量 >= 5 时，路由器可并行派发多个类的处理链：

- **并行粒度**：每个类独立走完 class_analyzer → dependency_tracer → test_writer → build_verifier → self_checker 全链
- **并行上限**：同时处理不超过 `min(类数, 4)` 个类（避免编译资源争抢）
- **状态隔离（分片 session）**：每个并行类写入独立的 `autotests/.ut-session.<classname>.json` 分片文件，**不并发写主 session**；全部并行类完成后，路由器按固定顺序将各分片合并回 `autotests/.ut-session.json`，再删除分片文件
- **CMake 合并**：test_writer 的 CMake 智能合并是 append-only，多类并行追加不冲突
- **编译隔离**：build_verifier 按类编译 `--target test_<classname>`，互不影响
- **失败不阻塞**：单类失败标记后继续，不影响并行中的其他类
- **收尾同步**：所有并行类完成后，路由器统一合并分片、检查覆盖率缺口；本批次无覆盖率缺口或缺口已交由 incremental_updater 处理后，按核心原则 11 执行批次提交闭环

### 迭代双信号触发

`build_verifier` 跑完后，路由器读结果判断：

| 信号 | 来源 | 触发 |
|------|------|------|
| 编译/运行失败 | build_verifier | `failure_repairer` |
| 全过 + 方法名差集非空 | self_checker 覆盖率差集 | `incremental_updater` |
| 全过 + lcov 函数覆盖率 < 阈值 | self_checker 函数覆盖率门禁 | `incremental_updater`（传入未覆盖函数清单） |
| 全过 + 覆盖达标 | build_verifier + self_checker | 下一类 or `report_generator` |

- **自动检测**：build_verifier/self_checker 结束后路由器自动判断
- **显式触发**：用户说"补全"/"修复"直接进对应 subagent

---

## Subagent 文件映射

| Subagent | 文件 | 职责 |
|----------|------|------|
| 项目准备 | `agent/project_preparer.md` | 拉取代码、校验基线、安装依赖、验证构建环境；用户提供 repo_url 时第一道前置 |
| 环境门禁 | `agent/environment_check.md` | MCP 提供方解析（远端优先，本地兜底）、索引、验证；失败硬终止 |
| 框架搭建 | `agent/framework_builder.md` | autotests/ 脚手架、CMake、stub、runner、report_generator |
| 类分析 | `agent/class_analyzer.md` | MCP 拉类+方法、GUI 识别、按复杂度规划用例数 |
| 依赖追踪 | `agent/dependency_tracer.md` | MCP trace_path 出向、stub 决策矩阵、收集源码目录 |
| 测试生成 | `agent/test_writer.md` | 读模板生成测试代码、AAA、命名、protected 暴露 |
| 编译验证 | `agent/build_verifier.md` | 强制编译+运行、错误分类→修复表、重试预算 |
| 报告生成 | `agent/report_generator.md` | 固定收尾出 HTML/CSV 报告（含源码缺陷清单） |
| 代码提交 | `agent/code_committer.md` | 每批次 self_checker 通过后增量提交本批次完成类的测试代码到 git（只 commit，不 push）；防重提；提交信息含批次统计与基线 commit |
| 自检（含提交规范） | `agent/self_checker.md` | 覆盖率/命名/SPDX/stub 正确性（单类模式）；提交规范自检（commit_check 模式）：已提交完整性 / 未误提交源码 / 未误提交构建产物 / 提交信息格式规范 |
| 增量补全 | `agent/incremental_updater.md` | 图谱差集补缺失用例、CMake 智能合并 |
| 失败修复 | `agent/failure_repairer.md` | 失败修复 + 根因分类 + 源码缺陷标红 |

---

## 状态文件：`autotests/.ut-session.json`

subagent 间唯一的状态传递媒介。结构：

```json
{
  "project_path": "/abs/path/to/project",
  "project_name_in_graph": "home-user-project-name",
  "mcp_provider": "remote-codebase-memory-mcp",
  "mcp_provider_type": "remote",
  "repo_url": "https://github.com/foo/bar.git",
  "branch": "dev",
  "baseline_commit": "abc1234",
  "baseline_date": "2025-07-29",
  "baseline_title": "feat: add new feature",
  "pull_method": "git_clone",
  "build_env": "verified",
  "qt_version": 5,
  "coverage_threshold": 80,
  "classes": [
    {
      "name": "MyClass",
      "qualified_name": "project.src.MyClass",
      "file_path": "src/lib/ui/myclass.h",
      "status": "done",
      "methods_total": 15,
      "methods_tested": 15,
      "function_coverage": 86.7,
      "test_file": "autotests/ui/test_myclass.cpp",
      "build_result": "pass",
      "run_result": "pass",
      "failure_reason": null,
      "skip_reason": null,
      "is_gui": false
    }
  ],
  "last_phase": "report_generation",
  "overall_status": "complete",
  "committed_classes": ["MyClass", "FooBar", "Baz"],
  "last_batch_commit": "9f3a2c1",
  "commit_history": [
    {"batch": 1, "commit_sha": "9f3a2c1", "classes": ["MyClass", "FooBar"], "committed_at": "2026-08-04T10:30:00+08:00"}
  ]
}
```

字段说明：
- `mcp_provider`：解析到的知识图谱 MCP 提供方（`remote-codebase-memory-mcp` 或 `codebase-memory-mcp`）
- `mcp_provider_type`：`remote` 或 `local`
- `status`: `pending` / `in_progress` / `done` / `failed` / `skipped`
- `build_result`: `pass` / `fail` / `not_run`
- `run_result`: `pass` / `fail` / `not_run`
- `failure_reason`: `null` / `compile_error` / `runtime_crash` / `source_defect_compile` / `source_defect_runtime` / `source_defect_logic` / `needs_manual`
- `overall_status`: `incomplete` / `partial` / `complete`
- `coverage_threshold`: 函数覆盖率门禁阈值，默认 80，可由用户指定；低于此值触发 `incremental_updater` 补全
- `function_coverage`: lcov 解析出的该类函数覆盖率百分比；低于 `coverage_threshold` 则触发补全
- `committed_classes`: 已通过 `code_committer` 提交到 git 的类名列表；下次 `code_committer` 跳过这些类避免重复提交
- `last_batch_commit`: 最近一次批次提交的 commit sha；`self_checker(commit_check=true)` 据此校验提交规范
- `commit_history`: 各批次提交记录（batch 序号 / commit_sha / 本批次类列表 / 提交时间），用于审计与回溯

---

## 快速参考

| 项 | 值 |
|----|----|
| 测试框架 | Google Test only |
| 测试文件 | `test_myclass.cpp` |
| 测试类名 | `MyClassTest`（禁止携带轮数/批次号如 `R18`） |
| 用例命名 | `{Feature}_{Scenario}_{ExpectedResult}`（禁止携带轮数/批次号如 `R18`） |
| MCP 工具（主） | 通过 `session.mcp_provider` 调用：`search_graph`, `get_code_snippet`, `trace_path`, `query_graph`, `index_status`；本地提供方额外支持 `index_repository` |
| LSP 工具（补充） | `lsp_symbols`(scope=document), `lsp_goto_definition`（仅精确签名） |
| Stub 模板 | `resources/templates/stub-patterns.cpp` |
| CMake 模板 | `resources/templates/cmake-*.txt` |
| 编译重试 | per-error 3 次，max 10 loops |
| 函数覆盖率阈值 | 默认 80%，可由用户指定；低于阈值触发 `incremental_updater` 补全 |
| MCP 提供方指南 | `resources/references/mcp-providers.md` |
| MCP 使用指南 | `resources/references/codebase-memory-guide.md` |
| 测试方法论 | `resources/references/test-types.md` |

---

## 红旗（出现即停）

- 用 `tests/` 而非 `autotests/`
- 用 Qt Test / Catch2 框架
- codebase-memory 未 ready 就开始生成
- MCP 提供方未解析或混用多个提供方（必须互斥）
- 未编译通过就报完成
- 跳过 subagent 直接手写
- 用 `ask` 工具问用户
- 从网络下载 stub-ext
- 修改用户源码（只标红不修）
- 单类失败阻塞整批

---

## 路由队长自用检查清单

```
□ 已执行 reconcile：读 .ut-session.json，比对 git HEAD，判断源码是否变更
□ 首次运行且用户提供了 repo_url：已派发 project_preparer（拉取代码+搭建环境）
□ 首次运行且用户提供本地路径：已派发 environment_check → framework_builder
□ MCP 提供方已解析（远端优先，本地兜底），session.mcp_provider 已记录
□ codebase-memory 索引 ready 后才派发后续 subagent
□ 逐类循环：每类走 class_analyzer → dependency_tracer → test_writer → build_verifier → self_checker
□ 单类失败：已记录 failure_reason + 跳过 + 继续下一个类
□ build_verifier 后读双信号：失败→failure_repairer；方法名差集→incremental_updater；函数覆盖率<阈值→incremental_updater
□ 批次提交：本批次所有目标类 self_checker 处理完毕（done/failed/skipped）后，已派发 code_committer 增量提交本批次完成类（跳过 committed_classes 中已记录的类）
□ 提交规范自检：code_committer 完成后已派发 self_checker(commit_check=true)，校验 4 项（已提交完整性 / 未误提交源码 / 未误提交构建产物 / 提交信息格式规范）
□ 提交规范自检未过：已派发 code_committer 修正（仅 message 不规范时 amend 本批次未 push 的 commit；文件误提交时必须新 commit 撤销）后再 self_checker(commit_check=true)
□ 全类完成 + 覆盖达标：已派发 report_generator 收尾（测试代码已在各批次提交中入库，不再 code_committer）
□ 疑似源码缺陷：已标 failure_reason，报告里标红，未自行修源码
□ 源码缺陷通知：report_generator 返回 source_defect_count > 0 时，已向用户输出醒目提示
□ 提交边界：所有 code_committer 仅 commit 未 push；未提交源码修改、构建产物、session 文件、缓存
```
