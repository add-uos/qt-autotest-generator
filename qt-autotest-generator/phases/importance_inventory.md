# 函数重要性探测

> 前置条件：知识图谱已就绪（`environment_check` 通过），`session.mcp_provider` 已确定。

## 概述

本项目级一次性探测，扫描知识图谱中所有函数/方法，按多因子评分模型判定每个可测函数的重要性等级（high / mid / low），产出 `${test_dir}/.ut-inventory.json` 作为后续分级补全的唯一真相源。

本阶段**不生成测试代码、不编译、不运行**，只建表。

## 触发条件

- 用户显式说"扫描函数重要性"、"建立分级表"、"探测分级"、"生成 inventory"
- 模式二启动时发现 `.ut-inventory.json` 不存在 → 自动触发本阶段

## 工作步骤

### 1. 环境确认

复用 `environment_check` 的图谱就绪检查：

```python
mcp = session.mcp_provider
projects = mcp.list_projects()
project_name = resolve_project_name(session.project_path, projects)
status = mcp.index_status(project=project_name)
# status="ready" → 继续
# status="indexing" → 等待
# not found → 需先索引（仅本地提供方）
```

### 2. 检查存量

```python
inventory_path = f"{session.test_dir}/.ut-inventory.json"
if file_exists(inventory_path):
    existing = read_json(inventory_path)
    if existing["base_sha"] == current_git_sha():
        # 表是最新的，跳过全量扫描，直接输出统计
        print_summary(existing)
        return
    else:
        # 增量更新（后续实现，本次先全量重建）
        pass
# 否则 → 全量建表（Step 3）
```

### 3. 全量扫描（3-pass）

#### Pass 1 — 批量图查询

~15 次 MCP 调用，毫秒~秒级。

**1A. 全量类**

```python
all_classes = []
offset = 0
while True:
    batch = mcp.search_graph(
        project=project_name,
        label="Class",
        limit=200,
        offset=offset
    )
    all_classes.extend(batch.results)
    if not batch.has_more:
        break
    offset += 200

# 过滤: is_test=true 的排除
target_classes = [c for c in all_classes if not c.get("is_test", False)]
```

**1B. 全量方法（含自由函数）**

```python
# 类方法
all_methods = []
offset = 0
while True:
    batch = mcp.search_graph(
        project=project_name,
        label="Method",
        limit=200,
        offset=offset
    )
    all_methods.extend(batch.results)
    if not batch.has_more:
        break
    offset += 200

# 自由函数（C 函数）
all_functions = []
offset = 0
while True:
    batch = mcp.search_graph(
        project=project_name,
        label="Function",
        limit=200,
        offset=offset
    )
    all_functions.extend(batch.results)
    if not batch.has_more:
        break
    offset += 200

# 合并，过滤 is_test=true
target_methods = [
    m for m in all_methods + all_functions
    if not m.get("is_test", False)
]
```

**1C. 调用热度百分位（客户端计算）**

> ⚠️ MCP 不支持 `percentileCont()` Cypher 函数，必须客户端计算。

```python
# 收集所有非测试方法的 in_degree
in_degrees = [
    m.get("in_degree", 0) or 0
    for m in all_methods + all_functions
    if not m.get("is_test", False)
]

# 关键：只对 in_degree > 0 的方法计算 P75
# 原因：大多数方法 in_degree=0，包含零值会使 P75=0 或 P75=1，
# 导致几乎所有有调用者的方法都变成"高热度"，分类失真。
# 实测：deepin-calculator 1091 方法中 412 个 in_degree=0，
# 包含零值 P75=1，排除零值后 P75=2，分类更合理。
non_zero = sorted([d for d in in_degrees if d > 0])
if non_zero:
    p75_idx = int(0.75 * (len(non_zero) - 1))
    in_degree_p75 = non_zero[p75_idx]
else:
    in_degree_p75 = 1  # fallback
```

**1D. 继承链匹配（DBus / 并发基类）**

> ⚠️ `search_graph(label="Class")` 不返回 `base_classes` 字段。
> 但 `query_graph` (Cypher) 可以返回 `base_classes`。
> 推荐方案：用 `query_graph` 直接筛选 DBus/并发基类，比逐类调 `get_code_snippet` 更高效。
> 备用方案：如 `query_graph` 不可用或超时，回退到候选类模式 + `get_code_snippet`。

```python
# 推荐方案：query_graph 直接筛选
dbus_classes = mcp.query_graph(
    project=project_name,
    query="""
        MATCH (c:Class)
        WHERE ANY(b IN c.base_classes WHERE b IN ['QDBusAbstractAdaptor', 'QDBusAbstractInterface'])
        RETURN c.name, c.qualified_name, c.file_path, c.base_classes
    """
)

concurrent_classes = mcp.query_graph(
    project=project_name,
    query="""
        MATCH (c:Class)
        WHERE ANY(b IN c.base_classes WHERE b IN ['QThread', 'QThreadPool', 'QMutex', 'QReadWriteLock', 'QSemaphore', 'QAtomicInt'])
        RETURN c.name, c.qualified_name, c.file_path, c.base_classes
    """
)

# 备用方案：query_graph 不可用时
# 步骤1：search_graph 获取所有 Class 节点
# 步骤2：按类名模式筛选候选类（Plugin/Adaptor/Interface 等）
# 步骤3：对候选类调 get_code_snippet 检查源码中的继承关系
```

**1E. DBus 内省 XML**

> ⚠️ 实测发现 XML 文件中的节点也被解析为 Class，
> 但 `search_graph` 不支持按 `base_classes` 过滤。
> `query_graph` 可直接按 `base_classes` 筛选 DBus 类。
> Q_SLOTS/Q_SIGNALS 解析仍需 `get_code_snippet`（Pass 2 中执行）。

```python
# 已在 1D 中通过 query_graph 按继承筛选了 DBus 类
# Pass 2 中对 DBus 类调 get_code_snippet 解析 Q_SLOTS/Q_SIGNALS
```

**1F. exempt 文件模式候选**

> ⚠️ `query_graph` (Cypher) 不被 MCP 支持。
> 改为：从 Pass 1B 的 `file_path` 字段中自动检测 exempt 模式。

```python
# scope_rules 已预定义 exempt 模式（3rdparty/**, moc_*, ui_*, .pb. 等）
# 不需要额外查询，在 Pass 3 评分时按 file_path 匹配即可
```

#### Pass 2 — 精准源码检查

只对候选类调 `get_code_snippet`（大项目通常 10-50 个候选）。

**候选类筛选条件**（任一命中即进入 Pass 2）：

```python
candidates = set()

# 来自 1D: 继承 DBus / 并发基类的类
for cls in dbus_classes + concurrent_classes:
    candidates.add(cls["qualified_name"])

# 来自 1E: DBus XML 关联的类（按 XML interface 名匹配类名）
for xml_iface in dbus_xml.results:
    candidates.add(xml_iface["qualified_name"])

# 类名模式匹配: Plugin / Adaptor / Interface / Manager / Service / Handler / Controller
name_pattern = re.compile(r'(Plugin|Adaptor|Interface|Manager|Service|Handler|Controller)$')
for cls in target_classes:
    if name_pattern.search(cls["name"]):
        candidates.add(cls["qualified_name"])
```

**逐候选类读源码**：

```python
dbus_slots = {}    # class_qn → [method_name, ...]
dbus_signals = {}  # class_qn → [signal_name, ...]
q_invokables = {}  # class_qn → [method_name, ...]
q_plugins = {}     # class_qn → True

for qn in candidates:
    snippet = mcp.get_code_snippet(qualified_name=qn)
    source = snippet.get("source", "")

    # 解析 Q_SLOTS 段
    # 策略: 找 "Q_SLOTS:" 或 "public Q_SLOTS:" 到下一个访问修饰符之间的方法声明
    slots = parse_qt_section(source, "Q_SLOTS")
    if slots:
        dbus_slots[qn] = slots

    # 解析 Q_SIGNALS 段
    signals = parse_qt_section(source, "Q_SIGNALS")
    if signals:
        dbus_signals[qn] = signals

    # 检查 Q_INVOKABLE
    invokables = re.findall(r'Q_INVOKABLE\s+\w+\s+(\w+)\s*\(', source)
    if invokables:
        q_invokables[qn] = invokables

    # 检查 Q_PLUGIN_METADATA / Q_INTERFACES
    if 'Q_PLUGIN_METADATA' in source or 'Q_INTERFACES' in source:
        q_plugins[qn] = True
```

**解析 DBus XML**：

```python
dbus_interface_methods = {}  # interface_name → [method_name, ...]

for xml_item in dbus_xml.results:
    xml_snippet = mcp.get_code_snippet(qualified_name=xml_item["qualified_name"])
    # 解析 XML 内容，提取 <method name="..."> 节点
    methods = parse_dbus_xml(xml_snippet.get("source", ""))
    if methods:
        dbus_interface_methods[xml_item["name"]] = methods
```

#### Pass 3 — 评分与产出

**逐方法评分**：

```python
DESTRUCTIVE_PATTERNS = re.compile(
    r'(delete|remove|destroy|truncate|write|save|persist|erase|clear|reset|wipe)',
    re.IGNORECASE
)

for method in target_methods:
    method_qn = method["qualified_name"]
    method_name = method["name"]
    parent_qn = method.get("parent_class")
    file_path = method.get("file_path", "")

    factors = []

    # ── high 因子 ──

    # DBus 契约槽
    if parent_qn and parent_qn in dbus_slots:
        if method_name in dbus_slots[parent_qn]:
            factors.append("dbus_slot")

    # Q_INVOKABLE
    if parent_qn and parent_qn in q_invokables:
        if method_name in q_invokables[parent_qn]:
            factors.append("q_invokable")

    # 插件导出方法
    if parent_qn and parent_qn in q_plugins:
        factors.append("plugin_export")

    # 并发基类方法
    if parent_qn:
        for cc in concurrent_classes:
            if cc["qualified_name"] == parent_qn:
                factors.append("concurrent_class")
                break

    # 复杂度
    complexity = method.get("complexity", 0)
    if complexity >= 10:
        factors.append(f"complexity:{complexity}")

    # 隐蔽 O(n²)
    tld = method.get("transitive_loop_depth", 0)
    if tld >= 3:
        factors.append(f"transitive_loop_depth:{tld}")

    lsl = method.get("linear_scan_in_loop", 0)
    if lsl >= 1:
        factors.append(f"linear_scan_in_loop:{lsl}")

    # ── mid 因子 ──

    # 调用热度
    in_deg = method.get("in_degree", 0)
    if in_deg >= in_degree_p75:
        factors.append(f"in_degree:{in_deg}")

    # 中等复杂度
    if 5 <= complexity < 10:
        factors.append(f"complexity:{complexity}")

    # ── suggested 因子（需人工复核） ──

    # 不可逆操作
    if DESTRUCTIVE_PATTERNS.search(method_name):
        factors.append(f"name_pattern:{method_name}")

    # ── 评分 ──
    if any(f.startswith(("dbus_slot:", "q_invokable:", "plugin_export:",
                         "concurrent_class:", "complexity:", "transitive_loop_depth:",
                         "linear_scan_in_loop:"))
           and not f.startswith("name_pattern:")
           for f in factors):
        # 去掉 name_pattern: 前缀判断，简化为：
        level = "high" if any_high_factor(factors) else \
                "mid" if any_mid_factor(factors) else "low"
```

**加权评分逻辑**：

```python
HIGH_FACTOR_SCORES = {
    "dbus_slot": 3, "q_invokable": 3, "plugin_export": 3,
}
COMPLEXITY_SCORES = {
    # complexity 值区间 → 得分
    "range_20_plus": 3,  # ≥ 20
    "range_10_19": 2,   # 10–19
    "range_5_9": 1,     # 5–9
}
OTHER_SCORES = {
    "concurrent_class": 1,
    "transitive_loop_depth": 3,  # tld ≥ 3
    "linear_scan_in_loop": 1,   # lsl ≥ 1
    "in_degree_high": 1,       # ≥ P75(非零), mid-booster only
    "name_destructive": 0,     # suggested, 不加得分
    "destructor": -1,          # 降级
    "operator": -1,            # 降级
}

def score_method(name: str, factors: list[str]) -> tuple[str, str]:
    """返回 (level, source)"""
    score = 0
    has_suggested = False
    is_constructor = False

    for f in factors:
        if f.startswith("name_pattern:"):
            has_suggested = True
            continue
        if f == "dbus_slot": score += 3
        elif f == "q_invokable": score += 3
        elif f == "plugin_export": score += 3
        elif f == "concurrent_class": score += 1
        elif f.startswith("complexity:"):
            val = int(f.split(":")[1])
            if val >= 20: score += 3
            elif val >= 10: score += 2
            elif val >= 5: score += 1
        elif f.startswith("transitive_loop_depth:"):
            val = int(f.split(":")[1])
            if val >= 3: score += 3
        elif f.startswith("linear_scan_in_loop:"):
            score += 1
        elif f.startswith("in_degree:"):
            score += 2  # in_degree alone = mid
        elif f == "destructor": score -= 1
        elif f == "operator": score -= 1

    # 构造函数检测（名称与类名相同）
    # 构造函数不加成也不扣分，保持 neutral

    if score >= 3:
        return ("high", "auto")
    elif score >= 1 or has_suggested:
        return ("mid", "suggested" if has_suggested and score < 1 else "auto")
    else:
        return ("low", "auto")
```

**scope_rules 应用**：

```python
import fnmatch

def apply_scope(file_path: str, scope_rules: list) -> tuple[bool, str | None]:
    """返回 (testable, exempt_reason | None)"""
    for rule in scope_rules:
        pattern = rule["pattern"]
        # glob 匹配: ** 任意深度, * 单层
        # 将 glob pattern 转为文件路径匹配
        if glob_match(pattern, file_path):
            if not rule.get("testable", True):
                return (False, f"scope:{pattern}")
    return (True, None)

def glob_match(pattern: str, path: str) -> bool:
    """支持 ** 的 glob 匹配"""
    # 3rdparty/** → 3rdparty/ 下的任意深度
    regex = glob_to_regex(pattern)
    return re.match(regex, path) is not None
```

**构建 review_queue**：

```python
review_queue = []
for m in scored_methods:
    if m["source"] == "suggested":
        review_queue.append({
            "qn": m["qn"],
            "name": m["name"],
            "auto_suggestion": "high",
            "auto_reason": next(
                (f for f in m["factors"] if f.startswith("name_pattern:")),
                ""
            ),
            "default_level": "mid",
            "status": "pending"
        })
```

### 4. 已有用例数统计

扫描 `${test_dir}/` 下所有 `test_*.cpp`，提取已有测试用例：

```python
usecase_map = {}  # "ClassQn.method_name" → count

for test_file in glob(f"{test_dir}/**/test_*.cpp"):
    content = read(test_file)
    # 匹配 TEST_F(ClassNameTest, MethodName_xxx)
    for match in re.finditer(
        r'TEST_F\s*\(\s*(\w+)Test\s*,\s*(\w+)', content
    ):
        class_test_name = match.group(1)
        method_test_name = match.group(2)
        # MethodName_xxx → 提取 MethodName
        method_name = method_test_name.split('_')[0] if '_' in method_test_name else method_test_name
        key = f"{class_test_name}.{method_name}"
        usecase_map[key] = usecase_map.get(key, 0) + 1

# 映射到 inventory methods
for m in inventory_methods:
    if m["testable"] and m["class_qn"]:
        # 尝试匹配: class_qn 短名 + method name
        class_short = m["class_qn"].split('.')[-1]
        key = f"{class_short}.{m['name']}"
        m["usecase_count"] = usecase_map.get(key, 0)
```

### 5. 人工复核

Agent 输出 Markdown 摘要 + review_queue，与用户交互：

```
1. Agent 输出统计摘要：

   | 类名 | high | mid | low | 非测试 | 合计 |
   |------|------|-----|-----|--------|------|
   | CalculatorInterface | 8 | 0 | 2 | 0 | 10 |
   | MainWindow | 0 | 5 | 15 | 0 | 20 |
   | ... | | | | | |

   待复核条目: 12 个（默认 mid）

2. Agent 问: "逐条确认还是全部用默认值？"
   - "全部跳过" → review_queue 所有 pending → confirmed, level=mid
   - "看看" → 逐条展示，用户回复 high/mid/low
   - 用户也可直接编辑 JSON 文件

3. 回写 review_status
```

### 6. 写表

产出两个文件：

- `${test_dir}/.ut-inventory.json` — 机器消费（模式二读取）
- `${test_dir}/.reports/inventory-summary.md` — 人读摘要

更新 session：

```json
{
  "inventory_path": "${test_dir}/.ut-inventory.json",
  "inventory_status": "confirmed"
}
```

## 产出文件结构

```jsonc
{
  "version": 1,
  "project": "deepin-calculator",
  "base_sha": "3838e807b9c5dfd2320d8e478d4054b962ae77d8",
  "generated_at": "2025-08-20T12:00:00Z",
  "scan_stats": {
    "total_nodes": 1726,
    "filtered_methods": 1091,
    "testable": 950,
    "non_testable": 141,
    "level_distribution": { "high": 23, "mid": 180, "low": 747 },
    "review_pending": 12,
    "usecase_covered": 340,
    "usecase_not_covered": 610,
    "scan_passes": {
      "batch_queries": 15,
      "source_checks": 8,
      "duration_ms": 4200
    }
  },

  "gate_thresholds": {
    "high": {"line": 90, "branch": 80, "function": 100},
    "mid":  {"line": 60, "branch": null, "function": 100},
    "low":  {"line": null, "branch": null, "function": null}
  },

  "scope_rules": [
    {"pattern": "3rdparty/**",    "testable": false, "reason": "第三方库"},
    {"pattern": "**/moc_*.cpp",   "testable": false, "reason": "MOC 生成"},
    {"pattern": "**/ui_*.h",      "testable": false, "reason": "UI 生成"},
    {"pattern": "**/.pb.",        "testable": false, "reason": "Protobuf 生成"},
    {"pattern": "tests/**",      "testable": false, "reason": "测试代码本身"},
    {"pattern": "autotests/**",  "testable": false, "reason": "测试代码本身"}
  ],

  "file_overrides": [],

  "methods": [
    {
      "qn": "CalculatorInterface.showWindow",
      "name": "showWindow",
      "signature": "bool showWindow()",
      "file": "src/calculatorInterface.cpp",
      "class_qn": "CalculatorInterface",
      "testable": true,
      "level": "high",
      "factors": ["dbus_slot"],
      "source": "auto",
      "exempt_reason": null,
      "review_status": null,
      "usecase_count": 0
    },
    {
      "qn": "floatnum.float_div",
      "name": "float_div",
      "signature": "char float_div(floatnum, cfloatnum, cfloatnum, int)",
      "file": "3rdparty/math/floatnum.c",
      "class_qn": null,
      "testable": false,
      "level": null,
      "factors": ["complexity:9"],
      "source": "auto",
      "exempt_reason": "scope:3rdparty/**",
      "review_status": null,
      "usecase_count": 0
    },
    {
      "qn": "MainWindow.deleteRecentHistory",
      "name": "deleteRecentHistory",
      "signature": "void deleteRecentHistory()",
      "file": "src/mainwindow.cpp",
      "class_qn": "MainWindow",
      "testable": true,
      "level": "mid",
      "factors": ["name_pattern:delete"],
      "source": "suggested",
      "exempt_reason": null,
      "review_status": "pending",
      "usecase_count": 0
    },
    {
      "qn": "MainWindow.initConnections",
      "name": "initConnections",
      "signature": "void initConnections()",
      "file": "src/mainwindow.cpp",
      "class_qn": "MainWindow",
      "testable": true,
      "level": "low",
      "factors": [],
      "source": "auto",
      "exempt_reason": null,
      "review_status": null,
      "usecase_count": 2
    }
  ],

  "review_queue": [
    {
      "qn": "MainWindow.deleteRecentHistory",
      "name": "deleteRecentHistory",
      "auto_suggestion": "high",
      "auto_reason": "方法名含 delete",
      "default_level": "mid",
      "status": "pending"
    }
  ]
}
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `qn` | string | ✅ | 全限定名，唯一键（来自图谱，不自己拼） |
| `name` | string | ✅ | 方法短名 |
| `signature` | string | ✅ | 完整签名（图谱返回） |
| `file` | string | ✅ | 源文件路径 |
| `class_qn` | string\|null | ✅ | 所属类 qn，自由函数为 null |
| `testable` | bool | ✅ | scope_rules 判定 |
| `level` | "high"\|"mid"\|"low"\|null | ✅ | testable=false 时为 null |
| `factors` | string[] | ✅ | 命中因子列表，空=low |
| `source` | "auto"\|"suggested"\|"manual" | ✅ | auto=全自动, suggested=自动建议待复核, manual=人工覆盖 |
| `exempt_reason` | string\|null | testable=false时 | "scope:3rdparty/**" |
| `review_status` | "pending"\|"confirmed"\|"rejected"\|null | suggested时 | null=不需要复核 |
| `usecase_count` | int | ✅ | 已有用例数，0=未测试 |

## 评分因子表

| 因子 | level | 检测方式 | source |
|------|-------|---------|--------|
| DBus 契约槽（Q_SLOTS 段内） | high (+3) | Pass 2 source 解析 | auto |
| Q_INVOKABLE 标注 | high (+3) | Pass 2 source 解析 | auto |
| 插件导出（Q_PLUGIN_METADATA） | high (+3) | Pass 2 source 解析 | auto |
| 并发基类方法 | mid (+1) | Pass 1D + Pass 2 source | auto |
| complexity ≥ 20 | high (+3) | Pass 1B 图属性 | auto |
| complexity 10–19 | mid (+2) | Pass 1B 图属性 | auto |
| complexity 5–9 | low (+1) | Pass 1B 图属性 | auto |
| transitive_loop_depth ≥ 3 | high (+3) | Pass 1B 图属性 | auto |
| linear_scan_in_loop ≥ 1 | mid (+1) | Pass 1B 图属性 | auto |
| in_degree ≥ P75(非零) | mid (+1) | Pass 1C 百分位 | auto |
| 构造函数 | 低优先级 | 名称=类名 | auto |
| 析构函数(~) | low (-1) | 名称模式 | auto |
| operator 重载 | low (-1) | 名称模式 | auto |
| 方法名含 delete/remove/destroy/truncate/write/save/persist/erase/clear/reset/wipe | mid (suggested) | 方法名模式匹配 | suggested |
| 无以上因子命中 | low | 默认 | auto |

> **评分规则**：各因子累加得分，score ≥ 3 → high，score ≥ 1 → mid，score < 1 → low。
> 关键：`in_degree ≥ P75` 单独只 +1（mid-booster），需叠加 complexity ≥ 10 (+2)
> 或 linear_scan_in_loop (+1) + complexity ≥ 5 (+1) 才能达到 high。
> 这防止了「complexity 5–9 + 高调用者」组合被误标为 high。

> **suggested 条目**默认 level=mid，进入 review_queue 待人工确认。
> 人工标注 source=manual，level 由用户指定，覆盖自动评分。

## scope_rules 匹配规则

- 语法：glob 风格，`**` 匹配任意深度目录，`*` 匹配单层
- 匹配对象：方法/函数的 `file` 字段（相对路径）
- 优先级：`file_overrides` > `scope_rules` > 默认 testable=true
- scope=exempt → `testable=false`，不论因子评分多高

## 关键约束

- 不修改项目源码
- 不编译/运行测试
- 不生成测试代码
- `qn` 必须来自图谱返回，禁止自己拼接
- scope_rules=exempt 时，方法 testable 硬压为 false
- suggested 条目默认 mid，不自动标 high
- 全量方法必须入表（含 low 和构造/析构），作为覆盖率分母

## 实测发现与注意事项

基于 deepin-calculator 项目（1091 方法）的真实 MCP 图谱测试结果：

### P75 计算必须排除零值

| 计算方式 | P75 | high_caller 数量 | 问题 |
|----------|-----|-----------------|------|
| 全值（含 0） | 1 | 426/607 (70%) | 几乎所有有调用者的方法都变成 high，分类失真 |
| 仅非零值 | 2 | 101/607 (17%) | P75=2 合理，仅 17% 的方法达到 in_degree≥2 |

**结论**：P75 必须基于 `in_degree > 0` 的方法计算，排除零调用者。

### in_degree 单独不应判 high

实测发现：许多构造函数、简单 getter 的 in_degree 很高（如 `IconButton` 构造函数 in_degree=86），
但它们不需要复杂的测试。如果 `in_degree ≥ P75` 直接判 high，会导致大量低风险方法被误标。

**解决方案**：`in_degree` 因子仅贡献 +1 分（mid-booster），需叠加 `complexity ≥ 10` (+2)
或 `linear_scan_in_loop` (+1) + `complexity ≥ 5` (+1) 才能达到 high。
跨项目全量验证（6 个项目，18340 方法，fetch_mcp_data.py 端到端）：`in_degree=+2` 导致 calculator 14.2% high（大量 cx_lo+in_deg 假阳性），
`in_degree=+1` 降至 9.6%，全部项目 high 比例保持在 1.2%–9.6% 的合理分布。
端到端脚本比手动流程多检出 26 个 high（DBus Adaptor 槽 + Q_INVOKABLE 正确检测）。

| 项目 | 方法总数 | 可测试 | high | mid | low | high% | high 主因 |
|------|---------|--------|------|-----|-----|-------|----------|
| deepin-calculator | 1091 | 607 | 58 | 240 | 309 | 9.6% | dbus_slot(9), cx_mid+in_deg, cx_hi |
| deepin-ocr | 261 | 101 | 3 | 56 | 42 | 3.0% | cx_hi, q_invokable(3) |
| deepin-camera | 1632 | 472 | 35 | 250 | 187 | 7.4% | dbus_slot(8), cx_mid+lsl, cx_hi |
| deepin-terminal | 2245 | 1112 | 17 | 360 | 735 | 1.5% | cx_mid+lsl, cx_hi |
| deepin-reader | 1098 | 1098 | 23 | 306 | 769 | 2.1% | cx_hi, cx_mid+lsl |
| dde-file-manager | 12013 | 12013 | 150 | 3493 | 8370 | 1.2% | q_invokable(9), cx_mid+in_deg, cx_hi |
| **合计** | **18340** | **15403** | **286** | **4705** | **10412** | **1.9%** | |

> 端到端收集（fetch_mcp_data.py，HTTP MCP 直连，query_graph + search_code 自动检测）。

### MCP 不支持 Cypher 查询

- `percentileCont()` 不可用，P75 必须客户端计算
- `query_graph` 可以返回 `base_classes`，用于 DBus/并发基类直接筛选（比 get_code_snippet 高效）
- `search_graph(label="Class")` 不返回 `base_classes` 字段，需用 `query_graph` 替代
- 继承检测必须通过 `get_code_snippet()` 读源码确认

### 端到端脚本（fetch_mcp_data.py）

**`resources/scripts/fetch_mcp_data.py`** 一条命令完成全流程：
MCP 收集 → 继承检测 → DBus 槽 → Q_INVOKABLE/Q_PLUGIN → P75 → 生成 `.ut-inventory.json`。

```bash
# 端到端：一条命令生成 .ut-inventory.json
python3 resources/scripts/fetch_mcp_data.py \
  --project home-uos-service-codebase-repos-dde-file-manager \
  --file-pattern "src/**" \
  --output ${test_dir}/.ut-inventory.json \
  --summary
```

脚本自动完成 5 个步骤：
1. **search_graph 分页**收集所有 Method（limit=2000/页，file_pattern 过滤 3rdparty）
2. **query_graph CONTAINS** 检测继承链（QDBusAbstractAdaptor 服务端 / QThread 并发）
3. **query_graph** 获取 DBus Adaptor 类方法 → dbus_slots（过滤构造/析构/emit*）
4. **search_code** 检测 Q_INVOKABLE / Q_PLUGIN_METADATA（best-effort）
5. **客户端计算 P75** 非零 in_degree → 调用 `scan_inventory.build_inventory()` 评分

- **HTTP 直连**：MCP 服务器 `http://10.8.12.80:13626/mcp`，JSON-RPC 2.0 协议
- **file_pattern 过滤**：收集时排除 3rdparty
  - deepin-reader: `reader/**` → 7780 降至 1098 方法
  - dde-file-manager: `src/**` → 14877 降至 12013 方法
- **性能**：12013 方法端到端仅需 ~2 秒
- **依赖**：同目录的 `scan_inventory.py`（提供 `build_inventory()` 评分逻辑）
- **可选**：`--keep-dump` 保留中间 `mcp_dump.json`，`--summary` 输出 Markdown 摘要

> 若 `fetch_mcp_data.py` 不可用（如 MCP URL 变更），
> Agent 可退回手动调用 `search_graph` 分页 + `query_graph` + `scan_inventory.py`。

### search_graph 分页参数（手动回退）

- `limit`: 每次最多 2000 条（默认 200，建议设为 2000 减少分页次数）
- `offset`: 分页偏移，`offset=0,2000,4000,...`
- `file_pattern`: glob 过滤源码目录
- `total` 字段告知过滤后的总数，`has_more` 字段指示是否还有下一页

### is_exported 不可靠

`is_exported` 字段对大多数方法返回 true（实际含义接近"non-static"而非"dllexport"），
不适合作为公开 API 的检测依据。已从评分因子中移除。

### get_code_snippet 限制

- 按 `qualified_name` 查询时返回**方法级**源码（.cpp 实现），非类声明
- Q_SLOTS/Q_INVOKABLE 声明在头文件中，get_code_snippet 无法直接获取
- **替代方案**：用 `query_graph(parent_class CONTAINS '<类名>')` 获取类方法列表，
  或用 `search_code(pattern='Q_INVOKABLE')` 搜索源码文本
