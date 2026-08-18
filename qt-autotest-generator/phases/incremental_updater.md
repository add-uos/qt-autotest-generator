# 增量补全

> 前置条件：目标类已有测试文件（session 中 `test_file` 存在，`status` 为 `done` 或 `self_check_failed`），图谱 ready。

> 通过 session.mcp_provider 调用知识图谱工具（详见 resources/references/mcp-providers.md）

## 概述

用知识图谱拉取目标类全量方法，与现有测试文件做差集，**只补缺失的用例**，不覆盖已有 TEST_F。支持用户显式"补全"和自动检测覆盖率缺口两种触发方式。

**额外触发**：当自检检出 lcov 函数覆盖率低于 `session.coverage_threshold`（默认 80）时，从 lcov `filtered.info` 的 `FNDA:0` 行解析**未被执行的函数**，补全这些函数的用例以提升函数覆盖率。

## 工作步骤

### 1. 确定补全目标

补全目标来自两个来源，取并集：

#### 来源 A：图谱方法名差集（结构性缺口）
```python
all_methods = codebase_memory_mcp.search_graph(
    project=session.project_name_in_graph,
    label="Method",
    qn_pattern=f".*\\.{target_class.name}\\..*",
    limit=100
)
all_method_names = {m.name for m in all_methods if m.access in ("public", "protected")}

existing_content = read(target_class.test_file)
tested_names = extract_tested_methods(existing_content)

untested_methods = all_method_names - tested_names  # 结构性缺口
```

#### 来源 B：lcov 未覆盖函数（函数覆盖率缺口）
```python
# 仅当自检传入 uncovered_functions 时执行
uncovered_from_lcov = target_class.self_check.get("uncovered_functions", [])

# 合并去重
methods_to_add = untested_methods | set(uncovered_from_lcov)
```

若 `methods_to_add` 为空 → 该类已全覆盖，无需补全，标记 `status=done`。

### 2. 解析函数名为图谱节点

`methods_to_add` 是方法名（字符串）的集合。补全前需将每个名字映射回图谱 `Method` 节点以获取 `qualified_name`：

```python
# 用 search_graph 按类名 + 方法名精确定位节点
resolved = {}
for name in methods_to_add:
    nodes = codebase_memory_mcp.search_graph(
        project=session.project_name_in_graph,
        label="Method",
        name_pattern=f".*\\.{target_class.name}\\.{name}$"
    )
    if nodes:
        resolved[name] = nodes[0]  # 取首个匹配，携带 qualified_name
    # 无匹配：lcov 函数名可能含参数/重载信息，用 name_pattern 宽松匹配重试
```

无图谱匹配的 lcov 函数名（如自由函数、运算符重载）记录后跳过，不阻塞补全。

### 3. 对每个待补方法补全

对 `resolved` 中的每个方法（跳过无图谱匹配的）：

a. 从图谱获取源码：
```python
snippet = codebase_memory_mcp.get_code_snippet(
    qualified_name=resolved[name].qualified_name   # 从图谱节点取
)
```

b. 追踪依赖（复用依赖追踪逻辑）：
```python
callees = codebase_memory_mcp.trace_path(
    project=session.project_name_in_graph,
    function_name=resolved[name].qualified_name,
    direction="outbound",
    depth=2
)
# 按 stub 决策矩阵决定 stub
```

c. 生成测试用例（AAA 模式、命名规范同测试代码生成阶段；**Fixture 类名和用例名禁止携带轮数/批次号**）

### 4. 追加到现有测试文件

在测试文件末尾、`{NamespaceEnd}` 之前追加：

```cpp
// === Auto-generated tests (incremental) ===

TEST_F(MyClassTest, MethodX_ValidInput_ReturnsExpected) {
    // Arrange
    ...
    // Act
    ...
    // Assert
    ...
}
```

> **注意**：绝不修改或删除已有 TEST_F，只 append。

### 5. CMake 智能合并

若新增方法引入了新的源码依赖目录：
- 读 `autotests/<module>/CMakeLists.txt`
- 检查是否已 glob 该目录
- 若无，追加到 `file(GLOB ...)` 或 `target_sources`

> **注意**：绝不修改已有 CMake 行，只追加。

### 6. 更新 session

```json
{
  "methods_tested": 18,
  "status": "test_written",
  "incremental_added": ["methodX", "methodY"],
  "incremental_source": "lcov"
}
```

## 关键约束

- 不覆盖已有 TEST_F：只 append，不修改不删除
- 不修改已有 CMake 行：只追加新依赖
- 不为 private 方法补测试
- `qualified_name` 必须从图谱返回值取，不自己拼
- 新增方法可能引入新依赖：必须 trace_path
- 不修改项目源码
- 追加后必须回到编译验证阶段重新验证
