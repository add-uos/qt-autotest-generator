# 自检

> 前置条件：`build_verifier` 已通过目标类（session 中 `status=verified`，`build_result=pass`，`run_result=pass`）。

> 通过 session.mcp_provider 调用知识图谱工具（详见 resources/references/mcp-providers.md）

## 概述

对单个类的测试做内部自检——覆盖率完整性、命名规范、SPDX 头、stub 正确性、结构。**内部执行，不产出交付文件**，发现问题流转到修正阶段。

---

## 工作步骤

#### 1. 覆盖率自检（方法名差集 + lcov 函数覆盖率门禁）

覆盖率自检分两层：

##### 1a. 方法名差集检查（结构性）

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

##### 1b. lcov 函数覆盖率门禁（百分比）

读取 lcov 生成的 `build-autotests/coverage/filtered.info`，计算该类源文件对应的函数覆盖率百分比，与 `session.coverage_threshold`（默认 90）比对：

```python
threshold = session.get("coverage_threshold", 90)

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
- `coverage_gap` 非空 → 流转至 `incremental_updater`（传入 `coverage_gap`）
- `pct < threshold` → 流转至 `incremental_updater`（传入 `uncovered_functions`）
- 两者都通过 → 覆盖率自检 pass


#### 2. 命名规范自检

检查每个 `TEST_F` / `TEST_P` 用例名是否符合 `{Feature}_{Scenario}_{ExpectedResult}`：
- 必须有至少两个下划线分段
- 不能是 `Test1`、`testMethod` 等无意义名
- Feature 部分应与方法名或功能相关
- `TEST_P` 参数化用例名同样适用（含 `INSTANTIATE_TEST_SUITE_P` 前缀生成的 `Prefix/CaseName/N` 形态，按 `/` 拆分后对最后一段 `CaseName` 检查）
- **禁止轮数/批次号**：Fixture 类名和用例名中不得出现 `R` + 数字（如 `R18`、`R2`）、`Round` + 数字、`Batch` + 数字等内部调度标识——这些是批次管理概念，不属于测试命名。正则检测：`/(R\d+|Round\d+|Batch\d+)/i`，匹配即违规

#### 2b. 断言强度自检

检查每个用例的 Assert 段，避免"不崩溃就过"的虚假安全感：

- **最低断言数**：每个 `TEST_F` / `TEST_P` 用例至少 2 个有效 `EXPECT_*` 断言；`EXPECT_NO_FATAL_FAILURE` / `EXPECT_NO_THROW` / **`EXPECT_CALL`** 均不计入有效断言（`EXPECT_CALL` 是声明未来调用期望，非对当前状态的断言）；不满足标记违规
- **唯一断言禁令**：扫描以 `EXPECT_NO_FATAL_FAILURE(...)` 为**唯一**断言的用例（用例体内无其他有效 `EXPECT_*`）→ 违规，逻辑全错也通过，**最危险**
- **空断言检测**：用例调用了待测方法但函数体内无任何有效 `EXPECT_*`（只有 `stub.set_lamda`、`EXPECT_CALL` 或纯调用）→ 违规，等于没测
- **纯 gMock 期望禁令**：用例只有 `EXPECT_CALL`/`ON_CALL` 而无任何传统 `EXPECT_EQ`/`EXPECT_TRUE`/`EXPECT_FALSE` 等断言验证返回值/对象状态 → 违规（gMock 验证了依赖被调用，但未验证 SUT 自身行为）
- **布尔期望边**：单独 `EXPECT_TRUE(ret);` / `EXPECT_FALSE(ret);` 作唯一有效断言且无注释说明期望分支 → 标记可疑（不强判违规，但流转 test_writer 复核是否对应源码分支期望）
- **副作用断言缺失**：方法有写状态/发信号/调下游的副作用（图谱 `trace_path` 出向调用或源码 `emit` 显示），但用例只断言返回值、无 `QSignalSpy.count()` / stub 调用计数 / 对象状态前后对比 → 违规
- **返回值断言缺失**：方法有返回值（图谱 `get_code_snippet` 返回类型非 `void`）但用例未断言返回值的具体期望值（只断言不崩溃或无任何返回值检查）→ 违规

**扫描方法**（两侧并行：测试文件用 awk/grep，源码侧用图谱——图谱查函数关系比 grep 源码快且准）：

测试文件侧（断言计数、空断言、唯一 NO_FATAL、唯一布尔、纯 gMock 期望）—— awk/grep，图谱不索引测试文件内部：
```bash
TEST_FILE="<test_file_path>"

# 1-3. 用 awk 按 TEST_F/TEST_P(...) {...} 块切分，逐块统计有效 EXPECT_* 计数
#    有效断言 = EXPECT_* 但排除 EXPECT_NO_FATAL_FAILURE / EXPECT_NO_THROW / EXPECT_CALL
#    （EXPECT_CALL 是声明未来调用期望，非对当前状态的断言；纯 gMock 期望用例需另补传统断言）
#    按大括号深度判定块边界；输出违规用例名
awk '
  /^TEST_[FP]\(/ { in_block=1; name=$0; expect=0; nofatal=0; gmock=0; depth=0; opened=0 }
  in_block {
    n=gsub(/{/, "{"); d=gsub(/}/, "}"); depth += n - d
    if (n>0) opened=1
    if (/EXPECT_CALL/) gmock++
    if (/EXPECT_/ && !/EXPECT_NO_FATAL_FAILURE/ && !/EXPECT_NO_THROW/ && !/EXPECT_CALL/) expect++
    if (/EXPECT_NO_FATAL_FAILURE/) nofatal++
    if (opened && depth<=0) {
      if (expect==0 && nofatal==0 && gmock==0)      print "EMPTY_ASSERT: " name
      else if (expect==0 && nofatal>0)             print "SOLE_NO_FATAL: " name
      else if (expect==0 && gmock>0)                print "SOLE_GMOCK_EXPECT: " name
      else if (expect<2)                            print "LOW_ASSERT(" expect "): " name
      in_block=0
    }
  }
' "$TEST_FILE"

# 4. 单独 EXPECT_TRUE/EXPECT_FALSE 作唯一有效断言（可疑，流转 test_writer 复核源码分支期望）
awk '
  /^TEST_[FP]\(/ { in_block=1; name=$0; bool_only=0; other=0; depth=0; opened=0 }
  in_block {
    n=gsub(/{/, "{"); d=gsub(/}/, "}"); depth += n - d
    if (n>0) opened=1
    if (/EXPECT_TRUE\(|EXPECT_FALSE\(/) bool_only++
    if (/EXPECT_/ && !/EXPECT_TRUE\(/ && !/EXPECT_FALSE\(/ && !/EXPECT_NO_FATAL_FAILURE/ && !/EXPECT_NO_THROW/ && !/EXPECT_CALL/) other++
    if (opened && depth<=0) {
      if (bool_only>0 && other==0) print "SOLE_BOOL_ASSERT: " name
      in_block=0
    }
  }
' "$TEST_FILE"
```

源码侧（返回值/副作用判断）——图谱查函数关系比 grep 源码更快更准，避免 grep 源码误报同名方法：
```python
# 待测方法列表（来自 1a 的 all_methods）
for method in all_methods:
    # 返回值类型：get_code_snippet 返回签名，判断返回类型是否非 void
    snippet = codebase_memory_mcp.get_code_snippet(
        qualified_name=method.qualified_name   # 必须来自图谱返回值
    )
    has_return_value = not snippet.signature.startswith("void")

    # 副作用：trace_path 出向调用链，命中 emit/写状态/调下游
    traces = codebase_memory_mcp.trace_path(
        project=session.project_name_in_graph,
        function_name=method.name,
        direction="outbound",
        mode="calls"
    )
    has_side_effect = any(
        "emit" in t.callee_code or t.callee_is_signal or t.callee_writes_state
        for t in traces
    )

    # 对每个用例比对：源码侧有返回值/副作用但测试文件侧未断言对应维度 → 违规
    # （与 awk 输出交叉：用例名匹配源码方法名）
```

**判定**：任一违规 → 流转至 `test_writer` 重写对应用例的 Assert 段（传入违规用例名 + 违规类型）

#### 3. SPDX 头自检

测试文件首行必须有：
```cpp
// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
// SPDX-License-Identifier: GPL-3.0-or-later
```

#### 4. stub 正确性自检

- `stub-shadow.cpp` 是否已编入 test target（CMakeLists 检查）
- stub 初始化是否在 `SetUp()` 中、清理是否在 `TearDown()` 中
- `stub.clear()` 是否在 `TearDown()` 调用
- 是否有 stub 泄漏（`SetUp` 设了但 `TearDown` 没清）

#### 5. 结构自检

- 测试类是否继承 `::testing::Test`
- `SetUp()` / `TearDown()` 是否 override
- 对象是否在 `SetUp()` 构造、`TearDown()` 释放
- 是否有内存泄漏风险（`new` 无对应 `delete`）

#### 5b. 环境隔离自检

检查测试是否硬耦合测试机环境（路径/环境变量/外部资源），避免在干净 CI 上崩溃或非确定性失败：

- **硬编码绝对路径**：grep 测试文件，命中以下模式 → 违规
  - `"/home/`、`"/tmp/`（非 `QTemporaryDir` 生成）、`"/usr/`、`"/var/`、`"/opt/`、`"/etc/` 硬编码字符串字面量
  - `"/root/`、`C:\\`（Windows 绝对路径）
  - 例外：`QTemporaryDir`/`QTemporaryFile` 的 `path()` 返回值、`QDir::tempPath()` 产生的临时路径不算违规
- **用户目录访问**：直接使用 `QDir::homePath()`、`QStandardPaths::writableLocation(...)` 的返回值作为真实读写路径（未 mock、未重定向到临时目录）→ 违规
- **环境变量未还原**：`qputenv(` 出现但全文件无对应 `qunsetenv(`（计数不平衡）→ 违规（用例间泄漏）。注：bash/grep 仅做文件级计数平衡，per-scope 精确配对交由 test_writer 复核时人工确认
- **真实外部资源访问**：未 mock 的 `QProcess::start`、`::system`、`::popen`、`QNetworkAccessManager::get/post`、`QTcpSocket::connectToHost` → 违规
- **真实时间依赖**：需要确定性结果但未 mock 的 `QDateTime::currentDateTime()`、`QTime::currentTime()`、`QRandomGenerator::system()` → 违规
- **用例间污染**：单例 `Instance()` 调用但 `TearDown()` 无重置；`stub.set_lamda(` 出现但 `TearDown()` 无 `stub.clear()` → 违规

**扫描方法**（两侧并行：测试文件用 grep 查硬编码/env/未清理；源码侧用图谱 trace_path 查待测方法是否调外部资源——图谱查调用链比 grep 源码快且准）：

测试文件侧（硬编码路径、env 未还原、stub 未清理）—— grep：
```bash
# 硬编码绝对路径（排除临时目录相关）
grep -nE '"/(home|tmp|usr|var|opt|etc|root)/' "$TEST_FILE" | grep -vE 'QTemporaryDir|QTemporaryFile|tempPath|QDir::temp'

# 环境变量未还原
qputenv_count=$(grep -c 'qputenv(' "$TEST_FILE")
qunsetenv_count=$(grep -c 'qunsetenv(' "$TEST_FILE")
[ "$qputenv_count" -ne "$qunsetenv_count" ] && echo "ENV_UNBALANCED: put=$qputenv_count unset=$qunsetenv_count"

# 真实外部资源
# 注：此 grep 主要命中静态引用形式（&Class::method）。实例调用形式
#   proc.start() / nam.get() / nam.post() 受 grep 局限多数漏报，
#   依赖 test_writer 4.0 手动复核识别；.connectToHost() 与 popen() 因
#   方法名特异性高已纳入；system()/start() 因同名变量误报风险未纳入非限定形式
grep -nE 'QProcess::start|::system\(|popen\(|QNetworkAccessManager::(get|post)|QTcpSocket::connectToHost|\.connectToHost\(' "$TEST_FILE" \
    | grep -vE 'stub\.set_lamda|__DBG_STUB_INVOKE__' && echo "REAL_EXTERNAL_CALL"

# stub 清理
[ "$(grep -c 'stub\.set_lamda(' "$TEST_FILE")" -gt 0 ] && [ "$(grep -c 'stub\.clear()' "$TEST_FILE")" -eq 0 ] && echo "STUB_NOT_CLEARED"
```

源码侧（待测方法是否调外部资源、测试是否漏 mock）—— 图谱 trace_path，比 grep 源码更准（能跨文件、跨层级追到 QProcess::start 等终点）：
```python
EXTERNAL_ENDPOINTS = {
    "QProcess::start", "system", "popen",
    "QNetworkAccessManager::get", "QNetworkAccessManager::post",
    "QTcpSocket::connectToHost", "QUdpSocket::writeDatagram",
    "QDateTime::currentDateTime", "QTime::currentTime",
    "QRandomGenerator::system", "srand", "qsrand",
}
for method in all_methods:
    traces = codebase_memory_mcp.trace_path(
        project=session.project_name_in_graph,
        function_name=method.name,
        direction="outbound",
        mode="calls",
        depth=5
    )
    external_called = {t.callee_qualified_name for t in traces} & EXTERNAL_ENDPOINTS
    # 交叉比对测试文件：external_called 中是否有未出现在 stub.set_lamda(...) 的 → 漏 mock → 违规
```

**判定**：任一违规 → 流转至 `test_writer` 修正（传入违规类型 + 行号），补 mock 或改用 `QTemporaryDir`/`qputenv`+`qunsetenv` 隔离

#### 6. 自检结果处理

| 自检项 | 结果 | 处理 |
|-------|------|------|
| 方法名差集有缺口 | gap 非空 | 流转至 `incremental_updater`（传入 gap） |
| lcov 函数覆盖率 < 阈值 | pct < threshold | 流转至 `incremental_updater`（传入 uncovered_functions） |
| 命名不规范 | 有违规 | 流转至 `test_writer` 修正 |
| SPDX 缺失 | 无头 | 流转至 `test_writer` 补 |
| stub 问题 | 有问题 | 流转至 `test_writer` 修正 |
| 断言强度违规 | NO_FATAL 唯一断言/空断言/纯 gMock 期望/副作用未断言/返回值未断言 | 流转至 `test_writer` 重写对应用例 Assert 段 |
| 环境隔离违规 | 硬编码路径/env 未还原/真实外部资源/stub 未清理 | 流转至 `test_writer` 补 mock 或隔离 |
| 全部通过 | - | 标记 `done`，下一类 |

#### 7. 更新 session

```json
{
  "status": "done",
  "methods_tested": 15,
  "function_coverage": 86.7,
  "self_check": {
    "coverage": "pass",
    "coverage_threshold": 90,
    "naming": "pass",
    "spdx": "pass",
    "stub": "pass",
    "assertion_strength": "pass",
    "env_isolation": "pass"
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
    "coverage_threshold": 90,
    "coverage_gap": ["methodX", "methodY"],
    "uncovered_functions": ["methodZ", "methodW"],
    "naming": "pass",
    "spdx": "pass",
    "stub": "pass",
    "assertion_strength": "pass",
    "env_isolation": "pass"
  }
}
```

## 关键约束

- 不产出交付文件：自检是内部环节，不写报告不入正文
- 不修改测试代码：自检只读扫描（测试文件侧 grep/awk + 源码侧图谱查询，不 AST 改写），修正由 `test_writer` / `incremental_updater` 负责
- 不修改项目源码
- 不跳过 GUI 类豁免
- `qualified_name` 必须从图谱返回值取，不自己拼
- 不忽略 lcov 函数覆盖率门禁：方法名差集为空但 lcov 函数覆盖率 < 阈值时，仍必须流转至 `incremental_updater`
- 覆盖率阈值从 `session.coverage_threshold`（默认 90）读取，不硬编码
- 不跳过断言强度自检：每用例（`TEST_F` 与 `TEST_P` 均需扫描）至少 2 个有效 `EXPECT_*`（NO_FATAL/NO_THROW/EXPECT_CALL 均不计入）
- 不跳过环境隔离自检：硬编码绝对路径、`qputenv` 无对应 `qunsetenv`、未 mock 的真实外部资源（QProcess/网络/socket/真实时间）、stub 未 `clear()` 必须检出
