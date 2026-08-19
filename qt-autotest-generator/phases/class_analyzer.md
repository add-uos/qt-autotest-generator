# 类分析

> 前置条件：`environment_check` 已通过（图谱 ready），`framework_builder` 已通过（`{test_dir}/` 存在），session 中 `project_name_in_graph` 已记录。

> 通过 session.mcp_provider 调用知识图谱工具（详见 resources/references/mcp-providers.md）

## 概述

用知识图谱批量拉取目标范围内的类与方法，识别 GUI 类，按复杂度规划每个方法的测试用例数量。此阶段只分析和规划，不生成测试代码。

支持两种模式：
- `full`（全量分析）：分析目标范围内所有类
- `diff`（对账模式）：与 session 记录做差集，找出新增/删除/签名变更的方法

### 分支切换后的 stale 类处理

当 reconcile 检测到分支切换时，类分析需额外处理：

1. 遍历 `session.classes`，用 MCP `get_code_snippet` 或 `git show HEAD:<file_path>` 检查每个类的源文件是否在当前分支仍存在
2. 不存在的类 → 标记 `status="stale"`，更新到 session
3. 从 `{test_dir}/CMakeLists.txt` 中移除 stale 类对应的 `add_subdirectory` 行（避免编译失败）
4. **保留** stale 类的测试文件（不删除，供切回原分支后恢复）
5. 将 stale 类名列表写入 `session.stale_classes`
6. 新分支的类走正常的 `full` 或 `diff` 分析

## 工作步骤

### 1. 确认图谱 ready

```python
status = codebase_memory_mcp.index_status(project=session.project_name_in_graph)
# 必须 status == "ready"，否则停止（environment_check 已保证）
```

### 2. 批量拉取目标类

```python
classes = codebase_memory_mcp.search_graph(
    project=session.project_name_in_graph,
    label="Class",
    file_pattern=target_scope,   # 如 "src/lib/ui/*"
    limit=200
)
```

**筛选规则**：
- 只保留 `is_exported=true` 的类（public 可见）
- 排除 `is_test=true` 的类（避免为测试代码生成测试）
- 检查 `has_more`，若截断则提高 limit 或缩小范围

### 3. 对每个类拉取方法

```python
for cls in classes:
    methods = codebase_memory_mcp.search_graph(
        project=session.project_name_in_graph,
        label="Method",
        qn_pattern=f".*\\.{cls.name}\\..*",
        limit=100
    )
```

**访问级别过滤**：
- 只保留 **public** 和 **protected** 方法
- **绝不**为 private 方法生成测试（不可访问）

### 4. GUI 继承识别

用 Cypher 查询继承链：

```python
gui_classes = codebase_memory_mcp.query_graph(
    project=session.project_name_in_graph,
    query="""
        MATCH (c:Class)-[:INHERITS*1..5]->(base:Class)
        WHERE base.name IN ['QWidget', 'QDialog', 'QMainWindow', 'DMainWindow',
                           'DFrame', 'DWidget', 'DAbstractDialog']
        RETURN c.name, c.qualified_name, c.file_path
    """
)
```

GUI 类标记 `is_gui=true`，后续测试代码生成阶段会特殊处理（用 `QCoreApplication` 而非 `QApplication`，避免 X11/Wayland 崩溃）。

### 5. 按复杂度规划用例数

读每个方法的图谱属性：`complexity`（圈复杂度）、`loop_count`（循环数）、`param_count`（参数数）、`cognitive`（认知复杂度）。

规划规则：

| 方法特征 | 最少用例数 | 用例类型 | 对应 test-types.md 章节 |
|---------|-----------|---------|------------------------|
| `complexity >= 10` | 3 | 正常 + 边界 + 异常 | §1 有效等价类 + §2 边界值 + §5 异常路径 + §6 负面测试 |
| `loop_count >= 1` | +1 | 循环边界（空集合、单元素、超大集合） | §2.1 循环计数 + §4.2 for 循环分支 |
| `param_count >= 4` | +1 | 参数组合 | §1.2 多维等价类组合 |
| 普通方法 | 1 | 正常路径 | §1 有效等价类 |

> 用例类型词汇与 `resources/references/test-types.md` 章节一一对应，测试代码生成阶段据此规划生成具体用例时按对应章节方法落地。

### 6. diff 模式（对账）

若 `mode="diff"`：与 session 中已记录的类/方法做 diff：

```python
# session 中已记录的方法
recorded_methods = {m.name for m in session.classes[cls].methods}
# 图谱当前全量方法
current_methods = {m.name for m in methods_from_graph}

new_methods = current_methods - recorded_methods          # 新增
removed_methods = recorded_methods - current_methods       # 删除
# 签名变更：对比 get_code_snippet 的签名部分
```

产出差异清单，按差异类型路由：
- 新增方法 → `incremental_updater`
- 签名变更 → `test_writer`（重新生成该类）
- 方法删除 → `failure_repairer`（清理引用）

### 7. 更新 session

为每个类写入/更新记录：

```json
{
  "name": "MyClass",
  "qualified_name": "project.src.MyClass",
  "file_path": "src/lib/ui/myclass.h",
  "status": "pending",
  "is_gui": false,
  "methods_total": 15,
  "methods_tested": 0,
  "test_plan": [
    {"name": "methodA", "access": "public", "complexity": 12, "planned_cases": 3},
    {"name": "methodB", "access": "protected", "complexity": 3, "planned_cases": 1}
  ]
}
```

### 8. 边界类处理

以下特殊类需标记 `special_handling` 字段，供后续阶段参考：

| 类特征 | 标记值 | 处理建议 |
|--------|--------|--------|
| 模板类（`template<typename T> class`） | `template` | 测试代码生成需指定具体类型实例化 |
| Q_OBJECT 宏类 | `q_object` | 依赖追踪需确认 MOC 处理 |
| 匿名命名空间类 | `anonymous_ns` | 内部类，按需测试 |
| 私有构造函数（单例/工厂） | `private_ctor` | 测试代码生成用 friend 或工厂创建实例 |
| 纯虚抽象类 | `abstract` | 测试代码生成创建最小具体子类 |
| PIMPL 模式 | `pimpl` | 依赖追踪需追踪 Private 类 |

## 关键约束

- 不生成测试代码（只分析和规划）
- 不为 private 方法规划测试
- `qualified_name` 必须从 `search_graph` 返回值取，不自己拼（命名空间/嵌套类规则复杂）
- 截断时必须提高 limit 或缩小范围，不忽略 `has_more`
- 不跳过 GUI 识别（GUI 类不特殊处理会导致 segfault）
- 不修改项目源码

### MCP 查询失败处理策略

| 查询类型 | 严重程度 | 失败处理 |
|---------|---------|----------|
| `search_graph`（类/方法结构） | 关键 | **硬终止** + 明确错误：`[FATAL] 图谱类结构查询失败，请检查 MCP 提供方和索引状态` |
| `get_code_snippet`（源码签名） | 关键 | **硬终止** + 明确错误：无法获取源码签名则不能生成测试 |
| `trace_path`（依赖链） | 关键 | **硬终止** + 明确错误：依赖追踪失败则 CMake 无法正确配置 |
| `query_graph`（辅助查询） | 非关键 | **降级** + 警告：返回空时用文件读取兜底，继续执行 |
