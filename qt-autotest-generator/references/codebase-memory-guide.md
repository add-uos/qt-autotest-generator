# codebase-memory-mcp 使用指南

> 本指南面向 `qt-autotest-generator` 技能的子 Agent。
> 它假设 MCP 服务已经由 `setup-codebase-memory.sh` 安装并配置完成。

> **提供方说明**：本技能支持两种知识图谱 MCP 提供方——远端（`remote-codebase-memory-mcp`）和本地（`codebase-memory-mcp`）。提供方在 `environment_check` 阶段一次性解析（远端优先，本地兜底），全流程互斥使用其一，记录为内存变量 `mcp_provider`。下文所有 `codebase_memory_mcp.*` 调用示例均为**概念性写法**，实际调用时替换为 `mcp_provider` 对应的工具前缀。完整解析算法见 `mcp-providers.md`。

## 目录

- [1. 核心理念：为什么用知识图谱代替 LSP](#1-核心理念为什么用知识图谱代替-lsp)
- [2. 前置检查：项目是否已索引](#2-前置检查项目是否已索引)
- [3. 场景速查表](#3-场景速查表)
- [4. 详细查询模式](#4-详细查询模式)
  - [4.1 批量扫描模块的所有类](#41-批量扫描模块的所有类)
  - [4.2 获取某个类的全部方法](#42-获取某个类的全部方法)
  - [4.3 读取函数源代码（替代 lsp_goto_definition）](#43-读取函数源代码替代-lsp_goto_definition)
  - [4.4 追踪调用链（替代 lsp_find_references）](#44-追踪调用链替代-lsp_find_references)
  - [4.5 分析依赖目录（#include 链）](#45-分析依赖目录include-链)
  - [4.6 增量更新：差异分析](#46-增量更新差异分析)
- [5. 查询技巧](#5-查询技巧)
- [6. 图谱局限与应对策略](#6-图谱局限与应对策略)
- [7. 常见陷阱](#7-常见陷阱)

---

## 1. 核心理念：为什么用知识图谱代替 LSP

**LSP 是「单文件、单符号」工具**。每次调用只看一个文件、一个符号。
当你要为 10 个类、共 80 个方法生成测试时，需要发起 80+ 次 LSP 调用，每次都要重新解析上下文。

**codebase-memory-mcp 是「全局知识图谱」工具**。它把整个代码库预先解析成一张图
（类→方法→调用关系→依赖关系），用一次 SQL/Cypher 查询就能批量取回结构化结果。

| 维度 | LSP 逐文件分析 | 知识图谱批量查询 |
|------|---------------|----------------|
| 10 个类的结构分析 | 10+ 次调用 ≈ 10-30 秒 | 1 次调用 ≈ 10-50ms |
| 依赖关系分析 | 手动递归 `#include` | `trace_path` 自动追踪 |
| 全局视角 | 无 | 架构、热点、聚类 |
| 索引复用 | 无（每次重算） | 持久化，daemon 自动增量同步 |

**因此**：能用 MCP 一次性拿到的信息，**绝不**逐个查询。LSP 未集成，图谱是唯一代码分析来源。
精确签名/类型推断通过 `get_code_snippet` 读源码补充（详见 [§6](#6-图谱局限与应对策略)）。

---

## 2. 前置检查：项目是否已索引

技能执行前，必须先确认目标项目已被索引。这是后续所有 MCP 查询的前提。

### 2.1 查询已索引项目列表

```python
projects = codebase_memory_mcp.list_projects()
# 返回示例:
# {
#   "projects": [
#     {"name": "my-qt-app", "root_path": "/path/to/project",
#      "nodes": 1500, "edges": 3200, "status": "ready"},
#     ...
#   ]
# }
```

### 2.2 检查目标项目状态

```python
status = codebase_memory_mcp.index_status(project="my-qt-app")
# status="ready"      → 直接使用，daemon watcher 已自动同步最新变更
# status="indexing"   → 正在索引，等待几秒
# 报错 project not found → 首次使用，需要显式索引（见 2.3）
```

### 2.3 首次索引（仅在未索引时）

> ⚠️ **仅本地提供方可触发索引**。若 `mcp_provider == "remote-codebase-memory-mcp"`，远端无法调用 `index_repository`——项目必须已在远端索引好（由 `environment_check` 在解析阶段确认）。本节仅适用于本地提供方。

**项目名规则**：codebase-memory-mcp 会把 repo 绝对路径转换成「斜杠变短横」的项目名，
例如 `/home/user/my-qt-app` → `home-user-my-qt-app`。后续所有查询都要用这个名字。

```python
# 推荐用 moderate 模式：平衡速度和深度（含调用链 + 语义边）
codebase_memory_mcp.index_repository(
    repo_path="/absolute/path/to/project",  # 必须绝对路径
    mode="moderate",                         # full | moderate | fast
    persistence=True                         # 写入 .codebase-memory/graph.db.zst 供团队复用
)
```

**模式选择**：

| 模式 | 速度 | 包含 semantic/similarity 边 | 适用场景 |
|------|------|---------------------------|---------|
| `fast` | 最快 | ❌ | 已有索引，快速增量同步 |
| `moderate` | 中等 | ✅ | **推荐**：单元测试生成场景 |
| `full` | 最慢 | ✅ | 小项目首次索引，需要最完整语义 |

### 2.4 索引后的自动维护

**无需手动重索引**。daemon 的后台 watcher 会监听 git 变更并自动增量重索引。
仅当出现以下情况才需要手动触发：
- 短时间内大量文件改动，watcher 还没跟上 → `index_repository(mode="fast")` 推一下
- 需要 semantic 边但当前是 fast 索引 → `index_repository(mode="moderate")`

---

## 3. 场景速查表

| 子 Agent 要做的事 | 对应 MCP 工具 | 详细章节 |
|------------------|--------------|---------|
| 扫描模块下所有类 | `search_graph(label="Class", file_pattern="...")` | [§4.1](#41-批量扫描模块的所有类) |
| 获取某个类的所有方法 | `search_graph(label="Method", qn_pattern=".*ClassName.*")` | [§4.2](#42-获取某个类的全部方法) |
| 读取函数签名和实现 | `get_code_snippet(qualified_name=...)` | [§4.3](#43-读取函数源代码替代-lsp_goto_definition) |
| 谁调用了某方法（决定 Stub） | `trace_path(direction="inbound")` | [§4.4](#44-追踪调用链替代-lsp_find_references) |
| 方法调用了哪些外部依赖 | `trace_path(direction="outbound")` | [§4.4](#44-追踪调用链替代-lsp_find_references) |
| 分析 `#include` 链找依赖目录 | `trace_path(direction="outbound", depth=2)` + 文件路径聚合 | [§4.5](#45-分析依赖目录include-链) |
| 增量更新：对比已测试 vs 全部函数 | `search_graph` + 文件读取 | [§4.6](#46-增量更新差异分析) |
| 项目架构概览 | `get_architecture()` | — |

---

## 4. 详细查询模式

### 4.1 批量扫描模块的所有类

**场景**：用户请求 `为 src/lib/ui 模块创建单元测试`。

```python
# 一次性获取模块下所有类
classes = codebase_memory_mcp.search_graph(
    project="my-qt-app",
    label="Class",
    file_pattern="src/lib/ui/*",   # 路径前缀过滤（glob 风格）
    limit=200                       # 上限保护
)
# 返回字段（每个类）：
# - name:               类名
# - qualified_name:     全限定名（后续 get_code_snippet 要用）
# - file_path:          头文件路径
# - in_degree/out_degree: 被引用数/引用数（评估重要性）
# - is_exported:        是否导出（public 才需要测试）
# - is_test:            是否本身就是测试代码（应排除）
```

**筛选规则**：
- 只测 `is_exported=true` 的类（public 可见）
- 排除 `is_test=true` 的类（避免为测试代码生成测试）
- 排除命名空间内的辅助类（根据项目惯例）

### 4.2 获取某个类的全部方法

```python
# 用 qn_pattern 按类的全限定名前缀过滤
methods = codebase_memory_mcp.search_graph(
    project="my-qt-app",
    label="Method",
    qn_pattern=".*\\.MyClass\\..*",  # 正则：匹配 MyClass 内的所有方法
    limit=100
)
# 返回字段（每个方法）：
# - name:               方法名
# - qualified_name:     全限定名（get_code_snippet 用）
# - parent_class:       所属类的 qualified_name
# - complexity:         圈复杂度（高复杂度需更多测试用例）
# - cognitive:          认知复杂度
# - loop_count:         循环数（边界测试重点）
# - param_count:        参数数
# - lines:              函数行数
```

**测试用例规划**：
- `complexity >= 10` 的方法 → 至少 3 个测试用例（正常 + 边界 + 异常）
- `loop_count >= 1` 的方法 → 增加循环边界测试（空集合、单元素、超大集合）
- `param_count >= 4` 的方法 → 参数组合测试

### 4.3 读取函数源代码（替代 lsp_goto_definition）

```python
snippet = codebase_memory_mcp.get_code_snippet(
    qualified_name="my-qt-app.src.lib.ui.MyClass.processData"
    # qualified_name 必须来自 search_graph 的返回，不能自己拼
)
# 返回完整函数定义（含签名、返回类型、参数、函数体）
```

**关键**：`qualified_name` **必须**来自 `search_graph` / `trace_path` 的返回值，
不要自己拼接——命名空间、嵌套类的全限定名规则复杂，自己拼必然出错。

### 4.4 追踪调用链（替代 lsp_find_references）

这是 MCP 相对 LSP 的最大优势：**自动、多跳、双向**。

```python
# 入向：谁调用了 MyClass::processData（用于评估改动影响）
callers = codebase_memory_mcp.trace_path(
    project="my-qt-app",
    function_name="MyClass::processData",  # 也支持短名，但精确名更好
    direction="inbound",
    depth=3
)

# 出向：MyClass::processData 调用了什么（用于决定 Stub 哪些依赖）
callees = codebase_memory_mcp.trace_path(
    project="my-qt-app",
    function_name="MyClass::processData",
    direction="outbound",
    depth=2
)
```

**出向追踪的 Stub 决策规则**：

```
遍历 callees：
├─ callee 属于本项目（file_path 在 src/ 下）
│   └─ 无需 Stub，但要确保 CMake 编译了这个文件
├─ callee 属于外部库（Qt/DTK/boost 等）
│   ├─ 是 UI 相关（QWidget::show 等）         → Stub（避免 GUI 依赖）
│   ├─ 是 IO 相关（QFile::open, QSqlQuery）    → Stub（避免副作用）
│   ├─ 是网络相关（QNetworkAccessManager）     → Stub（避免真实请求）
│   └─ 其他                                    → 评估是否需要 Stub
└─ callee 是全局函数
    └─ 通常需要 Stub（如 qPrintable、getenv）
```

### 4.5 分析依赖目录（#include 链）

**场景**：`qt-autotest-generator` 子 Agent 需要为 CMakeLists.txt 收集所有依赖源码目录
（原文档要求手动递归 `#include`）。

```python
# 步骤 1: 获取目标文件的 IMPORTS 边（一跳即可）
# 注意：IMPORTS 边的目标节点是 Module 标签（不是 File），属性名是 file_path
imports = codebase_memory_mcp.query_graph(
    project="my-qt-app",
    query="""
        MATCH (f:File)-[:IMPORTS]->(dep:Module)
        WHERE f.file_path STARTS WITH 'src/lib/ui/myclass'
        RETURN DISTINCT dep.file_path AS dep_file
    """
)

# 步骤 2: 聚合源码目录（去重）
source_dirs = set()
for row in imports.rows:
    dep_file = row["dep_file"]
    # 取所在目录（去掉文件名）
    source_dir = dirname(dep_file)
    if source_dir.startswith("src/"):
        source_dirs.add(source_dir)

# 结果：CMakeLists.txt 中需要 glob 这些目录的 *.cpp
```

**为什么 IMPORTS 指向 Module 而不是 File**：图谱把一个文件的 `#include` 目标建模为
「Module」节点（代表一个可编译单元），而非「File」节点。混淆这两个标签会让查询返回空。
拿不准时用 `get_graph_schema()` 查看实际的边两端标签。

**优势**：
- 一次查询替代多层 `#include` 递归
- 自动覆盖间接依赖（手动递归容易遗漏）
- 排除系统头文件（图谱只索引项目内文件）

### 4.6 增量更新：差异分析

**场景**：用户请求 `为 MyClass 补全测试`（已存在 `test_myclass.cpp`）。

```python
# 步骤 1: 从图谱获取类的全部方法
all_methods = codebase_memory_mcp.search_graph(
    project="my-qt-app",
    label="Method",
    qn_pattern=".*\\.MyClass\\..*"
)
all_method_names = {m.name for m in all_methods}

# 步骤 2: 从现有测试文件提取已测试的方法名
# （这一步仍需读取文件，因为图谱不解析测试用例名称）
existing_test_content = read("{test_dir}/ui/test_myclass.cpp")
tested_method_names = extract_test_names_from_testcases(existing_test_content)
# 匹配规则：TEST_F(MyClassTest, {MethodName}_...) → MethodName

# 步骤 3: 差集 = 需要补全的方法
untested_methods = all_method_names - tested_method_names
```

---

## 5. 查询技巧

### 5.1 用 `file_pattern` 缩小范围

```python
# 只看某个目录
search_graph(file_pattern="src/lib/ui/*")

# 只看头文件（通常类定义在 .h）
search_graph(file_pattern="*.h")
search_graph(file_pattern="*.hpp")
```

### 5.2 用 `qn_pattern` 按命名空间/类过滤

```python
# MyClass 的所有方法（无论在哪个命名空间）
search_graph(label="Method", qn_pattern=".*MyClass\\..*")

# 某命名空间下的所有类
search_graph(label="Class", qn_pattern=".*com::example::ui\\..*")
```

### 5.3 用 `min_degree` 找热点

```python
# 被调用 5 次以上的方法（核心 API，测试优先级高）
search_graph(label="Method", min_degree=5)
```

### 5.4 用 Cypher 做复杂查询

```python
# 找死代码（无人调用的函数）→ 可跳过测试或提示用户清理
# 注意：本图谱的 openCypher 子集不支持 `AND NOT prop` 这种布尔取反简写，
#       必须用 `AND prop = false` 显式比较。
query_graph(query="""
    MATCH (f:Function)
    WHERE NOT EXISTS { (f)<-[:CALLS]-() }
      AND f.is_entry_point = false
    RETURN f.name, f.file_path
""")

# 找继承 QWidget 的类（需要特殊处理，GUI 测试）
# 注意：变长路径必须写明范围（如 *1..5），不能只写 *（无界）。
query_graph(query="""
    MATCH (c:Class)-[:INHERITS*1..5]->(base:Class)
    WHERE base.name IN ['QWidget', 'QDialog', 'QMainWindow', 'DMainWindow']
    RETURN c.name, c.file_path
""")
```

---

## 6. 图谱局限与应对策略

MCP 图谱覆盖 95% 的场景，但以下情况图谱信息不足，需用其他 MCP 工具补充：

| 场景 | 为什么图谱不够 | 用什么 MCP 工具替代 |
|------|---------------|-------------------|
| **精确类型推断** | 图谱存的是 AST 级别的签名，模板/宏展开需要语义分析 | `get_code_snippet` 读源码查看完整模板定义 |
| **重载函数区分** | 同名方法图谱合并为一个节点，参数列表需精确签名 | `get_code_snippet` 读取方法签名区域 |
| **宏展开后的真实签名** | 图谱看的是源码字面量，宏展开后的签名看不到 | `get_code_snippet` 读源码 + 编译验证 |
| **private/protected 区分** | 部分语言的图谱对访问修饰符支持不全 | `get_code_snippet` 读取类声明区域 |
| **Q_SLOTS/Q_INVOKABLE 检测** | 图谱不存储 Qt 宏属性 | `get_code_snippet` 读取类声明中 Q_SLOTS 块 |

**重要：不使用 LSP，也不用 `read`/`grep`/`glob` 直读项目源码。** 图谱是项目源码理解的唯一来源——方法体用 `get_code_snippet`、调用链用 `trace_path`、类与方法用 `search_graph`、IMPORTS/复杂关系用 `query_graph`。LSP 未集成，不降级到文件扫描/LSP；`read`/`grep` 仅限技能自带文件（references/templates/scripts）、inventory/defects JSON、已生成的测试文件。逐文件 `read` 源码会漏传递依赖、漏隐式分支，是低质单测的首要根因（Iron Law #12）。

**判断流程**：

```
1. 先用 search_graph / query_graph 获取结构（快，全局视角）
2. 需要方法体/签名/分支 → 用 get_code_snippet（不是 read 源文件）
3. 需要调用链/隐式依赖 → 用 trace_path(direction="outbound", depth=3)
4. 编译失败且错误指向签名不匹配 → 用 get_code_snippet 重新读签名，再编译验证
```

---

## 7. 常见陷阱

### 7.1 qualified_name 自己拼会出错

**错**：
```python
# 自己拼 → 命名空间嵌套、模板参数都可能导致格式不对
snippet = get_code_snippet(qualified_name="myproject.MyClass<T>::method")
```

**对**：
```python
# 从 search_graph 拿
results = search_graph(label="Method", qn_pattern=".*MyClass.*method.*")
snippet = get_code_snippet(qualified_name=results[0].qualified_name)
```

### 7.2 忘记传 `project` 参数

```python
# 错：返回的可能是别的项目的结果
search_graph(label="Class")

# 对：明确指定项目
search_graph(project="my-qt-app", label="Class")
```

### 7.3 用 `limit` 太小漏掉结果

`search_graph` 默认 `limit=200`。检查返回的 `has_more` 字段：
```python
result = search_graph(...)
if result.has_more:
    # 结果被截断，需要提高 limit 或缩小范围
    result = search_graph(..., limit=500)
```

### 7.4 项目名记不住

项目名是 repo 绝对路径的转换（`/` → `-`）。拿不准时：
```python
import os
projects = list_projects()
project_basename = os.path.basename(project_path.rstrip('/'))
# 找 root_path 最后一段匹配的那个，用它的 name
matched = [p for p in projects if os.path.basename(p.root_path.rstrip('/')) == project_basename]
project_name = matched[0].name if matched else None
```

### 7.5 索引后立即查询返回空

索引是异步的。`index_repository` 返回 `status="indexed"` 后，daemon 还需要几秒
构建索引。最佳实践：
```python
index_repository(repo_path="/abs/path", mode="moderate")
# 等待 2-3 秒
while index_status(project="...").status == "indexing":
    sleep(1)
```

---

## 附录：完整工作流示例

为 `src/lib/ui/MyWidget` 类生成单元测试的完整 MCP 调用序列：

```python
PROJECT = "my-qt-app"

# 1. 确认索引（首次或手动刷新时）
if not is_project_indexed(PROJECT):
    index_repository(repo_path="/path/to/project", mode="moderate")

# 2. 查找目标类
classes = search_graph(
    project=PROJECT,
    label="Class",
    qn_pattern=".*MyWidget$"
)
target_class = classes[0]

# 3. 获取该类所有方法
methods = search_graph(
    project=PROJECT,
    label="Method",
    qn_pattern=f".*{target_class.name}\\..*"
)

# 4. 对每个方法，获取源代码 + 调用链
for method in methods:
    # 源代码（含签名）
    code = get_code_snippet(qualified_name=method.qualified_name)

    # 调用链（决定 Stub）
    callees = trace_path(
        project=PROJECT,
        function_name=method.name,
        direction="outbound",
        depth=2
    )

    # 生成测试用例...
    generate_test_case(method, code, callees)

# 5. 获取依赖目录（CMakeLists.txt 用）
dep_dirs = query_graph(
    project=PROJECT,
    query="""
        MATCH (f:File)-[:IMPORTS]->(dep:Module)
        WHERE f.file_path CONTAINS 'MyWidget'
        RETURN DISTINCT dep.file_path
    """
)
```
