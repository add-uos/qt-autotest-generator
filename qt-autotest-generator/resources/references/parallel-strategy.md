# 并行分片策略

> 本文档描述 qt-autotest-generator 技能在并行处理多个类时的 session 分片机制。

## 1. 何时创建分片

当目标类数量 >= 5 时，为提高效率，可并行处理多个类的闭环链（分析→追踪→生成→验证→自检）。并行开始时，为主 session 中的每个待处理类创建**分片文件**：

```
{test_dir}/.ut-session.<classname>.json
```

## 2. 分片文件格式

分片文件是主 session 的**子集**，仅包含当前类所需的数据：

```json
{
  "project_path": "/path/to/project",
  "project_name_in_graph": "project-name",
  "test_dir": "autotests",
  "mcp_provider": "remote-codebase-memory-mcp",
  "mcp_provider_type": "remote",
  "branch": "main",
  "baseline_commit": "abc1234",
  "qt_version": 5,
  "coverage_threshold": 90,
  "current_class": {
    "name": "MyClass",
    "qualified_name": "project.src.MyClass",
    "file_path": "src/lib/ui/myclass.h",
    "status": "pending",
    "methods_total": 15,
    "methods_tested": 0,
    "test_file": null,
    "build_result": "not_run",
    "run_result": "not_run",
    "failure_reason": null,
    "is_gui": false,
    "iteration_count": 1
  }
}
```

## 3. 写入规则

**并行类只写分片，不碰主 session**。这避免并发写入冲突。

- 每个并行 worker 在处理类时，只读写自己的分片文件
- 主 session 在并行期间不被修改
- 分片文件中的 `current_class` 随闭环流程实时更新

## 4. 合并时机

所有并行类完成后，统一合并分片到主 session：

- 合并发生在所有并行 worker 完成（无论成功/失败/skipped）
- **收尾同步**：合并后统一检查覆盖率缺口；本批次无覆盖率缺口或缺口已交由增量补全处理后，按核心原则 11 执行批次提交闭环

## 5. 合并顺序

按 `session.classes` 的原始顺序合并，不依赖并行完成顺序：

```python
for shard_file in sorted(glob.glob(f"{test_dir}/.ut-session.*.json")):
    shard = json.load(open(shard_file))
    class_name = shard["current_class"]["name"]
    # 找到主 session 中对应的类，用分片数据覆盖
    for i, c in enumerate(session["classes"]):
        if c["name"] == class_name:
            session["classes"][i] = shard["current_class"]
            break
```

## 6. 清理

合并完成后删除所有分片文件：

```bash
rm -f {test_dir}/.ut-session.*.json
```

仅保留主 session 文件 `{test_dir}/.ut-session.json`。

## 7. 异常处理

若某个并行 worker 崩溃（进程被杀、超时等），其分片文件可能不完整：

- **检测**：合并时检查分片文件的 `current_class.status` 是否为终态（`done` / `failed` / `skipped`）
- **非终态处理**：将该类标记为 `failed` + `failure_reason: "parallel_worker_crashed"`
- **缺失分片**：若某个类在主 session 中为 `in_progress` 但无对应分片文件，同样标记 `failed` + `parallel_worker_crashed`
- **清理**：合并后仍删除所有分片文件（包括不完整的），避免残留

## 8. 并行度控制

- 默认并行度：`min(类数量, CPU核心数)`，但不超过 8
- 用户可通过环境变量 `UT_PARALLEL` 指定并行度
- 类数量 < 5 时不启用并行，顺序处理（无需分片）

## 9. 互斥约束

- 同一文件不可被多个 worker 同时写入（分片机制天然避免此问题）
- `CMakeLists.txt` 合并（add_subdirectory）延迟到所有并行类完成后统一处理
- `run-ut.sh` 和 `UnitTestUtils.cmake` 只在框架搭建阶段生成，不在并行阶段修改
