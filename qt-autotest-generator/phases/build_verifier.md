# 编译验证

> 前置条件：`test_writer` 已完成目标类（session 中 `status=test_written`），`autotests/<module>/test_<classname>.cpp` 存在。

> 通过 session.mcp_provider 调用知识图谱工具（详见 resources/references/mcp-providers.md）

## 概述

强制编译并运行目标类的测试，按错误分类表修复，在重试预算内达成编译+运行通过。产出**双信号**（编译/运行结果 + 覆盖率信号）。**不修源码**，疑似源码缺陷标红交还用户。

## 工作步骤

### 1. 编译测试

```bash
cd ${PROJECT_PATH}/build-autotests
cmake .. -DBUILD_TESTS=ON 2>&1
cmake --build . -j$(nproc) --target test_<classname> 2>&1
```

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
| `undefined reference to stub_ext::freeWrapper` | 确认 `resources/stub/stub-shadow.cpp` 已编入 test target |
| `vtable for XXX` / `undefined type` | 检查 Q_OBJECT 宏、MOC 处理 |

> **注意**：只修测试代码和测试 CMakeLists，不修项目源码。每次修复后重新编译，确认该错误消除。同一错误 3 次修不好 → 标记为疑似源码缺陷，停止该错误。

### 3. 编译通过 → 运行测试

```bash
cd ${PROJECT_PATH}/build-autotests
timeout 120 ./autotests/<module>/test_<classname> --gtest_output=xml:${PROJECT_PATH}/autotests/.results/test_<classname>.xml 2>&1
```

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

### 6. 产出双信号

编译+运行结束后，向 session 写入：

```json
{
  "build_result": "pass",      // pass / fail
  "run_result": "pass",        // pass / fail / not_run
  "failure_reason": null,      // null / compile_error / runtime_crash / source_defect_*
  "build_log_excerpt": "...",  // 最小错误日志片段（失败时）
  "status": "verified"         // verified / failed
}
```

### 7. 覆盖率信号（轻量）

运行后从 gtest XML 输出提取已跑的 TEST_F 名，与 test_plan 对比：

```python
tested = parse_test_names_from_xml(xml_output)
planned = {m.name for m in test_plan}
coverage_gap = planned - tested
```

若 `coverage_gap` 非空 → 信号 B 触发 `incremental_updater`。

> **注意**：此处只做方法名差集（结构性检查）。lcov 函数覆盖率百分比的完整门禁（与 `session.coverage_threshold` 比对）在 `self_checker` 中执行——此处不解析 lcov 数据，避免与 self_checker 重复。

## 后续流程

| build_result | run_result | coverage_gap | 下一阶段 |
|-------------|-----------|-------------|--------|
| pass | pass | 空 | `self_checker` |
| pass | pass | 非空 | `self_checker`（自检后若通过 → `incremental_updater`） |
| fail | - | - | `failure_repairer` |
| pass | fail | - | `failure_repairer` |

若 `failure_reason` 含 `source_defect` → 标记该类 `status=failed`，跳过，继续下一类。

## 关键约束

- 不修改项目源码：只修测试代码和测试 CMakeLists；疑似源码缺陷只标红
- 编译失败时不报完成
- 编译通过必须接着运行，不跳过运行验证
- 不超过重试预算：per-error 3 次，总计 10 loops
- 不忽略 gtest XML 输出：覆盖率信号从 XML 提取
- 不自行宣布"源码缺陷"而不读源码：必须用 `get_code_snippet` 读源码确认
