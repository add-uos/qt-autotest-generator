# 依赖追踪

> 前置条件：知识图谱已就绪（`environment_check` 通过），`.ut-inventory.json` 存在，目标类已从 inventory 提取（`testable_classes`（内存变量）），图谱 ready。

> 通过 GitNexus 代码图谱 MCP 查询 CALLS/IMPORTS 边（详见 references/gitnexus-guide.md）

## 概述

从 inventory 读取目标类的 GUI 标记（`is_gui`），再用知识图谱的 CALLS 出向边追踪目标类每个方法的调用链，按决策矩阵决定哪些依赖需要 stub、哪些需要编入 CMake。此阶段只产出 GUI 标记、stub 清单和源码目录清单，不生成测试代码。

## 工作步骤

### 1. 读取 GUI 标记（is_gui）

从 `.ut-inventory.json` 的 `classes` 数组读取（Mode 1 建表时已用 GUI 基类检测写入，不查图谱）：

```python
gui_names = {c["name"] for c in inventory.get("classes", []) if c.get("is_gui")}
is_gui = class_qn in gui_names   # methods[].class_qn 是短名，用 classes[].name 匹配
```

GUI 类（`is_gui=true`）后续测试代码生成特殊处理：`QCoreApplication` 而非 `QApplication`、不直接实例化、CMake 链 `Qt::Widgets`，避免 X11/Wayland 下 segfault。

> **旧版 inventory 无 `classes` 字段时兜底**：读目标类头文件，检查基类列表是否含 GUI 基类（QWidget / QDialog / QMainWindow / DMainWindow / DFrame / DWidget / DAbstractDialog）。

### 2. 获取目标类所有方法的出向调用链

对 inventory 中该类的每个 testable 方法：

```python
# CALLS 出向边（repo 参数必带；入向把方向反转）
callees = cypher(
    f"MATCH (src {{filePath: '{method.file_path}'}})"
    "-[r:CodeRelation {{type:'CALLS'}}]->(t) "
    f"WHERE src.name = '{method.name}' AND src.startLine = {method.start_line} "
    "RETURN DISTINCT t.filePath AS file_path, t.name AS name, t.startLine AS start_line")
# 多跳：逐跳组合（对每个 callee 的 (file_path, name, start_line) 重复上述查询）
```

### 3. stub 决策矩阵

遍历所有 callees，按以下矩阵分类：

| callee 所属 | callee 类型 | 决策 |
|------------|------------|------|
| 本项目（`file_path` 在 `src/` 下） | 任意 | **不 stub**，但需将该文件所在目录编入 CMake |
| 外部库 - UI 相关 | `QWidget::show`、`hide`、`height`、`width`、`QDialog::exec` | **stub**（避免 GUI 依赖） |
| 外部库 - IO 相关 | `QFile::open`、`QDir`、`QDir::exists`、`QFileInfo::exists`、`QSettings`、`QSqlQuery` | **stub**（避免副作用） |
| 外部库 - 子进程 | `QProcess::start`、`::system`、`::popen` | **stub**（禁止真实启进程） |
| 外部库 - 网络相关 | `QNetworkAccessManager`、`QTcpSocket`、`QUdpSocket`、`QLocalSocket` | **stub**（禁止真实网络） |
| 外部库 - 定时器 | `QTimer` | **stub**（避免异步行为） |
| 外部库 - 时间/随机 | `QDateTime::currentDateTime`、`QTime::currentTime`、`QElapsedTimer`、`srand`/`qsrand`、`QRandomGenerator::system` | **stub**（需确定性结果） |
| 路径访问 | `QStandardPaths::writableLocation`、`QDir::currentPath`、`QCoreApplication::applicationDirPath`、`QDir::home` | **stub** 返回临时目录，或 `SetUp()` 用 `QTemporaryDir` 注入路径 |
| 环境变量 | `getenv`、`qEnvironmentVariable`、`qgetenv`、`QProcessEnvironment::systemEnvironment` | **stub** 或 `SetUp()` `qputenv` / `TearDown()` `qunsetenv` |
| 全局函数 | `qPrintable`、`getenv`、`qDebug` | **stub**（按行为） |
| 单例/全局状态 | 进程内单例、静态成员、`qApp` | **stub** 或 `TearDown` 重置，避免用例间污染 |
| 其他外部库 | 任意 | 评估是否需要 stub（默认不 stub，编译失败再补） |

### 4. 收集 CMake 源码目录

从"本项目"类别的 callees 聚合源码目录：

```python
source_dirs = set()
for callee in callees:
    if callee.file_path and callee.file_path.startswith("src/"):
        source_dirs.add(dirname(callee.file_path))
```

**规则**：若模块 A 的源码 `#include` 了模块 B 的头文件，测试 CMakeLists 必须编译 A 和 B 的源码文件。缺少传递依赖会导致 `undefined reference`。

### 5. 用 Cypher 补充 IMPORTS 链

```python
imports = cypher(
    f"MATCH (f:File)-[r:CodeRelation]->(dep) WHERE r.type = 'IMPORTS' "
    f"AND f.filePath STARTS WITH '{目标类文件路径前缀}' "
    "RETURN DISTINCT dep.filePath AS dep_file")
# 聚合到 source_dirs
```

注意：GitNexus 关系类型在边属性 `r.type` 上（无 `type()` 函数）；属性名 camelCase（`filePath`）。拿不准时先 `MATCH ()-[r:CodeRelation]->() RETURN DISTINCT r.type` 摸清边集合。

### 6. 产出 stub 清单

为每个需要 stub 的 callee，记录：

```json
{
  "callee_name": "QWidget::show",
  "stub_type": "lamda",        // lamda / VADDR / static_cast
  "stub_pattern": "stub.set_lamda(&QWidget::show, []() { return; });",
  "reason": "UI 相关，避免 GUI 依赖"
}
```

stub 类型选择规则：
- 虚函数 → `VADDR(Class, method)`
- 重载函数 → `static_cast<Ret (Class::*)(Params)>(&Class::method)`
- 普通函数 → `stub.set_lamda(...)`
- 具体模式参考 `templates/stub-patterns.cpp`

**循环依赖处理**：若 CALLS 链发现环（A→B→C→A），记录环路但不无限递归。在 stub_list 中标记 `circular: true`，可选择跳过该类或标记 needs_manual。标记 needs_manual 时调 `export-defects.py upsert` 落盘到 `.ut-defects.json`（`type=needs_manual, detected_at_stage=review`），归集进缺陷统计。

**深度限制**：depth=2 是基础值。若发现 callee 包含本项目代码且未完全覆盖传递依赖，提高到 depth=3。超过 depth=3 仍遗漏 → 标记 `incomplete_trace: true`。

### 7. 记录依赖追踪结果

将追踪结果记录到内存变量 `class_status[classname]`：

```json
{
  "is_gui": false,
  "stub_list": [...],
  "source_dirs": ["src/lib/ui", "src/lib/core"],
  "status": "dependency_traced"
}
```

## 关键约束

- 不生成测试代码（只产出 GUI 标记、stub 清单和目录清单）
- `qualified_name` 必须用 `.ut-inventory.json` 中的 `class_qn` / `qualified_name`，不自己拼
- IMPORTS 链必须用 Cypher 补充，不遗漏传递依赖
- 本项目代码编入 CMake 即可，不 stub
- UI/IO/网络/定时器类依赖不 stub 会导致测试崩溃或副作用，不跳过 stub 决策矩阵
- 不修改项目源码

### MCP 查询失败处理策略

| 查询类型 | 严重程度 | 失败处理 |
|---------|---------|----------|
| CALLS 边查询（依赖链） | 关键 | **硬终止** + 明确错误：`[FATAL] 依赖追踪失败，请检查 GitNexus 端点与索引状态` |
| cypher 符号定位（头文件/符号查找） | 关键 | **硬终止** + 明确错误：无法定位头文件则 CMake include 无法配置 |
| 本地切片（签名确认） | 关键 | **硬终止** + 明确错误：签名不确定则 stub 无法正确生成 |
| IMPORTS 边查询（辅助） | 非关键 | **降级** + 警告：返回空时改用 CALLS 多跳逐层展开重新聚合依赖目录，**不读项目源码文件**，继续执行 |
