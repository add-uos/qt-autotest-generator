# 过时测试主动清理

> 前置条件：reconcile 对账已完成，diff 报告产出 `removed` 方法列表。

> 不调用 GitNexus 图谱（方法已从图谱消失，无需查图谱）。

## 概述

源码演进删除方法后，引用这些方法的单元测试**必然编译失败**（`no member named` / `undefined reference`）。与其等编译炸了再修，不如在 **reconcile 对账阶段主动清理**，避免无效的编译尝试和时间浪费。

本步骤由 reconcile diff 报告的 `removed` 列表驱动，在对账完成后、进入 Mode 2 主流程之前执行。

## 触发时机

- `mcp-scan.py fetch --incremental` 产出 `-diff.md` 报告，其中 `removed` 列表非空
- 分支切换后，检查类的 `file_path` 不在当前分支存在（`stale_classes`）

## 工作步骤

### 1. 收集待清理方法

从 diff 报告读取 `removed` 方法列表：

```python
# diff["removed"] 由 mcp-scan.py compute_diff() 产出
# 每项含：qualified_name, name, class_qn, level, file_path, ...
removed_methods = diff["removed"]

# 按 class_qn 分组，便于批量处理同类的测试文件
removed_by_class = {}
for m in removed_methods:
    cls = m.get("class_qn") or "(free_functions)"
    removed_by_class.setdefault(cls, []).append(m)
```

### 2. 定位受影响的测试文件

对每个受影响的类，找到对应的测试文件：

```python
for class_qn, methods in removed_by_class.items():
    class_short = class_qn.split(".")[-1] if "." in class_qn else class_qn

    # 在 test_dir 下搜索对应的测试文件
    test_file = find_test_file(test_dir, class_short)
    # 匹配规则：test_{class_short}.cpp 或 {class_short}test.cpp
    # 如果找不到测试文件，跳过（该类可能本身就没有测试）

    if not test_file:
        continue
```

### 3. 逐方法清理测试用例

对每个已删除方法，在测试文件中定位并清理引用它的用例：

```python
test_content = read(test_file)
removed_method_names = {m["name"] for m in methods}

for method_name in removed_method_names:
    # 搜索引用该方法的 TEST_F / TEST_P 用例
    # 匹配规则：TEST_F(ClassNameTest, {MethodName}_*) 或
    #           TEST_P(ClassNameTest, {MethodName}_*)

    pattern = re.compile(
        rf'(TEST_[FP]\s*\(\s*{class_short}Test\s*,\s*{re.escape(method_name)}\w*\s*\))'
    )

    for match in pattern.finditer(test_content):
        case_name = extract_case_name(match)
        # 提取完整用例块（按大括号深度判定边界）
        block = extract_test_block(test_content, match.start())

        # 注释掉整个用例块
        commented = comment_out_block(block, reason=f"Removed: method '{method_name}' deleted from source")
        test_content = test_content.replace(block, commented)
```

### 4. 清理 TEST_P 的 INSTANTIATE_TEST_SUITE_P

若清理了 `TEST_P` 用例，需连带移除对应的参数化实例化定义，否则编译报 `undefined reference to suite`：

```python
# 扫描被注释掉的 TEST_P 用例名
instantiated_names = extract_instantiated_names(commented_blocks)

for name in instantiated_names:
    # 匹配 INSTANTIATE_TEST_SUITE_P(Prefix, ClassNameTest_SuiteName, ...)
    inst_pattern = re.compile(
        rf'INSTANTIATE_TEST_SUITE_P\s*\([^)]*{re.escape(name)}[^)]*\)[^;]*;?\s*',
        re.DOTALL
    )
    test_content = inst_pattern.sub(
        lambda m: f"// Removed: parameterized suite for deleted method '{name}'\n",
        test_content
    )
```

### 5. 更新 .ut-inventory.json 的 usecase_count + 同步 test_* 字段

已删除方法从 `methods[]` 中已消失（全量重建），但同类**其他仍存在的方法**可能也需要更新 `usecase_count`（因为被注释的用例可能也测试了其他方法，或总用例数减少）：

```python
# 重新统计测试文件中的用例数（仅统计未注释的 TEST_F/TEST_P）
active_cases = count_active_test_cases(test_content)

# 按方法名分组统计 usecase_count
new_usecase_map = extract_usecase_count_by_method(test_content, class_short)

# 更新 inventory 中该类方法的 usecase_count，并同步 test_* 字段
for m in inventory["methods"]:
    if m.get("class_qn") == class_qn and m.get("testable"):
        method_name = m["name"].lower()
        m["usecase_count"] = new_usecase_map.get(method_name, 0)
        # 该方法在本文件已无用例 → 本文件不再覆盖它，同步维护覆盖字段
        if new_usecase_map.get(method_name, 0) == 0:
            sync_remove_file_coverage(m, basename(test_file), removed_cases)
            # test_files 移除本文件，test_cover_count = len(剩余)
            # test_cases 移除本文件中被注释的用例名；覆盖完全消失时清除 test_source
```

### 6. 清理分支切换导致的 stale 测试

分支切换后，若类的源文件不存在（`stale_classes`），处理方式不同：

```python
for cls in stale_classes:
    test_file = find_test_file(test_dir, cls.name)

    # 不注释/删除测试用例（切回原分支后可恢复）
    # 只从 CMakeLists 移除 add_subdirectory（避免编译整个 stale 目录）
    remove_add_subdirectory(cmakelists_path, cls.name)

    # 在测试文件头部添加 stale 标记（供人工参考）
    prepend_stale_marker(test_file, branch=current_branch, reason="class source file not found in current branch")
```

切回原分支时恢复：
```python
for cls in stale_classes:
    restore_add_subdirectory(cmakelists_path, cls.name)
    remove_stale_marker(test_file)
```

### 7. 输出清理摘要

```markdown
## 过时测试清理摘要

| 类 | 已删方法 | 清理用例数 | 测试文件 | 操作 |
|----|---------|-----------|---------|------|
| FileManager | deleteFile, removeTemp | 5 | test_filemanager.cpp | 注释 5 个用例 |
| PluginLoader | loadPlugin | 2 | test_pluginloader.cpp | 注释 2 个用例 + 1 个 INSTANTIATE |
| ViewHelper | *(stale: 分支切换)* | 0 | test_viewhelper.cpp | 标记 stale，CMake 移除 |
```

## 关键约束

- **不等编译报错**：reconcile diff 发现方法删除后立即清理，避免无效编译
- **不删除测试文件**：注释用例或标记 stale，保留文件供历史参考/分支切换恢复
- **不修改源码**：只修改测试文件和测试 CMakeLists
- **不视为源码缺陷**：方法删除是正常的代码演进
- **不留墓碑在 inventory**：`.ut-inventory.json` 中方法删除就是删除，墓碑信息在 `-diff.md` 报告
- **TEST_P 连带清理**：移除 `INSTANTIATE_TEST_SUITE_P` 避免编译错误
- **usecase_count 实时更新**：清理后重新统计受影响类的用例数
- **test_* 字段同步**：清理后 `test_files` 移除本文件、`test_cover_count` 重算、`test_cases` 移除被注释用例名——与 usecase_count 保持语义一致（否则 utq 双信号判定仍显示"已覆盖"）
- **stale 不删用例**：分支切换导致的 stale 只做 CMake 隔离 + 文件头标记，不注释用例

## 与 failure-repairer 的关系

`failure-repairer.md` §5 仍保留"方法删除的清理"逻辑，作为**兜底**：
- 正常流程：reconcile → stale-test-cleanup 主动清理 → 编译 → 不会遇到方法删除错误
- 异常流程：若因某些原因 reconcile 未跑（如手动改了测试文件没跑对账），编译失败后 failure-repairer 仍能兜底清理

两者逻辑一致，**stale-test-cleanup 是主动防线，failure-repairer 是兜底防线**。
