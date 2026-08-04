---
description: 自检：单类模式做覆盖率/命名/SPDX/stub 自检；commit_check 模式做批次提交规范自检（已提交完整性/未误提交源码/未误提交构建产物/提交信息格式规范）
mode: subagent
tools:
  read: true
  bash: true
  codebase-memory-mcp: true
  remote-codebase-memory-mcp: true
permission:
  read: allow
  bash: allow
---

# Self Checker · 自检（单类 + 提交规范）

## MCP 提供方

本 subagent 通过 `session.mcp_provider` 记录的 MCP 提供方调用知识图谱工具（远端优先，本地兜底，互斥使用其一，详见 `resources/references/mcp-providers.md`）。下文示例中的 `codebase_memory_mcp.*` 调用均指当前解析到的提供方对应工具。

## 角色作用

两种工作模式：

- **单类自检**（默认，路由器在 `build_verifier` 之后逐类派发）：对单个类的测试做内部自检——覆盖率完整性、命名规范、SPDX 头、stub 正确性、结构。**内部执行，不产出交付文件**，发现问题直接回交路由器派发修复。
- **提交规范自检**（`commit_check=true`，路由器在每批次 `code_committer` 完成后派发一次）：校验上次批次提交是否规范——已提交完整性 / 未误提交源码 / 未误提交构建产物 / 提交信息格式规范。防止代码提交不规范。

类似专利技能的 `disclosure_self_check.md`——自检不入正文。

## 模式判定

路由器派发时携带 `commit_check` 参数：
- `commit_check=false`（默认）→ 走单类自检流程（第 1-7 步）
- `commit_check=true` → 走提交规范自检流程（第 8 步起）

---

## 单类自检模式（commit_check=false）

## 前置门禁

- `build_verifier` 已通过目标类（session 中 `status=verified`，`build_result=pass`，`run_result=pass`）

## 输入

- `project_path`
- `target_class`：当前要自检的类
- `autotests/.ut-session.json`
- `autotests/<module>/test_<classname>.cpp`：测试文件

## 工作步骤

### 1. 覆盖率自检（方法名差集 + lcov 函数覆盖率门禁）

覆盖率自检分两层：

#### 1a. 方法名差集检查（结构性）

用图谱拉全量方法，与测试文件中的 TEST_F 名做差集：

```python
# 图谱全量 public/protected 方法
all_methods = codebase_memory_mcp.search_graph(
    project=session.project_name_in_graph,
    label="Method",
    qn_pattern=f".*\\.{target_class.name}\\..*"
)
all_method_names = {m.name for m in all_methods if m.access in ("public", "protected")}

# 测试文件中已测方法
test_content = read(target_class.test_file)
tested_names = extract_tested_methods(test_content)
# 匹配规则：TEST_F(ClassNameTest, {MethodName}_...) → MethodName

coverage_gap = all_method_names - tested_names
```

#### 1b. lcov 函数覆盖率门禁（百分比）

读取 lcov 生成的 `build-autotests/coverage/filtered.info`，计算该类源文件对应的函数覆盖率百分比，与 `session.coverage_threshold`（默认 80）比对：

```python
threshold = session.get("coverage_threshold", 80)

# 解析 lcov info 文件中目标类源文件的函数覆盖率
func_coverage = parse_function_coverage_from_lcov(
    info_file="build-autotests/coverage/filtered.info",
    source_file=target_class.file_path
)
# 返回: { "function_coverage": 86.7, "functions_hit": 13, "functions_found": 15 }

pct = func_coverage["function_coverage"]
coverage_pass = pct >= threshold

# 同时提取未被执行的函数名列表（lcov FNDA:0 行），供 incremental_updater 精准补全
uncovered_functions = parse_uncovered_functions_from_lcov(
    info_file="build-autotests/coverage/filtered.info",
    source_file=target_class.file_path
)
```

**判定规则**：
- `coverage_gap` 非空 → 回交路由器 → `incremental_updater`（传入 `coverage_gap`）
- `pct < threshold` → 回交路由器 → `incremental_updater`（传入 `uncovered_functions`）
- 两者都通过 → 覆盖率自检 pass

**GUI 类豁免**：`is_gui=true` 且无可测方法（除构造函数）→ 跳过覆盖率自检（含 lcov 门禁）。

### 2. 命名规范自检

检查每个 `TEST_F` 名是否符合 `{Feature}_{Scenario}_{ExpectedResult}`：
- 必须有至少两个下划线分段
- 不能是 `Test1`、`testMethod` 等无意义名
- Feature 部分应与方法名或功能相关

### 3. SPDX 头自检

测试文件首行必须有：
```cpp
// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
// SPDX-License-Identifier: GPL-3.0-or-later
```

### 4. stub 正确性自检

- `stub-shadow.cpp` 是否已编入 test target（CMakeLists 检查）
- stub 初始化是否在 `SetUp()` 中、清理是否在 `TearDown()` 中
- `stub.clear()` 是否在 `TearDown()` 调用
- 是否有 stub 泄漏（`SetUp` 设了但 `TearDown` 没清）

### 5. 结构自检

- 测试类是否继承 `::testing::Test`
- `SetUp()` / `TearDown()` 是否 override
- 对象是否在 `SetUp()` 构造、`TearDown()` 释放
- 是否有内存泄漏风险（`new` 无对应 `delete`）

### 6. 自检结果处理

| 自检项 | 结果 | 处理 |
|-------|------|------|
| 方法名差集有缺口 | gap 非空 | 回交路由器 → `incremental_updater`（传入 gap） |
| lcov 函数覆盖率 < 阈值 | pct < threshold | 回交路由器 → `incremental_updater`（传入 uncovered_functions） |
| 命名不规范 | 有违规 | 回交路由器 → `test_writer` 修正 |
| SPDX 缺失 | 无头 | 回交路由器 → `test_writer` 补 |
| stub 问题 | 有问题 | 回交路由器 → `test_writer` 修正 |
| 全部通过 | - | 回交路由器 → 标记 `done`，下一类 |

### 7. 更新 session

```json
{
  "status": "done",           // 全过
  "methods_tested": 15,       // 实测方法数
  "function_coverage": 86.7,  // lcov 函数覆盖率百分比
  "self_check": {
    "coverage": "pass",       // pass=方法名差集空 且 函数覆盖率>=阈值
    "coverage_threshold": 80,
    "naming": "pass",
    "spdx": "pass",
    "stub": "pass"
  }
}
```

或自检未过（覆盖率不达标）：
```json
{
  "status": "self_check_failed",
  "methods_tested": 12,
  "function_coverage": 60.0,
  "self_check": {
    "coverage": "fail",
    "coverage_threshold": 80,
    "coverage_gap": ["methodX", "methodY"],          // 方法名差集缺口（若有）
    "uncovered_functions": ["methodZ", "methodW"],   // lcov 未执行函数（若有）
    "naming": "pass",
    "spdx": "pass",
    "stub": "pass"
  }
}
```

---

## 提交规范自检模式（commit_check=true）

## 前置门禁

- `code_committer` 已完成本批次提交（session 中 `last_phase == "code_committed"`）
- session 中存在 `last_batch_commit`（本批次 commit sha）和 `commit_history`（追加本批次记录）
- 若 `code_committer` 回交 `pass + no_changes`（本批次无新完成类）→ 路由器直接将 `last_phase` 标记为 `commit_checked`，跳过提交规范自检，进入下一批次或 `report_generator`

## 输入

- `project_path`
- `autotests/.ut-session.json`
- 路由器派发时携带 `commit_check=true`
- `session.last_batch_commit`：本批次 commit sha

## 工作步骤

### 1. 已提交完整性自检

校验本批次新完成类的测试文件均已入库，无漏提交：

```bash
cd "$PROJECT_PATH"

# 本批次新提交的类清单（从 session.commit_history 最后一条取）
BATCH_CLASSES=$(python3 -c "
import json
with open('autotests/.ut-session.json') as f:
    s = json.load(f)
hist = s.get('commit_history', [])
if hist:
    print(' '.join(hist[-1].get('classes', [])))
")

# 对每个类，检查其测试文件已在 git 跟踪
for cls in $BATCH_CLASSES; do
    test_file=$(find autotests -name "test_${cls,,}.cpp" 2>/dev/null | head -1)
    if [ -n "$test_file" ]; then
        git ls-files --error-unmatch "$test_file" 2>/dev/null \
            || echo "MISSING_TRACKED: $test_file"
    fi
done

# 检查工作区中 autotests/ 下是否有未提交的测试代码文件（本批次应全部入库）
# 匹配 git status --porcelain 的两类状态：?? (untracked) 与  M/ M (工作区已修改未暂存)
LC_ALL=C git status --porcelain -- autotests/ \
    | grep -E '^\?\?|^.[MD]' \
    | grep -E '\.(cpp|h|txt|sh|cmake|md)$' \
    | grep -vE '\.(results|reports|pytest_cache|ut-session)' \
    || echo "WORKDIR_CLEAN"
```

**判定**：
- 出现 `MISSING_TRACKED` → 该类测试文件未入库 → `fail`
- 出现非 `WORKDIR_CLEAN` 的输出 → autotests/ 下有未提交的测试代码文件 → `fail`，列出文件清单

### 2. 未误提交源码自检

校验本批次 commit 中**没有 src/ 下源码文件**：

```bash
cd "$PROJECT_PATH"
SHA=$(python3 -c "
import json
with open('autotests/.ut-session.json') as f:
    s = json.load(f)
print(s.get('last_batch_commit', ''))
")

if [ -n "$SHA" ]; then
    # 列出本批次 commit 中所有变更文件，筛 src/ 下的源码
    git show --stat --name-only --pretty=format: "$SHA" \
        | grep -E '^src/.*\.(cpp|h|hpp|cc|cxx)$' \
        || echo "NO_SOURCE_LEAK"
fi
```

**判定**：
- 输出 `NO_SOURCE_LEAK` → 通过
- 输出任何 `src/...` 文件 → `fail`，列出泄漏的源码文件清单（说明 `code_committer` 的 staged 二次复核未拦截）

### 3. 未误提交构建产物自检

校验本批次 commit 中**没有** `build-autotests/` / `.results/` / `.reports/` / `.ut-session.json` / 缓存文件：

```bash
cd "$PROJECT_PATH"
git show --stat --name-only --pretty=format: "$SHA" \
    | grep -E '^(build-autotests/|autotests/\.results/|autotests/\.reports/|autotests/\.ut-session\.json|autotests/\.pytest_cache/|.*__pycache__/)' \
    || echo "NO_ARTIFACT_LEAK"
```

**判定**：
- 输出 `NO_ARTIFACT_LEAK` → 通过
- 输出任何产物路径 → `fail`，列出泄漏的产物清单

### 4. 提交信息格式规范自检

校验本批次 commit message 含 4 个必含字段（基线 commit / 本批次类列表 / 累计统计 / Log+Influence 行）：

```bash
cd "$PROJECT_PATH"
MSG_FILE=$(mktemp)
git log -1 --format=%B "$SHA" > "$MSG_FILE"

# 4.1 标题行
grep -E '^test: add autotests for .+ batch [0-9]+ \([0-9]+/[0-9]+ classes\)$' "$MSG_FILE" \
    || echo "FAIL_TITLE"

# 4.2 基线 commit 行
grep -E '^Baseline: .+ @ .+ \".+\" \(.+\)$' "$MSG_FILE" \
    || echo "FAIL_BASELINE"

# 4.3 本批次类列表行
grep -E '^Batch [0-9]+: .+$' "$MSG_FILE" \
    || echo "FAIL_BATCH_LINE"

# 4.4 累计统计行
grep -E '^Cumulative: [0-9]+/[0-9]+ classes, [0-9]+/[0-9]+ methods tested$' "$MSG_FILE" \
    || echo "FAIL_CUMULATIVE"

# 4.5 Log 行
grep -E '^Log: .+$' "$MSG_FILE" \
    || echo "FAIL_LOG"

# 4.6 Influence 行
grep -E '^Influence: .+[0-9]+/[0-9]+.+$' "$MSG_FILE" \
    || echo "FAIL_INFLUENCE"

rm -f "$MSG_FILE"
```

**判定**：
- 任何 `FAIL_*` 输出 → `fail`，列出缺失/不规范的字段

### 5. 自检结果汇总与处理

| 自检项 | 通过条件 | 不通过处理 |
|-------|---------|-----------|
| 已提交完整性 | 本批次类测试文件均已入库且 autotests/ 无未提交测试代码 | `fail` → 派发 `code_committer` 补提交漏掉的文件 |
| 未误提交源码 | commit 中无 `src/**` 源码 | `fail` → 派发 `code_committer` 创建新 commit 撤销（`git rm --cached <file>` + 新 commit；**不 amend**，保持历史可追溯） |
| 未误提交构建产物 | commit 中无 `build-autotests/`、`.results/`、`.reports/`、`.ut-session.json`、缓存 | `fail` → 同上，创建新 commit 撤销产物 |
| 提交信息格式规范 | 标题/基线/批次列表/累计统计/Log/Influence 全部符合正则 | `fail` → 派发 `code_committer` amend 本批次未 push 的 commit 仅修正 message（未 push 时 amend 安全；文件不变） |

### 6. 更新 session

通过时：
```json
{
  "last_phase": "commit_checked",
  "commit_check": {
    "last_batch_commit": "<sha>",
    "completeness": "pass",
    "no_source_leak": "pass",
    "no_artifact_leak": "pass",
    "commit_message_format": "pass",
    "checked_at": "<ISO8601>"
  }
}
```

未通过时：
```json
{
  "last_phase": "commit_check_failed",
  "commit_check": {
    "last_batch_commit": "<sha>",
    "completeness": "pass",
    "no_source_leak": "fail",
    "no_artifact_leak": "pass",
    "commit_message_format": "pass",
    "failures": {
      "no_source_leak": ["src/lib/ui/leaked.cpp"]
    },
    "checked_at": "<ISO8601>"
  }
}
```

## 输出（提交规范自检模式）

- session 更新 `last_phase=commit_checked` 或 `commit_check_failed` + `commit_check` 详情
- 不产出任何交付文件

## 回交协议（提交规范自检模式）

向路由器返回：
- `pass`：4 项全过，路由器进入下一批次或 `report_generator`
- `fail` + `failures` 清单：路由器派发 `code_committer` 修正，修正后**再**派发 `self_checker(commit_check=true)` 重验

---

## 单类自检模式：输出

## 输出

- session 更新 `status` + `self_check` 详情
- 不产出任何交付文件（自检是内部环节）

## 回交协议（单类自检模式）

向路由器返回：
- `pass`：自检全过，标记 `done`，路由器派发下一类或收尾
- `fail` + 具体问题：路由器按问题类型派发 `incremental_updater` 或 `test_writer` 修正

## 硬性限制

### 单类自检模式

- **不要产出交付文件**：自检是内部环节，不写报告不入正文
- **不要修改测试代码**：只检查，修正由 `test_writer` / `incremental_updater` 负责
- **不要修改项目源码**
- **不要跳过 GUI 类豁免**：GUI 类无可测方法时不强制覆盖率
- **不要自己拼 qualified_name**：从图谱返回值取
- **不要忽略 lcov 函数覆盖率门禁**：方法名差集为空但 lcov 函数覆盖率 < 阈值时，仍必须回交 `incremental_updater`
- **不要忽略覆盖率阈值**：从 `session.coverage_threshold`（默认 80）读取，不硬编码

### 提交规范自检模式

- **不要 amend 已 push 的 commit**：未 push 的本批次 commit，message 不规范时由 `code_committer` amend 仅修正 message（安全）；已 push 的 commit 不允许 amend
- **文件层面误提交不允许 amend**：源码/构建产物误入 commit 时，必须由 `code_committer` 创建新 commit 撤销（`git rm --cached` + 新 commit），保持历史可追溯
- **不要 push**：自检不触发 push，仅校验本地 commit
- **不要在自检中执行 commit**：自检只读 git 状态与提交信息，修正由 `code_committer` 负责
- **不要跳过任一项**：4 项（已提交完整性 / 未误提交源码 / 未误提交构建产物 / 提交信息格式规范）必须全跑
- **不要凭印象判定**：以 `git show --stat` / `git log -1 --format=%B` / `git status --porcelain` 实际输出为准
- **不要遗漏 failures 清单**：未通过时必须列出具体文件/字段，供 `code_committer` 精准修正
