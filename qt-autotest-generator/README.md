<div align="center">

# Qt Autotest Generator

> Qt CMake 项目单元测试自动生成：基于 codebase-memory-mcp 知识图谱，**函数重要性探测**（Mode 1）、**按分级补全 GTest 用例**（Mode 2，编译验证+覆盖率门禁+更新 usecase_count）、**覆盖率采集与汇总**（Mode 3，一条命令出分级报告）。

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![C++17](https://img.shields.io/badge/C%2B%2B-17-blue.svg)](https://isocpp.org/)
[![Google Test](https://img.shields.io/badge/Google%20Test-required-green.svg)](https://github.com/google/googletest)
[![codebase-memory-mcp](https://img.shields.io/badge/codebase--memory--mcp-%3E%3D0.8.0-orange.svg)](https://github.com/DeusData/codebase-memory-mcp)

<br>

Qt 项目代码量大，**单测覆盖率上不去**？<br>
手动写测试**又慢又容易漏方法**？<br>
源码改了**不知道哪些测试要更新**？

[愿景](#愿景) · [功能特性](#功能特性) · [安装](#安装) · [使用](#使用) · [示例](#示例) · [参考文档](#参考文档) · [技能入口](SKILL.md)

</div>

---

## 愿景

> **写了 5 万行 Qt 代码，单测覆盖率不到 10%。**

不是不想写测试，是写不过来：类太多、方法太多、依赖太复杂、stub 太难配。手动写一个类的测试要半天，50 个类就是一个月。改了源码还得手动对账哪些测试要更新。

本技能把这一环打通：基于 codebase-memory-mcp 知识图谱批量分析类结构、自动追踪依赖、按复杂度规划用例数、生成 Google Test 代码、强制编译验证、覆盖率自检、生成报告。源码变更后自动对账，只补缺失的、只修失败的。让真正干活的人，也能把测试覆盖率提上去。

---

## 功能特性

<table>
<colgroup>
<col width="1%">
<col>
</colgroup>
<thead>
<tr><th align="left" nowrap width="1%">能力</th><th align="left">说明</th></tr>
</thead>
<tbody>
<tr><td nowrap width="1%"><strong>三模式架构</strong></td><td><strong>Mode 1</strong>（函数重要性探测）：全量扫描知识图谱，多因子评分，产出 <code>.ut-inventory.json</code> 分级表。<strong>Mode 2</strong>（单元测试生成）：读取分级表，按 high→mid→low 优先级逐类生成 GTest 用例，编译验证+覆盖率门禁。Mode 2 启动时若 inventory 不存在则自动触发 Mode 1。<strong>Mode 3</strong>（覆盖率采集与汇总）：一条命令采集 gtest XML + lcov HTML + 分级覆盖率 + 汇总 JSON，不生成测试代码。</td></tr>
<tr><td nowrap width="1%"><strong>知识图谱驱动</strong></td><td>基于 codebase-memory-mcp 知识图谱毫秒级拉取类结构、方法签名、调用链、依赖关系；硬门禁，无图谱不执行。<strong>支持远端（<code>remote-codebase-memory-mcp</code>）与本地两种提供方，远端优先</strong></td></tr>
<tr><td nowrap width="1%"><strong>框架搭建</strong></td><td>自动创建 <code>{test_dir}/</code> 目录（默认 <code>autotests/</code>，若项目已有 <code>tests/</code> 则沿用）：CMake 配置、stub-ext、测试运行脚本、报告生成器</td></tr>
<tr><td nowrap width="1%"><strong>逐类生成</strong></td><td>按复杂度规划用例数（高复杂度多写边界+异常），AAA 模式，<code>{Feature}_{Scenario}_{ExpectedResult}</code> 命名</td></tr>
<tr><td nowrap width="1%"><strong>依赖追踪</strong></td><td>MCP <code>trace_path</code> 自动追踪出向调用链，按决策矩阵决定 stub 哪些依赖、编入哪些源码目录</td></tr>
<tr><td nowrap width="1%"><strong>强制验证</strong></td><td>编译+运行必须通过才报完成；失败自动分类修复，重试预算内尽力修</td></tr>
<tr><td nowrap width="1%"><strong>覆盖率自检</strong></td><td>有 <code>.ut-inventory.json</code> 时按方法分级设差异化门禁（high 行90%+分支80%+函数100%，⚖mid 行60%+函数100%，💤low 行60%+函数100%）；无时回退单一门禁（默认 90%）。低于阈值触发自动补全</td></tr>
<tr><td nowrap width="1%"><strong>增量对账</strong></td><td>源码变更后自动 diff，只补新增方法、只修签名变更、只清理已删方法引用</td></tr>
<tr><td nowrap width="1%"><strong>源码缺陷标红</strong></td><td>疑似源码缺陷（编译不过/运行崩溃/逻辑矛盾）标红交还用户，不自行修源码</td></tr>
<tr><td nowrap width="1%"><strong>Mode 5 · 源码缺陷导出</strong></td><td>用例级缺陷持久化到 <code>.ut-defects.json</code>（不入 git），编译期即捕获；按需导出 <code>defects-summary.md</code> 标红清单（md 内链接跳转源码行）+ <code>defects.json</code>（<code>scripts/export-defects.py</code>）</td></tr>
<tr><td nowrap width="1%"><strong>Mode 3 · 覆盖率采集</strong></td><td>一条命令采集：gtest XML + lcov HTML + 分级覆盖率 + 汇总 JSON（<code>scripts/collect-coverage-report.py</code>）。不生成测试代码，只读采集。</td></tr>
</tbody>
</table>

---

## 安装

### 接入任意支持 Agent Skills 的环境

通用做法（任选其一）：

1. **克隆 / 复制到宿主的 skills 目录**（全局或当前项目均可），例如：
   ```bash
   git clone <本仓库 URL> <宿主-skills目录>/qt-autotest-generator
   ```
2. **直接用 Agent 打开本仓库根目录**作为工作区；此时把「含 `SKILL.md` 的目录」当作技能根。
3. 在对话里用自然语言或斜杠触发（如「建单测」「为 src/lib 生成测试」）。

Claude Code、Cursor、opencode 等兼容 AgentSkills 的客户端，具体落盘路径不同，详见 [INSTALL.md](INSTALL.md)。

### 依赖

#### 必需

| 依赖 | 版本 | 说明 |
|------|------|------|
| CMake | >= 3.16 | 构建系统 |
| Qt | 5 或 6 | Core + Widgets 模块 |
| Google Test | 任意 | `libgtest-dev` 或源码编译 |
| codebase-memory-mcp | >= 0.8.0 | 知识图谱 MCP（本地兜底，由 `setup-codebase-memory.sh` 自动安装）；或接入已索引本项目的 `remote-codebase-memory-mcp` 远端实例（远端优先） |
| Python | >= 3.8 | 覆盖率采集脚本（仅用标准库） |
| gcc/g++ | 支持 C++17 | 编译器 |

#### 可选

| 依赖 | 说明 |
|------|------|
| lcov + genhtml | 代码覆盖率 HTML 报告（`run-ut.sh` Step 5） |
| ccache | 加速重复编译 |

---

## 使用

在 Agent 中用自然语言即可触发：「建单测」「为 src/lib 生成测试」「补全测试」「修测试」「代码改了重新对账」等。完整触发表、工作流流程图、状态传递机制与 Iron Laws 详见 [SKILL.md](SKILL.md)。

建议说明 **项目路径** 或 **仓库地址 + 分支名**。例如：

```
拉取 https://github.com/deepin/terminal 的 dev 分支生成单测
```

或本地路径：

```
为 /home/user/my-qt-app 的 src/lib/core 模块生成单元测试
```

技能会自动：环境检查 → 搭建框架 → 逐类分析 → 追踪依赖 → 生成测试 → 编译验证 → 覆盖率自检 → 生成报告 → 提交测试代码（不 push）。

---

## 示例

- **示例项目**：见 [examples/README.md](examples/README.md)（含示例 Qt 类、生成的测试文件、inventory 分级表、报告样例）。

---

## 参考文档

- [技能入口与工作流](SKILL.md)
- [详细安装说明](INSTALL.md)
- [Inventory JSON 结构](reference/inventory-schema.md)
- [MCP 提供方解析指南](reference/mcp-providers.md)
- [覆盖率分级门禁](reference/coverage-tiers.md)
- [对账逻辑](reference/reconcile-logic.md)
- [codebase-memory-mcp 使用指南](reference/codebase-memory-guide.md)
- [单元测试用例设计方法论](reference/test-types.md)
- [示例项目](examples/README.md)

---

<div align="center">

GPL-3.0-or-later License © 2026 UnionTech Software Technology Co., Ltd.

</div>
