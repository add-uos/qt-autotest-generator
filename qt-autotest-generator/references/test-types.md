# 单元测试用例设计方法论

> 本技能生成用例的**方法论依据**。生成用例前必须按 §1 §2 §4 建模输入空间与分支，再按 §3 §6 §7 选定组织、覆盖与 stub/mock 方式，最后按 §5 §8 选定异常断言与 fixture 复用。
>
> `test-code-gen.md` §4 只给出"必须遵守的最小清单"与用例数下限表，详细方法以本文为准。
>
> 与 self-checker 的关系：self-checker 检查环境隔离与断言强度；本文 §8 §9 列出 self-checker 未覆盖的"用例设计质量"反模式，test-code-gen 生成前必须自检。

---

## 1. 等价类划分 (Equivalence Partitioning)

把输入空间按"对被测方法行为等价的输入子集"划分，每个等价类至少 1 个用例。

### 1.1 划分步骤

1. **识别每个输入维度**：方法参数（含 `this` 对象状态）、外部依赖返回值、全局/静态状态
2. **对每个维度按"有效/无效"切等价类**：
   - 有效等价类：合法输入，触发正常分支
   - 无效等价类：非法输入，触发错误/拒绝/异常分支
3. **每个等价类至少 1 个用例**；无效等价类**优先单独成用例**（不要和有效输入混在一起，崩溃时无法定位是哪个输入导致）

### 1.2 常见等价类边界

| 输入类型 | 有效等价类示例 | 无效等价类示例 |
|---|---|---|
| `int n` | `n > 0`、`n == 0`、`n < 0`、`INT_MIN`/`INT_MAX`（极值，溢出风险） | 越界值（若方法有范围约束，如 `n ∈ [0, 100]` 时 `n = -1` 或 `n = 101`） |
| `QString s` | 非空、纯空白、含 unicode | 空字符串、超长字符串、含 `\0` |
| 容器 `QList<T>` | 空、单元素、多元素 | — |
| 指针 `T*` | 非 null | null |
| 枚举 `E` | 每个枚举值 | 越界值（若可被 cast） |
| `QDate` | 有效日期（如 2024-02-29 闰年） | `QDate()`（默认构造，`isNull()==true`）、`QDate(0,1,1)`（非法年份 0）、`QDate(2024,2,30)`（2 月无 30 日） |

### 1.3 反模式

- ❌ 一个用例混合多个无效输入 → 失败时无法定位是哪个输入导致
- ❌ 只测有效等价类，漏掉错误分支
- ❌ 用例名不带 `Invalid`/`Empty`/`Negative` 等场景标识 → 看不出意图

---

## 2. 边界值分析 (Boundary Value Analysis)

对每个等价类的**边界点**单独生成用例。边界是缺陷高发区，必须显式覆盖，不得与"中间值"用例合并稀释。

### 2.1 数值边界

| 边界类型 | 必测点 |
|---|---|
| 闭区间 `[a, b]` | `a-1`、`a`、`a+1`、`b-1`、`b`、`b+1`（4 边界内 + 2 越界，共 6 点） |
| 非负区间 `[0, ∞)` | `-1`、`0`、`1`、`INT_MAX` |
| 长度/计数 | `0`、`1`、`capacity-1`、`capacity`、`capacity+1` |
| 循环计数 | `i = 0`、`i = n-1`、`i = n`（越界）、`n = 1`、`n = INT_MAX` |

### 2.2 字符串边界

| 边界 | 必测点 |
|---|---|
| 长度 | 空串 `""`、长度 1、长度 N（典型）、超长（如 `1 << 20`） |
| 字符集 | ASCII、UTF-8 多字节、含 `\0`、含控制字符 |
| 内容 | 纯空白、首尾空白、含路径分隔符、含特殊字符 `<>\"'` |

### 2.3 容器/集合边界

| 边界 | 必测点 |
|---|---|
| 大小 | 空 `QList<>{}`、单元素、N 元素 |
| 索引 | `0`、`size-1`、`size`（越界） |
| 迭代器 | `begin()`、`end()`（不得解引用/自增） |

### 2.4 时间/日期边界

| 边界 | 必测点 |
|---|---|
| 日期 | 闰年 2-29、非闰年 2-28、月末、跨年 12-31 → 1-1 |
| 时间 | 00:00:00、23:59:59、夏令时切换点 |
| 时区 | UTC、+14、-12、跨日 |

### 2.5 反模式

- ❌ 只测"中间值"如 `n = 5`，不测 `n = 0` 和 `n = -1`
- ❌ 边界用例和等价类用例重复合并，导致边界被稀释
- ❌ 边界点选不完整（如只测 `a` 和 `b`，漏 `a-1` 和 `b+1` 越界）

---

## 3. 用例组织方式

### 3.1 TEST vs TEST_F vs TEST_P

| 宏 | 适用 | 示例 |
|---|---|---|
| `TEST(Suite, Name)` | 无状态、无 SetUp/TearDown、单点验证 | `TEST(UrlUtils, ParseAbsolutePath)` |
| `TEST_F(Fixture, Name)` | 需 SetUp/TearDown、共享对象/stub、每用例独立 fixture 实例 | `TEST_F(ParserTest, ParseData_ValidInput_ReturnsTrue)` |
| `TEST_P(Fixture, Name)` | **多组输入跑同一断言**，避免复制粘贴 | `TEST_P(ParserTest, ParseData_ReturnsExpected)` + `INSTANTIATE_TEST_SUITE_P` |

### 3.2 参数化测试 (TEST_P) 何时使用

**必须使用 `TEST_P`**：
- 同一方法对 ≥ 3 组同质输入跑同一断言逻辑（如 `parse("a=1")` / `parse("a=1;b=2")` / `parse("")`）
- 边界值成组测试（如 §2.1 的 6 个点对同一断言）
- 等价类成组测试（如 §1.2 表格多组输入）

**禁止**：
- 用例间断言逻辑不同（每组 Assert 不同）→ 改用多个 `TEST_F`
- 输入参数需根据前例执行结果动态决定（链式测试，如"前一次返回值作为下一次输入"）→ 改用 `TEST_F` 在 SetUp 中按需构造

### 3.3 参数化测试模板

```cpp
// 顶部定义数据结构
struct ParseCase {
    QString input;
    bool expectedOk;
    int expectedValue;
};

class ParserParamTest : public ::testing::TestWithParam<ParseCase> {
protected:
    Parser parser;
};

TEST_P(ParserParamTest, ParseReturnsExpected) {
    const auto &c = GetParam();
    bool ok = false;
    int v = parser.parse(c.input, &ok);
    EXPECT_EQ(ok, c.expectedOk);
    EXPECT_EQ(v, c.expectedValue);
}

INSTANTIATE_TEST_SUITE_P(
    BasicCases,                          // 用例名前缀
    ParserParamTest,
    ::testing::Values(
        ParseCase{"a=1",     true,  1},
        ParseCase{"a=1;b=2", true,  2},
        ParseCase{"",        false, 0},
        ParseCase{"=",       false, 0}
    )
);
```

要点：
- `INSTANTIATE_TEST_SUITE_P` 第一参数是前缀，最终用例名为 `BasicCases/ParseReturnsExpected/0`、`/1`、…
- 命名仍可读，self-checker 不会因 `/` 分隔判违规
- `ParseCase` 必须放测试文件顶部、fixture 之前
- 一组参数 + 多用例（如同时 `ParseReturnsExpected` 和 `ParseUpdatesErrorState`）会形成 N×M 笛卡尔积，注意控制用例总数

### 3.4 Test Fixture 复用策略

#### 3.4.1 默认 fixture（本技能主路径）

`{ClassName}Test : public ::testing::Test`，每用例独立实例，`SetUp()` 构造对象 + 设置 stub，`TearDown()` 析构 + `stub.clear()`。这是 `google-test-base.cpp` 模板的默认形态。**Fixture 类名禁止携带轮数/批次号**（如 `R18`、`Round2`、`Batch3`），只允许 `{ClassName}Test` 或其派生（如 `{ClassName}Test_LoggedIn`）。

#### 3.4.2 共享 SetUp（SetUpTestSuite）

- 用于**全 suite 共享且只设一次**的资源：QCoreApplication 初始化、单例状态预置、配置文件创建
- 不得放用例间需要重置的状态（会污染，self-checker 5b 判违规）

#### 3.4.3 派生 fixture

当一个类有多组**显著不同**的前置条件（"未登录态" vs "已登录态"），从基 fixture 派生子 fixture：

```cpp
class UserTest_LoggedOut : public UserTest {};

class UserTest_LoggedIn : public UserTest {
protected:
    void SetUp() override {
        UserTest::SetUp();          // 复用基类 SetUp
        obj->login("u", "p");       // 子类追加
    }
};
```

仅在子 fixture 的 SetUp/TearDown 差异显著时使用；差异只是 stub 一两个方法时，**用普通 `TEST_F` + 分组命名更清晰**，不要滥用派生。

#### 3.4.4 异常安全

- `SetUp()` 抛异常 → Google Test 跳过用例并标记失败，**`TearDown()` 不会被调用**
- 因此**资源分配必须放在 SetUp 顺序的最后**；先抛异常的部分（如参数校验）放最前
- `TearDown()` 必须 `noexcept` 安全：清理都包 `try/catch` 或用 RAII（`std::unique_ptr`、`QTemporaryDir` 成员）兜底
- 临时资源（文件/目录/env）优先用 RAII 成员（`QTemporaryDir tmpDir;`），析构自动清理，避免 `SetUp` 抛异常时残留

---

## 4. 白盒分支覆盖与黑盒规约的融合

### 4.1 流程

1. **黑盒先行**：按 §1 §2 对公开接口生成"等价类 + 边界值"用例（只看规约/签名）
2. **白盒补全**：用 `scripts/mcp-scan.py extract-branches`（图谱定位+本地行切片）读源码，列出每条分支（`if/else/switch/for/while/异常`）
3. **覆盖率对账**：每条分支至少 1 个用例触发；若某分支无黑盒用例触达 → 补白盒用例（哪怕超出用例数下限）
4. **分支清单**写入测试文件顶部注释，格式：

```cpp
// 分支清单（来源：MyClass::parse(QString, int*, bool*)）
// B1: input.isEmpty()           → return false
// B2: !input.contains('=')      → return false
// B3: parts.size() > 2          → return false
// B4: ok 且 value 解析成功       → return true, *out = value
// B5: parts[0].isEmpty()（如 "=1"）→ return false
//
// 用例映射：
// - Parse_EmptyInput_ReturnsFalse           → B1
// - Parse_NoEquals_ReturnsFalse              → B2
// - Parse_TooManyEquals_ReturnsFalse         → B3
// - Parse_ValidInput_ReturnsTrueAndValue      → B4
// - Parse_EmptyKey_ReturnsFalse               → B5
// - Parse_BoundaryInput_ReturnsExpected /* TEST_P */ → B1+B2+B3+B5 边界 + B4
```

### 4.2 必检分支类型

| 分支类型 | 必须覆盖 |
|---|---|
| `if (cond)` / `else` | cond=true 与 cond=false 各 1 用例 |
| `switch` | 每个分支 + `default` |
| `for` 循环 | 0 次、1 次、N 次、`break` 提前退出、`continue` 跳过 |
| `while` 循环 | 入口不进入、进入后 1 次退出、进入后 N 次退出 |
| 异常抛出 | 每个显式 `throw` + 每个会抛异常的被调函数 |
| 提前 return | 每个 `return` 路径单独用例 |
| 短路求值 | `&&`/`||` 左右两侧各触发的用例 |
| 三元 `?:` | 条件两侧各 1 用例 |

### 4.3 反模式

- ❌ 只读签名不读实现，漏掉"边界外边界"（如 `if (n > 0 && n != 10)` 漏 `n=10`）
- ❌ 分支清单仅写注释不映射到用例名，无法对账
- ❌ 用例数下限（level/factors 推导）够了就停，漏掉的分支用 self-checker 也查不出（self-checker 不查分支覆盖）

---

## 5. 异常与错误路径测试

### 5.1 Google Test 异常断言

| 宏 | 语义 |
|---|---|
| `EXPECT_THROW(stmt, ExcType)` | 期望 stmt 抛 ExcType（或其子类） |
| `EXPECT_ANY_THROW(stmt)` | 期望 stmt 抛任意异常 |
| `EXPECT_NO_THROW(stmt)` | 期望 stmt 不抛任何异常 |
| `ASSERT_THROW(stmt, ExcType)` | 同 EXPECT_THROW 但失败时终止当前用例 |
| `ASSERT_NO_THROW(stmt)` | 同 EXPECT_NO_THROW 但失败时终止 |

### 5.2 精确匹配异常类型

```cpp
// ❌ 反模式：只断言"抛了异常"，不验异常类型和 message
EXPECT_ANY_THROW(obj->parse(""));

// ✅ 正模式：断言异常类型
EXPECT_THROW(obj->parse(""), std::invalid_argument);

// ✅ 进一步验证 message 内容
try {
    obj->parse("");
    FAIL() << "expected std::invalid_argument";
} catch (const std::invalid_argument &e) {
    EXPECT_STREQ(e.what(), "input is empty");
}
```

或用 `testing::Throws`（gMock ≥ 1.12）一步匹配类型 + message：

```cpp
EXPECT_THAT(
    [&]{ obj->parse(""); },
    testing::Throws<std::invalid_argument>(
        testing::Property(&std::invalid_argument::what,
                          testing::HasSubstr("empty"))
    )
);
```

### 5.3 错误码 / 错误字符串路径

Qt 代码常用 `bool` 返回 + `QString *err` 出参，或 `ErrorCode` 枚举：

```cpp
// bool + 出参 err
QString err;
bool ok = obj->parse("", &err);
EXPECT_FALSE(ok);
EXPECT_EQ(err, QString("input is empty"));   // 错误字符串精确值

// 枚举 + lastErrorString
EXPECT_EQ(obj->open("/nonexistent"), ErrorCode::NotFound);
EXPECT_EQ(obj->lastErrorString(), QString("not found"));
```

### 5.4 必测异常分支

| 异常源 | 必测点 |
|---|---|
| 显式 `throw` | 每个 throw 一用例 |
| 调用会抛的 STL API | `std::vector::at()` 越界（`std::out_of_range`）、`std::stoi` 非法数字（`std::invalid_argument`）、`std::map::at()` 越界 |
| 资源分配失败 | 通常不测（难以构造），但循环内 `new` 应有 try/catch 验证 |

### 5.5 反模式

- ❌ `EXPECT_NO_FATAL_FAILURE(obj->parse(""))` 作为唯一断言 → 不区分"抛了异常但被吞"和"成功"
- ❌ `EXPECT_ANY_THROW` 不验异常类型 → 实现改抛 `std::runtime_error` 而非 `std::invalid_argument` 也通过
- ❌ 异常路径用例只测"抛了"不测"抛了之后对象状态保持一致"（强异常安全，见 §6.3）

---

## 6. 负面测试 (Negative Testing)

### 6.1 范围

负面测试 = **主动喂错误输入/触发错误条件**，验证被测方法：

1. 不崩溃（不 segfault、不抛未捕获异常）
2. 返回错误标识（false、错误码、空值）
3. 设置正确的错误状态（`lastError`、信号）
4. 不破坏对象其他状态（强异常安全）

### 6.2 必测负面场景

| 类型 | 示例 |
|---|---|
| 空输入 | `""`、`QList<>{}`、`nullptr` |
| 越界 | `list[size]`（越界）、`s.mid(-1, 0)` |
| 类型不匹配 | `toInt("abc")`、`QDate::fromString("xyz")` |
| 重复操作 | 重复登录、重复注册、重复 init |
| 资源不足 | mock `QFile::write` 返回 -1（磁盘满）、mock `new` 抛 `bad_alloc` |
| 权限不足 | mock `::open` 返回 EACCES |
| 并发冲突 | mock 锁竞争 |

### 6.3 强异常安全验证

```cpp
// Arrange: 记录对象初始状态
obj->setCount(5);
const QString stateBefore = obj->snapshot();

// Act: 触发会失败的调用
EXPECT_FALSE(obj->loadFromBrokenSource());

// Assert: 失败后对象状态保持不变（强异常安全）
EXPECT_EQ(obj->count(), 5);
EXPECT_EQ(obj->snapshot(), stateBefore);
EXPECT_EQ(obj->lastError(), ErrorCode::LoadFailed);
```

### 6.4 反模式

- ❌ 只测"返回 false"不测"对象状态未损坏"
- ❌ 负面用例名不带 `Negative` / `Invalid` 前缀，看不出意图

---

## 7. Stub vs Mock

### 7.1 概念区分

| | Stub | Mock |
|---|---|---|
| 关注点 | **被调用的返回值**（喂 SUT 输入） | **是否被调用、调用次数、参数、顺序**（验证 SUT 输出） |
| 失败语义 | "喂的输入不对，SUT 行为异常" | "SUT 没正确驱动依赖" |
| 本技能载体 | `stub_ext::StubExt` + `set_lamda` | `gMock`：`EXPECT_CALL` + `Cardinality` |

**简记**：stub 喂数据，mock 验行为。

### 7.2 stub_ext::StubExt 适用场景（本技能主路径）

`stub_ext` 通过运行时函数替换（非虚表 hook），**可 mock 任意已存在函数**，包括：
- Qt 自带类无虚函数的方法：`QFile::open`、`QDir::exists`、`QProcess::start`
- 全局函数：`qPrintable`、`::getenv`、`::open`
- 第三方库函数

适用：**第三方类、Qt 内置类、无虚函数接口、无法注入的依赖**。参见 `templates/stub-patterns.cpp` 第 1-16 节。

### 7.3 gMock 适用场景

gMock 通过**虚函数 + 依赖注入**实现，要求：
- 被依赖类有虚函数（或纯虚接口类）
- 被测类**通过指针/引用持有该依赖**，可在构造时注入 mock 对象

适用：**项目内部定义的接口类、有虚函数的依赖、设计中已支持注入**。

### 7.4 gMock 基本用法

#### 7.4.1 定义 Mock 类

```cpp
// 被测代码假设有接口：
class IStorage {
public:
    virtual ~IStorage() = default;
    virtual bool save(const QString &path, const QByteArray &data) = 0;
    virtual QByteArray load(const QString &path) = 0;
    virtual void clear() = 0;
};

// 测试代码：定义 Mock
class MockStorage : public IStorage {
public:
    MOCK_METHOD(bool, save,
                (const QString &path, const QByteArray &data), (override));
    MOCK_METHOD(QByteArray, load,
                (const QString &path), (override));
    MOCK_METHOD(void, clear, (), (override));
};
```

#### 7.4.2 EXPECT_CALL：验证行为

```cpp
TEST_F(ManagerTest, Save_CallsStorageExactlyOnce) {
    MockStorage storage;
    Manager mgr(&storage);  // 依赖注入

    EXPECT_CALL(storage, save(QString("cfg.txt"), QByteArray("data")))
        .Times(1)
        .WillOnce(::testing::Return(true));

    EXPECT_TRUE(mgr.persist(QString("cfg.txt"), QByteArray("data")));
}
```

#### 7.4.3 ON_CALL：配置默认返回值（stub 行为）

```cpp
ON_CALL(storage, load(::testing::_))
    .WillByDefault(::testing::Return(QByteArray("cached")));
```

- `EXPECT_CALL(...).Times(0)` → 验证**未被调用**
- `EXPECT_CALL(...).Times(AtLeast(2))` → 至少 2 次
- `Sequence` → 验证调用顺序

#### 7.4.4 Cardinality 速查

| Cardinality | 语义 |
|---|---|
| `Times(0)` | 0 次（验证未调用） |
| `Times(1)` | 恰好 1 次 |
| `Times(2)` | 恰好 2 次 |
| `AtLeast(n)` | ≥ n |
| `AtMost(n)` | ≤ n |
| `Between(m, n)` | [m, n] |
| `AnyNumber()` | 任意次（相当于 stub） |

#### 7.4.5 Matcher 速查

| Matcher | 语义 |
|---|---|
| `_` | 任意 |
| `Eq(v)` / `v` | 等于 |
| `StrEq(s)` | `std::string` 等于（**不支持 QString**；QString 应用 `Eq(QString(...))`） |
| `StartsWith(p)` | 前缀 |
| `Contains(x)` | 包含 |
| `IsNull()` | 指针为 null |
| `NotNull()` | 指针非 null |
| `Field(&T::m, m_)` | 结构体字段匹配 |

### 7.5 stub_ext + gMock 混用策略

| 依赖类型 | 工具 | 示例 |
|---|---|---|
| Qt 内置类（QFile/QDir/QProcess） | stub_ext | `stub.set_lamda(&QFile::open, ...)` |
| 全局/POSIX 函数 | stub_ext | `stub.set_lamda(::getenv, ...)` |
| 项目内接口类（有虚函数 + 可注入） | gMock | `MockStorage storage; mgr.setStorage(&storage)` |
| 项目内类（无虚函数、不可注入） | stub_ext | `stub.set_lamda(&MyClass::method, ...)` |

**禁止**：
- 用 gMock 强行 mock Qt 类（Qt 类大多无虚函数，需自抽接口层，超出本技能范围）
- 同一依赖既 stub_ext 又 gMock 同一方法 → 重复替换导致未定义行为
- 测试文件既 include `gmock/gmock.h` 又 include `stubext.h` 后混用同一目标

### 7.6 选择决策树

```
依赖是项目内定义的类吗？
├─ 是 → 该类有虚函数且可注入吗？
│      ├─ 是 → gMock（行为验证更强，可验调用次数/顺序/参数）
│      └─ 否 → stub_ext
└─ 否（Qt/第三方/全局函数）→ stub_ext
```

### 7.7 切换 gMock 时 test-code-gen 必须做的

测试文件顶部 include：
```cpp
#include <gmock/gmock.h>   // 仅在使用 gMock 时添加；纯 stub_ext 测试不引入
```
gMock 模板示例见 `templates/stub-patterns.cpp` 第 17-19 节。

---

## 8. 最小用例设计清单（test-code-gen.md 必须遵守）

生成每个类的测试前，必须在测试文件顶部注释中输出以下清单的"完成情况"，未完成项不得提交 build-verifier：

| # | 检查项 | 来源 |
|---|---|---|
| 1 | 公开方法已列出，每个方法 ≥ 1 用例 | test-code-gen.md §4 |
| 2 | 每个输入维度按等价类划分，每类 ≥ 1 用例 | §1 |
| 3 | 每个等价类的边界值显式覆盖 | §2 |
| 4 | 同质多组输入用 `TEST_P` 参数化 | §3.2 |
| 5 | 分支清单已列出并映射到用例名 | §4.1 |
| 6 | 每条 `if/switch/throw/early-return` 分支有触发用例 | §4.2 |
| 7 | 异常路径用 `EXPECT_THROW` 精确匹配类型 + message | §5 |
| 8 | 负面场景（空/越界/类型不符/资源不足）有专门用例 | §6.2 |
| 9 | 负面用例验证强异常安全（状态未损坏） | §6.3 |
| 10 | 项目内接口类用 gMock，其他用 stub_ext | §7.5 |

---

## 9. 反模式汇总（出现即视为用例无效，需重写）

| # | 反模式 | 修正 |
|---|---|---|
| A1 | `EXPECT_NO_FATAL_FAILURE` 作为唯一断言 | §5.5 / test-code-gen.md §4.1 |
| A2 | 边界值不显式覆盖，只测"中间值" | §2.5 |
| A3 | 等价类未划分，凭直觉喂输入 | §1.3 |
| A4 | 同质多组输入复制粘贴 N 个 `TEST_F` | §3.2 → 改 `TEST_P` |
| A5 | 分支清单不写、不映射用例名 | §4.1 |
| A6 | 异常路径用 `EXPECT_ANY_THROW` 不验类型 | §5.2 |
| A7 | 负面用例只验返回值不验状态保持 | §6.3 |
| A8 | Qt 类强行抽 gMock 接口层 | §7.5 |
| A9 | 同一方法既 stub_ext 又 gMock | §7.5 |
| A10 | 用例名不带 `Negative`/`Invalid`/`Boundary`/`Empty` 等场景标识 | §1.1 / §6.4 |
| A11 | 用例数下限够了就停，未对账分支覆盖 | §4.1 |
| A12 | `SetUp()` 抛异常路径未保护资源（`TearDown()` 不会执行） | §3.4.4 |
