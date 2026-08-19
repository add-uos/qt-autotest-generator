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
    "low": { "line": 0, "branch": 0, "function": 0 }
  },
  "scope_rules": [
    { "pattern": "3rdparty/**", "scope": "exempt" },
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
      "class_qn": "project.src.MyClass",
      "file_path": "src/lib/ui/myclass.cpp",
      "access": "public",
      "level": "high",
      "score": 5,
      "factors": ["dbus_slot", "complexity_ge_10"],
      "source": "auto",
      "testable": true,
      "usecase_count": 3
    },
    {
      "qualified_name": "project.src.MyClass.~MyClass",
      "name": "~MyClass",
      "class_qn": "project.src.MyClass",
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
      "class_qn": "project.src.FileManager",
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
| `scope_rules` | array | 文件模式规则（exempt/core/normal） |
| `classes` | array | 类级画像：**只列 GUI 类**（`is_gui=true`），不在列表中的类隐含 `is_gui=false` |
| `methods` | array | 全量方法列表 |
| `review_queue` | array | 待人工复核条目 |

### gate_thresholds

| 级别 | line | branch | function | 说明 |
|------|------|--------|----------|------|
| `high` | ≥ 90% | ≥ 80% | 100% | 硬门禁：函数覆盖率 100% 是 hard gate |
| `mid` | ≥ 60% | — | 100% | 硬门禁：函数覆盖率 100% 是 hard gate |
| `low` | — | — | — | 无硬性门禁 |

> `branch: 0` 表示不检查分支覆盖率。

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
| `class_qn` | string \| null | 所属类的全限定名；自由函数为 null |
| `file_path` | string | 源文件路径（相对项目根） |
| `access` | string | `"public"` / `"protected"` / `"private"` |
| `level` | string \| null | `"high"` / `"mid"` / `"low"` / `null`（exempt 时为 null） |
| `score` | int | 评分总分 |
| `factors` | array | 命中的评分因子列表 |
| `source` | string | `"auto"` / `"suggested"` / `"manual"` |
| `testable` | bool | 是否可测试（scope=exempt 时为 false） |
| `usecase_count` | int | 已有测试用例数（Mode 1 初始为 0，Mode 2 编译通过后更新） |

### review_queue 条目

| 字段 | 类型 | 说明 |
|------|------|------|
| `qualified_name` | string | 方法全限定名 |
| `name` | string | 方法名 |
| `class_qn` | string | 所属类全限定名 |
| `suggested_level` | string | 建议级别（默认 mid） |
| `reason` | string | 建议原因 |
| `review_status` | string | `"pending"` / `"confirmed"` |

## 评分因子

| 因子 | 得分 | source |
|------|------|--------|
| `dbus_slot` | +3 | auto |
| `q_invokable` | +3 | auto |
| `plugin_export` | +3 | auto |
| `complexity_ge_20` | +3 | auto |
| `complexity_ge_10` | +2 | auto |
| `complexity_ge_5` | +1 | auto |
| `transitive_loop_depth_ge_3` | +3 | auto |
| `linear_scan_in_loop` | +1 | auto |
| `in_degree_ge_p75` | +1 | auto |
| `destructor` | -1 | auto |
| `operator_overload` | -1 | auto |
| `destructive_name` | suggested | suggested |

**评分规则**：score ≥ 3 → high，score ≥ 1 → mid，score < 1 → low。

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
| 源码变更对账 | 新增/删除/签名变更方法（TODO） | 增量更新脚本 |

> **注意**：`usecase_count` 更新是增量操作，只改当前类的数据，不覆盖其他类。
