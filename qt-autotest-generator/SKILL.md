---
name: qt-autotest-generator
description: "Qt CMake 项目 GTest 单元测试自动生成与质量闭环。基于 codebase-memory-mcp 知识图谱，支持：开发预检与本地图谱同步（Mode 0）、函数重要性探测与分级（Mode 1）、按分级补全 GTest 用例（Mode 2，编译验证+覆盖率门禁）、覆盖率采集与汇总（Mode 3）、变异测试（Mode 4，可选，验证测试有效性）、源码缺陷导出与统计（Mode 5，可选，用例级标红清单）。触发于：生成单测/补全测试/扫描函数重要性/采集覆盖率/变异测试/导出源码缺陷/dev preflight/本地模式/unpushed/add gtest/coverage gap/fix test failures/mutation score/defect report 等。硬门禁：codebase-memory-mcp 知识图谱（Mode 0 显式本地，其余远端唯一、不回退），无图谱不执行。不触发于：非 Qt 或非 CMake 项目、Qt Test/Catch2/doctest、仅运行测试/配 CI/不生成测试代码。"
metadata:
  version: "3.4.1"
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
  env_vars:
    - name: QTAG_MCP_URL
      default: "http://10.8.12.80:13626/mcp"
      description: "远端 MCP HTTP 端点，fetch-mcp-data.py 使用"
    - name: QTAG_CBM_INSTALL_URL
      default: "https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh"
      description: "codebase-memory-mcp 安装脚本 URL，内网可设为镜像"
    - name: QTAG_PROXY
      default: "http://proxy02.uniontech.com:3128"
      description: "HTTP/HTTPS 代理地址"
    - name: QTAG_GIT_EMAIL
      default: "autotest@uniontech.com"
      description: "自动提交 git 回退邮箱"
    - name: CBM_INSTALL_SHA256
      default: ""
      description: "install.sh SHA256 校验值，设置后启用完整性校验"
---

# Qt Autotest Generator

基于 **codebase-memory-mcp 知识图谱** 的 Qt CMake 项目单元测试技能。支持五种模式（Mode 4 变异测试、Mode 5 缺陷导出为可选增强）；分步指令在 **`references/`**，执行前须 **`Read`** 对应文件。

| 模式 | 何时用 | 主入口 |
|------|--------|--------|
| **Mode 0 · Dev Preflight** | 本地开发中、有未 push 代码、需要本地图谱同步 | 下文 + `Read references/dev-preflight.md` |
| **Mode 1 · 函数重要性探测** | 项目初始化、扫描方法分级、生成 inventory | 下文 + `Read references/inventory.md` |
| **Mode 2 · 单元测试编写** | 按 inventory 补全 GTest 用例 | 下文 + `Read references/test-writer.md` |
| **Mode 3 · 覆盖率采集与汇总** | 只采集/统计覆盖率，不生成测试代码 | `Read references/report-generator.md` + `scripts/collect-coverage-report.py` |
| **Mode 4 · 变异测试**（可选） | 验证已有测试能否拦住缺陷（变异得分） | `Read references/mutation-testing.md` + `scripts/mutation-score.py` |
| **Mode 5 · 源码缺陷导出与统计** | 导出/统计单元测试发现的源码缺陷 | `Read references/defect-exporter.md` + `scripts/export-defects.py` |

Mode 2 启动时若 `.ut-inventory.json` 不存在 → **自动触发 Mode 1**。

Mode 3 为**只读采集**，不生成/修改测试代码，适合「跑一下看覆盖率」或「出分级覆盖率报告」。

Mode 4 为**可选增强**，在 Mode 2 产出的测试上注入变异体验证有效性，不改变 Mode 2 产物状态。

Mode 5 为**可选增强**，在 Mode 2 闭环中实时持久化发现的源码缺陷（`.ut-defects.json`，不入 git），最终退出前导出为标红清单。与 Mode 4 数据模型/脚本各自独立。

## 环境与约定

- **语言**：默认与用户语种一致；技术术语用行业常用表述。
- **测试框架**：Google Test only，不用 Qt Test / Catch2。
- **测试目录**：优先 `autotests/`；若项目已有 `tests/` 且含 C++ GTest 代码，则沿用 `tests/`。目录在 environment_check 阶段一次性探测确定。
- **知识图谱 MCP 硬门禁**：无图谱索引不执行，不降级到文件扫描/LSP。互斥使用其一：Mode 0 显式走本地图谱，其余一律远端唯一、不回退本地（见 `references/mcp-providers.md` §2）。
- **不修源码**：疑似源码缺陷只标红交还用户。
- **只 APPEND 不改已有**：修改根 CMakeLists.txt 和测试 CMake 时只追加新行（测试用例源码的注释/清理不受此约束）。
- **不问用户确认**：直接执行。

## 触发条件

- **Mode 0**：dev preflight、开发预检、dev 模式、本地模式、local mode、用本地图谱、索引同步、同步图谱、sync graph、unpushed、未推送、mode 0（注意：普通"生成单测/写测试"归 Mode 2，即使用户在本地开发，不要因"本地"字样误路由到 Mode 0）
- **Mode 1**：扫描函数重要性、建立分级表、探测分级、生成 inventory、项目初始化单测分析、importance inventory、scan method importance
- **Mode 2**：生成单测、建测试框架、批量生成单测、补全测试、修测试、重新对账、加测试、add gtest、setup unit tests、coverage gap、fix test failures、sync tests、improve coverage
- **Mode 3**：采集覆盖率、统计覆盖率、生成覆盖率报告、collect coverage、coverage report、coverage summary
- **Mode 4**：变异测试、mutation testing、mutation score、验证测试有效性、测试能不能发现问题、测试够不够好、变异得分、high 级方法有效性
- **Mode 5**：导出源码缺陷、统计源码缺陷、defect report、缺陷清单、导出缺陷数据、源码缺陷标红清单

**不触发于**：非 Qt 或非 CMake 项目、Qt Test/Catch2/doctest 框架、仅运行测试/配 CI/看日志、集成测试/性能测试/UI 自动化

---

## Mode 0 · Dev Preflight（开发预检）

1. **`Read`** `references/dev-preflight.md` → 完整执行本地提供方锁定 + 索引同步
2. 完成后**自动路由**：
   - `.ut-inventory.json` 不存在 → Mode 1
   - `.ut-inventory.json` 存在 → Mode 2（自动走 reconcile）

Mode 0 **不生成测试代码、不编译、不运行**，只确保本地提供方就绪且图谱与本地 HEAD 同步。
Mode 0 适用于正在活跃开发的场景（本地有未 push 的 commit），主动选择本地提供方
以避免远端图谱落后导致的 reconcile 超时。

> 本地图谱**仅**经 Mode 0 进入；其余流程远端唯一，图谱不可用/过时即硬终止并指引
> 切换 Mode 0（见 `references/mcp-providers.md` §2/§5）。

---

## Mode 1 · 函数重要性探测

1. **对账（reconcile）**：`Read` `references/reconcile-logic.md` → 首次运行（无 inventory）直接进入步骤 2；inventory 已存在且 `base_sha` 漂移时按差异路由后再决定是否全量重扫
2. **`Read`** `references/environment-check.md` → MCP 门禁
3. **`Read`** `references/inventory.md` → 全量扫描 → 评分 → 产出 `.ut-inventory.json` + `inventory-summary.md`

Mode 1 **不生成测试代码、不编译、不运行**，只建表。

---


## Mode 2 · 单元测试编写

1. **对账（reconcile）**：`Read` `references/reconcile-logic.md` → 若 inventory 不存在走首次运行（步骤 3 会触发 Mode 1）；若 `base_sha` 已漂移按差异路由（新增/签名变更/删除/分支切换）后再进入下方主流程
2. **过时测试清理**：若 diff 报告含 `removed` 方法 → `Read` `references/stale-test-cleanup.md` → 主动注释/删除引用已删方法的用例 + 连带清理 INSTANTIATE_TEST_SUITE_P + 更新 usecase_count（**不等编译报错**）
3. **`Read`** `references/environment-check.md` → MCP 门禁
4. 检查 `{test_dir}/.ut-inventory.json` → 不存在则先执行 Mode 1
5. **`Read`** `references/test-writer.md` → 逐类闭环 → 编译验证 → 更新 `usecase_count`

Mode 2 的子步骤按需读取：

| 子步骤 | 文件 | 何时读 |
|--------|------|--------|
| 过时测试清理 | `references/stale-test-cleanup.md` | diff 报告含 removed 方法时 |
| 框架搭建 | `references/framework-builder.md` | `{test_dir}/` 不存在时 |
| 类准备 | `references/inventory.md` | 方法分级入表，`test-writer.md` §4 从中提取待测类（schema 见 `references/inventory-schema.md`） |
| 依赖追踪 | `references/dependency-tracer.md` | 读 inventory 的 is_gui、MCP trace_path 出向、stub 决策、CMake 目录（MCP 详见 `references/codebase-memory-guide.md`） |
| 测试代码生成 | `references/test-code-gen.md` | 逐类闭环第 2 步 |
| 编译验证 | `references/build-verifier.md` | 逐类闭环第 3 步 |
| 自检 | `references/self-checker.md` | 逐类闭环第 4 步 |
| 增量补全 | `references/incremental-updater.md` | 覆盖率缺口时（增量逻辑见 `references/incremental-inventory.md`） |
| 失败修复 | `references/failure-repairer.md` | 编译/运行失败时 |
| 代码提交 | `references/code-committer.md` | 批次自检通过后 |

> 报告（Mode 3 覆盖率 + Mode 5 缺陷导出）在**全部批次提交完成、最终退出前**统一生成一次，不在每笔批次提交后触发（提交可能多笔）；详见 `references/test-writer.md` §9。Mode 3 / Mode 5 也可被用户单独触发。

---

## Mode 3 · 覆盖率采集与汇总

1. **`Read`** `references/report-generator.md` → 调用 `scripts/collect-coverage-report.py`
2. 脚本一条命令完成：运行测试 → lcov 采集 → genhtml → 分级覆盖率 → 汇总 JSON
3. 产出：`report/`（gtest XML）+ `html/`（lcov HTML）+ `coverage_by_level.json`（分级详情）+ `ut-summary.json`（三合一汇总）

Mode 3 **不生成测试代码、不编译新测试、不修改项目**，只采集和统计。

---

## Mode 4 · 变异测试（可选增强）

1. **前置检查**：Mode 2 已产出可编译可运行的测试；`.ut-inventory.json` 存在；reconcile 通过
2. **`Read`** `references/mutation-testing.md` → 调用 `scripts/mutation-score.py`
3. 脚本对 high 级方法注入变异体 → 增量编译 → 跑 GTest → 计算变异得分 → 恢复源码
4. 产出：`mutation_report.md`（存活变异体建议清单）+ `.ut-mutation.json`（与 `.ut-inventory.json` 命名对齐）

Mode 4 **临时修改源码**（注入变异体），受"源码安全四铁律"约束（替代 Iron Law #7），退出时 `git diff` 必为空。**不阻塞 Mode 2 done、不占 3 轮预算、不污染 Mode 3 覆盖率**。存活变异体只出建议，回 Mode 2 补强。

---

## Mode 5 · 源码缺陷导出与统计（可选增强）

1. **持久化**（Mode 2 闭环中实时发生）：`failure-repairer` 标红时调 `export-defects.py upsert` 落盘到 `{test_dir}/.ut-defects.json`；`build-verifier` 编译期确认源码缺陷提前预记录；通过验证时调 `mark-fixed` 闭环
2. **导出**：Mode 2 全部批次提交完成后，最终退出前统一导出（或用户单独触发）：`Read` `references/defect-exporter.md` → 调用 `scripts/export-defects.py export`
3. 产出：`defects.json`（机读）+ `defects-summary.md`（人读标红清单，md 内链接跳转源码行）

Mode 5 **不跑测试、不编译、不改测试代码、不改源码**（与 Mode 3 同构，纯导出统计）。`.ut-defects.json` 不入 git（本地存储，加 `.gitignore`）。颗粒度精确到**用例级**（`defect_id = {method_qn}#{Fixture}.{Case}`）。与 Mode 4 数据模型/脚本各自独立。详见 `references/defect-schema.md`。

---

## 核心原则（Iron Laws）

1. **知识图谱 MCP 硬门禁** —— 无图谱索引不执行
2. **Google Test only** —— 不用 Qt Test / Catch2
3. **函数覆盖率门禁** —— 有 `.ut-inventory.json` 时按方法分级：🌟high 行90%+分支80%+函数100%，⚖mid 行60%+函数100%，💤low 行60%+函数100%（同 mid）
4. **强制编译+运行验证** —— 编译并跑通后才能报完成
5. **内置 stub-ext** —— 从 `templates/stub-ext/` 复制，不从网络下载
6. **逐类闭环** —— 每个类独立走完 依赖追踪→生成→验证→自检；单类失败记录跳过，不阻塞其他类
7. **不修源码** —— 疑似源码缺陷只标红交还用户；标红即落盘到 `.ut-defects.json`（Mode 5）供导出（Mode 4 例外：受源码安全四铁律约束，退出 `git diff` 必为空，详见 `references/mutation-testing.md`）
8. **只 APPEND 不改已有** —— 不注释/删除/修改已有项目构建 CMake 代码（Iron Law #8 不约束测试用例源码；stale-test-cleanup 注释已删方法的测试用例属于正当清理）
9. **批次提交** —— 只 commit，不 push
10. **全局闭环迭代上限** —— 同一类最多循环 3 轮；3 轮后仍未通过，标记 `failed` + `max_iterations_exceeded` 并跳过
11. **usecase_count 实时更新** —— 每类编译通过后立即更新 `.ut-inventory.json` 的 `usecase_count` 字段
12. **项目源码只走 MCP** —— 被测类的实现/签名/调用链/分支/隐式依赖**必须**通过 MCP 工具获取（`get_code_snippet` 拿方法体、`trace_path` 拿出向调用链、`search_graph` 拿类与方法、`query_graph` 拿 IMPORTS/复杂关系）。**禁止**用 `read`/`grep`/`glob` 直读项目源码文件去理解被测代码——图谱是预解析的全局视角，毫秒级拿到调用链与传递依赖；逐文件 `read` 慢、漏传递依赖、漏隐式分支，是低质单测的首要根因。`read` 仅限：本技能自带文件（`references/`/`templates/`/`scripts/`）、inventory/defects JSON、**已生成的**测试文件。
13. **白盒质量用 MCP 反查校验** —— 测试文件顶部声明分支清单后，自检必须用 `get_code_snippet` 取真实源码分支（if/switch/for/while/throw/early-return）做差集，声明分支缺失真实分支即 `BRANCH_NOT_MAPPED` 违规；不得只写注释不核对源码。

---

## 快速参考

| 项 | 值 |
|----|----|
| 测试框架 | Google Test only |
| 测试文件 | `test_myclass.cpp` |
| 测试类名 | `MyClassTest` |
| 用例命名 | `{Feature}_{Scenario}_{ExpectedResult}` |
| MCP 工具 | `search_graph`, `get_code_snippet`, `trace_path`, `query_graph`, `index_status` —— **项目源码理解的唯一来源，禁止 read/grep 直读源文件** |
| 编译重试 | per-error 3 次，max 10 loops |
| 函数覆盖率阈值 | 分级门禁：high 行90%+分支80%+函数100%，mid/low 行60%+函数100%
| Dev Preflight | Mode 0，显式本地入口，索引同步 | `Read references/dev-preflight.md` |
| 模板与 stub-ext | `templates/`，详见 `references/templates-guide.md` |
| 分级覆盖率采集 | `scripts/collect-coverage-report.py`（Mode 3） |
| 变异测试 | `scripts/mutation-score.py`（Mode 4，可选，阈值 85%） |
| 源码缺陷导出 | `scripts/export-defects.py`（Mode 5，可选，upsert/mark-fixed/export） |
| 过时测试清理 | `scripts/stale-test-cleanup.py`（reconcile 后主动清理，不等编译报错） |
| 分支清单交叉验证 | `scripts/fetch-mcp-data.py extract-branches`（self-checker §2c，MCP `get_code_snippet` 反查真实分支做差集） |
| 缺陷数据文件 | `.ut-defects.json`（本地，不入 git） |

---

## 模板文件

`templates/` 含代码生成模板（带占位符）和 vendored stub-ext 库，详见 `references/templates-guide.md`。

---

## 红旗（出现即停）

- 用 Qt Test / Catch2 框架
- codebase-memory 未 ready 就开始生成
- MCP 提供方未解析或混用多个提供方
- 未编译通过就报完成
- 从网络下载 stub-ext
- 修改用户源码（Mode 4 例外：受源码安全四铁律约束，退出 `git diff` 必为空）
- 单类失败阻塞整批
- 用 `read`/`grep`/`glob` 直读**项目源码**理解被测类（实现/签名/调用链/分支/依赖）—— 必须走 MCP；`read` 只用于技能自带文件 / inventory·defects JSON / 已生成的测试文件
- 测试文件声明了分支清单但未用 MCP `get_code_snippet` 反查真实源码分支（声明 < 实际即漏测）
- 远端图谱必然过时（有未 push commit）仍继续生成——必须硬终止并指引 Mode 0 或等待远端同步，不回退本地

---

## Agent 自用工作流检查清单

```
□ 已区分 Mode 0（预检）/ Mode 1（分析）/ Mode 2（编写）/ Mode 3（采集）/ Mode 4（变异，可选）/ Mode 5（缺陷导出，可选），未混跑
□ 已执行 reconcile（比对 git HEAD 与 inventory.base_sha，按差异路由；首次运行无 inventory 直接进入环境检查）
□ Mode 0：已 Read references/dev-preflight.md；本地 MCP 可用且图谱已同步到本地 HEAD；已设置 mode_0_active 标志
□ Mode 1：已 Read references/environment-check.md + references/inventory.md；产出 .ut-inventory.json
□ Mode 2：已 Read references/environment-check.md；.ut-inventory.json 存在（不存在则先执行 Mode 1）
□ Mode 2：已按步骤 Read 对应 reference 子步骤文件
□ 过时测试清理：若 diff 报告含 removed 方法，已 Read stale-test-cleanup.md 并主动清理（不等编译报错）
□ MCP 提供方已解析（Mode 0=本地，其余=远端唯一、不回退），互斥使用
□ 项目源码理解只走 MCP：方法体用 `get_code_snippet`、调用链/隐式依赖用 `trace_path`、类与方法用 `search_graph`、IMPORTS 用 `query_graph`；未用 `read`/`grep` 直读项目源码
□ 逐类闭环：每类走 依赖追踪 → 测试生成 → 编译验证 → 自检（类列表与 level 来自 inventory）
□ 白盒反查：分支清单已用 `get_code_snippet` 取真实源码分支做差集，无 `BRANCH_NOT_MAPPED` 漏报
□ 单类失败：已记录 failure_reason + 跳过 + 继续下一个类
□ 每类编译通过后：已更新 .ut-inventory.json 的 usecase_count
□ 批次提交：本批次自检通过后已执行代码提交（只 commit 不 push）
□ 疑似源码缺陷：已标红，未自行修源码；已调 export-defects.py upsert 落盘到 .ut-defects.json
□ 全部批次提交完成（Mode 2 结束）：最终退出前已统一生成一次 Mode 3 覆盖率报告 + Mode 5 缺陷导出（不在每笔提交后触发）
□ Mode 4（可选）：已 Read references/mutation-testing.md；变异后 git diff --exit-code 通过；存活变异体清单已交付（回 Mode 2 补强）
□ Mode 5（可选）：缺陷已落盘 .ut-defects.json（不入 git）；导出 defects-summary.md 标红清单
```
