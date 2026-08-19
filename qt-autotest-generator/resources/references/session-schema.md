# Session JSON 结构

> 跨 phase 唯一的状态传递媒介，存储在 `{test_dir}/.ut-session.json`。

## 完整结构

```json
{
  "project_path": "/abs/path/to/project",
  "project_name_in_graph": "home-user-project-name",
  "test_dir": "autotests",
  "mcp_provider": "remote-codebase-memory-mcp",
  "mcp_provider_type": "remote",
  "repo_url": "https://github.com/foo/bar.git",
  "branch": "dev",
  "baseline_commit": "abc1234",
  "baseline_date": "2025-07-29",
  "baseline_title": "feat: add new feature",
  "pull_method": "git_clone",
  "build_env": "verified",
  "qt_version": 5,
  "coverage_threshold": 90,
  "classes": [
    {
      "name": "MyClass",
      "qualified_name": "project.src.MyClass",
      "file_path": "src/lib/ui/myclass.h",
      "status": "done",
      "methods_total": 15,
      "methods_tested": 15,
      "function_coverage": 86.7,
      "test_file": "{test_dir}/ui/test_myclass.cpp",
      "build_result": "pass",
      "run_result": "pass",
      "failure_reason": null,
      "skip_reason": null,
      "is_gui": false,
      "iteration_count": 1,
      "test_plan": [
        {"name": "methodA", "access": "public", "complexity": 12, "planned_cases": 3}
      ],
      "stub_list": [],
      "source_dirs": ["src"],
      "self_check": {
        "coverage": "pass",
        "naming": "pass",
        "spdx": "pass",
        "stub": "pass",
        "assertion_strength": "pass",
        "env_isolation": "pass"
      }
    }
  ],
  "stale_classes": [],
  "last_phase": "report_generation",
  "overall_status": "complete",
  "committed_classes": ["MyClass", "FooBar", "Baz"],
  "last_batch_commit": "9f3a2c1",
  "commit_history": [
    {"batch": 1, "commit_sha": "9f3a2c1", "classes": ["MyClass", "FooBar"], "committed_at": "2026-08-04T10:30:00+08:00"}
  ],
  "report_path": "{test_dir}/.reports/report.html"
}
```

## 字段说明

### 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `project_path` | string | 项目绝对路径 |
| `project_name_in_graph` | string | 图谱中的项目标识（由 environment_check 写入） |
| `test_dir` | string | 测试目录名（`"autotests"` 或 `"tests"`），由 environment_check 探测确定 |
| `mcp_provider` | string | 解析到的 MCP 提供方（`remote-codebase-memory-mcp` 或 `codebase-memory-mcp`） |
| `mcp_provider_type` | string | `"remote"` 或 `"local"` |
| `repo_url` | string | 仓库地址（项目准备阶段写入） |
| `branch` | string | 当前分支名 |
| `baseline_commit` | string | 基线 commit short-sha |
| `baseline_date` | string | 基线日期 |
| `baseline_title` | string | 基线 commit 标题 |
| `pull_method` | string | `"git_clone"` 或 `"git_worktree_fallback"` |
| `build_env` | string | `"verified"` 或 `"failed"` |
| `qt_version` | int | Qt 版本（5 或 6） |
| `coverage_threshold` | int | 函数覆盖率门禁阈值，默认 90 |
| `classes` | array | 目标类列表 |
| `stale_classes` | array | 分支切换后不存在的类名列表 |
| `last_phase` | string | 最后执行的 phase |
| `overall_status` | string | `"incomplete"` / `"partial"` / `"complete"` |
| `committed_classes` | array | 已提交到 git 的类名列表 |
| `last_batch_commit` | string | 最近一次批次提交的 commit sha |
| `commit_history` | array | 各批次提交记录 |
| `report_path` | string | 报告文件路径 |

### class 对象字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 类名 |
| `qualified_name` | string | 图谱中的全限定名 |
| `file_path` | string | 源文件路径（相对项目根） |
| `status` | string | `"pending"` / `"in_progress"` / `"done"` / `"failed"` / `"skipped"` / `"stale"` |
| `methods_total` | int | 方法总数 |
| `methods_tested` | int | 已测方法数 |
| `function_coverage` | float | lcov 函数覆盖率百分比 |
| `test_file` | string | 测试文件路径 |
| `build_result` | string | `"pass"` / `"fail"` / `"not_run"` |
| `run_result` | string | `"pass"` / `"fail"` / `"not_run"` |
| `failure_reason` | string | `null` / `compile_error` / `runtime_crash` / `stub_incomplete` / `source_defect_compile` / `source_defect_runtime` / `source_defect_logic` / `needs_manual` / `max_iterations_exceeded` |
| `skip_reason` | string | 跳过原因（如有） |
| `is_gui` | bool | 是否 GUI 类 |
| `iteration_count` | int | 逐类闭环迭代轮数（1-3） |
| `test_plan` | array | 方法测试规划 |
| `stub_list` | array | stub 清单 |
| `source_dirs` | array | 源码目录列表 |
| `self_check` | object | 自检结果 |

### commit_history 条目

| 字段 | 类型 | 说明 |
|------|------|------|
| `batch` | int | 批次序号 |
| `commit_sha` | string | commit sha（全失败批次为 null） |
| `classes` | array | 本批次提交的类名列表（全失败为空） |
| `note` | string | 可选备注（如 `"all_failed_or_skipped"`） |
| `committed_at` | string | ISO 8601 时间戳 |
