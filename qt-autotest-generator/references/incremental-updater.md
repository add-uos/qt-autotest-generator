# 增量补全

> 前置条件：目标类已有测试文件（`class_status[classname]` 中 `test_file` 存在，`status` 为 `done` 或 `self_check_failed`（内存变量）），图谱 ready。

> 通过 GitNexus 代码图谱 MCP 定位符号（详见 references/gitnexus-guide.md）

## 概述

用知识图谱拉取目标类全量方法，与现有测试文件做差集，**只补缺失的用例**，不覆盖已有 TEST_F。支持用户显式"补全"和自动检测覆盖率缺口两种触发方式。

**额外触发**：当自检检出覆盖率低于门禁时，补全未覆盖函数的用例。必须有 `.ut-inventory.json`，按方法分级门禁（high: 行90%+分支80%+函数100%，⚖mid: 行60%+函数100%，💤low: 行60%+函数100%）。

### 0. 迭代计数递增（Iron Law #10）

当增量补全被触发（无论是覆盖率缺口还是方法名差集），意味着该类即将进入新一轮闭环，递增迭代计数：

```python
MAX_ITERATIONS = 3
iter_count = iteration_count.get(classname, 1) + 1
iteration_count[classname] = iter_count

if iter_count > MAX_ITERATIONS:
    # 超过上限，强制标红（self-checker Step 0 也会拦截，此处双重保障）
    class_status[classname].update({
        "status": "failed",
        "failure_reason": "max_iterations_exceeded",
    })
    return  # 不再补全，跳过
```

## 工作步骤

### 1. 确定补全目标

补全目标来自两个来源，取并集：

#### 来源 A：图谱方法名差集（结构性缺口）
```python
# GitNexus：HAS_METHOD 边取类的全部方法（repo 参数必带；关系类型在边属性 r.type 上）
rows = cypher(
    f"MATCH (c:Class {{name: '{target_class.name}'}})-[r:CodeRelation]->(m:Method) "
    "WHERE r.type = 'HAS_METHOD' "
    "RETURN m.name AS name, m.filePath AS filePath SKIP 0 LIMIT 100")
all_method_names = {r["name"] for r in rows}  # 按可见性过滤（如节点携带 access 属性）

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

#### 来源 C：inventory 覆盖率状态交叉校验（utq）
> 用 `utq` 从 inventory 视角核对该类哪些方法未覆盖（双信号：`test_cover_count` 与
> `usecase_count` 均为 0），与来源 A/B 取并集。
> 图谱差集（A）基于方法名匹配，可能漏掉被测但未在测试文件中出现的方法名变体；
> inventory 的 `test_cover_count` 来自 MCP CALLS 静态分析，是独立的覆盖信号。

```bash
# 该类所有未测方法（JSON，含 signature/factors 供用例设计）
python3 ${SKILL_DIR}/scripts/utq.py -P ${test_dir} todo --class ${target_class.name} --json
```
```python
uncovered_from_inv = {t["name"] for t in utq_todo_json["tasks"]}
methods_to_add |= uncovered_from_inv   # 三来源并集
```

若 `methods_to_add` 为空 → 该类已全覆盖，无需补全，标记 `status=done`。

### 2. 解析函数名为图谱节点

`methods_to_add` 是方法名（字符串）的集合。补全前需将每个名字映射回图谱 `Method` 节点以获取 `qualified_name`：

```python
# 用 cypher 按类名 + 方法名精确定位节点（返回 filePath/startLine 用于本地切片）
resolved = {}
for name in methods_to_add:
    rows = cypher(
        f"MATCH (c:Class {{name: '{target_class.name}'}})-[r:CodeRelation]->(m:Method) "
        "WHERE r.type = 'HAS_METHOD' AND m.name = '" + name + "' "
        "RETURN m.name AS name, m.filePath AS filePath, m.startLine AS startLine, m.endLine AS endLine")
    if rows:
        resolved[name] = rows[0]  # 取首个匹配；重载/多文件命中时按 filePath 消歧
    # 无匹配：lcov 函数名可能含参数/重载信息，用 CONTAINS 宽松匹配重试
```

无图谱匹配的 lcov 函数名（如自由函数、运算符重载）记录后跳过，不阻塞补全。

### 3. 对每个待补方法补全

对 `resolved` 中的每个方法（跳过无图谱匹配的）：

a. 从图谱获取源码：
```python
# 本地行切片（图谱 content 5016 字符截断，不作源）
snippet = slice_body(repo_root, resolved[name]["filePath"],
                     resolved[name]["startLine"], resolved[name]["endLine"])
```

b. 追踪依赖（复用依赖追踪逻辑）：
```python
# CALLS 出向边（入向把方向反转；变长路径未实测，逐跳组合）
callees = cypher(
    f"MATCH (src {{filePath: '{resolved[name]['filePath']}'}})"
    "-[r:CodeRelation {{type:'CALLS'}}]->(t) "
    f"WHERE src.name = '{resolved[name]['name']}' "
    "RETURN DISTINCT t.filePath AS filePath, t.name AS name")
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
- 读 `{test_dir}/<module>/CMakeLists.txt`
- 检查是否已 glob 该目录
- 若无，追加到 `file(GLOB ...)` 或 `target_sources`

> **注意**：绝不修改已有 CMake 行，只追加。

### 6. 记录补全结果

将补全结果记录到内存变量 `class_status[classname]`：

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
- `qualified_name` 必须与 inventory 一致（Class.name，撞名带 @文件名/@行号），不自己拼
- 新增方法可能引入新依赖：必须查 CALLS 出向边
- 不修改项目源码
- 分支切回原分支时：恢复 `stale_classes`（内存变量）中类的 `add_subdirectory` 行到 CMakeLists.txt，并将 status 从 `stale` 改回原值
- 追加后必须回到编译验证阶段重新验证
