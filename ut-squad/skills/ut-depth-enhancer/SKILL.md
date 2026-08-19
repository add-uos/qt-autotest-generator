---
name: ut-depth-enhancer
description: "单元测试深度补全：基于 lcov 逐行覆盖率缺口与源码理解，用边界值分析、等价类划分、决策表、状态迁移、异常路径、循环边界等用例设计技术，把行覆盖率推到 ≥90%。强制非 trivial 断言，每个用例必须验证被测对象的可观测行为。与 ut-coverage-verifier 配合循环消解行覆盖缺口。"
version: "1.0.0"
user-invocable: true
argument-hint: "[project-path] [--scope incremental|full]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
compatibility:
  required_mcp_any_of:
    - name: remote-codebase-memory-mcp
      purpose: "读函数控制流与签名，定位未覆盖分支"
    - name: codebase-memory-mcp
      min_version: "0.8.0"
      purpose: "本地兜底，读函数控制流与签名"
---

# UT Depth Enhancer

广度达标后的深度阶段能力。**核心区别于广度**：广度是「每个方法调一下」，深度是「读懂数据怎么流、分支怎么转，故意构造能走进未覆盖分支的输入」。

## 核心原则

1. **从代码层面理解，非凑数** —— 每个深度用例都基于对源码控制流的真实理解，先读代码再设计输入，不盲目补调。
2. **行缺口驱动** —— 只处理 line_coverage<100% 的函数；已 100% 的不碰。
3. **强制非 trivial 断言** —— 断言必须引用被测对象的可观测变化（返回值/状态/副作用/异常）。
4. **优先复用广度脚手架** —— 在广度阶段已有的测试文件、stub、CMake 上追加用例，不另起炉灶。
5. **不修源码** —— 疑似源码缺陷只标红交还用户。
6. **与 verifier 循环** —— 补一批用例 → 跑 verifier → 看缺口 → 再补，直到行覆盖率≥90%。

## 工作循环

```text
1. 读 session.targets（scope + changed_functions）和已 approved 的 .ut-exemptions.json
2. 调 ut-coverage-verifier(gate) 拿最新 coverage.info 与 ut-summary.json
3. 解析 lcov 逐行详情，筛出「行覆盖率<100% 且非豁免」的函数 → 缺口函数清单
4. 对每个缺口函数，执行「缺口消解五步」（见下）
5. 批次补完后调 ut-coverage-verifier(gate) 重新统计
6. 行覆盖率≥90% → 完成，回交队长；否则回到第 3 步
```

## 缺口消解五步（核心方法论）

对每个缺口函数：

### 第 1 步：读源码，定位未覆盖行

```bash
# lcov 逐行详情：每行源码前面标是否执行（DA:<line>,<count>）
lcov --list build-ut/coverage.info                       # 文件级
genhtml -o /tmp/lcov-detail build-ut/coverage.info        # HTML 逐行（含未覆盖行高亮）
# 或直接读 coverage.info 找目标文件的 DA 记录，count=0 即未覆盖
```

把「未覆盖行」映射到**控制流结构**：
- `if/else` 的某个分支
- `switch` 的某个 case / default
- `return` / `break` / `continue` / `throw` 提前退出
- `for/while` 的循环体或退出条件

### 第 2 步：选择用例设计技术

按未覆盖行的「控制流类型」选技术（这是深度区别于广度的关键）：

| 未覆盖结构 | 用例设计技术 | 典型输入 |
|---|---|---|
| 数值比较 / 集合判断 | **边界值分析** | 0、1、max、min、空、负、刚好越界、刚好不越界 |
| 多布尔条件组合 | **决策表** | 枚举条件真假组合（n 个条件 ≤ 2^n 行），含优先级 |
| 有状态对象 | **状态迁移** | init→active→done、init→error、非法状态转换被拒 |
| 有异常 / 失败路径 | **异常路径** | null 入参、越界下标、资源分配失败、DBus 不可用、IO 错误、文件不存在 |
| 循环 | **循环边界** | 0 次、1 次、N 次、首次/末次迭代、break/continue 触发 |
| 字符串 / 解析 | **等价类划分** | 合法格式、非法格式、空串、超长、特殊字符、编码边界 |

### 第 3 步：设计针对性输入

**不是随机造数据，而是反推**：要让执行流走进那个未覆盖分支，前置条件是什么？

例：未覆盖行是 `if (index < 0) { return ErrorInvalidIndex; }` 的 return 分支 → 输入必须让 `index < 0`，断言必须验证返回值==ErrorInvalidIndex。

### 第 4 步：写测试，断言验证可观测行为

```cpp
// ✅ 正确：断言引用被测对象的可观测变化
TEST_F(MyClassTest, GetItem_NegativeIndex_ReturnsErrorInvalidIndex)
{
    auto item = obj.getItem(-1);
    EXPECT_EQ(item.error(), ErrorCode::InvalidIndex);  // 验证返回值
}

// ✅ 正确：状态变更
TEST_F(ParserTest, ParseInvalidFormat_LeavesStateCleanAndThrows)
{
    EXPECT_THROW(parser.parse("garbage"), ParseError);  // 异常类型
    EXPECT_FALSE(parser.isDirty());                     // 副作用：状态保持干净
}

// ❌ 禁止：trivial 断言（会被 verifier lint 判废）
TEST_F(MyClassTest, GetItem_BadInput)
{
    EXPECT_NO_THROW(obj.getItem(-1));   // 只验不崩，没验证行为
}
```

命名规范沿用广度：`{Feature}_{Scenario}_{ExpectedResult}`。

### 第 5 步：重新构建，验证缺口消解

```bash
cd tests && ./test-prj-running.sh; cd ..
# 重新解析 coverage.info，确认该函数的未覆盖行变 0（或进入豁免）
```

## 增量 vs 全量

- `--scope incremental`：只处理 session.targets.changed_functions 的行缺口。
- `--scope full`：处理所有非豁免函数的行缺口。

## 难测路径的处理顺序（避免无效劳动）

当某函数的行缺口确实测不到时，按顺序判断：

1. **是豁免类（GUI/DBus/硬件/入口）？** → 不碰，豁免已在广度阶段处理。
2. **是 stub 不够？** → 补 stub（沿用广度的 stub-patterns），让外部依赖可控。
3. **是测试基础设施缺失（如需要 Qt 事件循环）？** → 用 `QSignalSpy` / `QTest::qWait` 等可控方式，不依赖真实 GUI。
4. **疑似源码缺陷（逻辑矛盾、必崩路径、死代码）？** → 标红交还用户，不修源码，不强行造用例。

## 断言有效性自检（写完即查，不等 verifier）

每写完一个 TEST 块，自检：
- [ ] 块内至少 1 个 `EXPECT_`/`ASSERT_` 引用了被测对象的成员/返回值/状态？
- [ ] 没有 `EXPECT_TRUE(true)` / `SUCCEED()` / 操作数全为常量？
- [ ] 没有「唯一断言是 EXPECT_NO_THROW」？

## 状态文件交互

读写 `autotests/.ut-session.json`：

```json
{
  "depth_status": "pending | done",
  "line_coverage": 0,
  "depth_iterations": [
    {"iter": 1, "before_line": 72.3, "after_line": 88.1, "functions_touched": 14},
    {"iter": 2, "before_line": 88.1, "after_line": 91.5, "functions_touched": 5}
  ],
  "source_defect_flags": [
    {"function": "...", "line": 123, "reason": "逻辑矛盾：此处 return 后下方的赋值永不执行，疑似死代码"}
  ]
}
```

## 红旗（出现即停）

- 未读源码就补用例（盲补 = 凑数）。
- 用 trivial 断言推高行覆盖（自检不过仍提交）。
- 修改 `src/` 源码修缺口。
- 修改 `tests/test-prj-running.sh` 或 `gen-ut-summary.py` 的统计口径。
- 行覆盖率<90% 就宣布完成。
- 处理豁免函数（豁免不强制补深度）。
