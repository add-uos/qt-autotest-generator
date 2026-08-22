# 源码缺陷导出与统计（Mode 5）

> 前置条件：`{test_dir}/.ut-defects.json` 存在（不存在则提示无缺陷记录，不报错）；可选 `.ut-inventory.json`（有则补 `method_level` / `severity` 字段）。

## 概述

只读导出/统计源码缺陷，**不跑测试、不编译、不改测试代码、不改源码**。将 `.ut-defects.json` 中积累的缺陷记录转化为机器可读快照和人读标红清单。与 Mode 3 的 `ut-summary.json` 解耦，缺陷数据走独立的 `defects.json` / `defects-summary.md` 产出。

## 适用时机 / 触发条件

用户意图为**查看/导出/统计源码缺陷**，典型触发词：

- 导出源码缺陷、统计源码缺陷、defect report、缺陷清单、导出缺陷数据
- 看看有哪些源码缺陷、列出缺陷、缺陷统计

**自动触发场景**：

- **最终退出前触发**（所有批次提交完成后，与 Mode 3 覆盖率报告一起产出，只执行一次）。原因：Mode 2 可能有**多笔批次提交**，每笔提交后立即导出会重复且状态不完整；最终退出前导出一次即可反映全部缺陷的完整快照。详见 `test-writer.md` §9。

## 主入口

```bash
python3 ${SKILL_DIR}/scripts/export-defects.py export \
  --project-dir ${PROJECT_PATH} \
  --defects-file {test_dir}/.ut-defects.json \
  --output-dir ${REPORT_DIR} \
  [--inventory {test_dir}/.ut-inventory.json]
```

**参数说明**：

| 参数 | 必选 | 默认值 | 说明 |
|------|------|--------|------|
| `--project-dir` | ✓ | — | 项目根目录 |
| `--defects-file` | | `{test_dir}/.ut-defects.json` | 缺陷记录本地真相源路径 |
| `--output-dir` | | `build-ut` | 报告输出目录名（相对项目根） |
| `--inventory` | | 自动探测 | `.ut-inventory.json` 路径，用于补 `method_level` / `severity` |

## 工作步骤

### 1. 探测缺陷文件

检查 `{test_dir}/.ut-defects.json` 是否存在：

- **存在** → 继续后续步骤
- **不存在** → 输出提示「当前无源码缺陷记录」，正常退出，不报错

### 2. 可选加载 inventory

若 `--inventory` 指定或自动探测到 `.ut-inventory.json`，读取方法分级信息，为每条缺陷记录补全：

- `method_level`：high / mid / low
- `severity`：`high` / `mid` / `low`（派生规则见 `references/defect-schema.md` §severity 派生规则）

无 inventory 时保留缺陷原始字段，不报错。

### 3. 导出缺陷数据

调用 `export` 子命令，产出两个文件：

- `{output_dir}/defects.json` — 机器读快照（完整缺陷数组 + 统计摘要）
- `{output_dir}/defects-summary.md` — 人读标红清单（Markdown 表格）

### 4. 打印统计摘要

在终端输出缺陷统计概览：

```
源码缺陷统计：
  总计: 5
  状态: open=4, fixed=1, reopened=0, wontfix=0
  类型: (各类型分布)
  严重度: (各严重度分布)
```

## 实时落盘说明（供其他 reference 引用）

Mode 2 闭环中，各子 Agent 在发现或确认缺陷时，通过 `export-defects.py` 实时写入 `.ut-defects.json`：

| 调用时机 | 子命令 | 调用方 |
|----------|--------|--------|
| `failure-repairer` §6 标红 | `upsert` | failure-repairer |
| `build-verifier` 编译期确认源码缺陷 | `upsert --detected-at-stage compile` | build-verifier |
| `test-code-gen` 私有构造无工厂 | `upsert --type needs_manual` | test-code-gen |
| `dependency-tracer` 循环依赖 | `upsert --type needs_manual` | dependency-tracer |
| `build-verifier` 用例通过 | `mark-fixed` | build-verifier |
| `reconcile` base_sha 漂移 | `_archive_on_sha_change`（脚本内部） | reconcile |

> `.ut-defects.json` 是**本地真相源**，不入 git。Schema 详见 `references/defect-schema.md`。

## 产出目录结构

```
{report_dir}/
├── defects.json              # 机器读快照
└── defects-summary.md        # 人读标红清单
```

## 数据文件

### `.ut-defects.json`

- 定位：本地真相源，记录 Mode 2 闭环中所有发现的源码缺陷
- 不入版本控制（加 `.gitignore`）
- 完整 Schema 详见 `references/defect-schema.md`

### `defects.json`（导出产物）

导出脚本的机器读快照，包含完整缺陷数组及统计摘要。导出产物也不入 git。

## 关键约束

- **只读导出**：不跑测试、不编译、不改测试代码、不改源码（与 Mode 3 同构）
- **`.ut-defects.json` 不入版本控制**：加入 `.gitignore`
- **导出产物也不入 git**：`defects.json`、`defects-summary.md` 均为本地产出
- **无缺陷记录时静默退出**：不报错，只输出提示信息