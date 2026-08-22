# Inventory JSON 结构

> 项目单元测试状态的唯一真相源，存储在 `{test_dir}/.ut-inventory.json`，**纳入版本控制**。

## 完整结构

```json
{
  "version": 1,
  "project": "home-user-project-name",
  "base_sha": "abc1234",
  "gate_thresholds": {
    "high": { "line": 90, "branch": 80, "function": 100 },
    "mid": { "line": 60, "branch": 0, "function": 100 },
    "low": { "line": 60, "branch": 0, "function": 100 }
  },
  "scope_rules": [
    { "pattern": "3rdparty/**", "scope": "exempt", "reason": "第三方库" },
    { "pattern": "moc_*", "scope": "exempt" },
    { "pattern": "ui_*", "scope": "exempt" },
    { "pattern": "*.pb.*", "scope": "exempt" }
  ],
  "classes": [
    {
      "qualified_name": "project.src.FileView",
      "name": "FileView",
      "file_path": "src/lib/ui/fileview.h",
      "is_gui": true
    }
  ],
  "methods": [
    {
      "qualified_name": "project.src.MyClass.methodA",
      "name": "methodA",
      "class_qn": "MyClass",
      "file_path": "src/lib/ui/myclass.cpp",
      "access": "public",
      "level": "high",
      "score": 5,
      "factors": ["dbus_slot", "complexity:25"],
      "source": "auto",
      "testable": true,
      "usecase_count": 3
    },
    {
      "qualified_name": "project.src.MyClass.~MyClass",
      "name": "~MyClass",
      "class_qn": "MyClass",
      "file_path": "src/lib/ui/myclass.cpp",
      "access": "public",
      "level": "low",
      "score": -1,
      "factors": ["destructor"],
      "source": "auto",
      "testable": true,
      "usecase_count": 0
    },
    {
      "qualified_name": "project.3rdparty.json.parse",
      "name": "parse",
      "class_qn": null,
      "file_path": "3rdparty/json/parser.cpp",
      "access": "public",
      "level": null,
      "score": 0,
      "factors": [],
      "source": "auto",
      "testable": false,
      "usecase_count": 0
    }
  ],
  "review_queue": [
    {
      "qualified_name": "project.src.FileManager.deleteFile",
      "name": "deleteFile",
      "class_qn": "FileManager",
      "suggested_level": "mid",
      "reason": "方法名含 delete",
      "review_status": "pending"
    }
  ]
}
```

## 字段说明

### 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | int | Schema 版本，当前 1 |
| `project` | string | 图谱中的项目标识 |
| `base_sha` | string | 生成时的 Git base SHA（对账用） |
| `gate_thresholds` | object | 三级覆盖率门禁阈值 |
| `scope_rules` | array | 文件模式规则，每条 `{pattern, scope, reason?}`：`scope` 为 `"exempt"`/`"normal"`；`reason` 可选，供人读 |
| `classes` | array | 类级画像：**只列 GUI 类**（`is_gui=true`），不在列表中的类隐含 `is_gui=false` |
| `methods` | array | 全量方法列表 |
| `review_queue` | array | 待人工复核条目 |

### gate_thresholds

| 级别 | line | branch | function | 说明 |
|------|------|--------|----------|------|
| `high` | ≥ 90% | ≥ 80% | 100% | 硬门禁：函数覆盖率 100% 是 hard gate |
| `mid` | ≥ 60% | — | 100% | 硬门禁：函数覆盖率 100% 是 hard gate |
| `low` | ≥ 60% | — | 100% | 门禁同 mid（构造/析构等仍需覆盖） |

> `branch: 0` 表示不检查分支覆盖率。
>
> **来源**：以上为首次建表的**默认值**。`gate_thresholds` 由外部确定，已有 inventory 时从旧 inventory 读取保留，
> 增量重建不覆盖外部设定。用户可直接编辑 `.ut-inventory.json` 中的 `gate_thresholds` 自定义阈值。

### classes 条目

类级静态画像，Mode 1 建表时一次写入，Mode 2 直接读取（**不再运行时查图谱**）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `qualified_name` | string | 图谱全限定名 |
| `name` | string | 类名 |
| `file_path` | string | 头文件路径 |
| `is_gui` | bool | 是否继承 GUI 基类（QWidget/QDialog/QMainWindow/DMainWindow/DFrame/DWidget/DAbstractDialog）|

> Mode 2 匹配时用 `name`（短名）与 `methods[].class_qn` 比对，不用 `qualified_name`（全限定名格式不同）。

> `is_gui` 是**环境约束**（怎么测不死），与 `methods[].level` 的**重要性分级**（测多严）正交：
> GUI 类决定 QCoreApplication、不直接实例化、CMake 链 Widgets 三处特殊处理，避免 X11/Wayland 下 segfault。

### methods 条目

| 字段 | 类型 | 说明 |
|------|------|------|
| `qualified_name` | string | 图谱全限定名（必须来自图谱返回，禁止自己拼） |
| `name` | string | 方法名 |
| `class_qn` | string \| null | 所属类短名（如 `ApplicationAdaptor`）；自由函数为 null。由 `scan-inventory.py` 产出，Mode 2 匹配时与 `classes[].name` 比对 |
| `file_path` | string | 源文件路径（相对项目根） |
| `access` | string | `"public"` / `"protected"` / `"private"` |
| `level` | string \| null | `"high"` / `"mid"` / `"low"` / `null`（exempt 时为 null） |
| `score` | int | 评分总分（当 `source=manual` 时，`score` 反映 auto 评分，可能与 `level` 不一致，`level` 以人工值为准） |
| `factors` | array | 命中的评分因子列表 |
| `source` | string | `"auto"` / `"suggested"` / `"manual"` |
| `testable` | bool | 是否可测试（scope=exempt 时为 false） |
| `usecase_count` | int | 已有测试用例数（Mode 1 初始为 0，Mode 2 编译通过后更新） |

#### 扩展字段（实现产出，非 schema 强制；供编辑器/调试用）

| 字段 | 类型 | 说明 |
|------|------|------|
| `signature` | string | 方法签名（截断 200 字符） |
| `exempt_reason` | string \| null | 豁免原因（如 `scope:3rdparty/**`） |
| `review_status` | string | `"auto"` / `"pending"` / `"confirmed"` / `"exempt"`；方法级复核状态（与 review_queue 条目互补） |
| `node_type` | string | `"Method"` / `"Function"`（自由函数） |
| `auto_reason` | string | suggested 条目的自动建议原因（仅 `source=suggested` 时存在） |

> 以上扩展字段由 `scan-inventory.py` 产出，Mode 2 消费方可忽略；`assets/ut-inventory-editor`（人工辅助工具，agent 不调用）依赖它们做展示。

### review_queue 条目

| 字段 | 类型 | 说明 |
|------|------|------|
| `qualified_name` | string | 方法全限定名 |
| `name` | string | 方法名 |
| `class_qn` | string | 所属类短名（与 methods[].class_qn 一致） |
| `suggested_level` | string | 建议级别（默认 mid） |
| `reason` | string | 建议原因 |
| `review_status` | string | `"pending"` / `"confirmed"` |

## 评分因子

| 因子 | 得分 | source | 说明 |
|------|------|--------|------|
| `dbus_slot` | +3 | auto | DBus 契约槽 |
| `q_invokable` | +3 | auto | Q_INVOKABLE 标记 |
| `plugin_export` | +3 | auto | 插件导出 |
| `complexity:≥20` | +3 | auto | 圈复杂度（主因子，与缺陷率最相关） |
| `complexity:8-19` | +2 | auto | 圈复杂度 |
| `complexity:5-7` | +1 | auto | 圈复杂度 |
| `cognitive:≥30` | +2 | auto | 认知复杂度（辅助因子，对嵌套和逻辑中断更敏感） |
| `cognitive:15-29` | +1 | auto | 认知复杂度 |
| `lines:≥150` | +1 | auto | 代码行数（保守加分，长函数不一定复杂） |
| `lines:50-149` | +1 | auto | 代码行数 |
| `transitive_loop_depth:≥3` | +3 | auto | 隐蔽 O(n²) |
| `linear_scan_in_loop:≥1` | +1 | auto | 隐蔽 O(n²) 辅助 |
| `loop_count:≥5` | +1 | auto | 循环数量风险 |
| `alloc_in_loop:≥1` | +1 | auto | 循环内分配（性能缺陷强信号） |
| `recursive` | +1 | auto | 递归函数（需额外测试） |
| `in_degree:≥P75` | +1 | auto | 跨文件被引用数（mid-booster，仅对工具/库函数有效） |
| `destructor` | -1 | auto | 析构函数降级 |
| `operator` | -1 | auto | 运算符重载降级 |
| `destructive_name` | suggested | suggested | 不可逆操作名 |

**评分规则**：score ≥ 3 → high，score ≥ 1 → mid，score < 1 → low。

> **因子体系设计原则**：
> - 主因子（complexity）与缺陷率最相关，权重最高
> - 辅助因子（cognitive / lines）不能独立推到 high——cognitive≥30 (+2) 或 lines≥50 (+1) 单独只能到 mid，需叠加 complexity≥5 才到 high
> - in_degree 仅贡献 +1（mid-booster），因为 Qt 项目中信号槽/虚函数回调不产生 CALLS 边，导致核心业务函数 in_degree=0
> - 风险因子（loop_count / alloc_in_loop / recursive）为缺陷提供独立信号

## 版本控制

`.ut-inventory.json` **纳入版本控制**，不加入 `.gitignore`。理由：

- 它是项目单元测试状态的唯一真相源
- Mode 2 依赖它读取方法分级和门禁
- `usecase_count` 反映当前测试覆盖情况
- `base_sha` 用于源码变更对账

## 更新时机

| 时机 | 更新内容 | 执行者 |
|------|---------|--------|
| Mode 1 首次建表 | 全量 methods + gate_thresholds + scope_rules | Mode 1 |
| Mode 2 每类编译通过 | 该类方法的 `usecase_count` | Mode 2 |
| 源码变更对账 | 新增/删除/签名变更方法 + 人工标记保留 | `fetch-mcp-data.py --incremental --existing` |

> **注意**：`usecase_count` 更新是增量操作，只改当前类的数据，不覆盖其他类。
