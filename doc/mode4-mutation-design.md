# Mode 4 · 变异测试模式设计方案（讨论稿）

> 状态：**已落地**（`reference/mutation_testing.md` + `scripts/mutation_score.py`，v3.2.0）。本文为设计归档，执行以落地文档为准。
> 配套：有效性对比见 `doc/test-effectiveness-comparison.md`；评分标准见 `doc/scoring-review-proposal.md`
> 技术实现细节（变异算子、成本优化、移植改造点）见 `doc/test-effectiveness-comparison.md §5`，本文聚焦**模式定位与约束**。

---

## 0. 为什么单独成模式（定位）

变异测试与现有三个模式有**本质差异：它会修改项目源码**（注入变异体）。而 autotest-generator 的 Iron Law #7 明确"不修源码"，Mode 2 的整个闭环（依赖追踪→生成→编译→自检→修复→补全）都建立在不碰源码之上。

若把变异测试塞进 Mode 2 的 self_checker：
- Iron Law #7 在自检环节破例，边界混乱——"什么时候能改源码、改了算不算违规"说不清
- 变异的源码恢复风险泄漏进 Mode 2 的编译产物（build-${test_dir} 的 .o/.gcda 被污染）
- 占用 3 轮迭代预算，挤压编译/覆盖率闭环
- "可选增强"无法和 Iron Law 硬门禁共存

**单独成 Mode 4 把"改源码"这个危险动作隔离在一个独立、可选、有专属安全约束的模式里**，现有三个模式的铁律和闭环完全不受影响。

### 与其他模式的关系

| 模式 | 改源码？ | 改测试？ | 强制？ | 本模式定位 |
|------|---------|---------|--------|-----------|
| Mode 1 探测 | ❌ | ❌ | — | 建表 |
| Mode 2 编写 | ❌ | ✅ 生成 | ✅（用户触发即执行） | 生成测试 |
| Mode 3 采集 | ❌ | ❌ | — | 只读覆盖率 |
| **Mode 4 变异** | ⚠️ **临时改+必恢复** | ❌（主流程）/ ✅（可选补强子步骤） | ❌ **可选增强** | 验证测试有效性 |

**前置依赖 Mode 2**：Mode 4 在"已写好的测试"基础上运行——没有测试就没东西可变异验证。`test-effectiveness-comparison.md §0` 的 L4 层（断言能拦住缺陷）只对已有测试有意义。

**不触发于**：项目还没写过测试（应先 Mode 2）、只要覆盖率不要有效性验证（走 Mode 3）、非 high 级方法的有效性验证（成本不值）。

---

## 1. 触发条件

**触发**（用户显式）：
- 变异测试、mutation testing、mutation score
- 验证测试有效性、看测试能不能发现问题、测试够不够好
- high 级方法有效性、变异得分
- "这些测试真能拦住 bug 吗"

**自动触发**（可选，默认关）：Mode 2 全类 done 后，用户在 SKILL 配置里开启 `auto_mutation_on_mode2_done`，则对 high 级方法自动跑 Mode 4。**默认关闭**——保持可选增强定位。

**前置检查**：
1. `.ut-inventory.json` 存在（取 high 级 testable 方法作为变异目标）
2. Mode 2 已产出可编译可运行的测试（`build-${test_dir}/` 存在且 target `test_<classname>` 可编）
3. reconcile 通过（源码与 inventory.base_sha 一致；不一致先对账，避免变异跑在旧代码上）

> 若前置不满足 → 提示"请先执行 Mode 2 生成测试"，不降级到从零生成。

---

## 2. 源码安全约束（Mode 4 的核心铁律，替代 Iron Law #7）

Mode 4 是**唯一豁免 Iron Law #7（不修源码）的模式**，但受等价的**源码安全四铁律**约束，绝不弱于 #7：

### 源码安全四铁律

1. **备份先于修改** —— 任何源码写入前必须 `cp` 到 `.mutation_backup`；备份不存在不开始变异
2. **恢复必有机制** —— `atexit` + `SIGTERM` + `SIGINT` 三重注册恢复函数；进程被 kill 也恢复
3. **变异不落盘** —— 变异后的源码**绝不 commit、绝不 push**；`code_committer` 在 Mode 4 禁用
4. **退出必校验** —— 模式结束时强制 `git diff --exit-code`，非空则硬终止 + 警告用户手动恢复

### 实现要点（复用 unit-test-generate 现成代码）

`qt-unit-test-generate/scripts/mutation_score.py` 已实现：
- `_PENDING_RESTORES` 全局列表记录 `(backup_path, source_file)` 对
- `atexit.register(_restore_on_exit)` + `signal.signal(SIGTERM/SIGINT, _signal_handler)`
- 每个变异-恢复周期在 `finally` 块移除 pending 条目

**直接复用**，补一条 unit-test-generate 没有的：**退出时 `git diff` 校验**（unit-test-generate 没有，因为它是独立 skill 不嵌入版本控制闭环；autotest 有 code_committer，必须挡住变异源码被误提交）。

### 与 Iron Law #7 的关系表述

```
Iron Law #7（不修源码）在 Mode 4 内部由"源码安全四铁律"替代：
- #7 的意图是"不污染用户源码" → 四铁律用"备份+恢复+不落盘+退出校验"达成同一意图
- Mode 1/2/3 仍严守 #7 原文，不受影响
- SKILL.md 的 Iron Laws 列表加注：#7 适用于 Mode 1/2/3；Mode 4 适用源码安全四铁律
```

---

## 3. 工作步骤

### Step 0. 前置检查与目标选择

```python
inventory = read_json(f"{test_dir}/.ut-inventory.json")
# 变异目标：level==high 且 testable 的方法（mid/low 不跑，成本不值）
targets = [m for m in inventory["methods"]
           if m["level"] == "high" and m["testable"]]

# 可选：用户指定单方法/单类 → 收窄 targets
# 可选：reconcile 增量模式 → 只对变更的 high 方法跑
```

> **只对 high 级跑**。理由：变异测试成本与价值成正比，high 级（dbus_slot/q_invokable/插件导出/高复杂度）是最该验证有效性的；mid/low 靠 Mode 2 的覆盖率门禁 + 断言强度自检足够。

### Step 1. 独立 build 目录准备（不污染 Mode 2/3 产物）

```bash
# 方案 A（推荐）：独立 build-mutation/，从头配置只编单类
mkdir -p build-mutation && cd build-mutation
cmake ${PROJECT_PATH} -DBUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Debug \
      -DCMAKE_CXX_COMPILER_LAUNCHER=   # 禁 ccache，避免变异被缓存跳过
cmake --build . --target test_<classname>   # 首次：编依赖+测试；后续变异只重编 1 个 .cpp

# 方案 B（备选，快）：快照 Mode 2 的 build-${test_dir}
cp -r build-${test_dir} build-mutation   # 复用已编译 .o，变异循环秒级
# 风险：CMake build 目录重定位可能失效，需验证
```

**为什么独立 build**：变异体跑测试会写 `.gcda` 覆盖率数据，污染 Mode 3 的覆盖率采集。复用 `build-${test_dir}` 会让 Mode 3 报告失真。独立 build 物理隔离。

**为什么禁 ccache**：ccache 按预处理器 hash 命中可能跳过重编，导致变异体没真正编译进去，测试跑的是原代码 → 假"存活"。这是变异测试特有的坑，unit-test-generate 没提。

### Step 2. 逐方法变异循环

对每个 high 级方法：

```python
# 2a. MCP 精确定位函数行范围（替代 unit-test-generate 的正则匹配，更准）
snippet = codebase_memory_mcp.get_code_snippet(qualified_name=method.qn)
func_start, func_end = snippet.line_range   # MCP 返回，不用正则猜

# 2b. 生成变异体（复用 unit-test-generate 的算子函数）
mutants = []
mutants += generate_aor_mutants(lines, func_start, func_end)   # 算术 +-*/
mutants += generate_ror_mutants(lines, func_start, func_end)   # 关系 <>=
mutants += generate_lor_mutants(lines, func_start, func_end)   # 逻辑 &&||
mutants += generate_crc_mutants(lines, func_start, func_end)   # 常量 0↔1
mutants += generate_rvf_mutants(lines, func_start, func_end)   # 返回值
# 上限 20/方法（比 unit-test-generate 的 50 保守，控成本）
mutants = mutants[:20]

# 2c. 逐变异体：备份→变异→增量编译→跑测试→恢复→记录
for mutant in mutants:
    backup = source_file + ".mutation_backup"
    shutil.copy2(source_file, backup)
    _PENDING_RESTORES.append((backup, source_file))
    try:
        write_mutated(source_file, mutant)        # 写入变异
        # 增量编译：只重编被改的 .cpp + 链接 test target（秒级，非全量）
        rc = cmake --build build-mutation --target test_<classname>
        if rc != 0: record(mutant, "compile_failed"); continue
        # 跑 GTest：退出码非0 或 gtest XML 有 failure = killed
        rc = ./build-mutation/.../test_<classname> --gtest_output=xml:...
        status = "killed" if rc != 0 or has_failure(xml) else "survived"
        record(mutant, status)
    finally:
        shutil.move(backup, source_file)          # 必恢复
        _PENDING_RESTORES.remove((backup, source_file))
```

**关键改造点**（相对 unit-test-generate）：
- 函数定位：正则 → MCP `get_code_snippet`（准）
- 编译：`make clean && make` 全量 → `cmake --build --target` 单文件增量 + 禁 ccache（快 100 倍）
- 测试判定：Qt Test `FAIL!` → GTest 退出码 / gtest XML（框架适配）
- 目标来源：`priority_report.json` P0 → `.ut-inventory.json` high（数据源适配）

### Step 3. 变异得分计算

```
变异得分 = killed / (killed + survived_non_equivalent)
```

- `compile_failed` 不计分母（变异体本身编译不过，不是测试的问题）
- `equivalent`（等价变异，不改变行为）不计分母——但**不自动识别**，标"疑似等价"交人工/test_writer 复核
- 阈值默认 **85%**（可配，比 unit-test-generate 的 80% 更严），**未达阈值不阻塞任何 Mode 2 的 done 状态**（可选增强）

### Step 4. 存活变异体分析与报告

产出：
- `build-mutation/mutation_report.md`：人读报告（按方法/按算子的杀死率 + 存活清单 + 建议）
- `build-mutation/mutation_report.json`：机读（每变异体 id/算子/行/状态/输出片段）

存活变异体清单格式（供补强）：
```
| 方法 | 文件:行 | 算子 | 变异 | 状态 | 判定 |
|------|--------|------|------|------|------|
| Calculator::divide | src/calc.cpp:18 | ROR | == → != | survived | 疑似测试缺口 |
| Calculator::add | src/calc.cpp:7 | AOR | + → - | survived | 疑似等价变异 |
```

### Step 5. 退出校验（源码安全四铁律 #4）

```bash
git diff --exit-code
# 非空 → [FATAL] 源码未恢复干净，请手动检查 .mutation_backup 残留
# 空 → ✅ Mode 4 结束，源码无改动
```

### Step 6.（可选）补强反馈

存活变异体（疑似测试缺口）→ **建议**用户回 Mode 2 用 `incremental_updater` 补强，或 Mode 4 内可选调 `test_writer` 针对性补用例。

> 这一步**默认只出建议清单不自动改测试**，避免 Mode 4 越权修改 Mode 2 产物。是否自动补强列为开放问题（§6.1）。

---

## 4. 成本控制

| 措施 | 效果 |
|------|------|
| 只对 high 级跑 | 控制目标数量（通常全项目 5-15% 的方法） |
| 变异体上限 20/方法 | 单方法变异体数封顶（unit-test-generate 是 50） |
| 单文件增量编译 | 每变异体 2-5 秒（vs 全量几分钟） |
| 禁 ccache | 避免假存活（编译被跳过） |
| 独立最小 build | 不污染 Mode 2/3，且只编 test target 不编全项目 |
| 增量模式（可选） | 只对 reconcile 检出的变更 high 方法跑，CI 友好 |

**预估**：10 个 high 方法 × 20 变异体 × 3 秒 = **10 分钟**，可接受。
对比 unit-test-generate 原版：50 变异体 × 100 次全量编译 × 数分钟 = **数小时~一天**，不可行。

---

## 5. 与现有机制的关系（解耦点）

| 现有机制 | Mode 4 的关系 |
|---------|--------------|
| Iron Law #7 不修源码 | Mode 4 豁免，由"源码安全四铁律"替代（等价约束） |
| Iron Law #10 3 轮迭代上限 | **不占用**——Mode 4 有自己的预算（变异体数），不抢 Mode 2 闭环 |
| Iron Law #11 usecase_count 实时更新 | **不更新**——Mode 4 不产生新用例（除非 §6.1 自动补强开启） |
| Mode 2 self_checker 覆盖率门禁 | **不干扰**——Mode 4 不改 Mode 2 的 done 判定 |
| Mode 3 覆盖率采集 | **不污染**——独立 build 目录，.gcda 隔离 |
| code_committer | **禁用**——变异源码绝不提交 |
| reconcile | **复用**——Mode 4 启动前对账，确保变异跑在当前代码上 |

**关键定位**：Mode 4 是**只读验证 + 产出建议**的模式（从最终状态看：源码 git diff 为空、测试不变、inventory 不变），不改变任何 Mode 2 产物的状态。它的产出是"有效性报告 + 补强建议清单"，决策权在用户。

---

## 6. 开放问题（待讨论）

### 6.1 存活变异体发现后，Mode 4 要不要自动补强测试？ ✅ 已定

**决定：方案 A（只出建议）**。存活清单写入报告，用户自行回 Mode 2 补强。Mode 4 纯验证，边界最干净——不改 Mode 2 产物，不越权。

### 6.2 独立 build 目录用方案 A（从头配）还是 B（快照 Mode 2）？ ✅ 已定

**决定：方案 A（增量更新）**。独立 build 目录 + `cmake --build --target test_<classname>` 单文件增量编译。首次配置后，每个变异体只重编被改的 .cpp + 链接，秒级。不用 `make clean`（unit-test-generate 的 clean 是过度保守）。

### 6.3 变异得分阈值分级还是统一？ ✅ 已定

**决定：统一 85%**（比 unit-test-generate 的 80% 更严）。autotest-generator 的 high 级已是"最该测"的核心方法，阈值应高。积累数据后再考虑分级（dbus_slot/q_invokable 可提至 90%）。

### 6.4 变异目标范围

- 只 high（默认）
- high + 用户显式指定的特定方法（即使 mid，用户觉得关键想验证）

倾向支持 `--function` / `--class` 显式指定，覆盖默认 high 筛选。

### 6.5 等价变异体谁判？

- test_writer 初判 + 报告标"疑似等价"让人复核（不自动从分母剔除）
- 还是必须人工？

倾向 test_writer 初判标"疑似"，**不自动剔除分母**（保守，宁可得分低点也不要把真缺口误判为等价）。unit-test-generate 是把等价当 survived 混入分母（压低得分，误判测试无效），autotest 不重蹈。

### 6.6 SKILL.md 集成点

- 模式表加一行 Mode 4
- 触发条件加"变异测试/mutation/验证测试有效性"
- Iron Laws 加注：#7 适用于 Mode 1/2/3，Mode 4 适用源码安全四铁律
- 新增 `reference/mutation_testing.md`（执行步骤详述）
- 新增 `scripts/mutation_score.py`（移植自 unit-test-generate + 改造）

这部分是实现阶段的事，确认上述设计后再动。

---

## 7. 总结

Mode 4 的设计核心是**三个隔离**：

1. **源码修改隔离** —— 唯一改源码的模式，但"备份+恢复+不落盘+退出校验"四铁律保证最终 git diff 为空，等价于不修源码
2. **有效性验证隔离** —— 不干扰 Mode 2 的 done 判定、不占 3 轮预算、不污染 Mode 3 覆盖率，纯可选增强
3. **成本隔离** —— 独立 build + 单文件增量 + 禁 ccache + 只跑 high 级，把 unit-test-generate 不可行的全量重编改到分钟级

它补的是 `test-effectiveness-comparison.md §0` 的 L4 层（断言能拦住缺陷）——autotest-generator 在 L1-L3 已经很强，缺的就是这一道客观有效性验证。做成独立可选模式，既补了缺口，又不破坏现有铁律和闭环。

> §6.1/6.2/6.3 已定并实现（只出建议 / 增量编译 / 阈值 85%）。脚本已完成 3 函数实测（见 `reference/mutation_testing.md` 成本控制表）。剩余开放问题 6.4（目标范围，当前支持 `--source`+`--function` 显式指定）待定、6.5（等价变异判定，当前策略为不自动识别、全部存活列入缺口清单由人工复核）待积累数据后评估。
