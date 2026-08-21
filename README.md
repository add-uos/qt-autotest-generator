<div align="center">

# UOS AI 单元测试技能集

> Qt/C++ 项目自动化单元测试的全链路技能：从**函数重要性探测**、**GTest 用例生成与编译验证**、**分级覆盖率门禁**，到**多智能体小队协作**补测交付。

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![codebase-memory-mcp](https://img.shields.io/badge/codebase--memory--mcp-%3E%3D0.8.0-orange.svg)](https://github.com/DeusData/codebase-memory-mcp)

</div>

---

## 项目定位

写了几万行 Qt/C++ 代码，单测覆盖率上不去？手动写测试又慢又容易漏方法？源码改了不知道哪些测试要更新？

本仓库是一套**面向 Deepin/UOS Qt 项目的自动化单元测试技能集**，解决从「零测试」到「覆盖率达标」的全链路问题：

- **知识图谱驱动**：基于 codebase-memory-mcp 毫秒级分析类结构、方法签名、调用链、依赖关系
- **多因子评分分级**：不是所有方法都值得一样严格——DBus 契约槽、高圈复杂度函数测 90%+，简单 getter 测 60% 够了
- **编译验证闭环**：生成 GTest 代码 → 编译 → 运行 → 覆盖率自检 → 不达标自动补全 → 提交
- **多智能体协作**：广度补全 + 深度补全 + 验证审查，用 Multica 小队编组交付 `.patch.gz`

---

## 仓库结构

```
skills/
├── qt-autotest-generator/      ← 核心 Skill：五模式单测生成引擎
│   ├── SKILL.md                   Agent 技能入口（触发条件 + 工作流）
│   ├── README.md                  Skill 详细说明
│   ├── INSTALL.md                 安装指南
│   ├── scripts/                   Python/Bash 工具脚本（5300+ 行）
│   │   ├── scan-inventory.py        Mode 1：函数重要性探测与评分
│   │   ├── fetch-mcp-data.py        Mode 1：MCP 数据采集端到端
│   │   ├── coverage-by-level.py     分级覆盖率统计
│   │   ├── collect-coverage-report.py  Mode 3：覆盖率采集与汇总
│   │   ├── mutation-score.py        Mode 4：变异测试
│   │   ├── export-defects.py       Mode 5：源码缺陷导出
│   │   ├── stale-test-cleanup.py   过时测试清理
│   │   ├── setup-codebase-memory.sh MCP 本地安装
│   │   ├── generate-cmake-utils.sh  CMake 工具链生成
│   │   ├── generate-runner.sh       测试运行脚本生成
│   │   └── tests/                   单元测试（309 项，pytest）
│   ├── references/                参考文档（24 篇，按需读取）
│   ├── templates/                 代码模板 + stub-ext 库
│   ├── examples/                  示例 Qt 项目
│   ├── assets/                    ut-inventory-editor 设计
│   └── evals/                     触发与质量评估
│
├── ut-squad/                    ← 多智能体小队：广度+深度+验证协作
│   ├── README.md                  小队设计总纲（度量模型 + 执行流程）
│   ├── squad-instructions.md      队长路由指令
│   ├── build-ut-squad.sh          Multica 建队脚本
│   ├── roles/                     四角色指令
│   │   ├── ut-leader.md             👑UT-队长/规划
│   │   ├── ut-breadth.md            UT-广度补全
│   │   ├── ut-depth.md             UT-深度补全
│   │   └── ut-verifier.md          UT-验证审查
│   ├── skills/                    小队专属 Skill
│   │   ├── ut-depth-enhancer/       深度补全技能
│   │   └── ut-coverage-verifier/    覆盖率验证技能
│   └── qt-autotest-generator-exemption-patch.md  豁免改造说明
│
├── doc/                         ← 设计文档与最佳实践
│   ├── 最佳实践规则.md            Multica 小队通用规则
│   ├── roles.md                  智能体大队角色提示词
│   ├── mode4-mutation-design.md  变异测试设计
│   ├── defect-export-design.md   缺陷导出设计
│   ├── scoring-review-proposal.md 评分复核提案
│   ├── test-effectiveness-comparison.md  测试有效性对比
│   ├── uos-ai开发小队实践.md     开发小队实践
│   ├── skills-lock.json          外部技能锁定
│   ├── test-all-projects.sh      批量验证脚本
│   └── ut-recover.sh             断点续跑脚本
│
└── .pi/skills/                  Pi 技能（skills-best-practices）
```

---

## 核心组件详解

### 1. qt-autotest-generator — 五模式单测生成引擎

这是本仓库的核心技能，单个 Agent 即可完成从分析到测试生成到覆盖率验证的全流程。支持五种模式，按需触发：

| 模式 | 何时用 | 产出 |
|------|--------|------|
| **Mode 1 · 函数重要性探测** | 项目初始化、扫描方法分级 | `.ut-inventory.json` 分级表 + Markdown 摘要 |
| **Mode 2 · 单元测试生成** | 按 inventory 补全 GTest 用例 | 测试代码 + 编译验证 + 覆盖率门禁 + usecase_count 更新 |
| **Mode 3 · 覆盖率采集与汇总** | 只看覆盖率，不改测试 | gtest XML + lcov HTML + 分级 JSON + 三合一汇总 |
| **Mode 4 · 变异测试**（可选） | 验证已有测试能否拦住缺陷 | 变异得分 + 存活变异体建议清单 |
| **Mode 5 · 源码缺陷导出**（可选） | 导出测试发现的源码缺陷 | `defects-summary.md` 标红清单 + `defects.json` |

**关键设计决策**：

- **知识图谱硬门禁**：无 codebase-memory-mcp 索引不执行，不降级到文件扫描/LSP。远端优先，本地兜底。
- **Google Test only**：不用 Qt Test / Catch2 / doctest。
- **函数重要性分级**：不是所有方法一视同仁。多因子评分（圈复杂度 + 认知复杂度 + DBus 契约 + 并发基类 + 循环风险 + ...），分为 high/mid/low 三级，差异化覆盖率门禁。
- **逐类闭环**：每个类独立走完 依赖追踪→生成→编译→自检；单类失败跳过不阻塞。
- **不修源码**：疑似源码缺陷标红交还用户，不自行修改。
- **增量对账**：源码变更后自动 diff，只补新增、只修签名变更、只清理已删引用。

**覆盖率门禁**（由 `.ut-inventory.json` 的 `gate_thresholds` 外部确定，可自定义）：

| 级别 | 行覆盖率 | 分支覆盖率 | 函数覆盖率 |
|------|----------|-----------|-----------|
| 🌟 high | ≥ 90% | ≥ 80% | 100% |
| ⚖ mid | ≥ 60% | — | 100% |
| 💤 low | ≥ 60% | — | 100% |

**评分因子体系**（19 种）：

```
主因子    : complexity（圈复杂度）        — 与缺陷率最相关
辅助因子  : cognitive + lines             — 互补，不能独立推到 high
风险因子  : transitive_loop_depth / linear_scan_in_loop / loop_count / alloc_in_loop / recursive
契约因子  : dbus_slot / q_invokable / plugin_export          — 直接 +3
并发因子  : concurrent_class / concurrent_base
热度因子  : in_degree                     — mid-booster，仅对工具/库函数有效
降级因子  : destructor / operator
建议因子  : name_pattern（含 delete/remove 等不可逆操作名）
```

**脚本工具链**（5300+ 行 Python/Bash，309 项单元测试覆盖）：

| 脚本 | 用途 |
|------|------|
| `scan-inventory.py` | 函数重要性探测：MCP 数据 → 多因子评分 → `.ut-inventory.json` |
| `fetch-mcp-data.py` | 端到端 MCP 采集：分页拉取 + 继承检测 + DBus 插槽 + Q_INVOKABLE + 增量 overlay |
| `coverage-by-level.py` | 按 inventory level 统计函数+行覆盖率，门禁判定 |
| `collect-coverage-report.py` | Mode 3 一条命令出报告（gtest + lcov + 分级 + 汇总） |
| `mutation-score.py` | Mode 4 变异测试：AOR/ROR 变异体注入 → 编译运行 → 变异得分 |
| `export-defects.py` | Mode 5 缺陷导出：upsert/mark-fixed/export 三操作 |
| `stale-test-cleanup.py` | 过时测试清理：removed 方法 → 注释用例 + 清理 INSTANTIATE |
| `setup-codebase-memory.sh` | MCP 本地安装脚本 |
| `generate-cmake-utils.sh` | CMake 工具链辅助生成 |
| `generate-runner.sh` | 测试运行脚本生成 |

### 2. ut-squad — 多智能体小队

当单 Agent 的「逐类闭环」遇到复杂项目时，ut-squad 用**四角色编组**实现广度+深度双达标：

```
单元测试小队
├── 👑UT-队长/规划（intake + 路由）
│   └── 解析输入形态、锁定流程基线、设定双目标、派发任务
├── UT-广度补全
│   └── 逐方法 ≥1 用例，有效函数覆盖率=100%
├── UT-深度补全
│   └── 读源码理解分支/边界/异常，行覆盖率≥90%
└── UT-验证审查
    └── 双门禁 + 回归 + ASAN + 断言 lint → 产出 .patch.gz
```

**核心度量模型**：

| 指标 | 角色 | 门禁 |
|------|------|------|
| **有效函数覆盖率** | 广度信号 | `passed / (total − exempted) × 100%` ≥ **100%** |
| **行覆盖率** | 深度信号 | ≥ **90%** |
| **用例回归** | 回归信号 | `test_case.failed` = **0** |

**豁免机制**（只四类，显式记账，不放水）：

| category | 含义 | 确认方式 |
|----------|------|----------|
| `gui_event` | GUI 渲染/绘制事件槽 | 批量预批 |
| `entry_only` | main/事件循环入口 | 批量预批 |
| `ipc_extern` | DBus/系统服务外部依赖 | 逐项确认 |
| `hardware` | 设备/硬件依赖 | 逐项确认 |

**断言有效性 lint**（防注水，出现即判不过）：

禁止 `EXPECT_TRUE(true)` / `SUCCEED()` / `EXPECT_NO_THROW` 作为唯一断言 / 全常量断言 / 无可观测断言。

**交付格式**：`.patch.gz`（基于流程基线的全量 gzip patch），不含编译产物/源码修改/session/缓存。

### 3. 设计文档（doc/）

| 文件 | 内容 |
|------|------|
| `最佳实践规则.md` | Multica 小队通用规则：角色设计、指令精简、四段式结构、流程基线 |
| `roles.md` | 智能体大队角色提示词（含队长路由逻辑） |
| `mode4-mutation-design.md` | 变异测试设计（AOR/ROR/SDL 变异算子） |
| `defect-export-design.md` | 缺陷导出设计（severity 映射 + 归档机制） |
| `scoring-review-proposal.md` | 评分复核提案（人工标记 overlay） |
| `test-effectiveness-comparison.md` | 测试有效性对比 |
| `uos-ai开发小队实践.md` | UOS AI 开发小队具体角色和流程 |

---

## 典型使用场景

### 场景 1：新项目从零补测（单 Agent）

```
用户：为 /home/user/dde-file-manager 的 src/ 模块生成单元测试

→ Agent 自动执行：
  1. 环境检查（MCP 知识图谱就绪）
  2. Mode 1：扫描函数重要性 → 产出 .ut-inventory.json
  3. Mode 2：搭建 autotests/ 框架
  4. 按 high→mid→low 逐类生成 GTest 用例
  5. 编译验证 + 覆盖率门禁自检
  6. 不达标自动补全
  7. 提交测试代码（只 commit 不 push）
  8. Mode 3：出覆盖率报告
```

### 场景 2：源码变更后增量对账

```
用户：代码改了，帮我重新对账测试

→ Agent 自动执行：
  1. reconcile：git HEAD vs inventory.base_sha 差异路由
  2. 新增方法 → 补测试
  3. 签名变更 → 重生成对应测试
  4. 已删方法 → 清理过时测试引用
  5. 编译验证 + 覆盖率自检
```

### 场景 3：多智能体小队交付（广度+深度双达标）

```
用户：给 dde-calendar 出一个覆盖率达标的 patch

→ 小队协作：
  1. 👑队长：解析目标 → 锁定流程基线 → 跑现状覆盖率
  2. UT-广度补全：逐方法 ≥1 用例 → 有效函数覆盖率=100%
  3. 人工确认豁免清单（webhook 通知）
  4. UT-深度补全：读源码理解逻辑 → 行覆盖率≥90%
  5. UT-验证审查：双门禁 + ASAN + 断言 lint → 产出 .patch.gz
  6. 人工验证 patch → 闭环
```

---

## 安装与依赖

### 必需依赖

| 依赖 | 版本 | 说明 |
|------|------|------|
| CMake | ≥ 3.16 | 构建系统 |
| Qt | 5 或 6 | Core + Widgets 模块 |
| Google Test | 任意 | `libgtest-dev` 或源码编译 |
| codebase-memory-mcp | ≥ 0.8.0 | 知识图谱 MCP（本地兜底，远端优先） |
| Python | ≥ 3.8 | 工具脚本（仅用标准库） |
| gcc/g++ | 支持 C++17 | 编译器 |

### 可选依赖

| 依赖 | 说明 |
|------|------|
| lcov + genhtml | 覆盖率 HTML 报告 |
| ccache | 加速重复编译 |

### 接入 Agent 环境

以 Claude Code 为例：

```bash
mkdir -p .claude/skills
git clone <本仓库 URL> .claude/skills/qt-autotest-generator
```

其他环境（opencode / Cursor / Pi）详见 [qt-autotest-generator/INSTALL.md](qt-autotest-generator/INSTALL.md)。

---

## 核心文件说明

### .ut-inventory.json（项目单元测试唯一真相源）

每个项目的 `{test_dir}/.ut-inventory.json` 是测试状态的唯一真相源，**纳入版本控制**：

```json
{
  "version": 1,
  "project": "home-user-my-qt-app",
  "base_sha": "abc1234",
  "gate_thresholds": {
    "high": { "line": 90, "branch": 80, "function": 100 },
    "mid":  { "line": 60, "branch": 0,  "function": 100 },
    "low":  { "line": 60, "branch": 0,  "function": 100 }
  },
  "scope_rules": [
    { "pattern": "3rdparty/**", "scope": "exempt", "reason": "第三方库" }
  ],
  "classes": [
    { "qualified_name": "proj.FileView", "name": "FileView", "is_gui": true }
  ],
  "methods": [
    {
      "qualified_name": "proj.Calc.add",
      "name": "add",
      "level": "high",
      "score": 5,
      "factors": ["dbus_slot", "complexity:15"],
      "source": "auto",
      "testable": true,
      "usecase_count": 3
    }
  ],
  "review_queue": []
}
```

**关键字段说明**：
- `gate_thresholds`：三级覆盖率门禁阈值，**由外部确定**（首次建表用默认值，用户可直接编辑自定义，增量重建不覆盖）
- `level`：方法重要性分级（high/mid/low/null），决定覆盖率门槛
- `source`：`"auto"` 机器评分 / `"manual"` 人工覆盖 / `"suggested"` 待复核
- `usecase_count`：已有测试用例数，Mode 2 编译通过后实时更新

### 参考文档体系

`qt-autotest-generator/references/` 包含 24 篇按需读取的参考文档，Agent 只在对应子步骤触发时加载，避免上下文膨胀：

```
环境与门禁   : environment-check.md, mcp-providers.md, codebase-memory-guide.md
Mode 1      : inventory.md, inventory-schema.md, reconcile-logic.md, incremental-inventory.md
Mode 2      : test-writer.md, test-code-gen.md, test-types.md, dependency-tracer.md,
              framework-builder.md, build-verifier.md, self-checker.md,
              incremental-updater.md, failure-repairer.md, code-committer.md,
              stale-test-cleanup.md
Mode 3      : report-generator.md, coverage-tiers.md
Mode 4      : mutation-testing.md
Mode 5      : defect-exporter.md, defect-schema.md
模板与资产  : templates-guide.md
```

详见 [references/README.md](qt-autotest-generator/references/README.md)。

---

## 红旗（出现即停）

- 用 Qt Test / Catch2 框架
- codebase-memory 未 ready 就开始生成
- MCP 提供方未解析或混用多个提供方
- 未编译通过就报完成
- 从网络下载 stub-ext
- 修改用户源码（Mode 4 变异测试例外：退出时 `git diff` 必为空）
- 单类失败阻塞整批

---

## 测试覆盖

本项目自身有 **309 项 pytest 单元测试**，覆盖脚本的核心逻辑：

```bash
cd qt-autotest-generator/scripts/tests
python3 -m pytest -v
```

测试文件清单：

| 文件 | 覆盖范围 |
|------|----------|
| `test_scan_inventory.py` | 评分因子、glob 匹配、scope 过滤、build_inventory |
| `test_fetch_mcp_data.py` | MCP 采集、增量 overlay、review queue 合并、diff 报告 |
| `test_gate_thresholds.py` | gate_thresholds 外部设定保留、增量不覆盖 |
| `test_coverage_by_level.py` | lcov 解析、demangle、分级覆盖率统计 |
| `test_export_defects.py` | 缺陷 severity、归档、导出 |
| `test_mutation_score.py` | 变异算子、函数定位、变异体应用 |
| `test_stale_test_cleanup.py` | 测试块提取、方法匹配、注释清理 |
| `test_collect_coverage_report.py` | gtest XML 解析、lcov 汇总 |

---

## 许可证

GPL-3.0-or-later © 2026 UnionTech Software Technology Co., Ltd.
