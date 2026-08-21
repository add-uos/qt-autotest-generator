# 源码缺陷导出与统计 · 设计方案

> 状态：**草案 v0.1，待评审**
> 作者：qt-autotest-generator
> 日期：2026-08-20
> 关联技能章节：`reference/failure_repairer.md` §6/§7、`reference/build_verifier.md` §5、`reference/report_generator.md`、`reference/code_committer.md`

---

## 0. TL;DR

当前技能在 Mode 2 逐类闭环中会**检测并分类**源码缺陷（`source_defect_compile` / `source_defect_runtime` / `source_defect_logic` / `needs_manual`），但只写**内存变量**，会话结束即丢失；`failure_repairer.md` §7 承诺的「报告生成阶段列出疑似源码缺陷清单」**未兑现**（`collect-coverage-report.py` 无此产出）。

本方案补齐「**持久化 → 导出 → 统计**」三层，颗粒度精确到**测试用例级**，使单元测试过程中发现的所有源码缺陷可累积、可导出、可统计。**捕获时机前移到编译期**（编译失败即可确认的源码缺陷立即记录，不等重试耗尽）；**缺陷数据与导出产物均不入 git**，本地存储即可。

> **Mode 5 · 源码缺陷导出与统计**。Mode 4 已分配给变异测试（见 `doc/mode4-mutation-design.md`），本能力定为 Mode 5。两者数据模型与导出脚本各自独立，不共用。

---

## 1. 背景与现状缺口

### 1.1 缺陷数据的「断头路」

| 环节 | 现状 | 问题 |
|------|------|------|
| **检测** | `build_verifier.md` §4/§5 初分类 → `failure_repairer.md` §4 终判根因 | ✅ 分类完整：`source_defect_compile` / `source_defect_runtime` / `source_defect_logic` / `needs_manual` |
| **记录** | 只写内存变量 `class_status[classname]`（含 `defect_evidence` + `defect_suggestion`） | ❌ 会话结束即丢失；无法跨会话累积；无法统计「所有」缺陷 |
| **导出** | `failure_repairer.md:121` 承诺「标红的类会在报告生成阶段的『疑似源码缺陷清单』中列出」 | ❌ **承诺未兑现** —— `report_generator.md` / `collect-coverage-report.py` 根本没产出此清单 |
| **统计** | 无 | ❌ 无任何按类型/类/方法/严重度的计数 |

### 1.2 隐式 `needs_manual` 未归集

两处隐式标记当前只写内存，不进任何清单：

- `reference/test_code_gen.md:173` —— 私有/protected 构造且无工厂方法 → `needs_manual`
- `reference/dependency_tracer.md:101` —— 循环依赖（trace_path 发现环）→ `needs_manual`

→ 统计会漏。

### 1.3 根因：缺持久层 + 缺导出层

`collect-coverage-report.py`（Mode 3）是**只读采集**脚本，拿不到内存里的 `class_status`。必须先把缺陷落盘成文件，导出层才能消费。这与 `.ut-inventory.json` 作为「项目单元测试状态唯一真相源」的定位完全同构。

---

## 2. 设计目标

1. **持久化** —— 缺陷写盘，跨会话累积，作为「缺陷真相源」（类比 `.ut-inventory.json`）
2. **用例级颗粒度** —— 同一方法多个用例失败各记一条，定位到具体 `TEST_F`，统计更准（详见 [§4](#4-颗粒度用例级)）
3. **去重 + 生命周期** —— 同一缺陷重跑不重复计数；修好后标 `fixed`，再坏标 `reopened`
4. **导出** —— JSON（机器读）+ Markdown（人读标红清单，md 内链接可跳转源码）
5. **三入口** —— Mode 2 收尾自动生成 / Mode 3 合并进 `ut-summary.json` / 按需独立触发
6. **不破坏 Iron Laws** —— 仍「不修源码」，导出只是把标红清单正式交付

---

## 3. 与 Mode 4 变异测试的边界

Mode 4（变异测试，见 `doc/mode4-mutation-design.md`）与 Mode 5（本方案）定位完全不同，**数据模型与导出脚本各自独立，不共用**：

| | Mode 4 变异测试 | Mode 5 缺陷导出（本方案） |
|---|---|---|
| 缺陷来源 | **故意注入**变异体，看测试能否抓到 | 测试跑出来的**真实失败**，根因回溯到源码 |
| 缺陷性质 | 源码可能没问题，是**测试弱** | 源码**确实有问题** |
| 数据文件 | `mutation_report.json` | `.ut-defects.json` |
| 导出脚本 | `mutation_score.py` | `export-defects.py` |
| 触发时机 | Mode 2 完成后可选验证 | Mode 2 闭环中实时捕获 + 按需导出 |
| 数据消费方 | 测试工程师补用例 | 开发者修源码 |

---

## 4. 颗粒度：用例级

### 4.1 为什么用例级

方法级（`defect_id = method_qn`）的问题：一个方法可能有多个测试用例，各自失败原因不同：

```
MyClass::processData
  ├─ ProcessData_NullInput_ShouldNotCrash     → 失败 → runtime 缺陷（空指针）
  ├─ ProcessData_LargeInput_ShouldReturnOk    → 失败 → logic 缺陷（边界算错）
  └─ ProcessData_Normal_ShouldReturnSum       → 通过
```

方法级只能记一条，丢失「同一方法多处缺陷」的信息，也无法定位到具体用例。**用例级**让每个失败用例独立成条，统计更准、定位更直接。

### 4.2 主键设计

```
defect_id = {method_qn}#{TestFixture}.{TestCaseName}
```

| 情形 | defect_id 示例 |
|------|----------------|
| 有具体失败用例 | `project.src.MyClass.processData#MyClassTest.ProcessData_NullInput_ShouldNotCrash` |
| 构造即崩（无具体用例） | `project.src.MyClass.MyClass#MyClassTest.__class_init__` |
| 自由函数 | `project.src.util.parse#ParseTest.Parse_EmptyString_ShouldReturnNull` |

- `#{Fixture}.{Case}` 部分保证同一方法多用例各一条、且跨会话稳定（用例名不变即同一条）
- 无具体用例时用保留标识 `__class_init__` / `__ctor__` 兜底

### 4.3 聚合维度

用例级记录 + 方法/类聚合统计：

- `by_test_case` —— 用例级缺陷数（明细）
- `by_method` —— 方法级：多少方法有缺陷、每方法缺陷数
- `by_class` —— 类级：多少类有缺陷
- `by_module` —— 按 `file_path` 顶层目录聚合

---

## 5. 数据模型：`.ut-defects.json`

存于 `{test_dir}/.ut-defects.json`，**不纳入版本控制**（加入 `.gitignore`，本地存储即可）。与 `.ut-inventory.json` 定位不同：inventory 是团队对齐的「测试状态真相源」必须入库；缺陷清单是检测过程的派生产物，会随每次重跑变化（open→fixed→reopened），入库会持续污染 git 历史，本地存留即可。

```jsonc
{
  "version": 1,
  "project": "deepin-image-viewer",
  "base_sha": "abc1234",
  "last_updated": "2026-08-20T10:30:00Z",
  "defects": [
    {
      // ── 主键 & 归属 ──
      "defect_id": "project.src.MyClass.processData#MyClassTest.ProcessData_NullInput_ShouldNotCrash",
      "method_qn": "project.src.MyClass.processData",
      "method_name": "processData",
      "class_qn": "project.src.MyClass",
      "class_name": "MyClass",
      "module": "core",                          // file_path 顶层目录, 聚合用

      // ── 源码位置 ──
      "file_path": "src/lib/core/myclass.cpp",
      "file_line": 42,

      // ── 用例级定位 ──
      "test_fixture": "MyClassTest",
      "test_case_name": "ProcessData_NullInput_ShouldNotCrash",
      "test_case_full": "MyClassTest.ProcessData_NullInput_ShouldNotCrash",
      "test_file": "autotests/core/test_myclass.cpp",

      // ── 分类 ──
      "type": "source_defect_runtime",           // 4 类之一
      "type_category": "runtime",                // compile/runtime/logic/manual
      "severity": "high",                        // 由 type × method_level 派生

      // ── 捕获阶段 (编译期即可确定的尽早记录, 不等重试耗尽) ──
      "detected_at_stage": "compile",            // compile / runtime / logic / review / manual

      // ── 生命周期 ──
      "status": "open",                          // open / fixed / reopened / wontfix
      "evidence": "segfault at line 42, stub fully applied, source has no null check",
      "suggestion": "检查 processData 对空输入的处理，疑似未做空指针检查",
      "root_cause_snippet": "void processData(Data* d) { d->size(); }",  // get_code_snippet 截取

      // ── 时序 ──
      "discovered_at": "2026-08-20T10:25:00Z",
      "discovered_in_batch": 3,
      "first_seen_sha": "abc1234",
      "last_updated": "2026-08-20T10:25:00Z",
      "fixed_at": null,
      "fixed_in_sha": null,

      // ── 闭环元数据 ──
      "repair_attempts": 10,
      "iteration_count": 3,
      "method_level": "high"                     // 取自 inventory, 影响严重度
    }
  ],
  "stats": {
    "total": 6,
    "by_status":   { "open": 5, "fixed": 1, "reopened": 0, "wontfix": 0 },
    "by_type":     { "source_defect_compile": 1, "source_defect_runtime": 3, "source_defect_logic": 1, "needs_manual": 1 },
    "by_category": { "compile": 1, "runtime": 3, "logic": 1, "manual": 1 },
    "by_severity": { "high": 4, "mid": 2, "low": 0 },
    "by_class":    { "MyClass": 2, "FileView": 1, "DataManager": 1, "...": "..." },
    "by_method":   { "MyClass.processData": 2, "FileView.onOpen": 1, "...": "..." },
    "by_module":   { "core": 4, "ui": 2 },
    "affected_methods": 5,                       // 去重后的方法数
    "affected_classes": 4                        // 去重后的类数
  },
  "history": {                                   // Q5: 按 base_sha 归档, 代码版本变更时旧缺陷移入
    "abc1234": {                                 // 旧 base_sha
      "defects": [ /* 该版本下的缺陷快照 */ ],
      "stats": { /* 该版本的统计 */ }
    }
  }
}
```

### 5.1 severity 派生规则

| type | type_category | severity 默认 |
|------|---------------|---------------|
| `source_defect_runtime` | runtime | **high**（崩溃） |
| `source_defect_compile` | compile | **high**（编译不过） |
| `source_defect_logic` | logic | **mid**（逻辑矛盾，可降级可升） |
| `needs_manual` | manual | **mid**（待排查） |

> **升级规则**：若 `method_level=high` 且 severity 原为 mid → 升为 high（重要方法缺陷更严重）。

### 5.2 type 与现有 failure_reason 映射

| 现有 `failure_reason` | 本方案 `type` | 来源文件 |
|----------------------|---------------|----------|
| `source_defect_compile` | `source_defect_compile` | `build_verifier.md:65` / `failure_repairer.md:65` |
| `source_defect_runtime` | `source_defect_runtime` | `build_verifier.md:67` / `failure_repairer.md:71` |
| `source_defect_logic` | `source_defect_logic` | `build_verifier.md:69` / `failure_repairer.md:75` |
| `needs_manual` | `needs_manual` | `build_verifier.md:71` / `failure_repairer.md:68,79` |
| 私有构造无工厂 | `needs_manual` | `test_code_gen.md:173`（隐式，待归集） |
| 循环依赖 | `needs_manual` | `dependency_tracer.md:101`（隐式，待归集） |

---

## 6. 去重与生命周期

以 `defect_id`（用例级）为主键 **upsert**。重跑时的状态机：

```
                 ┌─────────── fixed ───────────┐
                 │                              │
                 ▼                              │
新发现 ──► open ──┤                              │ (同用例再失败)
                 │                              │
                 │ (该用例通过)                  ▼
                 └─────────────────────── reopened
```

| 重跑情形 | 处理 |
|----------|------|
| 该用例仍失败 | 更新 `last_updated` / `evidence` / `repair_attempts` / `root_cause_snippet`；`status` 保持 `open` |
| 该用例已通过 | `status` → `fixed`，记 `fixed_at` + `fixed_in_sha`（保留记录做历史统计，不删除） |
| `fixed` 后又失败 | `status` → `reopened`，清空 `fixed_at` |
| 源码删除该用例引用 | `status` → `wontfix`，记 `reason: method_deleted`（参见 `failure_repairer.md` §5 方法删除清理） |

> **注意**：`fixed` 记录**保留**，不删除 —— 用于「历史缺陷统计」「修复回归检测」。`stats.by_status` 含 fixed 计数，但 `affected_methods/affected_classes` 只统计 `open + reopened`。
>
> **base_sha 归档**（Q5 决议）：缺陷记录与 `base_sha` 绑定。当 reconcile 检出代码变更（`base_sha` 漂移）时，当前 `defects` + `stats` 整体移入 `history[old_base_sha]`，新 `base_sha` 下的缺陷从空开始累积。`stats` 只反映当前 `base_sha` 的缺陷，历史版本缺陷在 `history` 中可查。

---

## 7. 导出产物

放 `{report_dir}/`（默认 `build-ut/`，或 `autotests/.reports/`）：

| 文件 | 用途 | 内容 |
|------|------|------|
| `{test_dir}/.ut-defects.json` | 本地真相源 | 完整 defects + stats + history，不入 git |
| `{report_dir}/defects.json` | 机器读快照 | 纯 defects 数组（无 stats），便于下游脚本消费 |
| `{report_dir}/defects-summary.md` | 人读标红清单 | 按 severity 分组表格 + 统计摘要 + md 内链接跳转源码 |
| `ut-summary.json` 新增 `source_defects` 字段 | Mode 3 合并 | 兑现 `failure_repairer.md` §7 承诺，覆盖率报告自带缺陷清单 |

### 7.1 defects-summary.md 样例

```markdown
# 源码缺陷清单 · deepin-image-viewer

> 基线: dev @ abc1234 · 生成: 2026-08-20 10:30

## 统计摘要

- 共 **6** 个缺陷（open 5 / fixed 1 / reopened 0）
- 严重度: 高 4 / 中 2 / 低 0
- 类型: 编译 1 / 运行 3 / 逻辑 1 / 待排查 1
- 影响: 5 个方法 / 4 个类 / 2 个模块

## 🔴 高危 (4)

| 类 | 方法 | 用例 | 文件:行 | 类型 | 证据 | 建议 |
|----|------|------|---------|------|------|------|
| MyClass | processData | ProcessData_NullInput_ShouldNotCrash | myclass.cpp:42 | runtime | segfault... | 加空指针检查 |
| FileView | onOpen | OnOpen_InvalidPath_ShouldNotCrash | fileview.cpp:88 | runtime | abort... | 路径校验 |
...

## 🟡 中危 (2)

| 类 | 方法 | 用例 | ... |

## ✅ 已修复 (1)

| 类 | 方法 | 用例 | 修复于 |
|----|------|------|--------|
| DataManager | load | Load_Empty_ShouldReturnFalse | def5678 |

## 下一步建议

- 优先处理 4 个 high 运行/编译缺陷（阻塞测试推进）
- 1 个 needs_manual 需人工排查根因
- 1 个 fixed 记录保留，关注回归
```

---

## 8. 架构与数据流

```
Mode 2 逐类闭环
   build_verifier §4 编译失败
        │ ├─ 特征判定为源码编译缺陷(缺include/缺Q_OBJECT/空实现/源码符号未定义)
        │ │  → [增量 upsert, stage=compile] ─┐  (编译期即可确定, 不等重试耗尽)
        │ └─ 测试侧问题(stub/include/CMake) → 走修复, 不记缺陷
        │                                    │
   build_verifier §5 运行失败分类 ─────────────┤
        │ ├─ runtime 崩溃(stub已补全) → [upsert, stage=runtime]
        │ └─ ASSERT 恒失败(逻辑正确) → [upsert, stage=logic]
        │                                    │
   failure_repairer §4 终判根因(重试耗尽) ────┤
        │ └─ 复核/补记 needs_manual → [upsert, stage=manual]
        │                                    │
        │              {test_dir}/.ut-defects.json   ← 持久层(本地, 不入 git)
        │                              │
        │ (该用例通过时)               │
   build_verifier 通过验证 ──► [mark-fixed] ┘
        │
   Mode 2 收尾 (code_committer 之后)
        │
        └──► [批量导出] {report_dir}/defects.json + defects-summary.md

Mode 3 collect-coverage-report.py Step 6
        └──► [合并] ut-summary.json += source_defects 字段  (兑现 failure_repairer §7 承诺)

Mode 5 按需独立触发
        └──► [只读导出] 同上, 不跑测试/不编译
```

**关键决策**：持久化与导出**拆开**。
- 持久化在缺陷**确认时实时增量写**（编译期/运行期/重试耗尽多个捕获点，防会话崩溃丢数据）
- 导出在收尾/按需时**批量生成**（快照统计）

### 8.1 捕获时机（编译期前移）

缺陷捕获不止在 `failure_repairer` 标红（重试耗尽）时，**编译期即可确定的应尽早记录**，对应 `detected_at_stage` 字段：

| stage | 捕获点 | 判定特征（源码侧，非测试侧） |
|-------|--------|------------------------------|
| `compile` | `build_verifier` §4 编译失败 | 源码缺 `#include`、缺 `Q_OBJECT`（moc 报错）、空实现导致链接失败、源码符号未定义（`trace_path` 确认非 stub 缺失） |
| `runtime` | `build_verifier` §5 运行崩溃 | stub 已补全仍 segfault/abort（`failure_repairer` 确认） |
| `logic` | `build_verifier` §5 ASSERT 恒失败 | 测试逻辑正确但与源码行为矛盾 |
| `review` | `test_writer` 读源码时 | 空函数体、`TODO` 未实现、明显空指针/越界（主观，标记后交人工复核） |
| `manual` | `failure_repairer` §4 重试耗尽 | 无法判定根因 / 私有构造无工厂 / 循环依赖 |

**编译期捕获的防误判**：编译失败多数是测试侧问题（stub 签名错、测试缺 include、CMake 缺依赖），必须先排除测试侧再记缺陷。判定顺序沿用现有 `build_verifier` §4 / `failure_repairer` §3 的修复表——**修不好且特征指向源码**才落盘，避免把测试代码问题误记为源码缺陷。

> `compile` 阶段若在重试预算内识别到明确源码特征（如 `fatal error: xxx.h: No such file` 指向源码目录、`undefined reference to` 经 `trace_path` 确认非 stub 缺失），可**提前预记录** `status=open, stage=compile`，不必等 10 loops 耗尽；后续若修复发现是测试侧误会，再 `mark-fixed` 或删除该条。

### 8.2 版本控制策略（不入 git）

`.ut-defects.json` 及导出产物**均不入版本控制**：

| 产物 | 位置 | 入 git？ | 理由 |
|------|------|---------|------|
| `.ut-inventory.json` | `{test_dir}/` | ✅ 入库 | 团队对齐的测试状态真相源 |
| `.ut-defects.json` | `{test_dir}/` | ❌ 不入库（加 `.gitignore`） | 检测过程派生产物，随重跑变化，入库污染历史 |
| `defects.json/md` | `{report_dir}/` | ❌ 不入库 | 报告快照，本地查看 |

**理由**：缺陷清单是「检测过程的副产品」，会随每次重跑变化（open→fixed→reopened），入库会持续污染 git 历史。团队对齐靠 `.ut-inventory.json`（哪些方法该测、测了多少），缺陷清单是个人/本地的排查辅助。

---

## 9. 集成点（精确到文件 / 章节）

### 9.1 新建（3 个）

| 文件 | 作用 |
|------|------|
| `reference/defect-schema.md` | `.ut-defects.json` 字段说明（类比 `inventory-schema.md`） |
| `reference/defect_exporter.md` | 按需触发入口 + 持久化/导出工作流分步指令 |
| `scripts/export-defects.py` | 双子命令：`upsert`（标红时增量写）/ `export`（批量导出 + 重算 stats）；含 `mark-fixed` 子命令 |

### 9.2 修改（7 个）

| 文件 | 章节 | 改动 |
|------|------|------|
| `reference/failure_repairer.md` | §6 | 标红时除写内存变量外，**追加调用** `export-defects.py upsert` 把缺陷落盘（含 `defect_id`/`evidence`/`suggestion`/`root_cause_snippet`） |
| `reference/failure_repairer.md` | §7 | 删掉「会在报告生成阶段列出」的空头承诺，改为「已写入 `.ut-defects.json`，由导出模块产出」 |
| `reference/build_verifier.md` | §6 | 类通过验证时，若 `.ut-defects.json` 有该类 open 缺陷 → 调 `export-defects.py mark-fixed`（闭环修复检测） |
| `reference/test_code_gen.md` | :173 | 私有构造无工厂时，追加写一条 `needs_manual` defect（归集隐式标记） |
| `reference/dependency_tracer.md` | :101 | 循环依赖时，追加写一条 `needs_manual` defect（归集隐式标记） |
| `reference/code_committer.md` | §2/§4 | ① **不** `git add` `.ut-defects.json`（已加 `.gitignore`，本地存储）；② 提交后触发 `export-defects.py export` 生成导出快照到本地 `report_dir` |
| `scripts/collect-coverage-report.py` | Step 6 | 读 `.ut-defects.json`，往 `ut-summary.json` 合并 `source_defects` 字段（兑现承诺） |

### 9.3 文档更新（2 个）

| 文件 | 改动 |
|------|------|
| `SKILL.md` | ① 新增 **Mode 5** 模块行；② 触发条件补「导出源码缺陷/统计源码缺陷/defect report/缺陷清单」；③ Iron Laws / 检查清单补「缺陷已落盘 `.ut-defects.json`」；④ 快速参考补脚本路径 |
| `README.md` | 功能特性表补一行「源码缺陷导出」 |

### 9.4 示例 & eval（2 个）

| 文件 | 内容 |
|------|------|
| `examples/sample-qt-project/autotests/.ut-defects.json` | 样例缺陷数据 |
| `examples/sample-qt-project/autotests/.reports/defects-summary.md` | 样例导出 |
| `evals/trigger-evals.json` | 补一条「标红后能导出缺陷」的触发 eval |

---

## 10. 工作流（按需触发）

> 触发词：**导出源码缺陷 / 统计源码缺陷 / defect report / 缺陷清单 / 导出缺陷数据**

```
Mode 5 · 源码缺陷导出与统计
1. Read reference/defect_exporter.md
2. 探测 {test_dir}/.ut-defects.json
   ├─ 不存在 → 提示「无缺陷记录」（不报错，正常情况）
   └─ 存在 → 继续
3. python3 scripts/export-defects.py {project} export \
            --defects {test_dir}/.ut-defects.json \
            --report-dir {report_dir} \
            [--inventory {test_dir}/.ut-inventory.json]   # 用于补 method_level/severity
   产出：defects.json + defects-summary.md + 重算 stats
4. （可选）--merge-summary 合并进 ut-summary.json
5. 打印统计摘要（总数/按类型/按严重度/按类/按方法）
```

**约束**：只读导出，不跑测试、不编译、不改测试代码、不改源码（与 Mode 3 同构，纯采集统计）。

---

## 11. 实施清单（建议顺序）

1. **定 schema** —— 写 `reference/defect-schema.md`（用例级，含 `history` 归档结构）
2. **写脚本** —— `scripts/export-defects.py`（`upsert` / `mark-fixed` / `export` 三子命令）
3. **写 reference** —— `reference/defect_exporter.md`
4. **接线 failure_repairer** —— §6 标红落盘 + §7 删空头承诺
5. **接线 build_verifier** —— §6 通过标 fixed
6. **归集隐式 needs_manual** —— `test_code_gen.md:173` + `dependency_tracer.md:101`
7. **接 code_committer** —— `.ut-defects.json` 加 `.gitignore` 不提交 + 提交后触发导出到本地
8. **接 collect-coverage-report.py** —— 合并进 `ut-summary.json`
9. **改 SKILL.md / README** —— 加 **Mode 5** 模块行 + 触发词 + 检查清单
10. **加示例 & eval** —— 样例数据 + 触发 eval

---

## 12. 已决问题

> 以下问题已与变异测试方案、团队约定协调后全部确定。

| # | 问题 | 决议 |
|---|------|------|
| Q1 | **模式编号** | **Mode 5**。Mode 4 已分配给变异测试，两者各自独立 |
| Q2 | **数据模型是否与变异测试共用** | **不共用**。本方案反映源码真实缺陷，与变异测试的「测试有效性」是不同维度，各自独立数据文件 |
| Q3 | **导出脚本是否与变异测试共用** | **不共用**。`export-defects.py` 纯粹服务缺陷导出，变异测试用 `mutation_score.py` |
| Q4 | **颗粒度** | **用例级足够**。不预留 `mutation_id`，变异测试有自己独立的数据模型 |
| Q5 | **fixed 记录保留策略** | **按 base_sha 归档**。缺陷与代码版本绑定，`base_sha` 变更时旧缺陷移入 `history[old_sha]`，stats 只反映当前版本 |
| Q6 | **severity 是否人工可调** | **纯派生**。后续都是人工修复，不需要 `severity_override` 字段 |
| Q7 | **导出格式** | **JSON + Markdown**。不要 CSV/HTML，md 内链接跳转源码即可 |
| Q8 | **PMS/Issue 关联** | **不导出**。只本地查看，不集成 pms-bugfix |
| Q9 | **`needs_manual` 是否拆开** | **不拆**。统一 `needs_manual` 类型，不设 `sub_reason` |

---

## 附录 A：与现有 Iron Laws 的兼容性

| Iron Law | 兼容性 |
|----------|--------|
| #7 不修源码 | ✅ 导出只是交付标红清单，不修源码 |
| #6 逐类闭环 | ✅ 持久化在编译期/运行期/重试耗尽多捕获点增量写，不阻塞其他类 |
| #10 迭代上限 3 轮 | ✅ `iteration_count` 记录入 defect，统计时可筛「max_iterations_exceeded」 |
| #11 usecase_count 实时更新 | ✅ 缺陷 upsert 与 usecase_count 更新互不干扰 |
| 批次提交只 commit 不 push | ✅ `.ut-defects.json` 不入 git（`.gitignore`），本地存储 |

## 附录 B：与 `.ut-inventory.json` 的关系

| | `.ut-inventory.json` | `.ut-defects.json` |
|---|---|---|
| 定位 | 单元测试状态真相源 | 源码缺陷真相源 |
| 产生 | Mode 1 全量扫描 | Mode 2 编译期/运行期/重试耗尽多捕获点增量 upsert |
| 更新 | Mode 2 每类编译通过更新 usecase_count | failure_repairer 标红 / build_verifier 通过 |
| 版本控制 | ✅ 入库（团队对齐真相源） | ❌ 不入库（本地存储 + `.gitignore`，检测派生产物） |
| 消费方 | Mode 2/3 | 导出模块 / Mode 3 合并 |
| 关联字段 | `methods[].qualified_name` / `level` | `defect.method_qn` / `method_level` 取自 inventory |

两者通过 `method_qn` 关联，`.ut-defects.json` 的 `method_level` 取自 inventory，severity 派生依赖此字段。
