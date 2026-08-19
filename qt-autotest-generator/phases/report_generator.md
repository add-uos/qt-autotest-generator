# 报告生成

> 前置条件：session 中所有类 `status` 为 `done` / `failed` / `skipped`（无 `pending` / `in_progress`），所有已完成批次均已提交完成（`session.last_phase == "code_committed"`）。

## 概述

全部类处理完成后，生成 HTML/CSV 测试报告。报告包含：覆盖率总览、逐类结果、疑似源码缺陷清单（标红交还用户）。**只读 session 和已有结果，不重新跑测试**。

## 工作步骤

### 1. 调用报告生成器

报告生成器入口是 `report_generator/main.py`，已内置 `__main__` CLI 块，可直接调用。它会自动从 `.results/test_*.xml`（gtest XML）和 `.reports/test_output.log`（ctest 输出）两个来源合并解析测试结果：

test_dir = session.test_dir  # "autotests" 或 "tests"

cd ${PROJECT_PATH}/${test_dir}
python3 -m report_generator.main \
  --build-dir ${PROJECT_PATH}/build-${test_dir} \
  --report-dir ${PROJECT_PATH}/${test_dir}/.reports \
  --project-root ${PROJECT_PATH} \
  --results-dir ${PROJECT_PATH}/${test_dir}/.results

若 `run-ut.sh` 已跑过（产出了 `test_output.log`），报告生成器优先用 ctest 合并输出；否则回退到逐类 gtest XML 解析。覆盖率数据从 `build-{test_dir}/coverage/filtered.info` 自动检测。

### 2. 补充 session 维度的报告数据

报告生成器产出的是 gtest 维度的数据。还需从 session 补充图谱维度的覆盖率数据：

- 每类 `methods_total` vs `methods_tested`
- 每类 `status`（done / failed / skipped）
- 每类 `failure_reason`

将这部分追加到报告或单独生成 `{test_dir}/.reports/session-summary.json`。

### 3. 生成疑似源码缺陷清单

从 session 中筛出 `failure_reason` 含 `source_defect` 或 `needs_manual` 的类：

```json
{
  "source_defects": [
    {
      "class": "MyClass",
      "method": "processData",
      "defect_type": "source_defect_runtime",
      "defect_label": "运行时崩溃",
      "evidence": "segfault at line 42 when input is empty",
      "suggestion": "检查 processData 对空输入的处理，疑似未做空指针检查"
    }
  ]
}
```

**标红规则**：
- `source_defect_compile` → "源码编译缺陷"
- `source_defect_runtime` → "源码运行时缺陷"
- `source_defect_logic` → "源码逻辑缺陷"
- `needs_manual` → "需人工排查"

#### 3.1 源码缺陷通知

若 `source_defects` 非空，在报告生成后输出通知：

```json
{
  "source_defect_count": 3,
  "notification": "发现 3 个疑似源码缺陷，已在报告中标红，请用户查看 report.html 第 3 节"
}
```

### 4. 流程复盘数据（可选）

从 session 聚合流程维度统计，追加到报告或单独生成 `{test_dir}/.reports/process-summary.json`：

```json
{
  "total_classes": 20,
  "passed": 15,
  "failed": 3,
  "skipped": 2,
  "failure_pattern": {
    "compile_error": 1,
    "source_defect_runtime": 2,
    "source_defect_logic": 1,
    "needs_manual": 1
  },
  "module_hotspots": [
    {"module": "src/lib/ui", "failure_rate": 0.4, "count": 5},
    {"module": "src/lib/core", "failure_rate": 0.0, "count": 8}
  ],
  "avg_repair_attempts": 2.3,
  "classes_needing_manual": ["MyClass", "DataLoader", "ConfigParser"]
}
```

### 5. 生成最终报告

报告结构（HTML）：

```
# Qt 单元测试报告

## 1. 总览
- 项目路径
- 基线 commit
- 测试类数 / 通过 / 失败 / 跳过
- 总覆盖率（已测方法 / 全量方法）

## 2. 逐类结果
| 类名 | 状态 | 覆盖率 | 编译 | 运行 | 失败原因 |
|------|------|--------|------|------|--------|

## 3. 疑似源码缺陷清单（标红）
⚠️ 以下问题疑似源码缺陷，需用户自行修复源码后重新运行：
| 类名 | 方法 | 缺陷类型 | 证据 | 建议 |
|------|------|---------|------|------|

## 4. 跳过类清单
| 类名 | 跳过原因 |
|------|--------|

## 5. 详细 gtest 输出
（链接到各 test_*.xml）
```

### 6. 更新 session

```json
{
  "last_phase": "report_generation",
  "overall_status": "complete",
  "report_path": "{test_dir}/.reports/report.html"
}
```

## 关键约束

- 不重新跑测试：只读已有结果和 session，不重新编译/运行
- 不修改测试代码或项目源码
- 不遗漏源码缺陷清单：所有 `source_defect_*` 和 `needs_manual` 必须标红列出
- 不自行修复源码缺陷：只标红交还用户
- 不跳过报告生成：这是固定收尾环节，不可选
- 不在报告里隐藏失败类：failed/skipped 类必须如实列出


## 产出

- `{test_dir}/.reports/report.html`：HTML 报告
- `{test_dir}/.reports/report.csv`：CSV 报告
- `{test_dir}/.reports/session-summary.json`：session 维度数据
- session 更新 `last_phase` + `overall_status=complete`
