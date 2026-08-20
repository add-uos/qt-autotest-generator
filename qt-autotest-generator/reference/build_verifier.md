# 编译验证

> 前置条件：`test_writer` 已完成目标类（`class_status[classname].status=test_written`（内存变量）），`{test_dir}/<module>/test_<classname>.cpp` 存在。

> 通过 mcp_provider 调用知识图谱工具（详见 reference/mcp-providers.md）

## 概述

强制编译并运行目标类的测试，按错误分类表修复，在重试预算内达成编译+运行通过。产出**双信号**（编译/运行结果 + 覆盖率信号）。**不修源码**，疑似源码缺陷标红交还用户。

若 `iteration_count[classname]` >= 3（Iron Law #10），跳过验证，直接标记 `failed` + `max_iterations_exceeded`。

## 工作步骤

### 1. 编译测试

test_dir = test_dir  # "autotests" 或 "tests"（内存变量）
cd ${PROJECT_PATH}/build-${test_dir}
cmake .. -DBUILD_TESTS=ON 2>&1
cmake --build . -j$(nproc) --target test_<classname> 2>&1

捕获完整编译输出。

### 2. 编译失败 → 错误分类与修复

按错误模式分类，逐个修复（per-error 3 次重试，总计 max 10 loops）：

| 错误模式 | 修复策略 |
|---------|--------|
| `undefined reference to` | 在 CMakeLists `target_link_libraries` 补依赖；用 MCP `trace_path` 重新追踪遗漏的传递依赖 |
| `No such file or directory`（头文件） | 在 CMakeLists `target_include_directories` 补路径 |
| `stub.set_lamda` 签名不匹配 | 用 MCP `get_code_snippet` 重新读方法签名，修正 stub |
| `expected primary-expression` | 检查返回类型/参数类型，用 `static_cast` 修正重载 |
| `CMake Error` | 修 CMakeLists.txt 语法 |
| `undefined reference to stub_ext::freeWrapper` | 确认 `templates/stub-ext/stub-shadow.cpp` 已编入 test target |
| `vtable for XXX` / `undefined type` | 检查 Q_OBJECT 宏、MOC 处理 |

> **注意**：只修测试代码和测试 CMakeLists，不修项目源码。每次修复后重新编译，确认该错误消除。同一错误 3 次修不好 → 标记为疑似源码缺陷，停止该错误。

### 3. 编译通过 → 运行测试

test_dir = test_dir  # 内存变量
cd ${PROJECT_PATH}/build-${test_dir}
timeout 120 ./${test_dir}/<module>/test_<classname> --gtest_output=xml:${PROJECT_PATH}/${test_dir}/.results/test_<classname>.xml 2>&1

> **注意**：用 `timeout 120` 限制单类测试执行不超过 2 分钟。超时 → 判定为 `runtime_crash`（可能死循环或 stub 缺失导致真实 IO），记录 `timeout` 标记。

捕获运行输出和退出码。

### 4. 运行失败 → 分类

| 运行失败类型 | 处理 |
|------------|------|
| Segfault / SIGABRT | 可能是 stub 不全或源码缺陷；先尝试补 stub，仍失败则标红 |
| ASSERT 失败 | 检查测试逻辑；若测试逻辑正确但 ASSERT 恒失败 → 疑似源码逻辑缺陷 |
| 超时 | 可能是死循环或 stub 缺失导致真实 IO；补 stub 后重试 |

### 5. 疑似源码缺陷判定

当重试预算耗尽仍失败时，判断根因：

```
重试耗尽仍失败
    ├─ 源码本身编译不过（无测试也编不过）
    │   → failure_reason = "source_defect_compile"
    ├─ 运行时崩溃（segfault/abort），stub 已补全
    │   → failure_reason = "source_defect_runtime"
    ├─ ASSERT 恒失败，测试逻辑正确
    │   → failure_reason = "source_defect_logic"
    └─ 无法判定
        → failure_reason = "needs_manual"
```

> **注意**：判定前必须用 `get_code_snippet` 读源码确认。尝试最小化复现：只构造对象、不调方法，看是否崩溃。若源码缺 `#include`、缺 `Q_OBJECT`、有空实现导致链接失败 → 源码缺陷。

**编译期提前捕获**（Mode 5）：若在重试预算内识别到明确源码特征（如 `fatal error: xxx.h: No such file` 指向源码目录、`undefined reference to` 经 `trace_path` 确认非 stub 缺失、`vtable for XXX` 提示缺 `Q_OBJECT`），可**提前预记录**到 `.ut-defects.json`（`detected_at_stage=compile`），不必等 10 loops 耗尽。后续若发现是测试侧误会，再调 `export-defects.py mark-fixed` 清除。防误判：必须先排除 stub/include/CMake 测试侧问题再落盘。

### 6. 产出双信号

编译+运行结束后，记录到内存变量 `class_status[classname]`：

```json
{
  "build_result": "pass",      // pass / fail
  "run_result": "pass",        // pass / fail / not_run
  "failure_reason": null,      // null / compile_error / runtime_crash / source_defect_*
  "build_log_excerpt": "...",  // 最小错误日志片段（失败时）
  "coverage_snapshot": "...",  // 7c 产出的分级覆盖率 JSON 路径或内联对象（供 self_checker 复用）
  "coverage_gap": [],          // 7b 方法名差集
  "status": "verified"         // verified / failed
}
```

> `verified` 满足条件：`build_result=pass` + `run_result=pass` + 覆盖率快照已产出（7c，lcov 不可用时降级为仅 7b）。门禁达标判定在 `self_checker`。

**缺陷闭环**（Mode 5）：类通过验证（`status=verified`）时，若 `{test_dir}/.ut-defects.json` 存在该类的 `open`/`reopened` 缺陷，调 `export-defects.py mark-fixed` 标记修复，形成「发现→修复」闭环：

```bash
python3 ${SKILL_DIR}/scripts/export-defects.py mark-fixed \
    --defects ${PROJECT_PATH}/${test_dir}/.ut-defects.json \
    --class ${classname} --fixed-in-sha "$(git rev-parse --short HEAD)"
```

### 7. 覆盖率信号（分级覆盖率统计）

产出**三信号**：方法名差集（结构性）+ lcov 数据采集 + 分级覆盖率快照（函数级+行级）。前两项为轻量检查，第三项调用 `scripts/coverage_by_level.py` 产出按 high/mid/low 的真实覆盖率数字，作为 `verified` 的满足条件之一（覆盖率快照必须产出，门禁达标判定在 `self_checker`）。

#### 7a. lcov 数据采集

运行测试后立即采集覆盖率（`self_checker` 第 1b 步依赖此产物）：

```bash
test_dir=test_dir  # 内存变量
build_dir=${PROJECT_PATH}/build-${test_dir}
mkdir -p ${build_dir}/coverage
# capture -> 只保留 src -> 剔除测试自身
lcov -d ${build_dir} -c -o ${build_dir}/coverage/total.info
lcov --extract ${build_dir}/coverage/total.info '*/src/*' -o ${build_dir}/coverage/filtered.info
lcov --remove  ${build_dir}/coverage/filtered.info '*/${test_dir}/*' '*/tests/*' -o ${build_dir}/coverage/filtered.info
```

> **注意**：lcov 不可用时跳过 7c，仅做 7b 方法名差集，并在 `class_status` 记 `coverage_signal="missing"`。逐类闭环阶段 filtered.info 反映「当前类 + 已跑类」的累积覆盖，全量分级汇总以批次收尾或 `run-ut.sh` 全量跑为准。

#### 7b. 方法名差集（结构性）

从 gtest XML 输出提取已跑的 TEST_F 名，与 inventory 待测方法对比：

```python
tested = {n.lower() for n in parse_test_names_from_xml(xml_output)}  # PascalCase → 小写归一化
planned = {m["name"].lower() for m in inventory["methods"]
           if m["testable"] and m.get("class_qn") == class_qn}
coverage_gap = planned - tested
```

若 `coverage_gap` 非空 → 触发 `incremental_updater`。

#### 7c. 分级覆盖率统计（调用脚本，满足条件）

调用 skill 内置脚本，产出当前类的分级覆盖率快照（per-class，逐类闭环口径）：

```bash
python3 ${SKILL_DIR}/scripts/coverage_by_level.py \
    -i ${PROJECT_PATH}/${test_dir}/.ut-inventory.json \
    -c ${build_dir}/coverage/filtered.info \
    --class ${classname} --json -o ${build_dir}/coverage/${classname}_by_level.json
```

脚本输出（JSON）：

```json
{
  "class": "IconButton",
  "by_level": {
    "high": {"methods": 1, "function_coverage": 100.0, "line_coverage": 61.9,
             "gate": {"line": 90, "branch": 80, "function": 100}, "pass": false},
    "mid":  {"methods": 4, "function_coverage": 100.0, "line_coverage": 74.7, "pass": true},
    "low":  {"methods": 12, "function_coverage": 100.0, "line_coverage": 97.3, "pass": true}
  },
  "total": {"methods": 17, "function_coverage": 100.0, "line_coverage": 80.9, "pass": false},
  "uncovered_functions": []
}
```

- `by_level.<lv>.pass` = 函数覆盖率达 `gate.function` 且行覆盖率达 `gate.line`（阈值取自 inventory 的 `gate_thresholds`）
- `uncovered_functions` = FNDA:0 的方法名列表，供 `incremental_updater` 精准补全
- 脚本依赖 `c++filt`（binutils 自带）；解析 FN/FNDA/DA + demangle 关联 inventory 分级，产出函数级+行级覆盖率

> **注意**：此处只产出覆盖率快照，**不做门禁达标判定**（不因 `pass:false` 阻塞 `verified`）。门禁达标在 `self_checker` 第 1b 步执行——`self_checker` 直接复用此 JSON，避免重复解析 lcov。

## 后续流程

| build_result | run_result | coverage_gap | coverage_snapshot | 下一阶段 |
|-------------|-----------|-------------|-------------------|--------|
| pass | pass | 空 | 已产出 | `self_checker`（复用快照做门禁判定） |
| pass | pass | 非空 | 已产出 | `self_checker`（自检后若通过 → `incremental_updater`） |
| pass | pass | - | missing（lcov 不可用） | `self_checker`（降级为仅方法名差集） |
| fail | - | - | - | `failure_repairer` |
| pass | fail | - | - | `failure_repairer` |

若 `failure_reason` 含 `source_defect` → 标记该类 `status=failed`，跳过，继续下一类。

## 关键约束

- 不修改项目源码：只修测试代码和测试 CMakeLists；疑似源码缺陷只标红
- 编译失败时不报完成
- 编译通过必须接着运行，不跳过运行验证
- 不超过重试预算：per-error 3 次，总计 10 loops
- 不忽略 gtest XML 输出：方法名差集从 XML 提取
- 不跳过 lcov 采集：运行通过后必须采集 filtered.info（lcov 可用时），供 7c 与 self_checker 复用
- 不自行判定门禁达标：7c 只产出覆盖率快照，`pass:false` 不阻塞 `verified`；达标判定在 `self_checker`
- 不自行宣布"源码缺陷"而不读源码：必须用 `get_code_snippet` 读源码确认
