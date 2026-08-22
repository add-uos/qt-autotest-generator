# 测试代码生成

> 前置条件：`.ut-inventory.json` 存在（方法分级 + `factors`），`dependency_tracer` 已完成目标类追踪（有 `is_gui` + `stub_list` + `source_dirs`（内存变量）），图谱 ready。

> 通过 mcp_provider 调用知识图谱工具（详见 references/mcp-providers.md）

## 概述

根据 inventory 的方法分级（level/factors）和依赖追踪的 stub 清单，读模板生成单个类的 Google Test 测试代码。此阶段只生成测试代码，不编译不运行（编译验证由后续阶段负责）。

## 测试方法论引用（必读）

生成用例前**必须先读** `${SKILL_DIR}/references/test-types.md`，按其方法论建模输入空间与组织用例。不读 test-types.md 直接生成 → 分支清单/等价类建模缺失，**self_checker §2c 会用 `get_code_snippet` 反查真实源码分支拦下**（MISSING_BRANCH_LIST / BRANCH_NOT_MAPPED），过不了自检。本节列出**必须遵守的最小清单**，详细方法以 test-types.md 为准。

### 最小清单（在测试文件顶部注释中输出完成情况，未完成不得提交编译验证）

| # | 检查项 | 出处 |
|---|---|---|
| 1 | 每个公开方法 ≥ 1 用例 | test_writer §4 |
| 2 | 每个输入维度按等价类划分，每类 ≥ 1 用例 | §1 |
| 3 | 每个等价类的边界值显式覆盖 | §2 |
| 4 | 同质多组输入用 `TEST_P` 参数化（≥ 3 组同断言逻辑强制参数化） | §3.2 |
| 5 | 分支清单已列出并映射到用例名（注释形式） | §4.1 |
| 6 | 每条 `if/switch/throw/early-return` 分支有触发用例 | §4.2 |
| 7 | 异常路径用 `EXPECT_THROW(stmt, ExcType)` 精确匹配类型 + message | §5 |
| 8 | 负面场景（空/越界/类型不符/资源不足）有专门用例 | §6.2 |
| 9 | 负面用例验证强异常安全（状态未损坏） | §6.3 |
| 10 | 项目内接口类用 gMock，Qt 类/全局函数/无虚函数类用 stub_ext | §7.5 |

> **#5/#6 是硬门禁**：分支清单必须基于 `get_code_snippet` 取的真实源码分支填写（不凭签名编造），self_checker §2c 会做差集校验；声明分支 < 真实分支即 `BRANCH_NOT_MAPPED` 违规，流转回此处补用例。

**必须读的关键章节**：
- §1 等价类划分、§2 边界值分析 → 决定用例输入空间
- §3.2 `TEST_P` 参数化 → 决定用例组织方式
- §4.1 分支清单 + 用例映射 → 注释必须落到测试文件顶部
- §5.2 异常精确匹配 → 决定异常路径断言形态
- §6.3 强异常安全 → 负面用例必检
- §7.2 / §7.3 / §7.5 stub_ext vs gMock 选择 → 决定 mock 工具

**反模式速查**：见 test-types.md §9（A1-A12），出现即视为用例无效，需重写。

## 工作步骤

### 1. 读取函数源码（含签名）

从图谱获取每个待测方法的完整源码：

```python
snippet = codebase_memory_mcp.get_code_snippet(
    qualified_name=method.qualified_name   # 必须来自 search_graph 返回值，不自己拼
)
```

### 2. 读模板

```python
# 测试文件骨架模板
read("${SKILL_DIR}/templates/google-test-base.cpp")

# stub 模式参考
read("${SKILL_DIR}/templates/stub-patterns.cpp")

# CMake 子模块模板
read("${SKILL_DIR}/templates/cmake-submodule.txt")
```

### 3. 生成测试文件

文件路径：`{test_dir}/<module>/test_<classname>.cpp`（`test_dir` 从内存变量读取，模块名取自 source_dirs 的最后一段）

替换模板占位符：
- `{header_file}` → 目标类头文件路径（相对项目根）
- `{ClassName}` → 类名
- `{TestCases}` → 生成的测试用例。**每个 TEST_F 必须包含 `// Arrange` / `// Act` / `// Assert` 三段注释**（self-check-structural 会验证，缺少报 MISSING_AAA 违规）。模板文件中已有强制注释说明。
- `{SPDX_YEAR}` → 当前年份（如 2025），由 test_writer 在生成时填入。**示例中不得硬编码年份，一律用 `{SPDX_YEAR}` 占位**

**占位符说明**：
- `{BranchList}` → 分支清单 + 用例映射注释块（test-types §4.1 要求，复杂方法必须落，简单方法可省）；插入位置：`{Namespace}` 之前、`#include` 之后
- `{Namespace}` / `{NamespaceEnd}` → 命名空间开闭（若有）。若类在命名空间 `namespace X { namespace Y { ... } }` 内，则 `{Namespace}` = `namespace X { namespace Y {`，`{NamespaceEnd}` = `}} // namespace X::Y`；无命名空间则两者均为空
- `{SetUpTestSuite}` → GUI 类填 QCoreApplication 初始化代码；非 GUI 类删除 SetUpTestSuite/TearDownTestSuite 整个函数
- `{SetUpObject}` → 非 GUI 类填 `obj = new {ClassName}()`；GUI 类填空或 helper 构造
- `{TearDownObject}` → 非 GUI 类填 `delete obj`；GUI 类填空
- `{SetUpStubs}` → dependency_tracer 产出的 stub 初始化代码

**同名类消歧**：若项目内存在不同路径下的同名类（如 `A/Manager.h` 和 `B/Manager.h`），测试文件路径按模块路径拆分，不合并：
- 测试文件路径 = `{test_dir}/{module_path_flattened}/test_{classname}.cpp`
- 例：`{test_dir}/a/test_manager.cpp` 和 `{test_dir}/b/test_manager.cpp`
- CMake 子目录按模块路径拆分，每个路径独立 `add_subdirectory`
- 依赖追踪（`references/dependency-tracer.md`）需在 `source_dirs` 中区分同名类的模块路径

### 4. 生成测试用例

每个待测方法按其 inventory `level`/`factors` 推导用例数下限：

| factors / level 特征 | 最少用例数 | 用例类型 | 对应 test-types.md 章节 |
|---------------------|-----------|---------|------------------------|
| high 或 `complexity_ge_20` | 3 | 正常 + 边界 + 异常 | §1 有效等价类 + §2 边界值 + §5 异常路径 + §6 负面测试 |
| `complexity_ge_10`（mid 档） | 2 | 正常 + 边界或异常 | §2 边界值 + §5 异常路径 |
| `loop_ge_1` / 循环类因子 | +1 | 循环边界（空集合、单元素、超大集合） | §2.1 循环计数 + §4.2 for 循环分支 |
| mid | 1–2 | 正常路径 + 主要分支 | §1 有效等价类 |
| low | 1 | 正常路径 | §1 有效等价类 |

> 分支覆盖优先于用例数量（见 4.0 第 4 条），上表是下限不是上限。

#### 4.0 前置：mock 深度分析（避免漏测与环境耦合）

`stub_list` 是依赖追踪给的起点，**不能盲信**。生成用例前必须对每个待测方法做以下分析，分析结论落入测试文件顶部注释或 `SetUp()` 实现：

1. **用 MCP 取方法体与调用链（禁止 read 源文件）**：被测方法的实现、签名、出向调用、分支、循环、异常路径**全部从图谱拿**，不 `read` 项目源码文件：
   ```python
   # 方法体（含签名、返回类型、函数体全文）—— 不只看签名，要看实现
   snippet = mcp.get_code_snippet(qualified_name=method.qualified_name)  # qn 必须来自 search_graph 返回

   # 出向调用链（识别分支/循环/异常/emit/隐式依赖）—— depth=3 覆盖传递依赖
   callees = mcp.trace_path(
       project=project_name_in_graph,
       function_name=method.qualified_name,
       direction="outbound", depth=3, mode="calls"
   )
   ```
   复杂方法（complexity≥10 或 lines≥50）**必须**先在测试文件顶部用注释列出「分支清单 → 用例映射」（来源标注 `get_code_snippet`），确保每条分支至少一个用例。**分支清单不得凭记忆/凭签名编造**——自检会用 `get_code_snippet` 反查真实分支做差集（见 self-checker §2c）。
2. **用 trace_path 出向链识别隐式依赖（不 grep 源码）**：`stub_list` 是依赖追踪的起点但常漏；隐式依赖**从 §1 的 `trace_path` 返回的 callees 里命中以下终点全限定名**判断，不 `read`/`grep` 源文件。命中即按右栏决策：
   | 依赖类别 | trace_path 命中的 callee（全限定名片段） | 决策 |
   |---|---|---|
   | 路径访问 | `QStandardPaths::writableLocation`、`QDir::currentPath`、`QCoreApplication::applicationDirPath`、`QDir::home` | mock 返回临时目录，或 `SetUp()` 用 `QTemporaryDir` 注入路径 |
   | 环境变量 | `getenv`、`qEnvironmentVariable`、`qgetenv`、`QProcessEnvironment::systemEnvironment` | `SetUp()` `qputenv` / `TearDown()` `qunsetenv`，或 mock |
   | 文件系统 | `QFile`、`QDir`、`QFileInfo`、`::open`/`::access`/`::stat`/`::mkdir`/`::unlink`、`fopen` | mock 访问函数或 `QTemporaryDir` 隔离，禁止真实读写 |
   | 子进程 | `QProcess::start`、`system`、`popen` | 必须 mock，禁止真实启进程 |
   | 网络 | `QNetworkAccessManager`、`QTcpSocket`/`QUdpSocket`、`QLocalSocket`、`gethostbyname` | 必须 mock，禁止真实网络 |
   | 时间/随机 | `QDateTime::currentDateTime`、`QTime::currentTime`、`QElapsedTimer`、`srand`/`qsrand`、`QRandomGenerator::system` | 需确定性结果时 mock |
   | 单例/全局状态 | 进程内单例、静态成员、`qApp` | `TearDown` 重置，避免用例间污染 |
   > 硬编码路径**字符串字面量**（`/usr/...`、`/tmp/...`）图谱 trace_path 不一定命中——此时**仍不 read 整个源文件**，用 `get_code_snippet` 取方法体后在方法体文本里查字符串即可（`get_code_snippet` 是 MCP 提供的结构化源码片段，不是 `read` 整文件）。
3. **对每个识别出的依赖决定 mock 策略**：能 mock 的走 `stub.set_lamda`；不能直接 mock 的（如硬编码路径字符串）在 `SetUp()` 用 `QTemporaryDir`/`QTemporaryFile` 构造临时环境并把路径注入被测对象；环境变量在 `SetUp()` 用 `qputenv` 设置、`TearDown()` 用 `qunsetenv` 还原。
4. **分支覆盖优先于用例数量**：基于 §1 `get_code_snippet` 取到的真实源码分支生成用例，按 level/factors 推导的用例数下限不是上限。嵌套 `if`/`switch`/循环边界/异常路径要单独生成用例，**哪怕超出下限也必须补**，避免漏测。
5. **private 方法的间接覆盖**：private 方法不直接 `TEST_F`，但**必须通过调用它的 public/protected 方法覆盖其分支和边界条件**，不得因"private 不可直接测"就跳过其内部逻辑分支。若某 public 方法全部逻辑就是调一个 private，则该 public 的用例必须覆盖 private 的所有分支。

#### 4.1 用例结构

**AAA 模式**（每个用例**强制**包含，self-check-structural 验证——缺少 `// Arrange` / `// Act` / `// Assert` 任一段注释即报 `MISSING_AAA` 违规）：
```cpp
// Arrange
<准备前置条件、stub、对象构造>

// Act
<调用待测方法>

// Assert
<多维度验证：返回值精确值 + 对象状态/副作用/信号/调用链，禁止只验证"不崩溃">
```

**Assert 验证维度**（每个用例至少覆盖 2 个维度，其中"返回值精确值"或"对象状态变更"必选 1 个）：

1. **返回值精确值**：`EXPECT_EQ(ret, expected_exact_value)`，不要只写 `EXPECT_TRUE(ret)`。布尔返回必须明确断言期望边（`EXPECT_TRUE` 或 `EXPECT_FALSE` 对应源码分支期望），不得用 `EXPECT_NO_FATAL_FAILURE` 充数。
2. **对象状态变更**：调用前后对比成员状态——getter 返回值、计数器增减、内部容器内容、`QVariant` 字段值、配置项前后值。
3. **副作用 / stub 调用验证**：在 stub lambda 内 `EXPECT_EQ(arg, expected)` 验证传入参数；用调用计数器（`int call_count = 0;` 在 stub 内 `++call_count`）验证调用次数与顺序；验证关键依赖被调用 / 未被调用。
4. **信号发射**：`QSignalSpy spy(obj, &Class::signalName);` 触发后 `EXPECT_EQ(spy.count(), n)` 并验证信号参数 `spy.at(0).at(k).toXxx()`。
5. **异常 / 错误路径**：异常分支验证抛出 / 错误码 / 错误信息字符串；正常路径验证不抛（`EXPECT_NO_THROW`）。
6. **出向调用链**：关键依赖被调用 / 未调用的验证（stub 调用计数 + 参数断言），确保方法实际触发了预期的下游行为。

**禁止的反模式**（出现即视为用例无效，需重写）：
- ❌ `EXPECT_NO_FATAL_FAILURE(obj->method());` 作为**唯一断言**——只验证"不崩溃"，逻辑全错也通过，**最危险**
- ❌ 单独 `EXPECT_TRUE(ret);` / `EXPECT_FALSE(ret);` 不写期望值注释，看不出期望是哪边
- ❌ 调用方法后无任何 `EXPECT_*` 断言——等于没测
- ❌ 只断言返回值、忽略对象状态和副作用——漏掉行为变更
- ❌ 用例名带 `ReturnsTrue` 但只 `EXPECT_NO_FATAL_FAILURE` 不实际断言返回值——名实不符
- ✅ `EXPECT_EQ(ret, 42);  // 期望返回 42` + `EXPECT_EQ(obj->count(), 3);` + `EXPECT_EQ(spy.count(), 1);`——多维度交叉验证

**用例自检**：生成每个用例后，**逐用例回读** Assert 段，确认——(a) 至少 2 个 `EXPECT_*` 断言；(b) 至少 1 个是精确值/状态断言而非纯布尔；(c) 若方法有返回值，必须断言返回值的具体期望值；(d) 若方法有副作用（写状态/发信号/调下游），必须断言副作用发生。不满足则补全或重写。

**生成后自检门（强制）**：每个类全部用例生成完毕后，**立即执行以下 3 步才可进入编译验证**，不得跳过：

1. **填入用例计数声明**：在测试文件顶部注释的「用例计数声明」表格中填入 actual 列。对每个方法：
   - 从 level/factors 查 §4 最少用例数
   - 统计该方法的实际用例数
   - `actual < min` → 必须补用例直到满足下限
2. **勾选最小清单**：在测试文件顶部注释的 10 项最小清单中逐项勾选 `[x]`。任一项无法勾选 → 回到生成步骤补齐
3. **运行 self-check-structural**：`python3 ${SKILL_DIR}/scripts/self-check-structural.py --file <test_file>` 确认无 MISSING_AAA / LOW_ASSERT / SOLE_BOOL_ASSERT 等违规。有违规 → 修复后重跑直到全 pass

> ⚠️ 跳过自检门直接进编译验证 = 流程违规。编译通过不代表用例质量达标。

**类级自检**（test-types.md §8 最小清单，每个类生成完后在测试文件顶部 `{BranchList}` 注释段落落完成情况）。

**命名规范**：
- 测试 Fixture 类名：`{ClassName}Test`（如 `MyClassTest`）
- 测试用例名：`{Feature}_{Scenario}_{ExpectedResult}`
- 例：`ParseData_ValidInput_ReturnsTrue`、`ParseData_EmptyInput_ReturnsFalse`
- **禁止在 Fixture 类名和用例名中携带轮数/批次号**（如 `R18`、`Round2`、`Batch3`），轮数是内部调度概念，不属于测试命名

**GUI 类特殊处理**（`is_gui=true`）：
- `SetUpTestSuite()` 用 `QCoreApplication`，**不用** `QApplication`（避免 X11/Wayland 崩溃）
- 不直接实例化 GUI 类，通过 helper 或信号槽测试状态
- 若无可测方法（除构造函数外），生成最小占位测试

**抽象类处理**：
- 创建最小具体子类用于测试
- 用 `using BaseClass::protectedMethod;` 在测试子类中暴露 protected 方法

**单例/工厂模式处理**（`special_handling=private_ctor`）：
- 单例：通过 `Instance()` 获取实例，不直接 `new`；测试后需重置单例状态
- 工厂：通过工厂方法创建实例，不绕过工厂直接构造
- 若构造函数是 private/protected 且无工厂方法 → 标记 `needs_manual`，并调 `export-defects.py upsert` 落盘到 `.ut-defects.json`（`type=needs_manual, detected_at_stage=review`），归集进缺陷统计

**PIMPL 模式处理**（`special_handling=pimpl`）：
- 只测 public 接口，不直接访问 Private 类
- 若 Private 类有独立可测逻辑 → 单独为 Private 类生成测试

**模板类处理**（`special_handling=template`）：
- 为模板类指定具体类型参数（如 `MyTemplate<int>`、`MyTemplate<QString>`）
- 优先用项目中已有的实例化类型

**stub 选择**（以依赖追踪的 `stub_list` 为起点，结合 §4.0 §2 的 `trace_path` 出向链补齐）：
- 继承 QWidget → stub `show`、`hide`、`height`、`width`
- 继承 QDialog → stub `exec`
- 虚函数 → `VADDR(Class, method)`
- 重载 → `static_cast<Ret (Class::*)(Params)>(&Class::method)`
- 外部依赖 → 按预期行为 stub
- **路径/文件系统**（必须 mock 或 `SetUp()` 隔离，禁止真实读写测试机磁盘）：
  - 硬编码绝对/相对路径字符串 → mock 访问该路径的函数（`QFile::open`、`QDir::exists`、`QFileInfo::exists`、POSIX `::access`/`::stat`），或在 `SetUp()` 用 `QTemporaryDir` 建临时目录并把路径注入被测对象
  - `QStandardPaths::writableLocation` → mock 返回 `QTemporaryDir` 路径，**绝不**返回真实 `~/.config`、`~/.cache` 等用户目录
  - `QCoreApplication::applicationDirPath` / `QDir::currentPath` → mock 返回临时目录，避免依赖测试机安装位置
- **环境变量** → `SetUp()` 用 `qputenv` 设置、`TearDown()` 用 `qunsetenv` 还原；或 mock `getenv`/`qEnvironmentVariable`/`qgetenv`
- **子进程** → `QProcess::start`、`system`、`popen` 必须 mock，**禁止真实启动外部进程**
- **时间/随机** → 需要确定性结果时 mock `QDateTime::currentDateTime`/`QTime::currentTime`；随机源固定种子或 mock `QRandomGenerator::system`
- **网络** → 任何 socket 类一律 mock，**禁止真实网络访问**
- **单例/全局状态** → 用例结束在 `TearDown()` 重置（单例 `Instance()` 提供 `reset()` 或析构重建），避免污染后续用例

### 5. 生成 CMakeLists.txt

读 `templates/cmake-submodule.txt`，替换：
- `{module_name}` → 模块名
- `{ClassName}` → 类名
- `{QT_VERSION}` → 内存变量中的 qt_version
- `{PROJECT_LIBRARIES}` → 从根 CMakeLists.txt 检测到的项目目标库（如 `dde-file-manager`）；无则留空
- `{QT_EXTRA_LIBS}` → GUI 类填 `Qt${QT_VERSION}::Widgets`（及其他 GUI 模块）；纯 Core 模块填空字符串
- `{source_module_path}` → 依赖追踪的 source_dirs（glob `*.cpp`）

### 6. 智能合并 CMake

将新生成的 `add_subdirectory(<module>)` 合并到 `{test_dir}/CMakeLists.txt`：
- 读现有 `{test_dir}/CMakeLists.txt`
- 若已有该模块的 `add_subdirectory`，跳过
- 若无，在 `{ADD_SUBDIRECTORIES}` 区域追加

> **注意**：绝不修改已有 `add_subdirectory` 行的顺序或内容。

### 7. 记录测试生成结果

将生成结果记录到内存变量 `class_status[classname]`：

```json
{
  "status": "test_written",
  "test_file": "{test_dir}/ui/test_myclass.cpp",
  "methods_tested": 15
}
```

## 关键约束

- 不编译或运行测试（编译验证由后续阶段负责）
- 不直接为 private 方法写 `TEST_F`：private 方法通过调用它的 public/protected 方法**间接覆盖**，必须覆盖其内部分支与边界
- `qualified_name` 必须从图谱返回值取，不自己拼
- 不跳过 GUI 特殊处理（GUI 类用 `QCoreApplication`，不直接实例化）
- 不修改已有 CMake 代码，只 APPEND `add_subdirectory`
- 不修改项目源码
- 不从网络下载模板，只读 `templates/`
- 测试文件必须有 `SPDX-FileCopyrightText` 和 `SPDX-License-Identifier` 头
- 不硬耦合测试机：所有路径、环境变量、文件系统、网络、子进程、时间、随机源访问必须 mock 或在 `SetUp()` 中隔离；禁止硬编码测试机绝对路径；禁止依赖测试机特定文件/用户/权限/时区/网络状态；用例必须可在任意干净 CI 环境复现
- 不盲信 `stub_list`：必须先用 MCP（`trace_path` 出向链 + `get_code_snippet` 方法体）识别待测方法的**隐式依赖**（路径、env、文件系统、子进程、时间、随机、单例/全局状态），按 §4.0 补齐 mock；**不用 `read`/`grep` 直读项目源码文件**
- 不让测试依赖外部资源：不读写真实文件系统、不连真实数据库、不发真实网络请求、不启动真实子进程、不依赖真实系统时间；一律 mock 或在 `SetUp()` 临时隔离并在 `TearDown()` 清理
- 不让用例间互相污染：单例/静态成员/全局状态在 `TearDown()` 重置；`stub.clear()` 必须在 `TearDown()` 调用；临时目录/文件必须在 `TearDown()` 释放
- 不用"不崩溃"或单一布尔作为唯一断言：每个用例至少 2 个 `EXPECT_*` 断言维度；禁止 `EXPECT_NO_FATAL_FAILURE` 或单独 `EXPECT_TRUE(ret)` 作为唯一断言
- 不凭直觉生成用例：必须先按等价类 + 边界值建模输入空间，再按分支覆盖补全；分支清单 + 用例映射写入测试文件顶部注释；按 level/factors 推导的用例数下限不是上限
- 不用 `EXPECT_ANY_THROW` / `EXPECT_NO_FATAL_FAILURE` 充数异常断言：异常路径必须 `EXPECT_THROW(stmt, ExcType)` 精确匹配异常类型
- 不混用 stub_ext 与 gMock 同一方法：项目内接口类用 gMock；Qt 内置类、全局函数、无虚函数/不可注入类用 stub_ext；同一目标不得既 `stub.set_lamda` 又 `MOCK_METHOD`
