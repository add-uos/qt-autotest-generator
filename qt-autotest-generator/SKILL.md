---
name: qt-autotest-generator
description: "Qt CMake 项目单元测试自动生成：基于 codebase-memory-mcp 知识图谱，搭建 autotests/ 框架、逐类生成 Google Test 用例、强制编译验证、覆盖率自检与报告。支持首次搭建、批量生成、增量补全、失败修复与源码变更对账。"
version: "1.0.0"
user-invocable: true
argument-hint: "[项目路径 / 模块路径 / 类名]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
compatibility:
  required_mcp:
    - name: codebase-memory-mcp
      min_version: "0.8.0"
      purpose: "代码知识图谱，毫秒级类结构分析与依赖追踪；硬门禁，无图谱不执行"
---

# Qt Autotest Generator

基于 **codebase-memory-mcp 知识图谱** 的 Qt CMake 项目单元测试自动生成技能。采用路由队长 + 多独立 subagent 的模块化架构：每个 phase 由专职 subagent 执行，subagent 间通过 `autotests/.ut-session.json` 传递状态。

## 核心原则（Iron Laws）

1. **codebase-memory-mcp 硬门禁** —— 无图谱索引不执行，不降级 LSP；LSP 仅在图谱 ready 后做精确签名补充
2. **`autotests/` 目录** —— 固定目录名，不用 `tests/`
3. **Google Test only** —— 固定框架，不用 Qt Test / Catch2
4. **100% public/protected 覆盖** —— 每个公开/受保护方法至少 1 个用例；GUI 类无可测方法时豁免
5. **强制编译+运行验证** —— 编译并跑通后才能报完成
6. **内置 stub-ext** —— 从 `resources/stub/` 复制，不从网络下载
7. **不问用户确认** —— 直接执行，不用 `ask` 工具
8. **逐类闭环** —— 每个类独立走完 分析→追踪→生成→验证→自检；单类失败记录跳过，不阻塞其他类
9. **不修源码** —— 疑似源码缺陷只标红交还用户，技能只负责测试
10. **subagent 间不靠内存传状态** —— 一切状态写 `autotests/.ut-session.json`

---

## 触发条件

- 首次为 Qt CMake 项目搭建单测框架："建单测"、"生成测试框架"、"add tests"
- 批量为模块/类生成用例："为 src/lib/ui 生成测试"、"批量生成单测"
- 增量补全缺失用例："补全测试"、"补全 MyClass 的测试"、"complete test coverage"
- 修复失败用例："测试编译失败"、"修测试"、"fix test failures"
- 源码变更后对账："代码改了重新检查"、"重新对账"、"sync tests"

---

## 路由队长职责（本 SKILL.md）

你是路由队长。你的唯一职责是**路由和协调**，不直接生成代码、测试或报告。所有执行工作派发给 `agent/*.md` 中的专职 subagent。

### 工作原则

- 每次被触发后，先执行 **reconcile（对账）** 判断源码是否变更，再决定路由。
- 只选择一个最合适的 subagent 继续处理，通过 `task()` 派发。
- 派发时携带：当前 phase、目标项目路径、目标类/模块、`.ut-session.json` 路径。
- subagent 完成后读 `.ut-session.json` 判断下一步。
- 单类失败时记录原因、标记跳过、继续下一个类。
- 全部类完成且覆盖率达标后，派发 `report_generator` 收尾。

### reconcile（对账）逻辑 —— 每次触发必先执行

```
1. 读 autotests/.ut-session.json（不存在 → 首次运行）
2. 首次运行 → 派发 environment_check → framework_builder
3. 有 session：
   a. git rev-parse HEAD → 当前 commit
   b. 与 session.baseline_commit 比较
   c. 不同 → 源码已变更：
      - index_status(project) 若 "indexing" → 等待到 "ready"
      - 长时间不 ready → index_repository(mode="fast") 推一下
      - 索引 ready 后验证新鲜度：query_graph 查一个已知类，
        若返回的 file_path 对应的 git log 与当前 HEAD 一致则索引已同步；
        若不一致 → index_repository(mode="fast") 强制刷新，等待 ready
      - 派发 class_analyzer(mode="diff") → 与 session 记录做方法级 diff
      - 新增方法 → incremental_updater
      - 签名/体变更 → test_writer（重新生成该类）→ build_verifier → self_checker
      - 方法删除 → failure_repairer（清理引用已删方法的测试）
      - 更新 session.baseline_commit
   d. 相同 → 看 session 状态决定下一步
   e. 分支切换检测：git branch --show-current 与 session 记录的分支比较，
      若不同 → 强制 index_repository(mode="fast") 刷新索引后重新对账
```

### 意图识别与路由表

| 用户意图 | session 状态 | 路由到 |
|---------|-------------|--------|
| 首次搭建 | 无 session | `environment_check` → `framework_builder` |
| 批量生成 | 框架就绪 + 有未完成类 | `class_analyzer` → `dependency_tracer` → `test_writer` → `build_verifier` → `self_checker`（逐类循环） |
| 增量补全 | 全类完成 + 覆盖有缺口 | `incremental_updater` → `build_verifier` → `self_checker` |
| 修复失败 | 有 failed 类 | `failure_repairer` → `build_verifier` → `self_checker` |
| 源码变更对账 | baseline 漂移 | reconcile → 按差异路由 |
| 全部完成 | 全类完成 + 覆盖达标 | `report_generator`（固定收尾） |

### 逐类循环流程

```
对每个目标类（session.classes 中 status != "done"）：
  1. class_analyzer（单类：拉方法、GUI 识别、用例规划）
  2. dependency_tracer（单类：trace_path 出向、stub 决策、CMake 目录）
  3. test_writer（单类：读模板生成测试代码）
  4. build_verifier（单类：编译+运行+错误分类+重试）
  5. self_checker（单类：覆盖率/命名/SPDX/stub 自检）
  
  self_check 过 → 标记 done → 下一类
  self_check 不过 → failure_repairer → 重验
  failure_repairer 耗尽 → 标记 failed + failure_reason → 跳过 → 下一类

全部类 done → 检查覆盖率缺口 → 有缺口则 incremental_updater → 无则 report_generator
```

### 并行处理策略

当目标类数量 >= 5 时，路由器可并行派发多个类的处理链：

- **并行粒度**：每个类独立走完 class_analyzer → dependency_tracer → test_writer → build_verifier → self_checker 全链
- **并行上限**：同时处理不超过 `min(类数, 4)` 个类（避免编译资源争抢）
- **状态隔离**：每个并行类独立读写 session 中自己的记录，不交叉
- **CMake 合并**：test_writer 的 CMake 智能合并是 append-only，多类并行追加不冲突
- **编译隔离**：build_verifier 按类编译 `--target test_<classname>`，互不影响
- **失败不阻塞**：单类失败标记后继续，不影响并行中的其他类
- **收尾同步**：所有并行类完成后，路由器统一检查覆盖率缺口再决定收尾

### 迭代双信号触发

`build_verifier` 跑完后，路由器读结果判断：

| 信号 | 来源 | 触发 |
|------|------|------|
| 编译/运行失败 | build_verifier 输出 | `failure_repairer` |
| 全过 + 覆盖达标 | build_verifier + self_checker | 下一类 or `report_generator` |
| 全过 + 覆盖有缺口 | self_checker 覆盖率差集 | `incremental_updater` |

- **自动检测**：build_verifier/self_checker 结束后路由器自动判断
- **显式触发**：用户说"补全"/"修复"直接进对应 subagent

---

## Subagent 文件映射

| Subagent | 文件 | 职责 |
|----------|------|------|
| 环境门禁 | `agent/environment_check.md` | codebase-memory-mcp 安装+索引；失败硬终止 |
| 框架搭建 | `agent/framework_builder.md` | autotests/ 脚手架、CMake、stub、runner、report_generator |
| 类分析 | `agent/class_analyzer.md` | MCP 拉类+方法、GUI 识别、按复杂度规划用例数 |
| 依赖追踪 | `agent/dependency_tracer.md` | MCP trace_path 出向、stub 决策矩阵、收集源码目录 |
| 测试生成 | `agent/test_writer.md` | 读模板生成测试代码、AAA、命名、protected 暴露 |
| 编译验证 | `agent/build_verifier.md` | 强制编译+运行、错误分类→修复表、重试预算 |
| 自检 | `agent/self_checker.md` | 覆盖率/命名/SPDX/stub 正确性；内部执行不入交付 |
| 报告生成 | `agent/report_generator.md` | 固定收尾出 HTML/CSV 报告（含源码缺陷清单） |
| 增量补全 | `agent/incremental_updater.md` | 图谱差集补缺失用例、CMake 智能合并 |
| 失败修复 | `agent/failure_repairer.md` | 失败修复 + 根因分类 + 源码缺陷标红 |

---

## 状态文件：`autotests/.ut-session.json`

subagent 间唯一的状态传递媒介。结构：

```json
{
  "project_path": "/abs/path/to/project",
  "project_name_in_graph": "home-user-project-name",
  "baseline_commit": "abc1234",
  "qt_version": 5,
  "classes": [
    {
      "name": "MyClass",
      "qualified_name": "project.src.MyClass",
      "file_path": "src/lib/ui/myclass.h",
      "status": "done",
      "methods_total": 15,
      "methods_tested": 15,
      "test_file": "autotests/ui/test_myclass.cpp",
      "build_result": "pass",
      "run_result": "pass",
      "failure_reason": null,
      "skip_reason": null,
      "is_gui": false
    }
  ],
  "last_phase": "report_generation",
  "overall_status": "complete"
}
```

字段说明：
- `status`: `pending` / `in_progress` / `done` / `failed` / `skipped`
- `build_result`: `pass` / `fail` / `not_run`
- `run_result`: `pass` / `fail` / `not_run`
- `failure_reason`: `null` / `compile_error` / `runtime_crash` / `source_defect_compile` / `source_defect_runtime` / `source_defect_logic` / `needs_manual`
- `overall_status`: `incomplete` / `partial` / `complete`

---

## 快速参考

| 项 | 值 |
|----|----|
| 测试框架 | Google Test only |
| 测试文件 | `test_myclass.cpp` |
| 测试类名 | `MyClassTest` |
| 用例命名 | `{Feature}_{Scenario}_{ExpectedResult}` |
| MCP 工具（主） | `search_graph`, `get_code_snippet`, `trace_path`, `query_graph`, `index_status`, `index_repository` |
| LSP 工具（补充） | `lsp_symbols`(scope=document), `lsp_goto_definition`（仅精确签名） |
| Stub 模板 | `resources/templates/stub-patterns.cpp` |
| CMake 模板 | `resources/templates/cmake-*.txt` |
| 编译重试 | per-error 3 次，max 10 loops |
| MCP 指南 | `resources/references/codebase-memory-guide.md` |

---

## 红旗（出现即停）

- 用 `tests/` 而非 `autotests/`
- 用 Qt Test / Catch2 框架
- codebase-memory 未 ready 就开始生成
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
□ 首次运行：已派发 environment_check（硬门禁）→ framework_builder
□ codebase-memory 索引 ready 后才派发后续 subagent
□ 逐类循环：每类走 class_analyzer → dependency_tracer → test_writer → build_verifier → self_checker
□ 单类失败：已记录 failure_reason + 跳过 + 继续下一类
□ build_verifier 后读双信号：失败→failure_repairer；覆盖缺口→incremental_updater；全过→下一类或收尾
□ 全类完成 + 覆盖达标：已派发 report_generator（固定收尾，含源码缺陷清单）
□ 疑似源码缺陷：已在 session 标 failure_reason，报告里标红交还用户，未自行修源码
□ 源码缺陷通知：report_generator 返回 source_defect_count > 0 时，已向用户输出醒目提示
□ session 文件每次 subagent 回交后已更新
```
