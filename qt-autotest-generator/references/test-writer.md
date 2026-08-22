# 单元测试编写（Mode 2）

> 前置条件：知识图谱已就绪（`environment_check` 通过），`.ut-inventory.json` 存在。

## 适用时机

用户意图为**编写/补全/修复单元测试**。典型触发：

- 生成单测、建测试框架、批量生成单测、补全测试、修测试、重新对账
- add gtest、setup unit tests、coverage gap、fix test failures、sync tests

**若 `.ut-inventory.json` 不存在** → 先执行 Mode 1（`Read references/inventory.md`）。

## 概述

读取 `.ut-inventory.json` 的分级信息，按 high → mid → low 优先级**以类为单位**逐类生成 Google Test 用例，强制编译验证，覆盖率门禁自检，每类通过后更新 `usecase_count`。全类完成后提交代码并生成报告。

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
    Read("references/inventory.md")
    # Mode 1 完成后 inventory_path 应存在

inventory = read_json(inventory_path)
gui_names = {c["name"] for c in inventory.get("classes", []) if c.get("is_gui")}
```

### 2. 环境门禁

`Read references/environment-check.md` → MCP 提供方解析、索引验证

### 3. 框架搭建（按需）

若 `{test_dir}/` 不存在 → `Read references/framework-builder.md`

### 4. 确定待测类列表

> **首选方式**：跑 `scripts/plan-test-classes.py`，消费 `{test_dir}/.reports/testable-classes.json`。
> 脚本固化了按 class_qn 分组 → 类 level 取最高 → level_rank 排序 → is_gui 匹配 →
> 自由函数归组全流程，兼容双 schema 字段（`qn`/`file` vs `qualified_name`/`file_path`）。
> 模型只读排好序的类清单，不读 inventory 全量。
>
> ```bash
> python3 ${SKILL_DIR}/scripts/plan-test-classes.py --inventory ${test_dir}/.ut-inventory.json
> # 输出：${test_dir}/.reports/testable-classes.json + stdout 摘要
> ```
>
> 下方伪代码仅作兜底（脚本不可用时）。

从 `.ut-inventory.json` 提取待测类：

```python
# 按优先级排序：high > mid > low
testable_classes = {}
for method in inventory["methods"]:
    if not method["testable"]:
        continue
    class_qn = method.get("class_qn", "")
    if not class_qn:
        continue  # 自由函数单独收集（见下方 free_functions 列表）
    if class_qn not in testable_classes:
        testable_classes[class_qn] = {
            "name": class_qn.rsplit(".", 1)[-1] if "." in class_qn else class_qn,  # 短名（class_qn 为短名时直接用）
            "qualified_name": class_qn,
            "level": method["level"],  # 取该类最高 level
            "is_gui": (class_qn.rsplit(".", 1)[-1] if "." in class_qn else class_qn) in gui_names,  # class_qn 短名匹配 classes[].name
            "methods": []
        }
    testable_classes[class_qn]["methods"].append(method)
    # 类 level 取其方法中最高级
    if level_rank(method["level"]) > level_rank(testable_classes[class_qn]["level"]):
        testable_classes[class_qn]["level"] = method["level"]

# 自由函数收集（class_qn 为空的 testable 方法）
free_functions = [m for m in inventory["methods"] if m["testable"] and not m.get("class_qn")]

# 排序：high → mid → low
sorted_classes = sorted(testable_classes.values(), key=lambda c: -level_rank(c["level"]))

# 初始化类处理状态（内存变量）
for c in sorted_classes:
    class_status[c["name"]] = {"status": "pending"}
```

> **优先级排序作用于类粒度，不按方法优先级跨类穿插**：排序对象是**类**（类 level = 其方法中最高 level）。同类内所有 testable 方法（含 low/析构）随该类**一次性闭环处理完**，不存在"先写完全部类的 high 方法再写 mid 方法"的跨类穿插。方法 level 只决定用例数下限与覆盖率门禁（见 test-code-gen.md §4 / §7），不决定处理顺序。

#### 自由函数处理策略

自由函数（`class_qn` 为空、`node_type="Function"`）按以下规则处理：

- **同文件自由函数归组**：按 `file_path` 分组，同一源文件下的自由函数归入一个测试文件 `{test_dir}/{module}/test_free_{module}.cpp`
- **Fixture 命名**：`Free{Module}Test`（如 `FreeUtilsTest`）
- **优先级排序**：与类方法相同，按 level 高低排序
- **依赖追踪 / stub / 编译验证 / 自检**：与类方法走相同闭环
- **usecase_count 更新**：自由函数的 `usecase_count` 独立统计并写回 inventory
- **不跳过**：自由函数不是"暂跳过"，而是延后处理——所有类完成后再处理自由函数

> **注意**：若自由函数与某类强耦合（如友元函数、仅服务于某类的工具函数），可考虑合并到该类测试文件中，由 Agent 根据源码上下文判断。
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
| 依赖追踪 | `references/dependency-tracer.md` | 读 inventory 的 is_gui、MCP trace_path 出向、stub 决策、CMake 目录 |
| 测试代码生成 | `references/test-code-gen.md` | 读模板生成测试代码、用例数下限从 level/factors 推导、AAA、命名 |
| 编译验证 | `references/build-verifier.md` | 强制编译+运行、错误分类→修复表 |
| 自检 | `references/self-checker.md` | 覆盖率/命名/SPDX/stub/断言强度/环境隔离 |
| 失败修复 | `references/failure-repairer.md` | 编译/运行失败时 |
| 增量补全 | `references/incremental-updater.md` | 覆盖率缺口时 |

**迭代上限**：同一类最多循环 3 轮（Iron Law #10）。3 轮后仍未通过 → 标记 `failed` + `max_iterations_exceeded`，跳过该类，继续下一个。

**单类失败不阻塞**：记录 `failure_reason`，跳过，继续下一个类。

### 6. 每类通过后更新 usecase_count

> **首选方式**：跑 `scripts/update-usecase-count.py`，脚本统计 TEST_F 用例数 +
> 按方法名匹配（首段 PascalCase vs 方法名 camelCase 小写归一化）增量写回 inventory。
> 失败安全：匹配不到的方法保持原值不动。只改当前类方法，不覆盖其他类。
>
> ```bash
> python3 ${SKILL_DIR}/scripts/update-usecase-count.py \
>     --test-file ${test_dir}/${module}/test_${classname}.cpp \
>     --inventory ${test_dir}/.ut-inventory.json --class ${classname}
> # 同名类歧义时用 --class-qn <全限定名> 精确匹配
> ```
>
> 下方伪代码仅作兜底（脚本不可用时）。

**关键**：每类编译通过（build-verifier pass + self-checker pass）后，立即更新 `.ut-inventory.json`：

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
```

自检阶段按方法 level 应用对应门禁。详见 `references/self-checker.md` 和 `references/coverage-tiers.md`。

### 8. 批次提交

本批次所有类自检通过后 → `Read references/code-committer.md`

只 commit，不 push。

### 9. 收尾与最终退出前报告

全部批次提交完成（无下一批次）即 Mode 2 结束。此时统一生成**最终报告**，只执行一次：

1. **Mode 3 覆盖率报告**：`Read references/report-generator.md` → 调用 `scripts/collect-coverage-report.py`（gtest XML + lcov HTML + 分级覆盖率 + 汇总 JSON）
2. **Mode 5 缺陷导出**：`Read references/defect-exporter.md` → 调用 `scripts/export-defects.py export`（`.ut-defects.json` → `defects.json` + `defects-summary.md`）

> ⚠️ **报告只在最终退出前生成一次**，不在每笔批次提交后触发。原因：Mode 2 可能有**多笔批次提交**（每批次一笔），中间重复跑报告既浪费又状态不完整；最终退出前的报告反映所有类、所有缺陷、累计覆盖率的完整状态。

> Mode 3 / Mode 5 也可被用户**单独触发**（只采集覆盖率 / 只导出缺陷），此时不走 Mode 2 流程，直接执行对应 reference。

## MCP 查询策略

Mode 2 的 MCP 查询集中在依赖追踪和测试代码生成阶段。**被测类的实现/签名/调用链/分支/隐式依赖一律走 MCP，禁止 `read`/`grep` 直读项目源码文件**（Iron Law #12）；`read` 只用于技能自带文件、inventory/defects JSON、已生成的测试文件：

| 查询 | 用途 | 阶段 |
|------|------|------|
| `trace_path(direction="outbound")` | 出向调用链（分支/隐式依赖/emit） | 依赖追踪 + 测试生成 |
| `query_graph(IMPORTS)` | 补充传递依赖 | 依赖追踪 |
| `search_graph` | 头文件/符号/类与方法查找 | 依赖追踪 |
| `get_code_snippet(qn)` | 方法体全文（含签名/返回类型/分支） | 测试生成 + 自检反查 |

**查询失败处理**：

| 严重程度 | 处理 |
|---------|------|
| 关键（search_graph/trace_path/get_code_snippet） | 硬终止 + 明确错误 |
| 非关键（query_graph 辅助查询） | 降级 + 警告，改用 `trace_path`+`search_graph` 重新聚合，**不读项目源码文件** |

## 关键约束

- 不修改项目源码
- 不从网络下载 stub-ext
- 不用 Qt Test / Catch2
- 不跳过编译验证
- 单类失败不阻塞其他类
- 每类通过后必须更新 `.ut-inventory.json` 的 `usecase_count`
- 迭代上限 3 轮
- 只 commit 不 push
