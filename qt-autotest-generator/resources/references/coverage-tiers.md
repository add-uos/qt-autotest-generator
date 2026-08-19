# 三级覆盖率分类体系

## 概述

方法按重要性分为三级，每级有差异化的覆盖率门禁。分类依据来自 Mode 1（函数重要性探测，详见 `phases/importance_inventory.md`）产出的 `.ut-inventory.json`。

## 分级定义

| 级别 | 标记 | 行覆盖率 | 分支覆盖率 | 函数覆盖率 | 说明 |
|------|------|---------|-----------|-----------|------|
| 🌟 核心 | high | ≥ 90% | ≥ 80% | 100% | DBus 契约槽、Q_INVOKABLE、插件导出、高复杂度核心逻辑 |
| ⚖ 普通 | mid | ≥ 60% | — | 100% | 一般业务方法、中等复杂度、有调用热度 |
| 💤 豁免 | low | — | — | — | 构造/析构、简单 getter/setter、运算符重载 |

> **注意**：函数覆盖率 100% 是 hard gate（每个 public/protected 函数至少被调用一次），
> 行/分支覆盖率是 soft gate（低于阈值触发增量补全，但不阻塞提交）。

## 分类来源

| source 字段 | 含义 | 触发行为 |
|------------|------|---------|
| `auto` | 纯自动评分 | 直接采用，无需人工干预 |
| `suggested` | 自动建议 | 默认 mid，进入 review_queue 待人工确认 |
| `manual` | 人工覆盖 | 用户在 review 中指定，优先级最高 |

## 向后兼容

- 旧格式 `session.coverage_threshold`（单个整数）仍有效，作为 mid 级行覆盖率阈值（默认 90→60，因为 90 是旧的函数覆盖率概念）
- 当 `.ut-inventory.json` 不存在时，回退到单一门禁：函数覆盖率 ≥ `coverage_threshold`%
- 当 `.ut-inventory.json` 存在时，按方法分级设定差异化门禁，`coverage_threshold` 不再生效

## scope_rules 与分级的关系

`scope_rules` 和方法分级是**正交维度**：

- `scope_rules` 决定 `testable`（是否可测试）
  - `exempt` 模式（如 `3rdparty/**`, `moc_*`, `ui_*`）→ `testable=false`，不入覆盖率分母
  - `core`/`normal` 模式 → `testable=true`
- 方法分级决定覆盖率门禁（在 testable=true 时）
  - `scope=exempt` 硬压 `level=null`，不论评分多高
  - `scope=core/normal` 不膨胀方法 level

## 类级分级

类级 tier 不单独存储，由其方法的最大 level 推导：

```python
class_tier = max(m.level for m in class_methods if m.testable)
# class_tier: "high" | "mid" | "low"
```

类级 tier 用于报告和优先级排序，不直接影响覆盖率计算（覆盖率始终按方法级 gate）。

## 覆盖率缺口检测

当自检阶段发现方法未达标时：

| 级别 | 行覆盖率缺口 | 分支覆盖率缺口 | 函数覆盖率缺口 |
|------|------------|-------------|-------------|
| 🌟 high | 触发增量补全 | 触发增量补全 | 触发增量补全（hard gate） |
| ⚖ mid | 触发增量补全 | 不检查 | 触发增量补全（hard gate） |
| 💤 low | 不检查 | 不检查 | 不检查 |

## 产出文件

- 产出路径：`${test_dir}/.ut-inventory.json`
- 格式详见：`phases/importance_inventory.md` 的 JSON Schema
- 由 `resources/scripts/scan_inventory.py` 从预采集的 MCP 图谱数据生成
- MCP 数据采集格式见：`resources/scripts/fetch_mcp_data.py`
