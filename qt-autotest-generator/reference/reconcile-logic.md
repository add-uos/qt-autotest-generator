# Reconcile（对账）逻辑

> 每次技能触发后必先执行 reconcile，判断源码是否变更，再决定执行哪个步骤。

## 流程

```
1. 读 {test_dir}/.ut-inventory.json（不存在 → 首次运行）
2. 首次运行（用户提供本地 project_path）：
   → 环境检查（reference/environment_check.md）→ 框架搭建（reference/framework_builder.md）
3. 有 inventory：
   a. 重新校验 MCP 提供方：确认该提供方仍可用（`list_projects()` 可调通）且目标项目仍索引 ready。
      若提供方已失联（如远端断开），重新走环境检查解析
   b. git rev-parse HEAD → 当前 commit
   c. 与 inventory.base_sha 比较
   d. 不同 → 源码已变更：
      - index_status(project) 若 "indexing" → 等待到 "ready"
      - 长时间不 ready（硬超时 300 秒 / 5 分钟）：
        · 本地提供方 → index_repository(mode="fast") 推一下
        · 远端提供方 → 等待远端 watcher 自动同步；超时则向用户提醒「远端索引未同步，请在远端手动刷新」
        · 超时后硬终止 + 输出「[FATAL] 远端索引 5 分钟未 ready，请手动刷新远端或切换本地提供方」
      - 索引 ready 后验证新鲜度：query_graph 查一个已知类，
        若返回的 file_path 对应的 git log 与当前 HEAD 一致则索引已同步；
        若不一致 → 同上按提供方类型处理（本地可 index_repository 刷新，远端只能等待/提醒）
      - 执行 inventory 对账（方法级 diff：图谱当前方法集 vs `methods[]`）
      - 新增方法 → 增量补全
      - 签名/体变更 → 测试生成（重新生成该类）→ 编译验证 → 自检
      - 方法删除 → 失败修复（清理引用已删方法的测试）
      - 更新 inventory.base_sha
   e. 相同 → 看当前状态决定下一步
   f. 分支切换检测：git branch --show-current 与内存变量记录的分支比较，
      若不同 → 强制刷新索引后重新对账：
        · 本地提供方 → index_repository(mode="fast")
        · 远端提供方 → 等待远端 watcher 同步；超时则向用户提醒
        · 重新对账前，检查每个类的 file_path 是否在当前分支仍存在
        · 不存在 → 标记该类 status="stale"，从 CMakeLists 移除对应 add_subdirectory（避免编译失败）
        · 保留 stale 类的测试文件（不删除，供切换回原分支后恢复），记录 stale_classes 列表
        · 新分支重新对账 → 新增/变更的类正常闭环
        · 更新内存变量 branch + inventory.base_sha + stale_classes
```

## 索引等待超时

- 硬超时：300 秒（5 分钟）
- 本地提供方超时处理：`index_repository(mode="fast")` 推一下
- 远端提供方超时处理：向用户提醒「远端索引未同步，请在远端手动刷新」
- 超时后硬终止 + 输出 `[FATAL] 远端索引 5 分钟未 ready，请手动刷新远端或切换本地提供方`

## 分支切换处理

1. 检查每个类的 `file_path` 是否在当前分支仍存在
2. 不存在 → 标记 `status="stale"`，从 CMakeLists 移除对应 `add_subdirectory`
3. 保留 stale 类的测试文件（不删除，供切回原分支后恢复）
4. 记录 `stale_classes` 列表（内存变量）
5. 新分支重新对账 → 新增/变更的类正常闭环
6. 更新内存变量 `branch` + `inventory.base_sha` + `stale_classes`
