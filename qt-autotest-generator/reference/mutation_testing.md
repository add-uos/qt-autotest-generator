# 变异测试（Mode 4）

> 前置条件：Mode 2 已产出可编译可运行的测试（`build-${test_dir}/` 存在且 `test_<classname>` target 可编），`.ut-inventory.json` 存在。
> 脚本：`scripts/mutation_score.py`。

## 概述

对**已写好的测试**注入变异体（人为缺陷），验证测试能否捕获——补的是有效性链条的 L4 层（断言能拦住缺陷），而非 L1-L3（编译/覆盖率/断言强度）。

Mode 1-3 已确保测试"看起来完整"（覆盖率高、断言强），但**覆盖率 ≠ 有效性**：一个 `EXPECT_TRUE(result.contains("56"))` 的弱断言能让行覆盖率 100%，却拦不住 `+` → `-` 的变异。Mode 4 用客观的"变异杀死率"度量测试的真实捕获力。

**核心特性**：
- **唯一改源码的模式**——临时注入变异体，受"源码安全四铁律"约束（替代 Iron Law #7），最终 `git diff` 为空
- **可选增强**——不阻塞 Mode 2 的 done 判定、不占 3 轮迭代预算、不污染 Mode 3 覆盖率
- **只出建议**——存活变异体写入报告作为"测试缺口清单"，建议回 Mode 2 补强，Mode 4 自身不改测试

### 与其他模式的关系

| 模式 | 改源码？ | 改测试？ | 强制？ | 定位 |
|------|---------|---------|--------|------|
| Mode 1 探测 | ❌ | ❌ | — | 建表 |
| Mode 2 编写 | ❌ | ✅ 生成 | ✅（用户触发即执行） | 生成测试 |
| Mode 3 采集 | ❌ | ❌ | — | 只读覆盖率 |
| **Mode 4 变异** | ⚠️ 临时改+必恢复 | ❌ | ❌ 可选增强 | 验证测试有效性 |

**前置依赖 Mode 2**：没有测试就没东西可变异验证。Mode 4 在 Mode 2 产出的测试基础上运行。

**不触发于**：项目还没写过测试（应先 Mode 2）、只要覆盖率不要有效性验证（走 Mode 3）、非 high 级方法的有效性验证（成本不值）。

---

## 触发条件

**触发**（用户显式）：
- 变异测试、mutation testing、mutation score
- 验证测试有效性、看测试能不能发现问题、测试够不够好
- high 级方法有效性、变异得分
- "这些测试真能拦住 bug 吗"

**自动触发**（可选，默认关）：Mode 2 全类 done 后，用户在配置里开启 `auto_mutation_on_mode2_done`，则对 high 级方法自动跑 Mode 4。默认关闭——保持可选增强定位。

**前置检查**：
1. `.ut-inventory.json` 存在（取 high 级 testable 方法作为变异目标）
2. Mode 2 已产出可编译可运行的测试（`build-${test_dir}/` 存在且 target `test_<classname>` 可编）
3. reconcile 通过（源码与 inventory.base_sha 一致；不一致先对账，避免变异跑在旧代码上）

> 若前置不满足 → 提示"请先执行 Mode 2 生成测试"，不降级到从零生成。

---

## 源码安全四铁律（替代 Iron Law #7）

Mode 4 是**唯一豁免 Iron Law #7（不修源码）的模式**，但受等价的源码安全四铁律约束，绝不弱于 #7：

1. **备份先于修改** —— 任何源码写入前必须 `cp` 到 `.mutation_backup`；备份不存在不开始变异
2. **恢复必有机制** —— `atexit` + `SIGTERM` + `SIGINT` 三重注册恢复函数；进程被 kill 也恢复
3. **变异不落盘** —— 变异后的源码**绝不 commit、绝不 push**；`code_committer` 在 Mode 4 禁用
4. **退出必校验** —— 模式结束时强制 `git diff --exit-code`（仅校验被变异过的文件），非空则硬终止 + 警告用户手动恢复

### 实现机制

脚本已实现完整的安全闭环：

```python
# 铁律 #1+#2: 备份与恢复
_PENDING_RESTORES = []  # [(backup_path, source_file), ...]
atexit.register(_restore_on_exit)              # 进程正常退出
signal.signal(signal.SIGTERM, _signal_handler)  # 被 kill
signal.signal(signal.SIGINT, _signal_handler)   # Ctrl+C

# 铁律 #4: 退出校验 (只检查被变异过的文件, 不检查全仓库)
_MUTATED_FILES = set()  # 记录被变异过的源文件
def _git_diff_check():
    cmd = ['git', 'diff', '--exit-code'] + sorted(_MUTATED_FILES)
    # rc != 0 → [FATAL] 源码未恢复干净
```

每个变异-恢复周期在 `finally` 块移除 pending 条目，保证正常路径下逐个恢复；异常路径由 atexit/signal 兜底。

> **与 Iron Law #7 的关系**：#7 的意图是"不污染用户源码"，四铁律用"备份+恢复+不落盘+退出校验"达成同一意图。Mode 1/2/3 仍严守 #7 原文，不受影响。

---

## 工作步骤

### 1. 前置检查与目标选择

```python
inventory = read_json(f"{test_dir}/.ut-inventory.json")
# 变异目标：level==high 且 testable 的方法（mid/low 不跑，成本不值）
targets = [m for m in inventory["methods"]
           if m["level"] == "high" and m["testable"]]
```

> **只对 high 级跑**。理由：变异测试成本与价值成正比，high 级（dbus_slot/q_invokable/插件导出/高复杂度）是最该验证有效性的；mid/low 靠 Mode 2 的覆盖率门禁 + 断言强度自检足够。

支持显式指定单方法/单类收窄 targets（`--source` + `--function` 或 `--class`），可覆盖默认 high 筛选——用户觉得某个 mid 方法关键也可单独验证。

### 2. 独立 build 目录准备（不污染 Mode 2/3 产物）

```bash
# 推荐：独立 build-mutation/，从头配置只编单类
mkdir -p build-mutation && cd build-mutation
cmake ${PROJECT_PATH} -DBUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Debug \
      -DCMAKE_CXX_COMPILER_LAUNCHER=   # 禁 ccache，避免变异被缓存跳过
cmake --build . --target test_<classname>   # 首次：编依赖+测试；后续变异只重编 1 个 .cpp
```

**为什么独立 build**：变异体跑测试会写 `.gcda` 覆盖率数据，污染 Mode 3 的覆盖率采集。复用 `build-${test_dir}` 会让 Mode 3 报告失真。独立 build 物理隔离。

**为什么禁 ccache**：ccache 按预处理器 hash 命中可能跳过重编，导致变异体没真正编译进去，测试跑的是原代码 → 假"存活"。脚本已内置 `CCACHE_DISABLE=1`。

> **测试阶段可复用已有 build 目录**：若只是验证脚本正确性或单方法，可直接用 Mode 2 的 `build-${test_dir}`（实测无 ccache 污染问题，因为脚本每次都强制重编被改文件）。生产环境推荐独立 build。

### 3. 函数定位与变异体生成

```python
# 函数行范围定位（当前用正则 find_function_range; 生产环境可换 MCP get_code_snippet 更准）
func_start, func_end = find_function_range(lines, func_name)

# 生成变异体（5 类算子, 框架无关）
mutants = []
mutants += generate_aor_mutants(lines, func_start, func_end)   # 算术 +-*/
mutants += generate_ror_mutants(lines, func_start, func_end)   # 关系 <>=
mutants += generate_lor_mutants(lines, func_start, func_end)   # 逻辑 &&||
mutants += generate_crc_mutants(lines, func_start, func_end)   # 常量 0↔1↔2
mutants += generate_rvf_mutants(lines, func_start, func_end)   # 返回值 true↔false / N±1

mutants = mutants[:max_mutants]  # 上限 20/方法（比 unit-test-generate 的 50 保守, 控成本）
```

### 4. 逐变异体：备份→变异→增量编译→跑测试→恢复→记录

```python
for mutant in mutants:
    backup = source_file + ".mutation_backup"
    shutil.copy2(source_file, backup)               # 铁律 #1
    _PENDING_RESTORES.append((backup, source_file)) # 铁律 #2 注册
    _MUTATED_FILES.add(source_file)                 # 铁律 #4 记录
    try:
        write_mutated(source_file, mutant)          # 写入变异
        # 增量编译：cmake --build . --target (单文件重编+链接, 秒级)
        rc = cmake --build . --target test_<classname>  # cwd=build_dir, CCACHE_DISABLE=1
        if rc != 0: record(mutant, "compile_failed"); continue
        # 跑 GTest
        rc = ./test_<classname> --gtest_filter='*MethodName*'
        # GTest 判定: 退出码非0 或输出含 [  FAILED  ] = killed
        status = "killed" if rc != 0 or '[  FAILED  ]' in stdout else "survived"
        record(mutant, status)
    finally:
        shutil.move(backup, source_file)            # 铁律: 必恢复
        _PENDING_RESTORES.remove((backup, source_file))
```

### 5. 变异得分计算

```
变异得分 = killed / (killed + survived)
```

- `compile_failed` **不计分母**——变异体本身编译不过（如 `QString operator+` 变成 `operator-` 无重载），是变异体的问题不是测试的问题
- `equivalent`（等价变异，不改变行为）：**脚本不自动识别**，所有存活变异体统一列入缺口清单，由人工复核判断是真测试缺口还是疑似等价；**不自动剔除分母**（保守，宁可得分低点也不要把真缺口误判为等价）
- 阈值默认 **85%**（可配 `--threshold`，比 unit-test-generate 的 80% 更严）

### 6. 报告与建议

产出双报告（写入 build 目录）：
- `mutation_report.md`：人读报告（概述表 + 按函数详情 + **存活变异体建议清单** + 编译失败清单 + 按算子统计）
- `.ut-mutation.json`：机读（与 `.ut-inventory.json` 命名对齐，格式见下文 §报告解读）

存活变异体清单（Mode 4 的核心输出）：

```markdown
## 存活变异体 (测试缺口 — 补强建议)
> Mode 4 只出建议不自动补强。以下存活变异体表示测试未覆盖该变异,
> 建议回 Mode 2 用 incremental_updater 补强对应用例。
| 函数 | 算子 | 行 | 变异描述 |
|------|------|----|---------|
| Utils::reformatSeparators | ROR | L169 | L169: == -> != |
| Utils::reformatSeparators | LOR | L169 | L169: || -> && |
```

### 7. 退出校验（源码安全四铁律 #4）

```bash
git diff --exit-code  # 仅校验被变异过的文件
# 非空 → [FATAL] 源码未恢复干净, 请手动检查 .mutation_backup 残留, exit 2
# 空 → ✅ 源码无改动, Mode 4 安全退出
```

### 8.（可选）补强反馈

存活变异体（疑似测试缺口）→ **建议**用户回 Mode 2 用 `incremental_updater` 补强对应用例。

> 这一步**默认只出建议清单不自动改测试**，避免 Mode 4 越权修改 Mode 2 产物。

---

## 变异算子

5 类算子，框架无关:

| 算子 | 全称 | 变异示例 |
|------|------|---------|
| AOR | 算术运算符替换 | `+` → `-`/`*`/`/`，`-` → `+`/`*`/`/` 等 |
| ROR | 关系运算符替换 | `==` → `!=`/`<`/`>`/`<=`/`>=`，`<` → `<=`/`>`/... |
| LOR | 逻辑运算符替换 | `&&` → `\|\|`，`\|\|` → `&&` |
| CRC | 常量替换 | `0` → `1`/`-1`，`1` → `0`/`2`，其他 → `±1` |
| RVF | 返回值修改 | `return true` → `return false`，`return N` → `return N±1` |

### C++ 特有的跳过规则（实测踩坑修复）

C++ 语法有大量复合符号，变异算子若不区分会生成无效代码导致编译失败（拉低有效变异体数）。脚本已内置以下跳过规则：

| 场景 | 问题 | 跳过规则 |
|------|------|---------|
| `//` `/*` `*/` 注释 | AOR 的 `/` 误匹配注释分隔符 → 语法错误 | `op=='/'` 时检查前后字符是否为 `/`/`*` |
| `->` 指针成员访问 | AOR 的 `-` 误匹配 → `+>` 无效；ROR 的 `>` 误匹配 → `-+` 无效 | AOR: `after=='>'` 跳过；ROR: `before=='-'` 跳过 |
| `<<` `>>` 流操作符 | ROR 的 `<`/`>` 误匹配 → `><`/`<>` 无效（qDebug 等） | ROR: `after`/`before == '<'`/`'>'` 跳过 |
| `<=` `>=` 单字符重复 | ROR 的 `<` 先于 `<=` 匹配，重复变异 | ROR: `after=='='` 跳过单字符，由 `<=`/`>=` 键处理 |
| `+=` `-=` `*=` `/=` 复合赋值 | AOR 误匹配 → `-= `/`+=` 等无意义 | AOR: `after=='='` 跳过 |
| `++` `--` 自增自减 | AOR 误匹配 | AOR: `before_char in ('+','-','*','/')` 跳过 |
| 字符串/字符字面量 | 算子出现在 `"a+b"` 中不应变异 | `in_string()` 状态机检测 |
| 一元运算符 `-1` `+a` | AOR 误匹配一元符号 | `_is_unary_op()` 检查前驱字符 |

> **Gotcha**：测试阶段曾因 `->` 误匹配导致 `formatThousandsSeparators` 产生 32 个无效变异体（24 个编译失败）。修复后降至 3 个真实的 `compile_failed`（`QString operator+` 无对应重载，属正常排除）。**这些跳过规则是 C++ 变异测试的必备防护，缺一不可。**

---

## 成本控制

| 措施 | 效果 |
|------|------|
| 只对 high 级跑 | 控制目标数量（通常全项目 5-15% 的方法） |
| 变异体上限 20/方法 | 单方法变异体数封顶（unit-test-generate 是 50） |
| 单文件增量编译 | 每变异体只重编被改的 `.cpp` + 链接，秒级（vs 全量几分钟） |
| 禁 ccache | 避免假存活（编译被跳过，测试跑的是原代码） |
| `--gtest-filter` | 只跑相关用例，避免每次跑全量测试套件 |
| 独立最小 build | 不污染 Mode 2/3，且只编 test target 不编全项目 |

**实测数据**（deepin-calculator，Qt6+DTK6，单方法）：

| 函数 | 变异体数 | killed | survived | compile_failed | 耗时 | 得分 |
|------|---------|--------|----------|----------------|------|------|
| `Utils::stringIsDigit` | 6 | 6 | 0 | 0 | ~45s | 100% |
| `Utils::reformatSeparators` | 18 | 10 | 8 | 0 | ~2m | 55.6% |
| `Utils::formatThousandsSeparators` | 20（截断自70） | 17 | 0 | 3 | ~2m27s | 100% |

> 单变异体平均耗时约 **7.4 秒**（增量编译 utils.o + 链接 + 跑 GTest）。
> 预估 10 个 high 方法 × 20 变异体 × 7.4s ≈ **25 分钟**，可接受。
> 对比 unit-test-generate 原版：50 变异体 × `make clean && make` 全量重编 × 数分钟 = **数小时~一天**，不可行。

---

## 与现有机制的关系（解耦点）

| 现有机制 | Mode 4 的关系 |
|---------|--------------|
| Iron Law #7 不修源码 | 豁免，由"源码安全四铁律"替代（等价约束） |
| Iron Law #10 3 轮迭代上限 | **不占用**——Mode 4 有自己的预算（变异体数），不抢 Mode 2 闭环 |
| Iron Law #11 usecase_count 实时更新 | **不更新**——Mode 4 不产生新用例 |
| Mode 2 self_checker 覆盖率门禁 | **不干扰**——Mode 4 不改 Mode 2 的 done 判定 |
| Mode 3 覆盖率采集 | **不污染**——独立 build 目录，`.gcda` 隔离 |
| code_committer | **禁用**——变异源码绝不提交 |
| reconcile | **复用**——Mode 4 启动前对账，确保变异跑在当前代码上 |

**关键定位**：Mode 4 是**只读验证 + 产出建议**的模式——从最终状态看：源码 `git diff` 为空、测试不变、inventory 不变。产出是"有效性报告 + 补强建议清单"，决策权在用户。

---

## 用法

### 直接模式（测试/单方法验证）

```bash
python3 ${SKILL_DIR}/scripts/mutation_score.py \
    --source src/utils.cpp \
    --function Utils::stringIsDigit,Utils::reformatSeparators \
    --build-dir build-test \
    --test-target deepin-calculator-test \
    --gtest-filter '*stringIsDigit*:*reformatSeparators*' \
    --project-dir .
```

### inventory 模式（生产 Mode 4）

```bash
python3 ${SKILL_DIR}/scripts/mutation_score.py \
    --inventory .ut-inventory.json \
    --all-high \
    --build-dir build-mutation \
    --test-target deepin-calculator-test
```

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--source` | 直接模式 | 源文件路径（相对项目根或绝对） |
| `--function` | 直接模式 | 函数全限定名（`Class::method`），多个用逗号分隔 |
| `--inventory` | inventory 模式 | `.ut-inventory.json` 路径 |
| `--all-high` | inventory 模式 | 对所有 high 级 testable 方法跑变异 |
| `--build-dir` | ✅ | 构建目录（需已 cmake 配置） |
| `--test-target` | ✅ | GTest 测试 target 名（如 `deepin-calculator-test`） |
| `--gtest-filter` | — | GTest 过滤器（如 `*stringIsDigit*`），加速只跑相关用例 |
| `--max-mutants` | — | 每函数最大变异体数（默认 20） |
| `--project-dir` | — | 项目根目录（用于 git diff 校验，默认 `.`） |
| `--threshold` | — | 变异得分阈值（默认 85） |

> 需指定 `--source + --function`（直接模式）或 `--inventory + --all-high`（inventory 模式）之一。

---

## 报告解读

运行结束产出 `build-<dir>/mutation_report.md` + `.ut-mutation.json`：

| 报告章节 | 含义 | 行动 |
|---------|------|------|
| 概述 | 全局杀死/存活/编译失败/得分 | 得分 ≥85% → 测试有效性达标 |
| 按函数详情 | 每个方法的得分与判定 | BELOW 的方法需关注 |
| **存活变异体** | **测试缺口清单** | **回 Mode 2 用 incremental_updater 补强** |
| 编译失败变异体 | 不计分母的无效变异 | 无需行动（正常排除） |
| 按算子统计 | 各算子杀死率 | 某算子存活多 → 该类断言普遍偏弱 |

**判定逻辑**：
- `PASS`：变异得分 ≥ 阈值（85%），测试有效性达标
- `BELOW_THRESHOLD`：得分 < 阈值，存活变异体过多，建议补强测试

### `.ut-mutation.json` 格式

与 `.ut-inventory.json` 命名对齐，顶层含元数据 + 全局 summary + functions 数组。示例见 `examples/sample-qt-project/autotests/.ut-mutation.json`。

```json
{
  "version": 1,
  "project": "deepin-calculator",
  "base_sha": "c9de5e9",
  "timestamp": "2026-08-20T13:44:37",
  "config": {
    "threshold": 85.0,
    "max_mutants_per_function": 20,
    "test_target": "deepin-calculator-test",
    "gtest_filter": "*stringIsDigit*:*reformatSeparators*"
  },
  "summary": {
    "total_mutants": 44,
    "killed": 41,
    "survived": 0,
    "compile_failed": 3,
    "mutation_score": 100.0,
    "verdict": "PASS"
  },
  "functions": [
    {
      "function": "Utils::stringIsDigit",
      "file": "src/utils.cpp",
      "line_range": [142, 157],
      "total_mutants": 6,
      "killed": 6,
      "survived": 0,
      "compile_failed": 0,
      "mutation_score": 100.0,
      "verdict": "PASS",
      "details": [
        {
          "id": "ROR_147_!=_==",
          "operator": "ROR",
          "line": 148,
          "description": "L148: != -> ==",
          "status": "killed",
          "output_snippet": "[  FAILED  ] Ut_Utils.stringIsDigit_..."
        }
      ]
    }
  ]
}
```

**字段说明**：

| 层级 | 字段 | 说明 |
|------|------|------|
| 顶层 | `version` | 格式版本（当前 1） |
| 顶层 | `project` | 项目名（basename of project-dir） |
| 顶层 | `base_sha` | 变异测试时的 git HEAD（短 SHA） |
| 顶层 | `timestamp` | 运行时间（ISO 8601） |
| 顶层 | `config` | 运行配置（阈值/上限/test-target/gtest-filter） |
| `summary` | `mutation_score` | 全局变异得分 = killed / (killed + survived) |
| `summary` | `verdict` | `PASS` / `BELOW_THRESHOLD` |
| `functions[]` | `file` | 源文件相对路径 |
| `functions[]` | `verdict` | 单函数 `PASS` / `BELOW_THRESHOLD` |
| `details[]` | `status` | `killed` / `survived` / `compile_failed` |
| `details[]` | `output_snippet` | 测试输出片段（killed 含 `[ FAILED ]`，compile_failed 含编译错误） |

---


## 开放问题（待定）

### 等价变异体判定

当前策略：**脚本不自动识别等价变异**，所有存活变异体统一列入缺口清单（报告“存活变异体”表），由人工复核判断是真测试缺口还是疑似等价，**不自动剔除分母**。

- 优点：保守，宁可得分低点也不把真缺口误判为等价
- 缺点：真等价变异会拉低得分（如 `i++` → `i+=1` 语义等价）

待积累数据后评估是否引入 test_writer 初判 + 报告标注。

### 目标范围扩展

当前默认只 high 级。待评估：
- 支持 `--class` 显式指定整类
- 支持 mid 级方法在用户显式要求时纳入（覆盖默认 high 筛选）

---

## 完成清单

□ 已确认 Mode 2 测试可编译可运行（前置条件）
□ 已用独立 build 目录或确认复用 Mode 2 build 无污染
□ 变异目标为 high 级 testable 方法（或用户显式指定）
□ `--gtest-filter` 已设置只跑相关用例（加速）
□ 运行结束 `git diff --exit-code` 通过（源码安全四铁律 #4）
□ 得分 < 阈值时，存活变异体清单已交付，建议用户回 Mode 2 补强
□ Mode 4 不更新 usecase_count、不 commit、不改 Mode 2 产物
