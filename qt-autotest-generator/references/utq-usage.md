# utq — .ut-inventory.json 快速筛查手册（补单元测试专用）

> 给 AI / 人查找"哪些函数要写单测"的查询工具。
> 脚本位置：`${SKILL_DIR}/scripts/utq.py`（自包含，纯标准库）。
> 用 `-P` 指定项目目录——技能流程里即 `${test_dir}`（含 `.ut-inventory.json` 的目录）。
> 完整字段语义与命令清单见本文档；命令均支持 `--json` 附加机读输出，供 Agent 消费。
>
> **与 MCP 的分工**：utq 只读 `.ut-inventory.json` + `test-mapping.json`（覆盖率状态、
> 用例数、分级、factors）；项目源码理解（方法体/调用链/分支）仍走 MCP，二者互补不冲突。

## 数据模型（先理解字段语义）

`.ut-inventory.json` 的核心是 `methods[]` 数组，每条记录一个函数：

| 字段 | 语义 | 用途 |
|---|---|---|
| `level` | high / mid / low 分级 | high 优先补（对应 gate_thresholds 更严的覆盖率门槛） |
| `score` | 重要性分数（可负） | 排序优先级 |
| `factors` | 打分依据 `complexity:8` `cognitive:25` `in_degree:2` 等 | 决定用例设计深度（分支多→多写分支用例） |
| `testable` | 是否可测（false=豁免） | 豁免项跳过 |
| `test_cover_count` | 调用该函数的**测试文件数**（MCP CALLS，外部工具回写） | 覆盖判定信号 ① |
| `usecase_count` | GTest 用例数（mode2-ops usecase 回写） | 覆盖判定信号 ②；已测函数的用例量，判断是否薄弱 |

> **覆盖判定为双信号**：`test_cover_count > 0` **或** `usecase_count > 0` 任一成立即已覆盖。
> 原因：Mode 2 写完测试立即回写 `usecase_count`，但外部 fetch-test-mapping 可能未跑
> （`test_cover_count=0`），只看信号 ① 会误判为待写，导致重复写测试。
| `test_files` / `test_cases` | 覆盖它的测试文件 / 用例名 | 避免重复写、参考已有用例风格 |
| `review_status` | auto / pending / exempt | pending = 待人工定级 |
| `exempt_reason` | 豁免原因（如 `scope:tests/**`） | 排查豁免是否合理 |

## 场景 → 命令速查

### S1. 项目摸底：先看总体缺口

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} stats
```
输出各级别 可测/已测/未测/覆盖率 + 门槛。当前项目：613 可测，273 已测(44%)，340 待写，high 级缺口 14 个。

### S2. 最该先写的：高分未测函数

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} top 20      # 按分数
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} todo --level high   # 只看 high 级
```

### S3. 定点补某个模块/文件

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} file imageeditcontroller
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} file pathviewproxymodel --level high
```
列出该文件全部函数及 例(用例数)/测(测试文件数)，一眼看出哪些没测。

### S4. 定点补某个类

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} class ImageEditController
```

### S5. 写测试前查参考：已有用例长什么样

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} info saveComposite   # 单函数完整信息
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} covered --kw detectImageFormat --show-cases
```
`info` 返回签名、factors（复杂度依据）、已有 test_cases 名及注释 → 直接指导用例设计。

### S6. 找薄弱模块（按文件/类聚合）

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} files --sort pct --limit 10   # 覆盖率最低的文件
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} classes --limit 10           # 未测最多的类
```

### S7. 弱覆盖：已测但用例太少

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} weak
```
score≥3（复杂）但用例≤1 → 补分支/边界用例的首选。

### S8. 避免重复：某测试文件已覆盖了什么

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} by-test-file ut_filecontrol
```
写 `ut_xxx.cpp` 前先反查，防止重复造用例。

### S9. 评审/豁免队列

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} pending     # 待人工定级 20 条
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} exempt      # 豁免清单+原因
```

### S10. 导出任务包（喂给子代理批量写）

```bash
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} export --level high > /tmp/tasks.json
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} export --file unionimage --limit 10 | tail -n +3
```
JSON 含 name/签名/factors/level/score，可直接作为子代理的输入任务单。

通用过滤项（todo/file/class/search/covered/weak/export 均支持）：
`--level high|mid|low`、`--file 子串`、`--class 子串`、`--kw 关键字`、`--limit N`、`--json`（附加 JSON 输出）、`--include-exempt`（默认隐藏豁免项）。

## 兜底：无脚本时用 jq（在任何有 .ut-inventory.json 的目录）

```bash
# 未覆盖 high 函数（TSV，按分数排序；双信号：两个计数都为 0 才算未覆盖）
jq -r '.methods[] | select(.testable and ((.test_cover_count // 0) == 0)
       and ((.usecase_count // 0) == 0) and .level=="high")
       | [(.score//0), (.class_qn // "-"), .name, .signature, .file_path] | @tsv' \
  .ut-inventory.json | sort -rn

# 某文件的全部未测函数名（同样双信号）
jq -r '.methods[] | select(.testable and ((.test_cover_count // 0) == 0)
       and ((.usecase_count // 0) == 0)
       and (.file_path|contains("文件名"))) | .name' .ut-inventory.json

# 弱覆盖：score>=3 但用例<=1（已有测试 = 双信号任一 >0）
jq -r '.methods[] | select(.testable and (((.test_cover_count//0)>0)
       or ((.usecase_count//0)>0))
       and (.score//0)>=3 and ((.usecase_count//0)<=1))
       | [(.score//0), (.class_qn//"-"), .name] | @tsv' .ut-inventory.json | sort -rn

# 总览
jq '.scan_stats' .ut-inventory.json

# 按级别统计已测/未测（双信号）
jq -r '.methods | group_by(.level) | .[] | [.[0].level, length,
       (map(select(((.test_cover_count//0)>0) or ((.usecase_count//0)>0)))|length)] | @tsv' .ut-inventory.json
```

## AI 补测试的推荐工作流

1. `stats` → 摸底，确定补测策略（high 优先）
2. `files --sort pct` / `classes` → 锁定薄弱模块
3. `file <模块>` / `class <类>` → 拿到待写函数清单（含签名）
4. `info <函数名>` → 看 factors 设计用例（complexity 高→分支覆盖；in_degree 高→集成调用方多）
5. `by-test-file ut_<模块>` → 查已有用例风格，新用例命名对齐
6. `export --file <模块> > tasks.json` → 分发给子代理写
7. （人工/编辑器侧）重跑 `fetch-test-mapping.py`（`assets/ut-inventory-editor/scripts/`）回写 `test_*` 字段，再 `stats` 验证缺口收敛。**Agent 流程不依赖此步**：`usecase_count` 由 `mode2-ops usecase` 每类编译通过后即时回写，已是权威覆盖信号；`test_*` 由编辑器 `batch-collect` 在人工侧回写，reconcile 增量重建会保留（见 `inventory-schema.md`「覆盖率状态字段」）。纯 agent 流程下 `by-test-file` / `info --show-cases` 等 `test_*` 反查命令可能无数据，需先跑 fetch-test-mapping（或直接 `read` 已生成的 `test_*.cpp`）。
