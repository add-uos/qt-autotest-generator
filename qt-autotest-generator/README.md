<div align="center">

# Qt Autotest Generator

> Qt CMake 项目单元测试自动生成：基于 codebase-memory-mcp 知识图谱，搭建 `autotests/` 框架、逐类生成 Google Test 用例、强制编译验证、覆盖率自检与报告。

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![C++17](https://img.shields.io/badge/C%2B%2B-17-blue.svg)](https://isocpp.org/)
[![Google Test](https://img.shields.io/badge/Google%20Test-required-green.svg)](https://github.com/google/googletest)
[![codebase-memory-mcp](https://img.shields.io/badge/codebase--memory--mcp-%3E%3D0.8.0-orange.svg)](https://github.com/DeusData/codebase-memory-mcp)

<br>

Qt 项目代码量大，**单测覆盖率上不去**？<br>
手动写测试**又慢又容易漏方法**？<br>
源码改了**不知道哪些测试要更新**？

[愿景](#愿景) · [功能特性](#功能特性) · [安装](#安装) · [使用](#使用) · [架构](#架构) · [示例](#示例) · [参考文档](#参考文档) · [技能入口](SKILL.md)

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
<tr><td nowrap width="1%"><strong>知识图谱驱动</strong></td><td>基于 codebase-memory-mcp 毫秒级拉取类结构、方法签名、调用链、依赖关系；硬门禁，无图谱不执行</td></tr>
<tr><td nowrap width="1%"><strong>框架搭建</strong></td><td>自动创建 <code>autotests/</code> 目录：CMake 配置、stub-ext、测试运行脚本、报告生成器</td></tr>
<tr><td nowrap width="1%"><strong>逐类生成</strong></td><td>按复杂度规划用例数（高复杂度多写边界+异常），AAA 模式，<code>{Feature}_{Scenario}_{ExpectedResult}</code> 命名</td></tr>
<tr><td nowrap width="1%"><strong>依赖追踪</strong></td><td>MCP <code>trace_path</code> 自动追踪出向调用链，按决策矩阵决定 stub 哪些依赖、编入哪些源码目录</td></tr>
<tr><td nowrap width="1%"><strong>强制验证</strong></td><td>编译+运行必须通过才报完成；失败自动分类修复，重试预算内尽力修</td></tr>
<tr><td nowrap width="1%"><strong>覆盖率自检</strong></td><td>100% public/protected 方法覆盖；缺口自动补全；SPDX 头、命名规范、stub 正确性内部自检</td></tr>
<tr><td nowrap width="1%"><strong>增量对账</strong></td><td>源码变更后自动 diff，只补新增方法、只修签名变更、只清理已删方法引用</td></tr>
<tr><td nowrap width="1%"><strong>源码缺陷标红</strong></td><td>疑似源码缺陷（编译不过/运行崩溃/逻辑矛盾）标红交还用户，不自行修源码</td></tr>
<tr><td nowrap width="1%"><strong>HTML/CSV 报告</strong></td><td>固定收尾生成覆盖率总览、逐类结果、源码缺陷清单、跳过类清单</td></tr>
<tr><td nowrap width="1%"><strong>并行处理</strong></td><td>类数 >= 5 时可并行派发多个类的处理链，加速大规模项目</td></tr>
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
| codebase-memory-mcp | >= 0.8.0 | 知识图谱 MCP，由 `setup-codebase-memory.sh` 自动安装 |
| Python | >= 3.8 | 报告生成（仅用标准库） |
| gcc/g++ | 支持 C++17 | 编译器 |

#### 可选

| 依赖 | 说明 |
|------|------|
| lcov + genhtml | 代码覆盖率 HTML 报告（`run-ut.sh` Step 5） |
| ccache | 加速重复编译 |

---

## 使用

在 Agent 中用自然语言即可触发：

| 说法 | 触发模式 |
|------|---------|
| 拉取 https://github.com/foo/bar 的 dev 分支生成单测、clone 项目建测试 | 拉取项目（用户提供 repo_url + branch） |
| 建单测、生成测试框架、add tests | 首次搭建 |
| 为 src/lib/ui 生成测试、批量生成单测 | 批量生成 |
| 补全测试、补全 MyClass 的测试 | 增量补全 |
| 测试编译失败、修测试、fix test failures | 修复失败 |
| 代码改了重新检查、重新对账、sync tests | 源码变更对账 |

建议说明 **项目路径** 或 **仓库地址 + 分支名**。例如：

```
拉取 https://github.com/deepin/terminal 的 dev 分支生成单测
```

或本地路径：

```
为 /home/user/my-qt-app 的 src/lib/core 模块生成单元测试
```

技能会自动：项目准备（拉取代码）→ 环境检查 → 搭建框架 → 逐类分析 → 追踪依赖 → 生成测试 → 编译验证 → 覆盖率自检 → 生成报告 → 提交测试代码（不 push）。

---

## 架构

### Subagent 流程

```
[project_preparer] → environment_check → framework_builder → [逐类循环] → report_generator → code_committer
  (用户提供 repo_url 时)                                    ↓
                                              class_analyzer → dependency_tracer → test_writer
                                              → build_verifier → self_checker
                                                              ↓
                                                  失败 → failure_repairer → 重验
                                                  缺口 → incremental_updater → 重验
```

### 状态传递

subagent 间通过 `autotests/.ut-session.json` 传递状态，不靠内存。

### Iron Laws

1. codebase-memory-mcp 硬门禁 —— 无图谱不执行
2. `autotests/` 固定目录名
3. Google Test only
4. 100% public/protected 覆盖
5. 强制编译+运行验证
6. 内置 stub-ext，不从网络下载
7. 不问用户确认，直接执行
8. 逐类闭环，单类失败不阻塞
9. 不修源码，只标红
10. 状态写 session 文件，不靠内存

详见 [SKILL.md](SKILL.md)。

---

## 示例

- **示例项目**：见 [examples/README.md](examples/README.md)（含示例 Qt 类、生成的测试文件、session 状态、报告样例）。

---

## 参考文档

- [技能入口与路由流程](SKILL.md)
- [详细安装说明](INSTALL.md)
- [codebase-memory-mcp 使用指南](resources/references/codebase-memory-guide.md)
- [环境搭建指南](docs/setup-guide.md)
- [示例项目](examples/README.md)

---

<div align="center">

GPL-3.0-or-later License © 2026 UnionTech Software Technology Co., Ltd.

</div>
