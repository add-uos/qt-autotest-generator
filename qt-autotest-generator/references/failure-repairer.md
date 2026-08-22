# 失败修复

> 前置条件：目标类 `build_result=fail` 或 `run_result=fail`（`class_status[classname].status=failed`（内存变量））；或源码变更后方法删除导致测试引用失效。

> 通过 mcp_provider 调用知识图谱工具（详见 references/mcp-providers.md）

## 概述

修复编译/运行失败的测试，在独立重试预算内尝试修复。**先按测试代码问题修**；修不好则判定根因，疑似源码缺陷的**标红交还用户，不修源码**。支持用户显式"修复"和自动检测失败两种触发方式。

当 failure-repairer 完成修复并成功后回到编译验证时，递增 `iteration_count[classname]`（内存变量）（因为这将开始新一轮闭环）。若 `iteration_count[classname]` 已 >= 3（Iron Law #10），不再尝试修复，直接保持 `failed` + `max_iterations_exceeded`。

## 工作步骤

### 1. 读失败上下文

从内存变量读取：
- `failure_reason`：失败类型
- `build_log_excerpt`：最小错误日志

若 `failure_reason` 已含 `source_defect` → 直接标红，不再尝试修复。

### 2. 读源码确认根因

用图谱读失败方法的源码：

```python
snippet = codebase_memory_mcp.get_code_snippet(
    qualified_name=method.qualified_name
)
```

判断失败是测试代码问题还是源码问题：

| 失败类型 | 测试代码问题特征 | 源码缺陷特征 |
|---------|---------------|------------|
| 编译失败 | stub 签名错、缺 include、CMake 缺依赖 | 源码本身缺 include、缺 Q_OBJECT、空实现 |
| 运行崩溃 | stub 不全导致真实调用 | stub 已补全仍崩溃，源码有空指针/越界 |
| ASSERT 恒失败 | 测试逻辑错（期望值写错） | 测试逻辑正确但源码行为与文档/常识矛盾 |

### 3. 按测试代码问题修复（独立重试预算）

per-error 3 次重试，总计 max 10 loops（与编译验证的预算独立）：

| 错误模式 | 修复策略 |
|---------|--------|
| `undefined reference to` | MCP `trace_path` 重新追踪传递依赖，补 CMake `target_link_libraries` |
| `No such file or directory` | 补 CMake `target_include_directories` |
| `stub.set_lamda` 签名不匹配 | MCP `get_code_snippet` 重读签名，修正 stub |
| `expected primary-expression` | `static_cast` 修正重载 |
| `undefined reference to stub_ext::freeWrapper` | 确认 `stub-shadow.cpp` 已编入 |
| Segfault | 补 stub（可能 trace_path 漏了依赖），重试 |
| ASSERT 失败 | 检查测试期望值，修正测试逻辑 |

### 4. 重试耗尽 → 根因分类

重试预算耗尽仍失败，做最终根因判定：

```
重试耗尽
    ├─ 编译失败：
    │   1. 检查错误是否涉及 stub 相关符号（stub 签名不匹配/未 stub 的调用）
    │      → 是 → failure_reason = "stub_incomplete"，走修复（允许额外 3 次重试补 stub）
    │   2. 检查错误是否涉及项目内非待测类代码（源码本身缺 include、缺 Q_OBJECT、空实现）
    │      → 是 → failure_reason = "source_defect_compile"
    │      → 标红：源码编译缺陷
    │   3. 无法确定 → failure_reason = "needs_manual"
    │      → 标红：需人工排查
    │
    ├─ stub 已补全，仍运行时崩溃
    │   → failure_reason = "source_defect_runtime"
    │   → 标红：源码运行时缺陷
    │
    ├─ 测试逻辑正确，ASSERT 恒失败
    │   → failure_reason = "source_defect_logic"
    │   → 标红：源码逻辑缺陷
    │
    └─ 无法判定
        → failure_reason = "needs_manual"
        → 标红：需人工排查
```

> **注意**：标红前必须用 `get_code_snippet` 读源码确认。尝试最小化复现：只构造对象不调方法，看是否崩溃。

### 5. 方法删除的清理

> **兜底防线**：正常流程中，reconcile 对账后由 `references/stale-test-cleanup.md` **主动清理**已删方法的测试用例（不等编译报错）。此处仅当 reconcile 未执行（如手动修改了测试文件未跑对账）且编译因引用已删方法失败时才触发。

若失败原因是源码删除了方法（对账检出 `removed_methods`）：
- 读测试文件，找到引用已删方法的 `TEST_F` 与 `TEST_P` 用例（搜索正则 `/TEST_[FP]\(/`）
- 注释或删除这些用例（加注释 `// Removed: method deleted from source`）；`TEST_P` 清理时连带移除对应的 `INSTANTIATE_TEST_SUITE_P`，避免悬空参数化定义
- 不视为源码缺陷（正常的代码演进）
- 逻辑与 `stale-test-cleanup.md` 一致；此为**兜底**，stale-test-cleanup 为**主动**

### 6. 记录修复结果

修复成功，记录到内存变量 `class_status[classname]`：

```json
{
  "status": "test_written",
  "build_result": "not_run",
  "run_result": "not_run",
  "failure_reason": null,
  "repair_attempts": 3
}
```

修复失败（标红），记录到内存变量：

```json
{
  "status": "failed",
  "failure_reason": "source_defect_runtime",
  "defect_evidence": "segfault at line 42, stub fully applied, source has no null check",
  "defect_suggestion": "检查 processData 对空输入的处理，疑似未做空指针检查",
  "repair_attempts": 10
}
```

**标红即落盘**（Mode 5）：除写内存变量外，立即调用 `export-defects.py upsert` 把缺陷持久化到 `{test_dir}/.ut-defects.json`（不入 git，本地存储），颗粒度精确到**用例级**：

```bash
python3 ${SKILL_DIR}/scripts/export-defects.py upsert \
    --defects ${PROJECT_PATH}/${test_dir}/.ut-defects.json \
    --defect-id "${method_qn}#${TestFixture}.${TestCaseName}" \
    --method-qn "${method_qn}" --method-name "${method_name}" \
    --class-qn "${class_qn}" --class-name "${class_name}" --module "${module}" \
    --file-path "${src_file}" --file-line ${src_line} \
    --test-fixture "${TestFixture}" --test-case-name "${TestCaseName}" \
    --test-file "${test_file}" \
    --type "${failure_reason}" --type-category "${type_category}" \
    --detected-at-stage "${stage}" \
    --evidence "${defect_evidence}" --suggestion "${defect_suggestion}" \
    --root-cause-snippet "${snippet}" \
    --method-level "${method_level}" --batch ${batch_no} \
    --project "${project_name}" --base-sha "${base_sha}" \
    --repair-attempts ${repair_attempts} --iteration-count ${iteration_count}
```

> `defect_id` 主键 = `{method_qn}#{TestFixture}.{TestCaseName}`，同一用例跨会话去重。构造即崩无具体用例时用 `__class_init__` 兜底。`type_category` 映射：compile/runtime/logic/manual。`stage` 取 detected_at_stage（标红阶段一般为 manual，编译期提前捕获为 compile）。详见 `references/defect-schema.md` 与 `references/defect-exporter.md`。

### 7. 后续流程

- **修复成功**（status=test_written）：回到编译验证阶段重新验证
- **修复失败（标红）**（status=failed + failure_reason 含 source_defect/needs_manual）：跳过该类，继续下一类；缺陷已写入 `{test_dir}/.ut-defects.json`，由 **Mode 5**（`references/defect-exporter.md`）在收尾或按需时导出为 `defects-summary.md` 标红清单

## 关键约束

- 不修改项目源码：只修测试代码和测试 CMakeLists；疑似源码缺陷只标红
- 不在重试预算耗尽前标红：先尽力修测试侧问题
- 不跳过根因判定：标红前必须用 `get_code_snippet` 读源码确认
- 不把测试代码问题误判为源码缺陷：先排除 stub/include/CMake 问题
- 不修改已通过的用例：只修失败的 `TEST_F` / `TEST_P` 用例，不动已通过用例
- 不超过重试预算：per-error 3 次，总计 10 loops
- 标红后即停，交还用户
