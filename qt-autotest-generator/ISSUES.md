# qt-autotest-generator 修复清单

> 审查日期：2025-08-20
> 基于完整技能审查 + 用户反馈生成

---

## P0 — 必须优先修复

### #1 兼容 `tests/` 目录（用户反馈）

**现状**：Iron Law #2 硬编码 `autotests/`，红旗清单也将 `tests/` 列为违规。

**问题**：部分项目已有 `tests/` 目录且不愿迁移，强制用 `autotests/` 会导致：
- 已有 `tests/` 的项目重复建目录
- 企业/社区既有约定被打破
- 红旗误报（`tests/` 完全合理却被标红）

**修复方案**：
1. **SKILL.md Iron Law #2** 改为：
   > 测试目录优先 `autotests/`；若项目已有 `tests/` 且含测试代码，则沿用 `tests/`，不强制迁移。目录选择在 environment_check 阶段一次性确定，记录在 `session.test_dir`，全流程统一读取。

2. **SKILL.md 红旗清单** 移除"用 `tests/` 而非 `autotests/`"，改为：
   > 测试目录未经 environment_check 确认就随意选择（未记录在 session.test_dir）

3. **environment_check.md** 增加目录探测逻辑：
   ```
   1. 检查项目根下是否存在 autotests/ 或 tests/
   2. 若 autotests/ 存在 → 用 autotests/
   3. 若 tests/ 存在（且含 C++ 测试代码）→ 用 tests/
   4. 若都不存在 → 创建 autotests/（默认）
   5. 结果写入 session.test_dir（如 "autotests" 或 "tests"）
   ```

4. **全流程** 所有硬编码 `autotests/` 的地方改为读 `session.test_dir`，涉及：
   - SKILL.md：session JSON 示例加 `test_dir` 字段
   - framework_builder.md：脚手架创建目录从 session 读
   - test_writer.md：测试文件路径 `autotests/<module>/test_*.cpp` → `{session.test_dir}/<module>/test_*.cpp`
   - build_verifier.md：编译目录 `build-autotests` → `build-{session.test_dir}`
   - report_generator.md：报告路径引用
   - code_committer.md：git add 路径
   - incremental_updater.md：CMake 合并路径
   - generate-runner.sh：`AUTOTEST_ROOT` 变量从硬编码改为参数注入
   - generate-cmake-utils.sh：同上
   - resources/templates/cmake-autotests.txt：文件名和路径注释
   - examples/：补充 tests/ 目录示例或说明

5. **session JSON** 增加字段：
   ```json
   "test_dir": "autotests"
   ```
   值为 `"autotests"` 或 `"tests"`，由 environment_check 写入。

**涉及文件**：
- `SKILL.md`
- `phases/environment_check.md`
- `phases/framework_builder.md`
- `phases/test_writer.md`
- `phases/build_verifier.md`
- `phases/report_generator.md`
- `phases/code_committer.md`
- `phases/incremental_updater.md`
- `phases/self_checker.md`
- `phases/class_analyzer.md`
- `phases/dependency_tracer.md`
- `phases/failure_repairer.md`
- `phases/project_preparer.md`
- `resources/scripts/generate-runner.sh`
- `resources/scripts/generate-cmake-utils.sh`
- `resources/templates/cmake-autotests.txt`
- `resources/templates/cmake-submodule.txt`
- `examples/sample-qt-project/autotests/.ut-session.json`

---

### #2 全局闭环迭代无上限

**现状**：编译验证失败 → 失败修复 → 测试生成修正 → 编译验证 → 又失败 → 无限循环。

**修复方案**：在 SKILL.md Iron Laws 中增加第 13 条：
> **全局闭环迭代上限** —— 同一类在 逐类闭环 内最多循环 3 轮（分析→追踪→生成→验证→自检 为 1 轮）。3 轮后仍未通过自检，标记 `failed` + `failure_reason: "max_iterations_exceeded"` 并跳过。

在 `phases/self_checker.md` 和 `phases/incremental_updater.md` 中补充迭代计数检查逻辑。

session JSON class 对象增加字段：
```json
"iteration_count": 1
```

**涉及文件**：`SKILL.md`、`phases/self_checker.md`、`phases/incremental_updater.md`、`phases/build_verifier.md`

---

### #3 分支切换后旧测试文件处理未定义

**现状**：reconcile 检测到分支切换后只刷新索引重新对账，但未处理旧分支遗留的测试文件。

**问题**：旧分支的测试文件引用的源码类在新分支可能不存在，编译必定失败。

**修复方案**：在 reconcile 逻辑中增加：
```
分支切换检测到后：
1. 遍历 session.classes，检查每个类的 file_path 是否在当前分支仍存在
2. 不存在 → 标记该类 status="stale"
3. stale 类的测试文件：保留但从 CMakeLists 中移除 add_subdirectory（避免编译）
4. 新分支重新走类分析 → 新增/变更的类正常闭环
5. 更新 session，记录 stale_classes 列表
```

**涉及文件**：`SKILL.md` reconcile 伪代码、`phases/class_analyzer.md`、`phases/incremental_updater.md`

---

### #4 并行分片 session 机制未在 phase 文件中说明

**现状**：SKILL.md 提到 `autotests/.ut-session.<classname>.json` 分片与合并，但 12 个 phase 文件均未提及。

**修复方案**：新建 `resources/references/parallel-strategy.md`，详述：
1. 何时创建分片（并行类处理开始时）
2. 分片文件格式（主 session 子集，仅含当前类数据）
3. 写入规则（并行类只写分片，不碰主 session）
4. 合并时机（所有并行类完成后）
5. 合并顺序（按 session.classes 原始顺序）
6. 清理（合并后删除分片文件）
7. 异常处理（某个并行类崩溃时如何恢复）

在 `phases/framework_builder.md` 和 `phases/self_checker.md` 中引用此文。

**涉及文件**：新建 `resources/references/parallel-strategy.md`，修改 `phases/framework_builder.md`、`phases/self_checker.md`

---

### #5 框架搭建缺少 `.gitignore` 生成

**现状**：code_committer.md 提到"不提交 session 文件、缓存"，但无 `.gitignore` 规则确保。

**修复方案**：`framework_builder.md` 增加步骤：在 `{test_dir}/` 下生成 `.gitignore`：
```gitignore
# Build artifacts
build-*/

# Session & cache
.ut-session.json
.ut-session.*.json
.results/

# Python cache
__pycache__/
*.pyc

# Coverage
coverage/
```

同时在 `phases/code_committer.md` 中增加：提交前 `git add {test_dir}/.gitignore`。

**涉及文件**：`phases/framework_builder.md`、`phases/code_committer.md`

---

## P1 — 重要修复

### #6 `cmake-submodule.txt` 空字符串 if 判断有 bug

**现状**：
```cmake
if(PROJECT_LIBRARIES)
```
当 `PROJECT_LIBRARIES` 为空字符串 `""` 时，CMake 仍判断为 true。

**修复**：改为：
```cmake
if(PROJECT_LIBRARIES AND NOT PROJECT_LIBRARIES STREQUAL "")
```

**涉及文件**：`resources/templates/cmake-submodule.txt`

---

### #7 reconcile 索引等待无硬超时

**现状**：远程提供方 "indexing" 状态下只能等待/提醒，无超时。

**修复**：在 reconcile 逻辑增加硬超时 300 秒（5 分钟）：
```
超时后 → 硬终止 + 输出 "[FATAL] 远端索引 5 分钟未 ready，请手动刷新远端或切换本地提供方"
```

**涉及文件**：`SKILL.md` reconcile 伪代码、`phases/environment_check.md`

---

### #8 `generate-cmake-utils.sh` 包含非 GTest 框架选项

**现状**：`UnitTestUtils.cmake` 含 `USE_QT_TEST`、`USE_CATCH2` 分支，违反 Iron Law #3。

**修复**：移除 Qt Test 和 Catch2 分支，只保留 GTest。在 `ut_init_test_environment` 中直接 `find_package(GTest REQUIRED)`。

**涉及文件**：`resources/scripts/generate-cmake-utils.sh`

---

### #9 `run-ut.sh` 覆盖率提取路径硬编码 `*/src/*`

**现状**：
```bash
lcov --extract "$BUILD_DIR/coverage/total.info" "*/src/*"
```
部分项目源码目录非 `src/`，导致覆盖率数据为空。

**修复**：由 generate-runner.sh 从 session 读取 `source_dirs`，动态生成 extract 模式：
```bash
# {SOURCE_DIRS} 由 framework_builder 从 session.source_dirs 注入
lcov --extract "$BUILD_DIR/coverage/total.info" {SOURCE_DIRS_PATTERN}
```

**涉及文件**：`resources/scripts/generate-runner.sh`

---

### #10 LSP 引用悬空

**现状**：SKILL.md 提到 LSP 工具但 phase 文件未具化，`allowed-tools` 也未列出。

**修复**：二选一——
- A) 在 `phases/class_analyzer.md` 和 `phases/dependency_tracer.md` 补充 LSP 使用场景：签名精确参数类型时调用 `lsp_goto_definition`；`allowed-tools` 加上 `Lsp`
- B) 从 SKILL.md 移除 LSP 引用，避免混淆

**涉及文件**：`SKILL.md`、`phases/class_analyzer.md`、`phases/dependency_tracer.md`

---

### #11 全 failed 批次无提交记录

**现状**：全批 failed 无类可提交，`commit_history` 不记录该批次，审计断裂。

**修复**：在 `phases/code_committer.md` 增加：空批次也记录：
```json
{"batch": N, "commit_sha": null, "classes": [], "note": "all_failed_or_skipped"}
```

**涉及文件**：`phases/code_committer.md`

---

### #12 MCP 查询失败无统一处理策略

**现状**：`search_graph`/`trace_path` 返回错误/空时，各 phase 未定义处理路径。

**修复**：统一策略——MCP 关键查询失败（类结构/依赖）→ 硬终止 + 明确错误；非关键查询降级到文件读取 + 警告。在 `phases/class_analyzer.md` 和 `phases/dependency_tracer.md` 补充。

**涉及文件**：`phases/class_analyzer.md`、`phases/dependency_tracer.md`

---

## P2 — 改进优化

### #13 补充英文触发语和 argument-hint

**现状**：description 全中文，argument-hint 缺少 `repo_url + branch`。

**修复**：
- description 增加英文触发语："add gtest"、"setup unit tests"、"coverage gap"、"fix test failures"、"sync tests"
- argument-hint 改为：`[项目路径 / 模块路径 / 类名 / repo_url 分支名]`

**涉及文件**：`SKILL.md` frontmatter

---

### #14 SKILL.md 瘦身

**现状**：约 200 行，reconcile 伪代码/并行策略/session 结构可下放。

**修复**：
- reconcile 伪代码 → `resources/references/reconcile-logic.md`
- 并行处理策略 → `resources/references/parallel-strategy.md`（与 #4 合并）
- session JSON 结构 → `resources/references/session-schema.md`
- SKILL.md 保留 Iron Laws、路由表、Phase 映射、红旗、检查清单

**涉及文件**：`SKILL.md`、新建 3 个 reference 文件

---

### #15 `google-test-base.cpp` 模板改进

**问题**：
- 缺少 `#include <QCoreApplication>` 条件注释
- `{BranchList}`、`{Namespace}`、`{NamespaceEnd}` 占位符无说明
- SPDX 年份硬编码 2026

**修复**：
- 加条件注释：`// #include <QCoreApplication>  // GUI 类需要此 include`
- 占位符在 test_writer.md 中加替换规则说明
- SPDX 年份改为 `{SPDX_YEAR}`，由 test_writer 填入

**涉及文件**：`resources/templates/google-test-base.cpp`、`phases/test_writer.md`

---

### #16 `stub-patterns.cpp` 补充 protected 方法测试模式

**现状**：SKILL.md 提到 "protected 暴露"但模板中无代码模式。

**修复**：在 stub-patterns.cpp 末尾加第 20 节：
```cpp
// 20. 访问 protected/private 成员（仅测试文件内，不改源码）
// 方法 1：#define 预处理（最常用，推荐）
#define protected public
#define private public
#include "myclass.h"
#undef protected
#undef private
// 注意：#define 必须在 #include 之前，且 #undef 紧随其后防止污染

// 方法 2：测试友元（需源码加 FRIEND_TEST 声明，但 Iron Law #9 禁止改源码，此方法不适用）
```

**涉及文件**：`resources/templates/stub-patterns.cpp`

---

### #17 `stub-patterns.cpp` gMock include 位置警告

**现状**：第 17 节在文件中间插入 `#include <gmock/gmock.h>` 并注释"不可照搬到文件中部"。

**修复**：在节首加醒目警告框：
```cpp
// ⚠️⚠️⚠️ 以下 #include <gmock/gmock.h> 仅为展示 gMock 模式起始位置！
// 实际使用时，gMock include 必须放测试文件顶部（与 #include <gtest/gtest.h> 同区）。
// 绝不可照搬此位置到文件中部！
```

**涉及文件**：`resources/templates/stub-patterns.cpp`

---

### #18 build_verifier 显式增量编译命令

**现状**：SKILL.md 提到"按类编译"，但 build_verifier.md 未写出具体命令。

**修复**：在 `phases/build_verifier.md` 增加明确命令模板：
```bash
cmake --build build-autotests --target test_<classname> -j $(nproc)
```

**涉及文件**：`phases/build_verifier.md`

---

### #19 同名类冲突消歧

**现状**：两个不同路径的同名类（如 `A/Manager.h` 和 `B/Manager.h`）会生成同名测试文件。

**修复**：在 `phases/test_writer.md` 增加消歧逻辑：
```
若项目内存在同名类：
  测试文件路径 = {test_dir}/{module_path_flattened}/test_{classname}.cpp
  例：tests/a/test_manager.cpp 和 tests/b/test_manager.cpp
  CMake 子目录按模块路径拆分，不合并
```

**涉及文件**：`phases/test_writer.md`、`phases/dependency_tracer.md`

---

### #20 self_checker lcov 函数覆盖率解析方式未详述

**现状**：self_checker 提到"函数覆盖率 < 阈值"但未说明如何从 `filtered.info` 提取 per-class 数据。

**修复**：在 `phases/self_checker.md` 补充解析逻辑：
```bash
# 提取单类函数覆盖率
lcov --summary build-autotests/coverage/filtered.info 2>&1 | grep "functions"
# 或更精确：按源文件路径过滤
lcov --extract coverage/filtered.info "*/myclass.cpp" --output-file /tmp/class.info
lcov --summary /tmp/class.info 2>&1 | grep "functions"
```

**涉及文件**：`phases/self_checker.md`

---

### #21 failure_repairer "源码缺陷" 判定边界模糊

**现状**：编译失败标为 `source_defect_compile`，但可能是 stub 不完整导致。

**修复**：增加判定层级：
```
编译失败：
  1. 检查错误是否涉及 stub 相关符号 → 是 → 标 stub_incomplete，走修复
  2. 检查错误是否涉及项目内非待测类代码 → 是 → 标 source_defect_compile，标红
  3. 无法确定 → 标 needs_manual
```

**涉及文件**：`phases/failure_repairer.md`

---

### #22 report_generator.md 未说明源码缺陷清单输出格式

**修复**：在 `phases/report_generator.md` 补充：
```
源码缺陷清单格式：
- HTML 报告中单独 "源码缺陷" 章节，红色标注
- CSV 列：类名 | 文件路径 | 缺陷类型 | 严重程度 | 错误信息摘要
- JSON：report_data.json 中 source_defects 数组
```

**涉及文件**：`phases/report_generator.md`

---

## P3 — 低优先级

### #23 report_generator 单源分发

**现状**：`resources/report_generator/` 和 `examples/sample-qt-project/autotests/report_generator/` 双份拷贝。

**修复**：example 中的 report_generator 改为从 `resources/report_generator/` 复制的说明，或 example 中删除此目录、generate-runner.sh 统一从 resources 复制。

**涉及文件**：`examples/`、`resources/scripts/generate-runner.sh`

---

### #24 `setup-codebase-memory.sh` 远程 install.sh 无校验

**修复**：加 SHA256 校验：
```bash
EXPECTED_SHA256="..."  # 定期更新
echo "$EXPECTED_SHA256  $tmp_script" | sha256sum -c || { log_error "Checksum mismatch"; return 1; }
```

**涉及文件**：`resources/scripts/setup-codebase-memory.sh`

---

### #25 `run-ut.sh` ctest `--output-junit` 版本兼容

**现状**：`--output-junit` 需 CMake 3.21+。

**修复**：加版本检测：
```bash
CTEST_VERSION=$(ctest --version | head -1 | grep -oP '[\d.]+')
if version_ge "$CTEST_VERSION" "3.21"; then
    ctest --output-junit "$REPORT_DIR/test_results.xml" ...
else
    # Fallback: 用 GTEST_OUTPUT 环境变量让 gtest 直接输出 XML
    export GTEST_OUTPUT="xml:$REPORT_DIR/test_results.xml"
fi
```

**涉及文件**：`resources/scripts/generate-runner.sh`

---

### #26 `run-ut.sh` 增加并行测试选项

**修复**：增加 `--parallel N` 参数，默认 `--parallel $(nproc)`。

**涉及文件**：`resources/scripts/generate-runner.sh`

---

### #27 增加 eval 提示词集

**修复**：新建 `evals/output-quality.json`，至少包含：
1. "为 /path/to/qt-project 生成单测"（首次搭建）
2. "补全 Calculator 的测试覆盖率"（增量补全）
3. "测试编译失败了，帮我修"（失败修复）
4. "代码改了重新对账"（reconcile）
5. "为已有 tests/ 目录的项目生成单测"（tests/ 兼容）

**涉及文件**：新建 `evals/output-quality.json`

---

### #28 增加 trigger eval 集

**修复**：新建 `evals/trigger-evals.json`，测试 description 触发精准度。

**涉及文件**：新建 `evals/trigger-evals.json`

---

## 修复进度追踪

| 编号 | 优先级 | 状态 | 修复日期 |
|------|--------|------|----------|
| #1 | P0 | ✅ 已修复 | 2025-08-20 |
| #2 | P0 | ✅ 已修复 | 2025-08-20 |
| #3 | P0 | ✅ 已修复 | 2025-08-20 |
| #4 | P0 | ✅ 已修复 | 2025-08-20 |
| #5 | P0 | ✅ 已修复 | 2025-08-20 |
| #6 | P1 | ✅ 已修复 | 2025-08-20 |
| #7 | P1 | ✅ 已修复 | 2025-08-20 |
| #8 | P1 | ✅ 已修复 | 2025-08-20 |
| #9 | P1 | ✅ 已修复 | 2025-08-20 |
| #10 | P1 | ✅ 已修复 | 2025-08-20 |
| #11 | P1 | ✅ 已修复 | 2025-08-20 |
| #12 | P1 | ✅ 已修复 | 2025-08-20 |
| #13 | P2 | ✅ 已修复 | 2025-08-20 |
| #14 | P2 | ✅ 已修复 | 2025-08-20 |
| #15 | P2 | ✅ 已修复 | 2025-08-20 |
| #16 | P2 | ✅ 已修复 | 2025-08-20 |
| #17 | P2 | ✅ 已修复 | 2025-08-20 |
| #18 | P2 | ✅ 已修复 | 2025-08-20 |
| #19 | P2 | ✅ 已修复 | 2025-08-20 |
| #20 | P2 | ✅ 已修复 | 2025-08-20 |
| #21 | P2 | ✅ 已修复 | 2025-08-20 |
| #22 | P2 | ✅ 已修复 | 2025-08-20 |
| #23 | P3 | ✅ 已修复 | 2025-08-20 |
| #24 | P3 | ✅ 已修复 | 2025-08-20 |
| #25 | P3 | ✅ 已修复 | 2025-08-20 |
| #26 | P3 | ✅ 已修复 | 2025-08-20 |
| #27 | P3 | ✅ 已修复 | 2025-08-20 |
| #28 | P3 | ✅ 已修复 | 2025-08-20 |
