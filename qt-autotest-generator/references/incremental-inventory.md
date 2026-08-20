# Inventory 增量更新（人工标记 overlay）

> 落地于 `scripts/fetch-mcp-data.py --incremental`。填补 `references/inventory.md` Step 2 的 TODO 与 `references/reconcile-logic.md` 的"方法级 diff"。

## 核心思路

**以图谱最新数据全量重建 `methods`，旧 inventory 中只有"人工标记"需要同步回写。**

- qn 对得上 → 同步人工字段
- qn 对不上（方法已删）→ **直接丢弃，不留墓碑**
- **不做改名软匹配**：qn 是唯一主键，qn 变了即视为新方法，人工标记自然丢失（会在 diff 报告里列出）

> 不依赖 `git diff`。所谓"增量"是新旧 inventory 的集合差（按 qn），不是 git diff。全量采集 + 全量评分保证 P75、factor 始终最新，无需额外连带处理。

## 触发方式

```bash
python3 scripts/fetch-mcp-data.py \
  --project <project_name> \
  --output ${test_dir}/.ut-inventory.json \
  --base-sha $(git -C <project_path> rev-parse HEAD) \
  --incremental \
  --existing ${test_dir}/.ut-inventory.json \
  --summary
```

- `--incremental`：开启 overlay 模式（前置校验 `--existing` 存在，否则 fail-fast 退出）
- `--existing`：旧 `.ut-inventory.json` 路径，从中提取人工标记
- `--output` 可以与 `--existing` 相同（原地覆盖，自动备份 `.bak`），也可不同（写新文件）
- `--base-sha`：当前 HEAD SHA，写入新 inventory 的 `base_sha`

## 同步的字段

### methods[] 人工字段（按 qn 匹配回写）

| 条件 | 同步字段 | 说明 |
|------|---------|------|
| `source == "manual"` | `source` + `level` | 人工设定的 level 覆盖新评分 |
| `review_status == "confirmed"` | `review_status` | 人工确认状态保留（即使 level 未改） |
| `usecase_count > 0` | `usecase_count` | Mode 2 写入的用例数保留 |

> 一个方法可同时命中多条（如 manual + confirmed + usecase>0），全部回写。
> 新评分产出的 `factors` / `score` 保留不动 —— 即使 level 被 manual 覆盖，factors 仍反映"auto 会怎么判"，供透明参考。

### 顶层配置

| 字段 | 处理 |
|------|------|
| `file_overrides` | **整体保留**（per-file 人工覆盖，正交于评分） |
| `review_queue` 中 `confirmed` 条目 | **保留**（仅当对应方法仍存在） |
| `scope_rules` / `gate_thresholds` | 跟随 `build_inventory` **重新生成**（与 `testable` 计算保持一致；如需自定义豁免，用 `file_overrides`） |

### review_queue 合并规则

- 旧 `confirmed` 且方法仍存在 → 保留（`review_status=confirmed`）
- 旧 `confirmed` 但方法已删 → 丢弃（与"不存在的都去掉"一致）
- 新 `pending`（fresh build 生成）且未被旧 confirmed 覆盖 → 追加
- 新 `pending` 但该方法已 confirmed → **抑制**（避免重复 pending 条目）

## 产出

| 文件 | 说明 |
|------|------|
| `${output}` | 新 `.ut-inventory.json`（`base_sha` 已更新） |
| `${output}.bak` | 旧文件备份（仅当 output == existing 原地覆盖时生成；建议添加 `*.bak` 到项目 `.gitignore`） |
| `${output%.json}-summary.md` | 人读摘要（`--summary` 时） |
| `${output%.json}-diff.md` | **增量报告**：新增/删除/签名变更/level 变化/人工标记保留与丢失 |

## 增量报告内容

```markdown
## 概览
| 类别 | 数量 |
| 新增方法 | N |          # 新图谱有、旧 inventory 无
| 删除方法（已清理） | N |    # 旧有、新无 → 已丢弃
| 签名变更 | N |            # qn 相同 signature 变了 → 测试可能需重生成
| level 变化（auto 方法） | N | # 仅 source!=manual 的方法，manual 不受影响
| 人工标记保留 | N |          # 成功回写的方法数
| 人工标记丢失（方法已删） | N | # ⚠️ 带人工标记但方法已删，已清理
```

- **level 变化**只报 `source != "manual"` 的方法：manual 的 level 被 overlay 保留，必然不变
- **人工标记丢失**单独列出，提示用户从 git 历史恢复旧 inventory（若需找回人工判定）

## 流程图

```
旧 .ut-inventory.json (--existing)        图谱最新数据（全量采集，同全量模式）
      │                                          │
      │ extract_human_overlay()                  │ collect_methods + collect_inheritance
      │ → {qn: {level,source,                    │ + collect_dbus_slots + collect_qt_macros
      │     review_status,usecase_count}}        │ + compute_p75_nonzero
      ▼                                          ▼
   overlay                          build_inventory() 全量评分 → 新 inventory
                                                   │
                                   apply_overlay_to_methods(new, overlay)
                                   merge_review_queue(new, old, new_qns)
                                   保留 file_overrides
                                   重算 scan_stats (review_pending/usecase_covered)
                                                   │
                                   compute_diff(old, new) → diff
                                                   ▼
                            写 ${output} (+ .bak if 原地) + -summary.md + -diff.md
```

## 关键约束

- `qualified_name` 必须来自图谱（build_inventory 已保证），增量只做按 qn 匹配，不拼接
- 全量方法必须入表（含 low / 构造 / 析构），作为覆盖率分母 —— 全量重建天然满足
- P75 每次全量重算，始终最新
- 人工标记是"软保护"：manual level 永不被 auto 覆盖；但方法本身删除时人工标记随之丢失（报告提示）
- 增量结果可直接落盘（git 是终极备份），`.bak` + diff 报告供人工复核

## 边界情况

| 情况 | 处理 |
|------|------|
| `--existing` 不存在 | fail-fast 退出 1，不连 MCP |
| `--existing` JSON 损坏 | JSON 解析异常退出（未做容错，刻意暴露问题） |
| 图谱与 HEAD 未同步 | 本脚本不校验图谱新鲜度，由 `reconcile-logic.md` 上层负责 |
| output == existing | 自动备份 `.bak` 后覆盖 |
| output != existing | 写新文件，不备份（用户可对比后手动替换） |
