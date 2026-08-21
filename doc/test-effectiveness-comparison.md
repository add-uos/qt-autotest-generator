# 测试有效性保障对比与优化方案（讨论稿）

> 对象：`qt-autotest-generator/`（GTest + MCP 知识图谱）vs `qt-unit-test-generate/`（Qt Test + libclang）
> 目标：**保证生成的测试真能发现问题**（测试有效性），给 `qt-autotest-generator` 提优化，并指出冗余。
> 配套：评分标准对比见同目录 `scoring-review-proposal.md`，本文聚焦"有效性保障链"。

---

## 0. 判定框架：什么叫"测试有效"

| 层次 | 含义 | 谁能证明 | 充分性 |
|------|------|---------|--------|
| L1 能编译能跑 | 测试不崩、不编译错 | build_verifier / validate_coverage | 必要不充分 |
| L2 覆盖率高 | 代码被执行到了 | lcov 行/分支/函数覆盖率 | 必要不充分（placeholder 断言可刷高） |
| L3 断言是真的 | 不是 `QVERIFY(true)` 充数 | 断言强度自检（awk 扫描） | 比 L2 强，仍不充分 |
| L4 断言能拦住缺陷 | 改坏代码后测试会失败 | **变异测试（mutation score）** | **有效性的客观代理** |

> **核心论点**：autotest-generator 做到了 L1~L3（而且比 unit-test-generate 做得好），但**停在 L3**——没有 L4 的变异测试，无法证明"这些断言真能拦住代码被改坏"。这是"保证能发现问题"的最后一道、也是唯一客观的一道关。

---

## 1. 有效性保障链全景对比

| 保障层 | autotest-generator | unit-test-generate | 谁强 |
|--------|--------------------|--------------------|------|
| 测试设计方法论 | `test-types.md`：等价类/边界值/分支/异常/负面/强异常安全/stub-vs-mock 决策树 + 反模式 A1-A12 + 最小清单 10 项 | `test-design-theory.md`：Level 0-4（Smoke/规范/结构/交互/故障注入） | **autotest**（超集且可执行，见 §3.1） |
| mock 深度分析 | `test_code_gen.md §4.0`：隐式依赖排查（路径/env/文件系统/子进程/时间/随机/单例） + 分支清单注释映射 | ❌ 无 | **autotest** |
| 断言强度 | `test_code_gen §4.1` 6 维度 + `self_checker §2b` awk 扫描（空断言/唯一NO_FATAL/唯一布尔/纯gMock/低断言<2）+ 图谱交叉查返回值/副作用 | ❌ 自己承认生成 `QVERIFY(true)` placeholder（SKILL.md Gotcha 12） | **autotest** |
| 环境隔离自检 | `self_checker §5b`：硬编码路径/env未还原/真实外部资源/stub未清理/单例污染 | ❌ 无 | **autotest** |
| 强异常安全 | `test-types §6.3`：负面用例验证状态未损坏 | ❌ 无 | **autotest** |
| 失败根因分类 | `failure_repairer.md`：compile/runtime/logic 源码缺陷标红 | ❌ 无 | **autotest** |
| 分级覆盖率门禁 | high 行90%+分支80%+函数100% / mid+low 行60%+函数100% | P0 分支90% / P1 80% | **autotest**（函数100% hard gate 更严） |
| Qt 契约感知 | dbus_slot/q_invokable/plugin_export 评分 | ❌ 无 | **autotest** |
| 人工复核 | review_queue + source=auto/suggested/manual | ❌ 全自动 | **autotest** |
| **变异测试** | ❌ **无** | ✅ `mutation_score.py` + `mutation-testing.md`，变异得分≥80% | **unit-test**（唯一能证 L4） |
| 错误猜测清单 | 边界值+负面测试（散在 test-types） | `test-design-theory §Level4` + `mutation-testing`：空值/数值边界/字符串边界/资源泄漏/并发的系统化清单 | **unit-test**（更系统） |
| 经验信号 | ❌ 无 change_rate | ✅ git log change_rate | **unit-test**（见 scoring proposal） |
| 编译验证 | build_verifier 强制+timeout+分类修复 | validate_coverage | 持平 |
| 迭代闭环 | 3 轮上限逐类闭环 | Stage 1-6 线性 | autotest 更扎实 |

**一句话**：autotest-generator 在"生成有效测试的静态保障"上**全面碾压** unit-test-generate（9 层 vs 0 层），唯独在"有效性的动态验证"（变异测试）上缺一块。unit-test-generate 反过来——静态保障很弱（连 placeholder 断言都承认），但靠变异测试兜住了有效性底线。

> 这是个有意思的反差：**unit-test-generate 生成质量差，但能发现自己差；autotest-generator 生成质量好，但没法证明自己好。** 后者更危险——"看起来都达标"的假象下，缺陷测试可能潜伏。

---

## 2. autotest-generator 的有效性缺口：为什么缺变异测试是硬伤

### 2.1 L3 通过 ≠ L4 通过（具体例子）

以 autotest 自己的断言强度规则为例，下列用例**完全合规**（2 个 EXPECT_*、精确值、有返回值断言）：

```cpp
TEST_F(CalculatorTest, Add_TwoPositive_ReturnsSum) {
    Calculator calc;
    int ret = calc.add(1, 2);
    EXPECT_EQ(ret, 3);              // ✅ 精确值
    EXPECT_EQ(calc.lastResult(), 3); // ✅ 状态断言
}
```

覆盖率 100%、断言强度自检通过。但如果开发者把 `return a + b` 写成 `return a + b + (a*b - a*b)`（逻辑等价但意图错误，或重构时手滑），这个测试照样过——因为它只测了 `(1,2)` 一组输入。

再比如边界盲区：`divide(INT_MIN, -1)` 溢出、`parseNumber("99999999999999999999")` 溢出，这些 autotest 的 `test-types §2.1` 提了"数值边界必测"，但**没有任何机制验证 AI 真的测了**——分支清单注释是 AI 自己写的，self_checker 只查"有没有断言"，不查"边界值齐不齐"。

**只有变异测试能补这个洞**：它主动把 `a + b` 变成 `a - b`、把 `b == 0` 变成 `b != 0`、把 `return 0` 变成 `return 1`，然后看测试抓不抓得到。抓不到 = 测试有缺口 = 真可能漏掉缺陷。

### 2.2 为什么覆盖率+断言强度不够

| 失效类型 | 覆盖率能抓 | 断言强度能抓 | 变异测试能抓 |
|---------|-----------|-------------|-------------|
| 整个方法没被调 | ✅ | ✅（空断言） | ✅ |
| 分支没走到 | ✅（分支覆盖） | ⚠️（只查断言数，不查分支） | ✅ |
| 断言是假的（NO_FATAL 充数） | ❌ | ✅ | ✅ |
| 断言是真的但期望值错 | ❌ | ❌ | ✅（变异后期望不匹配→测试失败→killed） |
| 输入空间有盲区（只测了 happy path） | ❌ | ❌ | ✅（边界变异体存活→暴露盲区） |
| 逻辑等价变异（a+b+0） | ❌ | ❌ | ⚠️（等价变异体无法杀死，需人工识别） |

> 变异测试的局限：**等价变异体**（不改变行为的变异，如 `a+b` → `a+b+0`）无法被任何测试杀死，需人工剔除。unit-test-generate 的 `mutation_score.py` 也承认"当前实现标记为 survived，不自动识别等价变异体"。这是变异测试的固有成本，移植时要接受。

---

## 3. autotest-generator 已有的有效性优势（保持，别退化）

这些是 unit-test-generate 完全没有的，移植变异测试时**不能丢**：

### 3.1 test-types.md ⊃ test-design-theory.md（方法论超集）

`test-types.md` 的反模式表 A1-A12、stub-vs-mock 决策树（§7.6）、最小清单（§8）、强异常安全（§6.3）比 unit-test-generate 的 `test-design-theory.md` 更可执行。unit-test-generate 的 Level 0-4 分级框架好，但落地细节薄。**不要为了"统一"而退化成 unit-test-generate 的方法论。**

### 3.2 self_checker §2b 断言强度自检（awk 扫描）

这是 autotest 独有的**静态有效性门禁**——在变异测试之前就挡住假断言。变异测试成本高（每变异体一次编译），先用 awk 静态筛掉空断言/唯一 NO_FATAL，能减少变异测试要跑的用例量。**两者是互补的，不是替代**：awk 查"断言形态"，变异测试查"断言效力"。

### 3.3 self_checker §5b 环境隔离自检

变异测试假设"测试在干净环境可复现"。如果测试硬编码 `/tmp/xxx`、依赖测试机时区，变异体跑出来结果不可信（可能是环境差异而非变异被杀）。5b 的环境隔离自检是变异测试结果可信的前置条件。

### 3.4 MCP 知识图谱 vs libclang 正则定位

`mutation_score.py` 的 `find_function_range()` 用正则匹配函数定义行，对重载、多行定义、宏展开容易出错。autotest 有 MCP `get_code_snippet(qualified_name)` 能精确拿函数源码和行范围，**移植时用 MCP 替代正则定位，准确率更高**。

---

## 4. 冗余指出

### 4.1 跨 skill 冗余（若两 skill 共存）

| 文件 | 状态 | 处理建议 |
|------|------|---------|
| autotest `test-types.md` ⊃ unit-test `test-design-theory.md` | 等价类/边界值/分支/异常/负面高度重叠，autotest 是超集 | unit-test-generate 的 `test-design-theory.md` 可标记为"方法论以 autotest test-types.md 为准"，仅保留 Level 0-4 分级框架和 Level 4 故障注入清单（autotest 没有） |
| unit-test `qt-test-patterns.md` | 讲 Qt Test（QVERIFY/QCOMPARE/QTest::keyClicks），autotest 用 GTest | **框架错配的冗余**——对 autotest 无效。其中的"信号测试/Mock/GUI 测试"思路，autotest 已用 GTest+gMock+QSignalSpy 在 test-types §7 覆盖 |
| unit-test `risk-prioritization.md` vs autotest `inventory-schema.md` | 评分模型重叠 | 见 `scoring-review-proposal.md`，建议共享一份评分模型 reference |
| unit-test `code-graph-analysis.md` | libclang AST + networkx | autotest 用 MCP 知识图谱，技术栈不同，**不算冗余**（各有适用场景） |

### 4.2 autotest-generator 内部（轻微，可接受）

| 位置 | 现象 | 判定 |
|------|------|------|
| `test-types.md §9` 反模式 A1/A6 ↔ `self_checker §2b` SOLE_NO_FATAL/SOLE_GMOCK | 一个说"别这么写"，一个说"查有没有这么写" | **合理分层**（生成时预防 + 验证时检查），不算冗余 |
| `test_code_gen §4.1` 断言 6 维度 ↔ `test-types §8` 最小清单第 10 项 | 都讲断言强度 | 轻微重复，可合并表述 |
| `build_verifier §2` 错误分类表 ↔ `failure_repairer §3` 修复策略表 | 几乎逐行重复 | **真冗余**，可抽成共享的 `reference/compile-error-catalog.md`，两边引用 |

---

## 5. 优化方案：移植变异测试（核心）

### 5.1 移植范围与改造点

unit-test-generate 的 `mutation_score.py` 框架无关的部分**可直接复用**，只需改三处对接 autotest 技术栈：

| 模块 | unit-test-generate 原实现 | autotest 移植改造 |
|------|--------------------------|-------------------|
| 变异算子（AOR/ROR/LOR/CRC/RVF/SDL） | 框架无关，纯源码文本操作 | **直接复用** |
| `in_string()` 状态机 / `_is_unary_op()` | 框架无关 | **直接复用** |
| 源码安全恢复（atexit + signal + `_PENDING_RESTORES`） | 框架无关 | **直接复用** |
| 函数定位 `find_function_range()` | 正则匹配，易错 | **改用 MCP `get_code_snippet(qn)`** 拿精确行范围 |
| 编译 | `make` 全量 | **改 `cmake --build build-${test_dir} --target test_<classname>`**（见 5.2 成本优化） |
| 测试运行 | Qt Test 二进制，看 `FAIL!` | **改 GTest 二进制**，看退出码非0 或 `--gtest_output=xml` 解析 |
| 目标选择 | `--all-p0` | **改 `--all-high`**：从 `.ut-inventory.json` 取 `level==high` 的 testable 方法 |
| 触发时机 | Stage 6 独立 | **嵌入 self_checker**：覆盖率门禁通过后，对 high 方法跑变异，存活率高→流转 test_writer |

### 5.2 成本优化（unit-test-generate 自己没解决的关键问题）

`mutation_score.py` 的 `compile_and_test()` 每个变异体都执行：

```python
# 变异前：make 全量编译
make -j$(nproc)
# 变异后恢复：make clean && make 全量重编   ← 灾难
make clean
make -j$(nproc)
```

**50 个变异体 = 100 次全量编译**。对 dde-file-manager（12000 方法）这种大项目，一次全量编译几分钟到十几分钟，100 次 = **几小时到一整天**，CI 完全不可行。这是 unit-test-generate 的 `mutation_score.py` 只在 example 小项目跑得动的根本原因。

移植时必须改成**单文件增量编译**：

```bash
# 变异只改了一个 .cpp，只需重编该 .o + 链接 test target
cmake --build build-${test_dir} --target test_<classname>   # ninja/make 会增量
```

变异写入新文件内容后，构建系统检测到 .cpp 比 .o 新，自动只重编该文件 + 链接。**无需 clean**（unit-test-generate 的 clean 是过度保守，源于 Gotcha 7 对 stale .o 的恐惧——但 make/ninja 的时间戳检测足以避免 stale，clean 是误判）。

额外保险：
- **禁用 ccache**（`CCACHE_DISABLE=1`）：ccache 可能因预处理器 hash 命中跳过重编，导致变异没生效。这是变异测试特有的坑。
- **独立最小 build 目录**：只编译"被测类源码 + 该类测试 + stub-ext"，不拉全项目依赖。用 `cmake -DBUILD_MINIMAL=ON` 或单独的 test-only build。这样单文件重编 + 链接在秒级。

预估成本：单文件重编 + 链接 ≈ 2-5 秒/变异体。20 变异体/方法 × 10 个 high 方法 = 200 次 × 3 秒 = **10 分钟**，可接受。

### 5.3 触发策略（控制跑多少）

不要对所有方法跑变异，按成本/价值分层：

| 方法 level | 是否跑变异 | 变异体上限 | 理由 |
|-----------|-----------|-----------|------|
| high | ✅ 跑 | ≤20（比 unit-test 的 50 保守） | 核心，变异测试价值最高 |
| mid | ❌ 不跑（成本不值） | — | 覆盖率门禁 + 断言强度自检足够 |
| low | ❌ 不跑 | — | 只求语句覆盖 |
| 新增/变更的 high | ✅ 跑（增量） | ≤20 | 只对 reconcile 检出的变更方法跑，CI 增量友好 |

**闭环**：存活变异体（survived）→ 流转 `test_writer` 加强断言/补边界用例 → 重跑变异 → 直到存活率 < 阈值或迭代上限。这与现有"3 轮闭环"机制天然契合。

### 5.4 等价变异体处理（诚实降级）

移植时**明确不自动识别等价变异体**（unit-test-generate 也没做），但要比它诚实：
- 存活变异体输出清单（文件:行:算子:变异描述），交 test_writer 分析"是真缺口还是等价变异"
- test_writer 判定为等价变异的，在报告里标 `equivalent`，不计入分母
- 变异得分 = killed / (killed + survived_non_equivalent)
- 不像 unit-test-generate 那样把等价变异当 survived 混入分母（会压低得分，误判测试无效）

### 5.5 与现有 self_checker 的集成

在 `self_checker.md` Step 1（覆盖率自检）和 Step 2b（断言强度自检）**通过后**，新增 Step 1c：

```markdown
#### 1c. 变异测试（仅 high 级方法，有效性 ground truth）

复用 build_verifier 7c 的覆盖率快照，取 level==high 且 testable 的方法集，
调 scripts/mutation_score.py --inventory .ut-inventory.json --class <classname>。

判定：
- 变异得分 ≥ 80%（可配）→ pass
- 变异得分 < 80% → 取存活变异体清单，流转 test_writer 加强（传入 survived_mutants 列表）
- 编译失败变异体不计入分母（等同 unit-test-generate 的 compile_failed）

注意：变异测试在覆盖率门禁 + 断言强度自检都通过后才跑——
先用静态门禁筛掉假断言，再跑昂贵的动态验证，避免在无效测试上浪费编译。
```

---

## 6. 优化方案：吸收 Level 4 错误猜测清单（轻量，立即可做）

`mutation-testing.md` 的 Level 4 错误猜测清单比 autotest `test-types.md` 散落的边界值更系统，建议补进 test-types.md 作为 §10：

| 清单项 | autotest 现状 | unit-test Level4 | 建议 |
|--------|--------------|------------------|------|
| 空值/无效输入 | §6.2 有（空串/空容器/null） | 更全（+空对象 QObject()） | 补空对象 |
| 数值边界 | §2.1 有（INT_MAX/MIN/0/-1） | +浮点 NaN/Infinity/±0.0/DBL_MIN/MAX | **补浮点边界**（autotest 完全没提，Qt 数值处理常见坑） |
| 字符串边界 | §2.2 有（长度/字符集） | +\0/控制字符/emoji/混合编码 | 补 emoji 和混合编码 |
| 资源泄漏 | ❌ 无 | 重复调用/异常路径释放/信号断开后安全 | **新增**（autotest 缺） |
| 并发安全 | ❌ 无 | 非重入函数重复调用/跨线程 | **新增**（autotest 缺，Qt 信号槽跨线程常见） |

这条不依赖变异测试，可独立先做，立即提升"测什么输入"的完备性。

---

## 7. 优化优先级总表

| 优先级 | 措施 | 价值 | 成本 | 依赖 |
|--------|------|------|------|------|
| P0 | **移植变异测试**（§5，含成本优化） | 治"有效性"根因，唯一 L4 证明 | 高（移植+调编译） | — |
| P1 | 吸收 Level 4 错误猜测清单（§6） | 补输入空间盲区 | 低（改 test-types.md） | 无 |
| P1 | 抽 `compile-error-catalog.md` 消除 build_verifier/failure_repairer 重复（§4.2） | 减维护负担 | 低 | 无 |
| P2 | 跨 skill 标注方法论权威源（§4.1） | 若两 skill 共存避免漂移 | 低 | 需跨 skill 协调 |
| P3 | 等价变异体半自动识别（§5.4） | 提升变异得分准确性 | 中 | 依赖 P0 |

---

## 8. 开放问题（待讨论）

1. **变异测试跑在哪个 build 目录？** 复用 `build-${test_dir}` 还是独立 `build-mutation/`？复用会污染覆盖率 .gcda（变异体跑过会写覆盖率数据），独立要多配一个 build。倾向独立 + `--target test_<classname>` 单类编译。
2. **变异得分阈值定多少？** unit-test-generate 定 80%。autotest 的 high 级已是"最该测"的，是否提到 85%？还是分级：dbus_slot/q_invokable 的 high 要 85%，complexity_high 的 75% 即可？
3. **存活变异体闭环几轮？** 现有"3 轮上限"是针对编译/覆盖率闭环的。变异补强是否占用同一预算，还是单开 2 轮变异预算？倾向单开，避免覆盖率闭环被变异挤掉。
4. **等价变异体谁判？** test_writer 判"这是等价变异"可信吗？还是必须人工？建议 test_writer 初判 + 报告标红让人复核，不自动从分母剔除。
5. **变异测试是否纳入 Iron Law？** 现在 Iron Law 11 条没有有效性验证。是否新增"#12 high 级方法变异得分 ≥ 阈值方可标 done"？这会把变异从"可选"变成"强制"，成本上要配套 CI 时间预算。
6. **unit-test-generate 要不要也吸收 autotest 的断言强度自检？** 反向优化：unit-test-generate 连 placeholder `QVERIFY(true)` 都挡不住，移植 autotest 的 awk 扫描能给它立刻提效。但这属于给 unit-test-generate 提优化，不在本次范围——提一下供你判断两个 skill 的协同方向。

---

## 9. 结论

- **autotest-generator 的有效性保障在静态层（L1-L3）已经是两个 skill 里更好的**，9 项 unit-test-generate 完全没有的优势要保留。
- **唯一硬缺口是变异测试（L4）**——这是"保证能发现问题"的最后一道客观关。覆盖率+断言强度只能证明"测了"，不能证明"测对了"。
- **移植变异测试的最大工程风险是编译成本**：unit-test-generate 的 `make clean && make` 全量重编在大项目不可行，必须改成单文件增量 + 禁 ccache + 独立最小 build 目录。
- **冗余主要是跨 skill 的方法论重复**（test-types ⊃ test-design-theory），以及 autotest 内部 build_verifier/failure_repairer 的错误表重复——后者可抽共享文件消除。
- **Level 4 错误猜测清单可独立先吸收**（补浮点边界/资源泄漏/并发安全），不依赖变异测试，立即提升输入空间完备性。

> 建议讨论顺序：先定 §8 的阈值/闭环/是否纳入 Iron Law（决定变异测试的强制程度），再定 §5.2 的 build 策略（决定工程可行性），最后再谈跨 skill 冗余清理。
