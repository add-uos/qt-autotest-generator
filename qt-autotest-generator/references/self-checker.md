# 自检

> 前置条件：`build-verifier` 已通过目标类（session 中 `status=verified`，`build_result=pass`，`run_result=pass`）。

> 通过 mcp_provider 调用知识图谱工具（详见 references/mcp-providers.md）

## 概述

对单个类的测试做内部自检——覆盖率完整性、命名规范、SPDX 头、stub 正确性、结构。**内部执行，不产出交付文件**，发现问题流转到修正阶段。

### 0. 迭代次数检查（Iron Law #10）

在执行任何自检之前，先检查 `iteration_count[classname]`：

```python
MAX_ITERATIONS = 3
iter_count = iteration_count.get(classname, 1)

if iter_count >= MAX_ITERATIONS:
    # 达到全局闭环迭代上限，强制标红跳过
    class_status[classname].update({
        "status": "failed",
        "failure_reason": "max_iterations_exceeded",
    })
    return  # 跳过该类，不进入修正流转
```

若未达上限，正常执行后续自检步骤。

---

## 工作步骤

> **结构性检查首选方式**：跑 `scripts/self-check-structural.py`，固化 §2/§2b/§3/§4/§5/§5b
> 的纯文件正则检查（spdx/naming/assertion/aaa/structure/stub/env 七类），输出违规清单。
> 模型只看清单决定改什么，不回读自己的文件做正则。方法名差集的 tested_names 提取
> 已内置（`extract_tested_names`），图谱侧拉全量方法仍需 MCP（§1a 下文保留）。
>
> ```bash
> python3 ${SKILL_DIR}/scripts/self-check-structural.py \
>     --file ${test_dir}/${module}/test_${classname}.cpp [--json] [-o report.json]
> # 退出码 0=无 error / 1=有 error（warnings 不阻塞）
> # stdout 摘要 + 违规清单
> ```
>
> 新增检查项：
> - **aaa**：每个 TEST_F 必须包含 `// Arrange` / `// Act` / `// Assert` 三段注释（MISSING_AAA=error）；
>   空段标 EMPTY_AAA warning
> - **assertion** 增加 `BELOW_MIN_CASES`：用例计数声明表中 actual < min 报 error；
>   无声明表标 MISSING_DECL warning
>
> 语义检查（断言名实相符、期望值正确性）仍留模型，不固化。
>
> **与 test-code-gen 生成后自检门的关系**：test-code-gen 阶段已运行 self-check-structural.py 的
> 生成期子集（AAA/断言强度/用例计数声明），本阶段运行全量检查（含 SPDX/命名/stub 清理/env 隔离），
> 覆盖生成期子集并补充其余检查项，不重复但会重跑同一脚本（结果应与生成期一致，如有差异说明
> 生成期后有变更需关注）。

#### 1. 覆盖率自检（方法名差集 + lcov 函数覆盖率门禁）

覆盖率自检分两层：

##### 1a. 方法名差集检查（结构性）

用图谱拉全量方法，与测试文件中的 TEST_F 名做差集：

```python
# 图谱全量 public/protected 方法
all_methods = codebase_memory_mcp.search_graph(
    project=project_name_in_graph,
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

复用 `build-verifier` 第 7c 步产出的分级覆盖率快照（`${build_dir}/coverage/${classname}_by_level.json`），避免重复解析 lcov。快照不存在或 stale 时重新调用脚本：

- **有 inventory 时**：按方法分级差异化门禁（阈值取自 inventory 的 `gate_thresholds`，详见 `references/coverage-tiers.md`）
- **无 inventory**：技能不可运行，先执行 Mode 1 生成 inventory

```python
test_dir = test_dir
inventory_path = f"{test_dir}/.ut-inventory.json"
if not os.path.exists(inventory_path):
    raise FatalError("无 .ut-inventory.json，先执行 Mode 1")

# 复用 build-verifier 7c 的快照；不存在则重新生成
snapshot_path = f"build-{test_dir}/coverage/{target_class.name}_by_level.json"
if not os.path.exists(snapshot_path):
    subprocess.run([
        "python3", f"{SKILL_DIR}/scripts/coverage-report.py",
        "--level-only",
        "-i", inventory_path,
        "-c", f"build-{test_dir}/coverage/filtered.info",
        "--class", target_class.name, "--json", "-o", snapshot_path,
    ], check=True)

snapshot = json.load(open(snapshot_path))
# snapshot["by_level"][lv]["pass"] 已按 gate_thresholds 判定：函数覆盖率达 function 阈值 且 行覆盖率达 line 阈值
# snapshot["uncovered_functions"] = FNDA:0 方法名列表，供 incremental_updater 精准补全
gate_failed_levels = [lv for lv in ("high", "mid", "low") if not snapshot["by_level"][lv]["pass"]]
uncovered_functions = snapshot["uncovered_functions"]
```

> 脚本 `scripts/coverage-report.py` 解析 FN/FNDA/DA + `c++filt` demangle 关联 inventory 分级，产出函数级+行级覆盖率。门禁阈值取自 inventory 的 `gate_thresholds`，不在 self_checker 内 hardcode。

**判定规则**：
- `coverage_gap` 非空 → 流转至 `incremental-updater`（传入 `coverage_gap`）
- 有 inventory 时：high/mid 级方法的函数覆盖率 < 100% 或行覆盖率 < 阈值 → 流转至 `incremental-updater`
- 无 inventory 时：`pct < threshold` → 流转至 `incremental-updater`（传入 `uncovered_functions`）
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
- **字面量布尔断言禁令**：用例唯一有效断言为 `EXPECT_TRUE(true)` / `ASSERT_FALSE(false)` 等字面量布尔 → 违规（critical，与空断言同责：占位断言未验证任何行为）；混有真实断言时不报，非字面量布尔归下一条“布尔期望边”只作可疑警告
- **布尔期望边**：单独 `EXPECT_TRUE(ret);` / `EXPECT_FALSE(ret);` 作唯一有效断言且无注释说明期望分支 → 标记可疑（不强判违规，但流转 test-writer.md 复核是否对应源码分支期望）
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

# 4. 单独 EXPECT_TRUE/EXPECT_FALSE 作唯一有效断言（可疑，流转 test-writer.md 复核源码分支期望）
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

# 5. 字面量布尔断言（EXPECT_TRUE(true) 等）——字面量不计入 other；仅字面量 → TRIVIAL_ASSERT
awk '
  /^TEST_[FP]\(/ { in_block=1; name=$0; trivial=0; other=0; depth=0; opened=0 }
  in_block {
    n=gsub(/{/, "{"); d=gsub(/}/, "}"); depth += n - d
    if (n>0) opened=1
    if (/\b(EXPECT|ASSERT)_(TRUE|FALSE)\s*\(\s*(true|false)\s*\)/) trivial++
    else if (/\b(EXPECT|ASSERT)_/ && !/EXPECT_NO_FATAL_FAILURE/ && !/EXPECT_NO_THROW/ && !/EXPECT_CALL/) other++
    if (opened && depth<=0) {
      if (trivial>0 && other==0) print "TRIVIAL_ASSERT: " name
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
        project=project_name_in_graph,
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

**判定**：任一违规 → 流转至 `test-writer.md` 重写对应用例的 Assert 段（传入违规用例名 + 违规类型）

#### 2c. 分支清单交叉验证（白盒质量门禁，MCP 反查）

§4.1 要求测试文件顶部注释声明「分支清单 → 用例映射」。本步**用 MCP `get_code_snippet` 反查真实源码分支**，校验声明是否对得上实现——这是白盒覆盖质量的硬门禁，不靠 agent 自觉读 test-types.md。

> **首选方式**：跑 `scripts/mcp-scan.py extract-branches`，脚本固化 §2c 全流程——从 inventory 取类方法、调 MCP `get_code_snippet` 拉方法体、正则数真实分支（if/else if/switch case/for/while/throw/early return/三元）、解析测试文件声明的 `// B1:` 分支清单、做差集输出 `MISSING_BRANCH_LIST` / `BRANCH_NOT_MAPPED` 违规清单。模型只消费违规清单决定补什么用例，不自己回读源码数分支。
>
> ```bash
> python3 ${SKILL_DIR}/scripts/mcp-scan.py extract-branches \
>   --project <project_name_in_graph> \
>   --test-file ${test_dir}/${module}/test_${classname}.cpp \
>   --inventory ${test_dir}/.ut-inventory.json \
>   [--class ${classname}] [--json] [-o ${test_dir}/.reports/branch-check.json]
> # 退出码 0=无 error / 1=有 error（warning 不阻塞）
> # stdout 摘要 + 违规清单
> ```
>
> 脚本核心逻辑（`extract_branches` / `parse_declared_branches` / `cross_check_branches`）为纯函数，下方伪代码仅作原理说明（脚本不可用时兑底）。

```python
# 对每个 testable 方法（§1a 的 all_methods），用 MCP 取真实方法体
for method in all_methods:
    snippet = mcp.get_code_snippet(qualified_name=method.qualified_name)  # qn 必须来自图谱返回
    body = snippet.body  # 方法体全文（不是 read 源文件）

    # 提取真实分支：if / else if / switch case + default / for / while / throw / early return / 三元
    real = extract_branches(body)          # 返回 dict，real["total"] 为真实分支总数

    # 解析测试文件顶部声明的分支清单（// B1: cond → outcome 格式，见 test-code-gen §4.1）
    declared = parse_declared_branches(test_content, method.name)  # 返回 int

    is_complex = method.complexity >= 10 or real["total"] >= 3
    if is_complex and declared == 0:
        violations.append(("branch", "error", "MISSING_BRANCH_LIST", method.name))
    elif declared > 0 and declared < real["total"]:
        violations.append(("branch", "error",
            f"BRANCH_NOT_MAPPED declared={declared} actual={real['total']}", method.name))
    # 简单方法 declared==0 → 不判违规（§4.1 允许简单方法省略清单）
```

**判定**：
- `MISSING_BRANCH_LIST`（复杂方法无分支清单注释）→ 流转 `test-writer.md`，用 `get_code_snippet` 补分支清单 + 对应用例
- `BRANCH_NOT_MAPPED`（声明分支数 < 真实分支数）→ 流转 `test-writer.md`，按漏掉的分支补用例
- 简单方法（complexity<10 且分支<3）无分支清单不判违规（§4.1 允许简单方法省略）

> 本步是「语义质量」里**唯一可机器校验**的项：分支清单是注释，但真实分支来自 MCP 源码，两者做差集即可判定漏测。等价类/边界值的语义正确性仍留模型，但分支覆盖这条硬指标不再靠自觉。

#### 3. SPDX 头自检

测试文件首行必须有：
```cpp
// SPDX-FileCopyrightText: {SPDX_YEAR} UnionTech Software Technology Co., Ltd.
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
- **环境变量未还原**：`qputenv(` 出现但全文件无对应 `qunsetenv(`（计数不平衡）→ 违规（用例间泄漏）。注：bash/grep 仅做文件级计数平衡，per-scope 精确配对交由 test-writer.md 复核时人工确认
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
#   依赖 test-writer.md §4.0 手动复核识别；.connectToHost() 与 popen() 因
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
        project=project_name_in_graph,
        function_name=method.name,
        direction="outbound",
        mode="calls",
        depth=5
    )
    external_called = {t.callee_qualified_name for t in traces} & EXTERNAL_ENDPOINTS
    # 交叉比对测试文件：external_called 中是否有未出现在 stub.set_lamda(...) 的 → 漏 mock → 违规
```

**判定**：任一违规 → 流转至 `test-writer.md` 修正（传入违规类型 + 行号），补 mock 或改用 `QTemporaryDir`/`qputenv`+`qunsetenv` 隔离

#### 6. 自检结果处理

| 自检项 | 结果 | 处理 |
|-------|------|------|
| 方法名差集有缺口 | gap 非空 | 流转至 `incremental-updater`（传入 gap） |
| lcov 函数覆盖率 < 阈值 | pct < threshold | 流转至 `incremental-updater`（传入 uncovered_functions） |
| 弱覆盖（已测但用例太少） | high 级方法 `usecase_count≤1` 且 `score≥3` | 流转至 `incremental-updater`；先用 `utq -P ${test_dir} weak --json` 定位弱覆盖函数清单 |
| 命名不规范 | 有违规 | 流转至 `test-writer.md` 修正 |
| SPDX 缺失 | 无头 | 流转至 `test-writer.md` 补 |
| stub 问题 | 有问题 | 流转至 `test-writer.md` 修正 |
| 断言强度违规 | NO_FATAL 唯一断言/空断言/纯 gMock 期望/副作用未断言/返回值未断言 | 流转至 `test-writer.md` 重写对应用例 Assert 段 |
| 分支清单违规 | MISSING_BRANCH_LIST / BRANCH_NOT_MAPPED（声明分支 < 真实分支） | 流转至 `test-writer.md`，用 `get_code_snippet` 补分支清单 + 补用例 |
| AAA 结构违规 | 缺少 // Arrange / // Act / // Assert 注释（MISSING_AAA） | 流转至 `test-writer.md` 补 AAA 注释 |
| 用例数低于下限 | 声明表中 actual < min（BELOW_MIN_CASES） | 流转至 `test-writer.md` 补用例 |
| 环境隔离违规 | 硬编码路径/env 未还原/真实外部资源/stub 未清理 | 流转至 `test-writer.md` 补 mock 或隔离 |
| 全部通过 | - | 标记 `done`，下一类 |

#### 7. 更新 session

```json
{
  "status": "done",
  "methods_tested": 15,
  "function_coverage": 86.7,
  "self_check": {
    "coverage": "pass",
    "coverage_mode": "tiered",
    "inventory_path": "autotests/.ut-inventory.json",
    "naming": "pass",
    "spdx": "pass",
    "stub": "pass",
    "assertion_strength": "pass",
    "branch_list": "pass",
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
    "coverage_gap": ["methodX", "methodY"],
    "uncovered_functions": ["methodZ", "methodW"],
    "naming": "pass",
    "spdx": "pass",
    "stub": "pass",
    "assertion_strength": "pass",
    "aaa": "pass",
    "usecase_decl": "pass",
    "branch_list": "pass",
    "env_isolation": "pass"
  }
}
```

## 关键约束

- 不产出交付文件：自检是内部环节，不写报告不入正文
- 不修改测试代码：自检只读扫描（测试文件侧 grep/awk + 源码侧图谱查询，不 AST 改写），修正由 `test-writer.md` / `incremental-updater` 负责
- 不修改项目源码
- 不跳过 GUI 类豁免
- `qualified_name` 必须从图谱返回值取，不自己拼
- 不忽略覆盖率门禁：方法名差集为空但覆盖率 < 阈值时，仍必须流转至 `incremental-updater`
- 覆盖率门禁规则：必须有 `.ut-inventory.json`，按方法分级（详见 `references/coverage-tiers.md`）；无 inventory 时技能不可运行
- 不跳过断言强度自检：每用例（`TEST_F` 与 `TEST_P` 均需扫描）至少 2 个有效 `EXPECT_*`（NO_FATAL/NO_THROW/EXPECT_CALL 均不计入）
- 不跳过环境隔离自检：硬编码绝对路径、`qputenv` 无对应 `qunsetenv`、未 mock 的真实外部资源（QProcess/网络/socket/真实时间）、stub 未 `clear()` 必须检出
- 不跳过分支清单交叉验证：复杂方法必须有分支清单注释，且声明分支数 ≥ `get_code_snippet` 提取的真实分支数；漏报即流转 `test-writer.md` 补用例（白盒质量硬门禁）
