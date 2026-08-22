# Defect JSON 结构

> Mode 5（源码缺陷导出与统计）的本地数据模型，存储在 `{test_dir}/.ut-defects.json`，**不入版本控制**（加入 `.gitignore`）。

## 完整结构

```json
{
  "version": 1,
  "project": "deepin-image-viewer",
  "base_sha": "abc1234",
  "last_updated": "2026-08-20T10:30:00Z",
  "defects": [
    {
      "defect_id": "project.src.MyClass.processData#MyClassTest.ProcessData_Normal",
      "method_qn": "project.src.MyClass.processData",
      "method_name": "processData",
      "class_qn": "project.src.MyClass",
      "class_name": "MyClass",
      "module": "src",
      "file_path": "src/lib/core/myclass.cpp",
      "file_line": 42,
      "test_fixture": "MyClassTest",
      "test_case_name": "ProcessData_Normal",
      "test_case_full": "MyClassTest.ProcessData_Normal",
      "test_file": "autotests/tst_myclass.cpp",
      "type": "source_defect_runtime",
      "type_category": "runtime",
      "severity": "high",
      "detected_at_stage": "runtime",
      "status": "open",
      "evidence": "TEST_F(MyClassTest, ProcessData_Normal) 执行时发生段错误 (SEGV)",
      "suggestion": "检查 processData 第 42 行空指针解引用，添加 nullptr 守卫",
      "root_cause_snippet": "void MyClass::processData(Data *d) {\n    return d->value();  // d may be null\n}",
      "discovered_at": "2026-08-20T10:15:00Z",
      "discovered_in_batch": 1,
      "first_seen_sha": "abc1234",
      "last_updated": "2026-08-20T10:15:00Z",
      "fixed_at": null,
      "fixed_in_sha": null,
      "repair_attempts": 0,
      "iteration_count": 0,
      "method_level": "high"
    },
    {
      "defect_id": "project.src.FileView.onOpen#FileViewTest.OnOpen_NullFile",
      "method_qn": "project.src.FileView.onOpen",
      "method_name": "onOpen",
      "class_qn": "project.src.FileView",
      "class_name": "FileView",
      "module": "src",
      "file_path": "src/lib/ui/fileview.cpp",
      "file_line": 88,
      "test_fixture": "FileViewTest",
      "test_case_name": "OnOpen_NullFile",
      "test_case_full": "FileViewTest.OnOpen_NullFile",
      "test_file": "autotests/tst_fileview.cpp",
      "type": "source_defect_compile",
      "type_category": "compile",
      "severity": "high",
      "detected_at_stage": "compile",
      "status": "open",
      "evidence": "error: no member named 'loadPixmap' in 'FileView'",
      "suggestion": "确认 FileView::loadPixmap 方法是否已重命名或移除",
      "root_cause_snippet": "void FileView::onOpen(const QString &path) {\n    loadPixmap(path);  // no such method\n}",
      "discovered_at": "2026-08-20T10:15:00Z",
      "discovered_in_batch": 1,
      "first_seen_sha": "abc1234",
      "last_updated": "2026-08-20T10:15:00Z",
      "fixed_at": "2026-08-20T11:00:00Z",
      "fixed_in_sha": "def5678",
      "repair_attempts": 1,
      "iteration_count": 1,
      "method_level": "mid"
    },
    {
      "defect_id": "project.src.Parser.parse#ParserTest.Parse_EmptyInput",
      "method_qn": "project.src.Parser.parse",
      "method_name": "parse",
      "class_qn": "project.src.Parser",
      "class_name": "Parser",
      "module": "src",
      "file_path": "src/lib/core/parser.cpp",
      "file_line": 15,
      "test_fixture": "ParserTest",
      "test_case_name": "Parse_EmptyInput",
      "test_case_full": "ParserTest.Parse_EmptyInput",
      "test_file": "autotests/tst_parser.cpp",
      "type": "source_defect_logic",
      "type_category": "logic",
      "severity": "mid",
      "detected_at_stage": "logic",
      "status": "fixed",
      "evidence": "EXPECT_EQ(result.size(), 0) 失败，实际返回 1 个元素",
      "suggestion": "检查空输入时是否提前返回",
      "root_cause_snippet": "QList<Token> Parser::parse(const QString &input) {\n    if (input.isEmpty()) return {};\n    // 缺少 early return，下方仍执行解析\n}",
      "discovered_at": "2026-08-20T10:15:00Z",
      "discovered_in_batch": 1,
      "first_seen_sha": "abc1234",
      "last_updated": "2026-08-20T12:00:00Z",
      "fixed_at": "2026-08-20T11:30:00Z",
      "fixed_in_sha": "ghi9012",
      "repair_attempts": 1,
      "iteration_count": 1,
      "method_level": "high"
    },
    {
      "defect_id": "project.src.MyClass.processData#MyClassTest.ProcessData_LargeInput",
      "method_qn": "project.src.MyClass.processData",
      "method_name": "processData",
      "class_qn": "project.src.MyClass",
      "class_name": "MyClass",
      "module": "src",
      "file_path": "src/lib/core/myclass.cpp",
      "file_line": 50,
      "test_fixture": "MyClassTest",
      "test_case_name": "ProcessData_LargeInput",
      "test_case_full": "MyClassTest.ProcessData_LargeInput",
      "test_file": "autotests/tst_myclass.cpp",
      "type": "source_defect_runtime",
      "type_category": "runtime",
      "severity": "high",
      "detected_at_stage": "runtime",
      "status": "reopened",
      "evidence": "TEST_F(MyClassTest, ProcessData_LargeInput) 超时 (>60s)",
      "suggestion": "检查大输入时的循环边界条件",
      "root_cause_snippet": "for (int i = 0; i < data.size(); ++i) {\n    for (int j = i; j < data.size(); ++j) {  // O(n^2)\n    }\n}",
      "discovered_at": "2026-08-20T10:15:00Z",
      "discovered_in_batch": 1,
      "first_seen_sha": "abc1234",
      "last_updated": "2026-08-20T13:00:00Z",
      "fixed_at": "2026-08-20T12:00:00Z",
      "fixed_in_sha": "jkl3456",
      "repair_attempts": 2,
      "iteration_count": 2,
      "method_level": "high"
    },
    {
      "defect_id": "project.src.ConfigManager.load#ConfigManagerTest.Load_MissingFile",
      "method_qn": "project.src.ConfigManager.load",
      "method_name": "load",
      "class_qn": "project.src.ConfigManager",
      "class_name": "ConfigManager",
      "module": "src",
      "file_path": "src/lib/core/configmanager.cpp",
      "file_line": 22,
      "test_fixture": "ConfigManagerTest",
      "test_case_name": "Load_MissingFile",
      "test_case_full": "ConfigManagerTest.Load_MissingFile",
      "test_file": "autotests/tst_configmanager.cpp",
      "type": "needs_manual",
      "type_category": "manual",
      "severity": "mid",
      "detected_at_stage": "manual",
      "status": "open",
      "evidence": "ConfigManager 构造函数为 private，无法在测试中实例化",
      "suggestion": "添加工厂方法或友元测试夹具",
      "root_cause_snippet": "class ConfigManager {\nprivate:\n    ConfigManager();  // private ctor, no factory\n};",
      "discovered_at": "2026-08-20T10:15:00Z",
      "discovered_in_batch": 1,
      "first_seen_sha": "abc1234",
      "last_updated": "2026-08-20T10:15:00Z",
      "fixed_at": null,
      "fixed_in_sha": null,
      "repair_attempts": 0,
      "iteration_count": 0,
      "method_level": "mid"
    },
    {
      "defect_id": "project.src.Network.fetch#NetworkTest.Fetch_Timeout",
      "method_qn": "project.src.Network.fetch",
      "method_name": "fetch",
      "class_qn": "project.src.Network",
      "class_name": "Network",
      "module": "src",
      "file_path": "src/lib/net/network.cpp",
      "file_line": 35,
      "test_fixture": "NetworkTest",
      "test_case_name": "Fetch_Timeout",
      "test_case_full": "NetworkTest.Fetch_Timeout",
      "test_file": "autotests/tst_network.cpp",
      "type": "source_defect_runtime",
      "type_category": "runtime",
      "severity": "high",
      "detected_at_stage": "runtime",
      "status": "open",
      "evidence": "QNetworkReply 超时未触发 finished 信号，测试挂起",
      "suggestion": "使用 QSignalSpy 或 mock QNetworkAccessManager",
      "root_cause_snippet": "void Network::fetch(const QUrl &url) {\n    QNetworkReply *reply = m_manager->get(QNetworkRequest(url));\n    // 缺少超时处理\n}",
      "discovered_at": "2026-08-20T10:15:00Z",
      "discovered_in_batch": 1,
      "first_seen_sha": "abc1234",
      "last_updated": "2026-08-20T10:15:00Z",
      "fixed_at": null,
      "fixed_in_sha": null,
      "repair_attempts": 0,
      "iteration_count": 0,
      "method_level": "high"
    }
  ],
  "stats": {
    "total": 6,
    "by_status": { "open": 4, "fixed": 1, "reopened": 1, "wontfix": 0 },
    "by_type": {
      "source_defect_compile": 1,
      "source_defect_runtime": 3,
      "source_defect_logic": 1,
      "needs_manual": 1
    },
    "by_category": { "compile": 1, "runtime": 3, "logic": 1, "manual": 1 },
    "by_severity": { "high": 4, "mid": 2, "low": 0 },
    "by_class": { "MyClass": 2, "FileView": 1, "Parser": 1, "ConfigManager": 1, "Network": 1 },
    "by_method": { "project.src.MyClass.processData": 2, "project.src.FileView.onOpen": 1, "project.src.Parser.parse": 1, "project.src.ConfigManager.load": 1, "project.src.Network.fetch": 1 },
    "by_module": { "src": 6 },
    "affected_methods": 5,
    "affected_classes": 5
  },
  "history": {
    "abc1234": {
      "defects": [],
      "stats": {
        "total": 0,
        "by_status": { "open": 0, "fixed": 0, "reopened": 0, "wontfix": 0 },
        "by_type": {},
        "by_category": { "compile": 0, "runtime": 0, "logic": 0, "manual": 0 },
        "by_severity": { "high": 0, "mid": 0, "low": 0 },
        "by_class": {},
        "by_method": {},
        "by_module": {},
        "affected_methods": 0,
        "affected_classes": 0
      }
    }
  }
}
```

## 字段说明

### 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | int | Schema 版本，当前 1 |
| `project` | string | 项目标识（与 `.ut-inventory.json` 一致） |
| `base_sha` | string | 当前检测基线的 Git base SHA |
| `last_updated` | string | 最后更新时间（ISO 8601） |
| `defects` | array | 当前缺陷列表（用例级颗粒度） |
| `stats` | object | 聚合统计 |
| `history` | object | 按 base_sha 归档的历史快照 |

### defects[] 条目

| 字段 | 类型 | 说明 |
|------|------|------|
| `defect_id` | string | 主键，用于去重。见 [defect_id 主键规则](#defect_id-主键规则) |
| `method_qn` | string | 方法全限定名 |
| `method_name` | string | 方法名 |
| `class_qn` | string | 类全限定名 |
| `class_name` | string | 类名 |
| `module` | string | `file_path` 顶层目录，用于模块级聚合 |
| `file_path` | string | 源码文件路径（相对项目根） |
| `file_line` | int | 源码行号 |
| `test_fixture` | string | 测试 fixture 类名（TEST_F 第一个参数） |
| `test_case_name` | string | 测试用例名（TEST_F 第二个参数） |
| `test_case_full` | string | `{TestFixture}.{TestCaseName}` 完整标识 |
| `test_file` | string | 测试文件路径 |
| `type` | string | 缺陷分类，见 [type 枚举](#type-枚举与来源映射) |
| `type_category` | string | 缩略分类：`compile` / `runtime` / `logic` / `manual` |
| `severity` | string | 严重度：`high` / `mid` / `low`，由 type × method_level 派生，见 [severity 派生规则](#severity-派生规则) |
| `detected_at_stage` | string | 检测阶段：`compile` / `runtime` / `logic` / `review` / `manual` |
| `status` | string | 生命周期状态：`open` / `fixed` / `reopened` / `wontfix`，见 [状态机](#生命周期状态机) |
| `evidence` | string | 缺陷证据描述（编译错误、运行时崩溃信息、断言失败输出等） |
| `suggestion` | string | 修复建议 |
| `root_cause_snippet` | string | `get_code_snippet` 截取的源码片段 |
| `discovered_at` | string | 发现时间（ISO 8601） |
| `discovered_in_batch` | int | 发现时的批次号 |
| `first_seen_sha` | string | 首次发现时的 `base_sha` |
| `last_updated` | string | 最后更新时间（ISO 8601） |
| `fixed_at` | string \| null | 修复时间（ISO 8601）或 null |
| `fixed_in_sha` | string \| null | 修复时的 commit SHA 或 null |
| `repair_attempts` | int | 修复尝试次数 |
| `iteration_count` | int | 闭环迭代次数 |
| `method_level` | string \| null | 取自 `.ut-inventory.json` 中的方法分级：`high` / `mid` / `low` / `null` |

## defect_id 主键规则

`defect_id` 是缺陷的唯一标识，用于去重和关联。根据缺陷发现场景分三种模式：

| 场景 | 格式 | 示例 |
|------|------|------|
| 有具体失败用例 | `{method_qn}#{TestFixture}.{TestCaseName}` | `project.src.MyClass.processData#MyClassTest.ProcessData_Normal` |
| 构造即崩（无具体用例） | `{class_qn}.{ctor_name}#{TestFixture}.__class_init__` | `project.src.ConfigManager.ConfigManager#ConfigManagerTest.__class_init__` |
| 自由函数 | `{function_qn}#{Fixture}.{CaseName}` | `project.src.utils.formatSize#UtilsTest.FormatSize_Negative` |

## type 枚举与来源映射

| type 值 | type_category | 含义 | 现有 failure_reason 来源 |
|----------|---------------|------|----------------------|
| `source_defect_compile` | `compile` | 源码编译缺陷 | `build-verifier.md:65` / `failure-repairer.md:65` |
| `source_defect_runtime` | `runtime` | 源码运行时缺陷（崩溃/超时） | `build-verifier.md:67` / `failure-repairer.md:71` |
| `source_defect_logic` | `logic` | 源码逻辑缺陷（断言失败） | `build-verifier.md:69` / `failure-repairer.md:75` |
| `needs_manual` | `manual` | 需人工介入 | `build-verifier.md:71` / `failure-repairer.md:68,79`、`test-code-gen.md:173`（私有构造无工厂）、`dependency-tracer.md:101`（循环依赖） |

## severity 派生规则

severity 由 `type` 确定默认值，再根据 `method_level` 可能升级：

### 基础规则

| type | 默认 severity | 理由 |
|------|--------------|------|
| `source_defect_runtime` | `high` | 崩溃/超时，影响程序可用性 |
| `source_defect_compile` | `high` | 编译不过，阻塞后续检测 |
| `source_defect_logic` | `mid` | 逻辑矛盾，程序可运行但结果错误 |
| `needs_manual` | `mid` | 待人工排查，性质未定 |

### 升级规则

> 若 `method_level` 为 `"high"` 且 severity 原为 `"mid"` → 升级为 `"high"`。

即：`source_defect_logic` 在 `high` 级方法上触发时，severity 从 `mid` 升为 `high`；`needs_manual` 同理。

## 生命周期状态机

```
新发现 → open ──→ fixed（用例通过时）
                 └──→ reopened（fixed 后又失败）
                       └──→ wontfix（方法已删除）
```

| 转换 | 触发条件 |
|------|----------|
| → `open` | 首次检测到缺陷 |
| `open` → `fixed` | 修复后用例编译通过且运行通过 |
| `fixed` → `reopened` | 原已 fixed 的用例在后续批次再次失败 |
| 任意 → `wontfix` | 对应源码方法已从代码库中删除 |

## stats 聚合统计

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | int | 缺陷总数 |
| `by_status` | object | 按状态聚合：`open` / `fixed` / `reopened` / `wontfix` 各自计数 |
| `by_type` | object | 按 type 聚合：`source_defect_compile` / `source_defect_runtime` / `source_defect_logic` / `needs_manual` 各自计数 |
| `by_category` | object | 按 type_category 聚合：`compile` / `runtime` / `logic` / `manual` 各自计数 |
| `by_severity` | object | 按严重度聚合：`high` / `mid` / `low` 各自计数 |
| `by_class` | object | 按类名聚合（key 为 `class_name`） |
| `by_method` | object | 按方法全限定名聚合（key 为 `method_qn`） |
| `by_module` | object | 按模块聚合（key 为 `module`） |
| `affected_methods` | int | 受影响方法数，仅统计 `status` 为 `open` + `reopened` 的去重方法数 |
| `affected_classes` | int | 受影响类数，仅统计 `status` 为 `open` + `reopened` 的去重类数 |

> `affected_methods` 和 `affected_classes` 是衡量当前仍需修复的缺陷影响范围的关键指标。一个方法或类可能关联多个用例级缺陷，这里只计一次。

## history 归档结构

当 reconcile 检出代码变更（`base_sha` 漂移）时，当前 `defects` + `stats` **整体移入** `history[old_base_sha]`，新 `base_sha` 下从空开始累积。

```json
{
  "history": {
    "old_sha_1": {
      "defects": [ ... ],
      "stats": { ... }
    },
    "old_sha_2": { ... }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `history` | object | key 为 base_sha，value 为 `{defects, stats}` 快照 |
| `history[sha].defects` | array | 该 SHA 下的完整缺陷列表 |
| `history[sha].stats` | object | 该 SHA 下的完整统计 |

## 版本控制策略

**`.ut-defects.json` 不入 git**。在 `{test_dir}/.gitignore` 中添加：

```
.ut-defects.json
```

理由：

- 缺陷数据是**检测派生产物**，随重跑而变化，不是项目源码的一部分
- 入库会污染 git 历史（频繁增删改）
- 不同开发者本地检测结果不同，入库会导致无意义的合并冲突
- 历史归档已内置于 `history` 字段，不需要 git log 辅助追溯
