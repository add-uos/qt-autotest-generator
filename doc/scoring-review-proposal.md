# 评分标准对比与改进方案（讨论稿）

> 对象：`qt-autotest-generator/reference/inventory-schema.md` 的评分标准
> 参照：`qt-unit-test-generate/references/risk-prioritization.md` 的风险评分模型
> 目标：先厘清两套多级评分的优缺点与"AI 飘忽不定"的真正根因，再定改进方案，供讨论。

---

## 1. 问题陈述

你的核心关切：**"多级评分依赖 AI 评价，容易飘忽不定"**。

但读完两个 skill 的实现后，第一个要厘清的判断是：

> **"评分"本身基本不飘——两个 skill 的打分都是确定性 Python 脚本，同一份输入永远产出同一分数。**
> 真正飘的是**评分之后的"测试设计 + 质量自检"环节**，以及**权重/阈值本身是拍脑袋定的、从没拿真实 bug 校准过**。

所以本文先把"飘"定位准确，再谈 inventory-schema 要补什么。否则容易把"打分脚本"误改成"让 AI 重新打分"，反而更飘。

---

## 2. 两套评分体系速览

| 维度 | inventory-schema（autotest-generator） | risk-prioritization（unit-test-generate） |
|------|----------------------------------------|-------------------------------------------|
| 打分方式 | **加法因子表**：命中因子累加分（+3/+2/+1/−1） | **加权归一化公式**：R(f)=Σ wᵢ·归一化因子ᵢ，∈[0,100] |
| 分级数 | 3 级 high / mid / low | 4 级 P0 / P1 / P2 / P3 |
| 分级规则 | score≥3→high，≥1→mid，<1→low（绝对阈值） | R≥70→P0，≥40→P1，≥20→P2，<20→P3 |
| 归一化方式 | **绝对阈值**（complexity≥20 / ≥10 / ≥5） | **max 相对归一化**（÷全项目最大值）×4 个因子 |
| 因子来源 | 知识图谱 + 源码解析（MCP） | libclang AST + networkx + git log |
| Qt 感知 | ✅ dbus_slot / q_invokable / plugin_export | ❌ 无 Qt 契约概念 |
| 变更频率 | ❌ 无 | ✅ git log（已有项目） |
| 安全敏感 | ❌ 仅 destructive_name（delete/remove…） | ✅ auth/validate/parse/verify… → 直接 P0 |
| 公共接口 | 存 `access` 字段但**不参与打分** | ✅ signal=100/public=80/protected=40/private=20 |
| 扇出/调用链深度 | transitive_loop_depth（循环嵌套，非调用链） | fan_out + branch_depth（调用链） |
| 豁免机制 | ✅ scope_rules（3rdparty/moc_/ui_）正交干净 | ❌ 无，第三方/生成代码也被打分 |
| 人工复核 | ✅ review_queue + source(auto/suggested/manual) | ❌ 全自动，无 override |
| 变异测试 | ❌ 无（只看覆盖率） | ✅ Stage 6 变异得分=测试有效性的 ground truth |
| 权重可配 | ❌ 因子分值 hardcode 在脚本 | ✅ state.json 的 risk_weights |

一句话：**inventory-schema = Qt 专用、绝对阈值、加法、有人工兜底；risk-prioritization = 通用、相对归一化、加权、有变异测试兜底。** 两者优缺点几乎正交。

---

## 3. 同一份代码的评分对照（Calculator 实例，已核对源码）

用 `qt-unit-test-generate/example/src/calculator.cpp` 这 11 个方法，对照 `example/priority_report.json` 的真实输出 vs inventory-schema 规则的手算推演，**失真方向看得最清楚**：

| 方法 | 实际复杂度 | risk-prioritization 真实输出 | inventory-schema 推演 | 谁更准 |
|------|-----------|------------------------------|----------------------|--------|
| `add` | 1（`return a+b`） | **54.5 → P1** ❌ | score 0 → **low** ✅ | inventory |
| `sub` | 1（`return a-b`） | **54.5 → P1** ❌ | score 0 → **low** ✅ | inventory |
| `doMultiply` | 1，private | **48.5 → P1** ❌ | score 0 → **low** ✅ | inventory |
| `doDivide` | 2，private | **50.0 → P1** ❌ | score 0 → **low** ✅ | inventory |
| `Calculator()` | 1，构造 | **24.5 → P2** ❌ | score 0 → **low** ✅ | inventory |
| `compute` | ~6（switch×5） | 41.0 → P1（fan_out=100 对） | complexity≥5 +1 → **mid** ✅ | 持平 |
| `formatResult` | ~11 | 39.5 → P2 | complexity≥10 +2 → **mid** ✅ | 持平 |
| `validateInput` | ~10 | 38.0 → **P0**（靠 security 关键词救回来） | complexity≥10 +2，name 不命中 → **mid** ❌ | risk（特殊规则赢） |
| `parseNumber` | ~9 | 36.5 → **P0**（靠 security 关键词） | complexity≥5 +1 → **mid** ❌ | risk（特殊规则赢） |
| `classify` | 3 | 27.5 → P2 | score 0 → low | 持平 |
| `divide` | 2 | 26.0 → P2 | score 0 → low | 持平 |

**两个结论非常硬：**

1. **risk-prioritization 在小项目/简单函数上严重失真**：11 个函数里 5 个被判 P1，其中 `add/sub/doMultiply/doDivide/构造函数` 全是一行实现的 trivial 函数，却拿了 48~55 分。根因是 4 个 max 相对归一化因子——`add` 被调用 1 次=全项目最大 → centrality=100；所有函数同在一个文件、提交数相同=最大 → change_rate=100（白送 15 分）；depth=1=最大 → 100。**相对归一化在"图很小、everyone 都是 max"时塌缩成常数，失去区分度。**

2. **inventory-schema 漏掉"安全敏感"这类该升的函数**：`validateInput/parseNumber` 是输入校验，理应 high，但 inventory 的 `destructive_name` 只匹配 delete/remove/destroy…（降级方向），没有"校验/解析"（升级方向）。risk-prioritization 靠 `SECURITY_KEYWORDS` 特殊规则直接拉到 P0，这一手 inventory 没有。

> 附带发现一个独立 bug：priority_report 里 `compute` 的 complexity 记成 2，但源码是 switch 5 个 case，应为 ~6。说明 `build_call_graph.py` 的圈复杂度统计对 switch-case 计数有误——确定性脚本也会错，但那是脚本 bug，不是"AI 飘"，修脚本即可。

---

## 4. qt-unit-test-generate 多级评分的优缺点

### 优点

1. **连续分数 [0,100] 比三档更细**，排序 Top-N 选目标时更顺滑。
2. **权重可配**（state.json `risk_weights`），新/老项目自动重分配（无 git 历史时 change_rate=0、其他按比例放大）——思路对。
3. **change_rate 是真·经验信号**：git 历史是"哪些函数真出过问题"的最便宜代理，inventory 完全没有。
4. **public_surface 区分 access**：signal/public/protected/private 分档打分，比 inventory"存了 access 但不参与打分"合理。
5. **特殊规则兜底**：security-sensitive / complexity≥15 / pagerank top5% 直接拉级，弥补基础分偏弱的缺陷。
6. **变异测试（Stage 6）是抗漂的关键**：不管 AI 测试设计得多花哨，变异体存活率是客观 ground truth，逼着测试真正能杀缺陷。**这是 inventory 体系最大的缺口。**

### 缺点（重点是失真与漂移源）

1. **max 相对归一化在小项目/小图上塌缩**（见上节实例），trivial 函数被抬到 P1。这是结构性缺陷，不是调参能解决的。
2. **change_rate 用"文件提交数 ÷ 全项目最大提交数"**：同一文件的多个函数拿到相同 change_rate，且新文件/低活跃文件的函数也因 max 归一被相对放大。应改绝对阈值（如"近 90 天被改 ≥N 次"）。
3. **6 个权重 0.30/0.20/0.15/0.10/0.15/0.10 是拍脑袋的**，没有用真实 bug 数据回归校准。换一组权重，P0/P1 分布会大幅变化——这是"系统性偏差"，表现为"换人就飘"。
4. **特殊规则是关键词匹配**（auth/parse/validate…），假阳假阴都在：`parseConfig` 命中、`loadSettings` 漏掉；`checkMailbox` 命中但未必安全敏感。脆弱。
5. **branch_depth 定义"从入度为 0 的入口到 f 的最长路径"**：对库项目没有明确"入口"，且用 `all_simple_paths(cutoff=20)` 仍可能慢/不准。
6. **无 Qt 契约感知**：DBus 槽、Q_INVOKABLE、插件导出这些"跨进程/跨模块契约点"是最该重点测的，通用图分析识别不到。
7. **无豁免机制**：3rdparty / moc_ / ui_ / protobuf 生成代码也被打分进分母，污染统计。
8. **无人工复核**：全自动分级，模型判断错也没有 review_queue 让人纠偏——这恰恰是"飘了没人拦"。
9. **P0~P3 → Level 0~4 的映射靠 AI 在 Stage 3 落地**（SKILL.md 明说"Stage 3 不可脚本化"）：选哪级、写几个用例、用等价类还是基路径，全靠 AI 临场发挥——**这才是"飘忽不定"的主战场，而它不在评分层。**

---

## 5. "AI 评价飘忽不定"——根因定位

把两个 skill 的全流程按"确定性 vs AI 驱动"拆开，漂移点一目了然：

| 环节 | autotest-generator | unit-test-generate | 是否漂移 |
|------|--------------------|--------------------|---------|
| 图谱/AST 构建 | MCP 知识图谱（客观） | libclang（客观，有 switch 计数 bug） | ❌ 不漂（脚本 bug 另算） |
| **打分** | `scan_inventory.py` 纯规则 | `prioritize_targets.py` 纯公式 | ❌ 不漂（但权重/阈值是启发式 → 系统性偏差） |
| 分级判定 | score→level 规则 | R→P 规则 + 特殊规则 | ❌ 不漂 |
| **测试设计** | Mode 2 `test_writer`：AI 写用例 | Stage 3：AI 选 Level/技术/用例数 | ✅ **主漂移源** |
| 编译运行 | build_verifier 客观 | Stage 5 客观 | ❌ 不漂 |
| 覆盖率自检 | self_checker Step1：方法名差集+lcov% 客观 | validate_coverage 客观 | ❌ 不漂 |
| 断言强度自检 | self_checker Step2b：awk 计数规则（客观）+ bool 断言意图复核（AI，轻漂） | — | ⚠️ 轻漂 |
| **有效性验证** | ❌ 无（只看覆盖率） | ✅ Stage 6 变异测试（客观 ground truth） | — |
| 人工兜底 | review_queue + source=manual | ❌ 无 | — |

**结论三句话：**

1. **打分不飘**——两个 skill 的分数都是确定性脚本算的，复现性 100%。把"飘"归因到打分层是误诊。
2. **真正飘的是"打分→测试设计"这一跳**：AI 拿着 level/P 标签自由发挥写用例，质量随模型/温度/上下文波动。autotest-generator 在这层**没有 ground truth 约束**（只看覆盖率，而覆盖率可被 placeholder 断言刷高）；unit-test-generate 有变异测试兜底，相对稳。
3. **权重/阈值不飘但偏**：+3/+2/+1、0.30/0.20…、≥3→high、R≥70→P0 这些数从没拿真实 bug 数据校准过，是"系统性偏差"而非"随机漂移"——换作者就换分布，给人"飘"的错觉。

> 这个区分很重要：**治"漂"要靠 ground-truth 约束（变异测试）+ 人工复核；治"偏"要靠经验数据校准（git/bug 历史）。** 两味药不一样。

---

## 6. inventory-schema.md 评分标准需要补充的点

对照 risk-prioritization 的优点和真实 bug 信号，inventory-schema 缺这些（按性价比排序）：

### A. 缺经验信号（治"偏"，优先级最高）

1. **`change_rate` 因子**：从 git log 取"近 N 天/近 M 次提交中该方法所在文件的改动次数"，**用绝对阈值**（≥10→+2，≥20→+3），不要 max 相对归一。新项目无历史则该因子为 0，不重分配（inventory 是加法模型，天然不用重分配，比 risk-prioritization 的权重重分配更简单）。
2. **`bug_touched` 因子**（可选，强信号）：你已有 `pms-bugfix` skill 抓禅道 bug。若能拿到"某函数被近 K 个 bug 修复 commit 触及"的列表，直接 +3 升 high——这是比任何静态因子都强的"真出过事"信号。

### B. 缺"该升没升"的因子（治漏报）

3. **`security_sensitive` 因子**：补一个与 `destructive_name` 对称的 `suggested` 升级因子。函数名/文件名命中 `validate|verify|check|parse|sanitize|escape|auth|login|password|encrypt|decrypt|permission|token` → 进 review_queue，建议 high（默认 mid，等人确认）。直接抄 risk-prioritization 的 `SECURITY_KEYWORDS` 列表即可。
4. **`public_surface` 打分**：现在 `access` 字段存了却没参与评分，浪费。补：`public` 且是 `slot`/`signal`/`Q_INVOKABLE` → 已被 dbus_slot/q_invokable 覆盖（+3）；普通 `public` → +1；`protected` → 0；`private` → 0（不额外罚，靠 access 过滤即可）。

### C. 缺结构信号（中优先级）

5. **`fan_out` 因子**：方法出向调用数（trace_path outbound）≥ P75 → +1（mid-booster，和 in_degree 同档）。capture"交互复杂度"，risk-prioritization 有、inventory 没有。
6. **`call_chain_depth` 因子**：从公开入口到该方法的最长调用链深度，**用绝对阈值**（≥5 → +1）。注意和现有 `transitive_loop_depth`（循环嵌套深度，检测隐蔽 O(n²)）是两回事，别合并——后者是性能信号，前者是可测性/故障定位难度信号。

### D. 缺抗漂闭环（治"漂"，最重要但工程量大）

7. **变异测试作为 ground truth**（长期目标）：autotest-generator 目前只看 gcov 覆盖率，而覆盖率能被 `QVERIFY(true)` 类 placeholder 刷高（self_checker Step2b 虽然查"空断言/唯一 NO_FATAL"，但查不住"断言是真的但很弱"）。引入轻量变异测试：对 high 级方法注入几个变异体（AOR/ROR），存活率高 → 标记"测试可能无效" → 流转 test_writer 加强。这是把 unit-test-generate 最有价值的一块搬过来。
8. **review_queue 的人工 override 路径要坐实**：现在 `source=manual` 字段定义了，但 SKILL.md/inventory.md 里"人工复核"那段是交互式问答，没有持久化"谁、何时、为什么改成这个 level"的审计字段。补 `review_log: [{who, when, from, to, reason}]`，让 manual 覆盖可追溯、可复核。

### E. schema 小问题

9. `methods[].level` 在 exempt 时，schema 文档写 `null`，但 `scan_inventory.py` 实际写的是字符串 `"exempt"`（见 `build_inventory` 里 `level = "exempt"`）——**文档与实现不一致**，要么改脚本写 `null`、要么改 schema 写 `"exempt"`。建议统一为 `null`（exempt 由 `testable=false` 表达，level 语义只该有 high/mid/low）。
10. `methods[]` 实际产出的字段名（`qn`/`file`/`class_qn`/`exempt_reason`/`review_status`/`node_type`）和 schema 文档写的（`qualified_name`/`file_path`/`source`/`testable`/`usecase_count`）**对不上**，schema 描述的是设计意图、脚本是早期实现，需要一次对齐（这是 Mode 2 读取 inventory 的契约，不对齐会出错）。

---

## 7. 改进方案（核心，供讨论）

### 总原则

> **评分要可证伪，评价要有锚，AI 要有界。**

- **可证伪**：每个 level 判定都能追溯到具体因子 + 阈值，不靠 AI 主观。
- **有锚**：因子尽量锚到经验数据（git/bug 历史）或客观 ground truth（变异测试），少靠纯静态启发式。
- **有界**：AI 只在"测试设计"这一层发挥，且必须被 ground truth（变异/覆盖率）约束；分级、自检结论由规则裁定，AI 不越权。

### 三档措施

#### 第一档：低成本、立即做（改 inventory-schema + scan_inventory.py）

- 补 `security_sensitive`（suggested 升级，抄关键词表）→ 治 validateInput/parseNumber 这类漏报。
- 补 `public_surface` 打分（access 字段已存，加 +1/+0）。
- 补 `fan_out` ≥ P75 → +1（trace_path outbound 已有能力）。
- 修 §6.E 的 schema/实现不一致（level=exempt vs null、字段名对齐）。
- **保留 inventory 的绝对阈值加法模型**，不学 risk-prioritization 的 max 相对归一化——实例已证明后者在小项目失真。新加的 change_rate 也用绝对阈值。

预期效果：Calculator 实例里 `validateInput/parseNumber` 能进 review_queue 建议升 high，trivial 函数仍稳稳 low。零漂移引入。

#### 第二档：中等成本、本季度做（经验信号 + 闭环）

- 补 `change_rate` 因子（git log，绝对阈值）。
- 补 `review_log` 审计字段，坐实 manual override。
- **引入变异测试作为自检的一环**（从 unit-test-generate 的 `mutation_score.py` 移植精简版，只对 high 级方法跑几个变异体）：这是治"AI 测试设计飘"的唯一硬手段——AI 写得再花哨，变异体存活率高就是不行，客观可复现。
- self_checker 增加一条："high 级方法变异存活率 > 30% → 流转 test_writer 加强断言"。

预期效果：从"覆盖率达标即 pass"升级到"测试真能杀缺陷才 pass"，AI 漂移被 ground truth 兜住。

#### 第三档：高成本、视价值再做（校准 + 跨项目）

- 用 `pms-bugfix` 抓的历史 bug 数据做一次**权重/阈值回归校准**：统计"被 bug 触及的函数命中了哪些因子、分数分布如何"，反推最优阈值。把拍脑袋的 +3/+2/+1 换成数据驱动的。
- 跨项目对比：不同规模项目（calculator 11 函数 vs dde-file-manager 12000 函数）的分级分布是否合理，据此调阈值。
- 评估是否要把 risk-prioritization 的 pagerank/SCC/桥边等图分析因子纳入（大项目才有价值，小项目 over-engineering）。

### 明确不做的

- **不要把打分改成"让 AI 重新打分"**——会从"系统性偏差"退化成"随机漂移"，更糟。
- **不要学 risk-prioritization 的 max 相对归一化**——实例已证伪。
- **不要为了"4 级比 3 级细"就加 P0~P3**——inventory 的 high/mid/low + coverage gate 已够用，分级数应匹配"测试投入档位"而非"看起来精细"。

---

## 8. 待讨论的开放问题

1. **变异测试的成本可接受吗？** high 级方法可能几十上百个，每个注入 N 个变异体、各编译运行一轮，CI 时间会涨。要不要只对"high 且 change_rate 高"的子集跑？还是只在新增/变更时增量跑？
2. **change_rate 的窗口取多长？** 近 90 天 / 近 100 次提交 / 全历史？窗口太短老代码信号弱，太长噪声多。
3. **bug_touched 因子要不要做？** 依赖 pms-bugfix 抓的数据质量和函数级归因准确度（bug commit 经常 Touch 多个文件，归到具体函数难）。先验证数据可得性再决定。
4. **security_sensitive 关键词表谁来维护？** 抄 risk-prioritization 的列表起步，但 DDE 项目可能有特有关键点（如 dconfig/dconfigbin 相关）。是否需要一个项目可覆盖的关键词配置？
5. **第一档改动会改变现有 inventory 的分级结果吗？** 补 security_sensitive 会让一批 validate/check/parse 方法进 review_queue（默认 mid，等确认）。需要评估对已生成 inventory 的影响——可能需要全量重扫一次并人工过一遍 review_queue。
6. **要不要把 unit-test-generate 整个 skill 合并进 autotest-generator？** 两者重叠面越来越大（图谱、评分、覆盖率），但 unit-test-generate 用 Qt Test + libclang，autotest-generator 用 GTest + MCP 知识图谱，技术栈不同。建议先共享"评分模型 + 变异测试"两个 reference 文件，保持 skill 独立。

---

*本文为讨论稿，结论待评审。优先确认 §5 的根因定位是否认同——这决定后续是"改打分"还是"改测试设计的 ground truth 约束"。*
