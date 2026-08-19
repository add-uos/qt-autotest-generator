# 单元测试编写（Mode 2）

> 前置条件：知识图谱已就绪（`environment_check` 通过），`.ut-inventory.json` 存在。

## 适用时机

用户意图为**编写/补全/修复单元测试**。典型触发：

- 生成单测、建测试框架、批量生成单测、补全测试、修测试、重新对账
- add gtest、setup unit tests、coverage gap、fix test failures、sync tests

**若 `.ut-inventory.json` 不存在** → 先执行 Mode 1（`Read reference/inventory.md`）。

## 概述

读取 `.ut-inventory.json` 的分级信息，按 high → mid → low 优先级逐类生成 Google Test 用例，强制编译验证，覆盖率门禁自检，每类通过后更新 `usecase_count`。全类完成后提交代码并生成报告。

## 状态传递

本模式**不使用 session 文件**。运行时状态通过以下方式传递：

| 状态 | 来源 | 读取方式 |
|------|------|---------|
| MCP 提供方 | `environment_check` 解析结果 | 内存变量 `mcp_provider` |
| 项目名（图谱） | `environment_check` 解析结果 | 内存变量 `project_name_in_graph` |
| 测试目录 | `environment_check` 探测结果 | 内存变量 `test_dir` |
| Qt 版本 | `framework_builder` 检测结果 | 内存变量 `qt_version` |
| 方法分级 + 门禁 | `.ut-inventory.json` | 读文件 |
| 用例数 | `.ut-inventory.json` 的 `usecase_count` | 读/写文件 |
| 类处理状态 | 逐类闭环运行时 | 内存变量 `class_status[classname]` |
| 迭代计数 | 逐类闭环运行时 | 内存变量 `iteration_count[classname]` |
| 已提交类 | `git log` | `git log --oneline` 查询 |

## 工作流程

### 0. 前置检查

```python
inventory_path = f"{test_dir}/.ut-inventory.json"
if not file_exists(inventory_path):
    # 自动触发 Mode 1
    Read("reference/inventory.md")
    # Mode 1 完成后 inventory_path 应存在

inventory = read_json(inventory_path)
gui_names = {c["name"] for c in inventory.get("classes", []) if c.get("is_gui")}
```

### 2. 环境门禁

`Read reference/environment_check.md` → MCP 提供方解析、索引验证

### 3. 框架搭建（按需）

若 `{test_dir}/` 不存在 → `Read reference/framework_builder.md`

### 4. 确定待测类列表

从 `.ut-inventory.json` 提取待测类：

```python
# 按优先级排序：high > mid > low
testable_classes = {}
for method in inventory["methods"]:
    if not method["testable"]:
        continue
    class_qn = method.get("class_qn", "")
    if not class_qn:
        continue  # 自由函数暂跳过
    if class_qn not in testable_classes:
        testable_classes[class_qn] = {
            "name": class_qn.split(".")[-1],
            "qualified_name": class_qn,
            "level": method["level"],  # 取该类最高 level
            "is_gui": class_qn in gui_names,  # methods[].class_qn 是短名，用 classes[].name 匹配
            "methods": []
        }
    testable_classes[class_qn]["methods"].append(method)
    # 类 level 取其方法中最高级
    if level_rank(method["level"]) > level_rank(testable_classes[class_qn]["level"]):
        testable_classes[class_qn]["level"] = method["level"]

# 排序：high → mid → low
sorted_classes = sorted(testable_classes.values(), key=lambda c: -level_rank(c["level"]))

# 初始化类处理状态（内存变量）
for c in sorted_classes:
    class_status[c["name"]] = {"status": "pending"}
```

### 5. 逐类闭环

对 `sorted_classes` 中每个类，执行闭环链：

```
依赖追踪 → 测试代码生成 → 编译验证 → 自检
   ↑                          |
   └──── 失败修复 ←───────────┘
   ↑                          |
   └──── 增量补全 ←── 覆盖率缺口 ┘
```

每步读取对应 prompt 文件：

| 步骤 | 文件 | 说明 |
|------|------|------|
| 依赖追踪 | `reference/dependency_tracer.md` | 读 inventory 的 is_gui、MCP trace_path 出向、stub 决策、CMake 目录 |
| 测试代码生成 | `reference/test_code_gen.md` | 读模板生成测试代码、用例数下限从 level/factors 推导、AAA、命名 |
| 编译验证 | `reference/build_verifier.md` | 强制编译+运行、错误分类→修复表 |
| 自检 | `reference/self_checker.md` | 覆盖率/命名/SPDX/stub/断言强度/环境隔离 |
| 失败修复 | `reference/failure_repairer.md` | 编译/运行失败时 |
| 增量补全 | `reference/incremental_updater.md` | 覆盖率缺口时 |

**迭代上限**：同一类最多循环 3 轮（Iron Law #10）。3 轮后仍未通过 → 标记 `failed` + `max_iterations_exceeded`，跳过该类，继续下一个。

**单类失败不阻塞**：记录 `failure_reason`，跳过，继续下一个类。

### 6. 每类通过后更新 usecase_count

**关键**：每类编译通过（build_verifier pass + self_checker pass）后，立即更新 `.ut-inventory.json`：

```python
# 1. 扫描测试文件统计用例数
test_file = f"{test_dir}/{module}/test_{classname}.cpp"
content = read(test_file)
case_count = len(re.findall(r'TEST_F\s*\(\s*\w+Test\s*,', content))

# 2. 更新 inventory
inventory = read_json(inventory_path)
for method in inventory["methods"]:
    if method.get("class_qn") == class_qn and method["testable"]:
        # 按方法名匹配测试用例（用例名首段 PascalCase，方法名 camelCase → 小写归一化后比对）
        method_cases = count_cases_for_method(content, method["name"].lower())
        method["usecase_count"] = method_cases

# 3. 写回
write_json(inventory_path, inventory)
```

> **注意**：更新 inventory 是增量操作，只改当前类的 `usecase_count`，不覆盖其他类的数据。

### 7. 覆盖率门禁

从 `.ut-inventory.json` 读取分级门禁：

```python
gate = inventory["gate_thresholds"]
# high: 行90% + 分支80% + 函数100%
# mid:  行60% + 函数100%
# low:  行60% + 函数100%（同 mid）
```

自检阶段按方法 level 应用对应门禁。详见 `reference/self_checker.md` 和 `reference/coverage-tiers.md`。

### 8. 批次提交

本批次所有类自检通过后 → `Read reference/code_committer.md`

只 commit，不 push。

### 9. 报告生成

全类完成 → `Read reference/report_generator.md`

产出覆盖率采集报告：gtest XML + lcov HTML + 分级覆盖率 + 汇总 JSON。

## MCP 查询策略

Mode 2 的 MCP 查询集中在依赖追踪和测试代码生成阶段：

| 查询 | 用途 | 阶段 |
|------|------|------|
| `trace_path(direction="outbound")` | 出向调用链 | 依赖追踪 |
| `query_graph(IMPORTS)` | 补充传递依赖 | 依赖追踪 |
| `search_graph` | 头文件/符号查找 | 依赖追踪 |
| `get_code_snippet(qn)` | 读取方法源码/签名 | 测试代码生成 |

**查询失败处理**：

| 严重程度 | 处理 |
|---------|------|
| 关键（search_graph/trace_path/get_code_snippet） | 硬终止 + 明确错误 |
| 非关键（query_graph 辅助查询） | 降级 + 警告，用文件读取兜底 |

## 关键约束

- 不修改项目源码
- 不从网络下载 stub-ext
- 不用 Qt Test / Catch2
- 不跳过编译验证
- 单类失败不阻塞其他类
- 每类通过后必须更新 `.ut-inventory.json` 的 `usecase_count`
- 迭代上限 3 轮
- 只 commit 不 push
