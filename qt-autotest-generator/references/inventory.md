# 函数重要性探测（Mode 1）

> 前置条件：知识图谱已就绪（`references/environment-check.md` 门禁通过），GitNexus 端点已配置（QTAG_MCP_URL）。

## 适用时机

用户意图为**分析项目函数重要性**，而非直接编写测试。典型触发：

- 扫描函数重要性、建立分级表、探测分级、生成 inventory
- 项目初始化单测分析
- Mode 2 启动时发现 `.ut-inventory.json` 不存在 → 自动触发

**与 Mode 2 关系**：本模式**不**执行测试代码生成、编译、运行。

## 概述

本项目级一次性探测，扫描知识图谱中所有函数/方法，按多因子评分模型判定每个可测函数的重要性等级（high / mid / low），产出 `${test_dir}/.ut-inventory.json` 作为 Mode 2 的唯一真相源。

本阶段**不生成测试代码、不编译、不运行**，只建表。

## 工作步骤

### 1. 环境确认

复用 `references/environment-check.md` 的图谱就绪检查：

```python
repos = list_repos_pages()   # 分页遍历（limit ≤ 200）
target = next((r for r in repos if r["name"] == project_name), None)
# target 命中 → 继续，target["lastCommit"] 即图谱基线
# not found → 平台未索引本项目 → 硬终止（GitNexus 无本地索引可补）
```

### 2. 检查存量

```python
inventory_path = f"{test_dir}/.ut-inventory.json"
if file_exists(inventory_path):
    existing = read_json(inventory_path)
    if existing["base_sha"] == current_git_sha() and existing.get("methods"):
        # 表是最新的（且非空骨架）跳过全量扫描，直接输出统计。
        # methods 守卫：environment-check §6 可能预写空骨架（base_sha=图谱 lastCommit、
        # methods=[]），不守卫会误判“表最新”而跳过 Mode 1 全量建表，永远建不出真表。
        print_summary(existing)
        return
    else:
        # 增量更新：全量重建 + 同步旧 inventory 的人工标记
        # 完整方案见 references/incremental-inventory.md
        # 落地：mcp-scan.py fetch --incremental --existing <path>
        #   - qn 对得上 → 回写 level/source/review_status/usecase_count
        #   - qn 对不上 → 直接丢弃（不留墓碑，不做改名软匹配）
        #   - file_overrides 整体保留；review_queue confirmed 条目保留
        #   - 产出 -diff.md 报告供人工复核
        head = current_git_sha()
        # QTAG_MCP_URL: 可选环境变量，覆盖 MCP HTTP 端点默认值；
        # QTAG_MCP_API_KEY / QTAG_MCP_HEADERS: 部分本地/代理端需认证（见 dev-preflight.md）
        mcp_url = os.environ.get("QTAG_MCP_URL")
        cmd = [
            "python3", f"{skill_dir}/scripts/mcp-scan.py",
            "fetch",
            "--project", project_name,
            "--output", inventory_path,        # 原地覆盖，脚本自动备份 .bak
            "--base-sha", head,
            "--incremental",
            "--existing", inventory_path,    # 旧 inventory（即当前文件）
            "--summary",
        ]
        # file-pattern 可选：不传默认全项目；多源目录需逐条追加，勿固化单一默认值
        for fp in (file_pattern or []):
            cmd.extend(["--file-pattern", fp])
        if mcp_url:
            cmd.extend(["--mcp-url", mcp_url])
        subprocess.run(cmd, check=True)
        # 脚本产出：inventory_path（base_sha=head）+ .ut-inventory-diff.md + .ut-inventory-summary.md
        return
# 否则 → 全量建表（Step 3）
```

### 3. 运行 `mcp-scan.py fetch`（主路径）

**首选方式**：一条命令完成 MCP 采集 → 评分 → 产出 `.ut-inventory.json`。

```bash
# 不传 --file-pattern 时默认扫描整个项目（scope_rules 自动排除 tests/3rdparty 等）
python3 scripts/mcp-scan.py fetch \
  --project <project_name> \
  --output ${test_dir}/.ut-inventory.json \
  --summary

# 多源目录项目（如 src/ + daemon/）需多次指定 --file-pattern
python3 scripts/mcp-scan.py fetch \
  --project <project_name> \
  --file-pattern "src/**" \
  --file-pattern "daemon/**" \
  --output ${test_dir}/.ut-inventory.json \
  --summary
```

- **project**：`references/environment-check.md` 确认的 GitNexus 仓库名
- **file-pattern**：可选。**不传时扫描整个项目**（`scope_rules` 自动排除 tests/3rdparty/build 等）；仅当需要收窄范围时才传，且不同项目目录结构不同，**不要固化某个默认 pattern**——单源目录项目可传 `"src/**"`，多源目录项目（如 `src/` + `daemon/`）需多次指定
- **output**：写入 `${test_dir}/.ut-inventory.json`
- **--summary**：同时产出 `${test_dir}/.ut-inventory-summary.md`（与 output 同名、`.json` 换 `-summary.md`）
- **--base-sha**：图谱基线 SHA（缺省取 `list_repos.lastCommit`，不传本地 HEAD）
- **--keep-dump**：保留中间 `mcp_dump.json`（调试用）

脚本内部自动完成：图谱方法/类分页采集（cypher）→ 继承边检测（EXTENDS/IMPLEMENTS）→ DBus 槽（本地宏解析）→ Q_INVOKABLE/Q_PLUGIN（本地 `--repo-root` 宏扫描）→ P75 计算 → `build_inventory()` 评分。复杂度/认知复杂度/签名/宏扫描一律来自本地仓库，图谱只当索引。

- HTTP 直连 MCP 服务器（JSON-RPC 2.0），~12000 方法端到端仅需 ~2 秒
- MCP 采集与评分逻辑同在 `mcp-scan.py` 内，`fetch` 子命令直接调用 `build_inventory()` + `generate_summary()`，无跨文件 import

> ⚠️ **不要手动调 MCP 再跑 `mcp-scan.py scan --mcp-dump`**，直接用 `mcp-scan.py fetch` 即可。

<details>
<summary>📖 备选：手动 3-pass 流程（仅当 mcp-scan.py 不可用时）</summary>

若 `mcp-scan.py` 不可用（如 MCP URL 变更、脚本损坏），Agent 可退回手动 3-pass 流程。

#### Pass 1 — 批量图查询（~15 次 MCP 调用）

**1A. 全量类**：`MATCH (c:Class) RETURN ...` 分页，按路径前缀过滤 is_test。

**1B. 全量方法（含自由函数）**：分别 `MATCH (m:Method)` 和 `MATCH (m:Function)` 分页（不支持标签析取，两条语句），合并后过滤 is_test。

**1C. 调用热度百分位**：客户端计算 P75（仅非零 in_degree；in_degree 由 CALLS 边 `r.type='CALLS'` 聚合）。

**1D. 继承链匹配**：`r.type='EXTENDS'/'IMPLEMENTS'` 边 + 基类名白名单筛选 DBus / 并发 / GUI 基类（节点无 base_classes 属性）。

**1E. DBus 内省**：Pass 2 中对 DBus 类本地读头文件解析 Q_SLOTS/Q_SIGNALS。

**1F. exempt 文件模式候选**：scope_rules 预定义（3rdparty/**, moc_*, ui_*, .pb. 等）。

#### Pass 2 — 精准源码检查

只对候选类本地读源文件（图谱定位 filePath，本地读头文件；大项目通常 10-50 个候选）。

**候选类筛选条件**（任一命中即进入 Pass 2）：

```python
candidates = set()

# 来自 1D: 继承 DBus / 并发基类的类
for cls in dbus_classes + concurrent_classes:
    candidates.add(cls["qualified_name"])

# 类名模式匹配
name_pattern = re.compile(r'(Plugin|Adaptor|Interface|Manager|Service|Handler|Controller)$')
for cls in target_classes:
    if name_pattern.search(cls["name"]):
        candidates.add(cls["qualified_name"])
```

逐候选类读源码，解析 Q_SLOTS、Q_SIGNALS、Q_INVOKABLE、Q_PLUGIN_METADATA。

#### Pass 3 — 评分与产出

加权评分逻辑见下表，完整实现详见 `scripts/mcp-scan.py` 的 `score_method()` 函数。

| 因子 | 得分 | 检测方式 | source |
|------|------|---------|--------|
| DBus 契约槽 | +3 | Pass 2 source 解析 | auto |
| Q_INVOKABLE | +3 | Pass 2 source 解析 | auto |
| 插件导出 | +3 | Pass 2 source 解析 | auto |
| complexity ≥ 20 | +3 | Pass 1B 图属性 | auto |
| complexity 8–19 | +2 | Pass 1B 图属性 | auto |
| complexity 5–7 | +1 | Pass 1B 图属性 | auto |
| transitive_loop_depth ≥ 3 | +3 | Pass 1B 图属性 | auto |
| linear_scan_in_loop ≥ 1 | +1 | Pass 1B 图属性 | auto |
| cognitive ≥ 30 | +2 | Pass 1B 图属性 | auto |
| cognitive 15–29 | +1 | Pass 1B 图属性 | auto |
| lines ≥ 150 | +1 | Pass 1B 图属性 | auto |
| lines 50–149 | +1 | Pass 1B 图属性 | auto |
| loop_count ≥ 5 | +1 | Pass 1B 图属性 | auto |
| alloc_in_loop ≥ 1 | +1 | Pass 1B 图属性 | auto |
| recursive | +1 | Pass 1B 图属性 | auto |
| in_degree ≥ P75(非零) | +1 | Pass 1C 百分位 | auto |
| 析构函数(~) | -1 | 名称模式 | auto |
| operator 重载 | -1 | 名称模式 | auto |
| 方法名含 delete/remove/destroy/... | suggested | 方法名模式匹配 | suggested |

**评分规则**：score ≥ 3 → high，score ≥ 1 → mid，score < 1 → low。

> **复杂度因子体系（3 层）**：
> - **主因子**：`complexity`（圈复杂度）— 与缺陷率最相关，McCabe 复杂度 ≥8 的函数 bug 率剧增
> - **辅助因子**：`cognitive`（认知复杂度）+ `lines`（代码行数）— 补充圈复杂度无法捕获的嵌套深度和规模风险
> - **风险因子**：`loop_count` / `alloc_in_loop` / `recursive` — 循环和递归是常见缺陷来源
>
> 核心原则：**辅助因子不能独立推到 high**。cognitive≥30 (+2) 或 lines≥50 (+1) 单独只能到 mid，
> 需叠加 complexity≥5 才能到 high。这避免将大型但简单的函数（如纯数据组装）误判为 high。
>
> `in_degree ≥ P75` 单独只 +1（mid-booster），需叠加 complexity ≥ 8 或其他因子才达到 high。
> **原因**：Qt 项目中 in_degree 仅衡量跨文件被引用数，信号槽/虚函数回调不产生 CALLS 边，
> 导致核心业务函数 in_degree=0。in_degree 对工具/库函数有效，对 Qt 回调函数无效。

**scope_rules 应用**：scope=exempt → `testable=false`，不论因子评分多高。

手动流程完成后，需自行组装 mcp_dump JSON 并调 `mcp-scan.py scan --mcp-dump` 评分，或用 Agent 内置逻辑复现 `build_inventory()` 的评分逻辑。

</details>

### 4. 已有用例数统计 + 写表

`mcp-scan.py` 默认 `usecase_count=0`（不扫描已有测试）。若需统计已有用例，扫描 `${test_dir}/` 下所有 `test_*.cpp`：

```python
usecase_map = {}  # "ClassQn.method_name" → count

for test_file in glob(f"{test_dir}/**/test_*.cpp"):
    content = read(test_file)
    for match in re.finditer(r'TEST_F\s*\(\s*(\w+)Test\s*,\s*(\w+)', content):
        class_test_name = match.group(1)
        method_test_name = match.group(2)
        method_name = method_test_name.split('_')[0] if '_' in method_test_name else method_test_name
        key = f"{class_test_name}.{method_name.lower()}"
        usecase_map[key] = usecase_map.get(key, 0) + 1

# 映射到 inventory methods
for m in inventory_methods:
    if m["testable"] and m["class_qn"]:
        class_short = m["class_qn"].split('.')[-1]
        key = f"{class_short}.{m['name'].lower()}"
        m["usecase_count"] = usecase_map.get(key, 0)
```

最终产出：
- `${test_dir}/.ut-inventory.json` — 机器消费（Mode 2 读取）
- `${test_dir}/.ut-inventory-summary.md` — 人读摘要（`--summary` 自动生成，与 inventory 同名、`.json` 换 `-summary.md`）

## 产出文件结构

完整 JSON Schema 详见 `references/inventory-schema.md`。

关键字段速查：

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | int | 当前 1 |
| `project` | string | 项目名 |
| `base_sha` | string | Git base SHA（对账用） |
| `gate_thresholds` | object | high/mid/low 三级覆盖率门禁 |
| `scope_rules` | array | exempt 文件模式规则 |
| `classes` | array | 类级画像：GUI 类列表（`is_gui=true`），不在列表中的类 `is_gui=false` |
| `methods` | array | 全量方法列表（含 level/factors/usecase_count） |
| `review_queue` | array | 待人工复核条目 |

## 人工复核

Agent 输出 Markdown 摘要 + review_queue，与用户交互：

```
1. Agent 输出统计摘要：

   | 类名 | high | mid | low | 非测试 | 合计 |
   |------|------|-----|-----|--------|------|
   | CalculatorInterface | 8 | 0 | 2 | 0 | 10 |
   | ... | | | | | |

   待复核条目: 12 个（默认 mid）

2. Agent 问: "逐条确认还是全部用默认值？"
   - "全部跳过" → review_queue 所有 pending → confirmed, level=mid
   - "看看" → 逐条展示，用户回复 high/mid/low
   - 用户也可直接编辑 JSON 文件，或用可视化编辑器 `../assets/ut-inventory-editor/index.html`（仓库级独立人工工具，agent 不调用）
   - 查询统计用 `scripts/utq.py`（如 `utq -P ${test_dir} stats` / `todo --level high`），详见 `references/utq-usage.md`

3. 回写 review_status
```

## 已落地

- **增量更新脚本**：已实现于 `mcp-scan.py fetch --incremental --existing`。全量重建 + 同步旧 inventory 的人工标记（`source=manual` 的 level、`review_status=confirmed`、`usecase_count`）；方法删除直接清理（不留墓碑，不做改名软匹配）；`file_overrides` 整体保留。生成增量 diff 报告（新增/删除/签名变更/level 变化/人工标记保留与丢失）。详见 `references/incremental-inventory.md`。

## 关键约束

- 不修改项目源码
- 不编译/运行测试
- 不生成测试代码
- `qn` 必须来自图谱返回，禁止自己拼接
- scope_rules=exempt 时，方法 testable 硬压为 false
- suggested 条目默认 mid，不自动标 high
- 全量方法必须入表（含 low 和构造/析构），作为覆盖率分母
- P75 必须基于 `in_degree > 0` 的方法计算，排除零值

## 实测发现与注意事项

### P75 计算必须排除零值

| 计算方式 | P75 | high_caller 数量 | 问题 |
|----------|-----|-----------------|------|
| 全值（含 0） | 1 | 426/607 (70%) | 几乎所有有调用者的方法都变成 high，分类失真 |
| 仅非零值 | 2 | 101/607 (17%) | P75=2 合理，仅 17% 的方法达到 in_degree≥2 |

### in_degree 单独不应判 high

`in_degree` 因子仅贡献 +1 分（mid-booster），需叠加 `complexity ≥ 8` (+2) 或其他因子才达到 high。

**Qt 项目的 in_degree 失效问题**：当前知识图谱中 CALLS/USAGE 边只有 Module→Method（文件级），不存在 Method→Method（函数级）调用边。Qt 核心业务函数（信号槽回调 `handleKeypadButtonPress`、虚函数重写 `paintEvent`）不被其他模块显式调用，导致 in_degree=0。
实际数据（deepin-calculator）：complexity=63~71 的核心处理函数 in_degree=0，而构造函数 in_degree=78~86（因多文件 #include 实例化）。**in_degree 仅对工具/库函数有参考价值，对 Qt 回调函数基本无效**。

### cognitive 与 complexity 的互补性

圈复杂度（complexity）和认知复杂度（cognitive）高度相关但互补：
- complexity 计算分支数，对 switch-case 友好（每 case +1）
- cognitive 计算理解难度，对深层嵌套和中断流更敏感
- 示例：`enterEvent`（cx=14, cog=105）圈复杂度不高但认知复杂度极高 = 嵌套深、逻辑中断多
- 新增 cognitive 因子后，此类函数不再被低估

### lines 因子应保守

代码行数是最直观的规模指标，但大型函数不一定复杂（如纯数据组装）。因此 lines 因子得分保守（+1/+1），
需叠加 complexity 或 cognitive 才能推到 high。避免对长但简单的函数过度评分。

### GitNexus Cypher 方言限制

- `percentileCont()` 不可用，P75 必须客户端计算
- 不支持标签析取：`(t:Method OR t:Function)` / `(t:Method|t:Function)` 均 Parser exception → 分两条语句
- `SKIP` 必须在 `LIMIT` 前；`type(r)` 函数不存在，关系类型走边属性 `r.type`
- 节点无 `base_classes`/命名空间属性 → 继承用 EXTENDS/IMPLEMENTS 边；qn 由 mcp-scan.py 分配（Class.name，撞名 @文件名/@行号）

### is_exported 不可靠

`is_exported` 字段对大多数方法返回 true（实际含义接近"non-static"），不适合作为公开 API 检测依据。已从评分因子中移除。

### 图谱 content 截断（方法体不作源）

- 图谱 content 字段在 5016 字符硬截断 → 方法体一律本地行切片（`--repo-root`）
- Q_SLOTS/Q_INVOKABLE 声明在头文件中，图谱不载头文件宏声明
- 替代方案：本地宏扫描 `scan_qt_macros_in_file`（`class X` 声明上下文内识别 Q_INVOKABLE/Q_SLOTS/Q_PLUGIN_METADATA）
