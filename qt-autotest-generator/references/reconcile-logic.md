# Reconcile（对账）逻辑

> 每次技能触发后必先执行 reconcile，判断源码是否变更，再决定执行哪个步骤。

## 流程

```
1. 读 {test_dir}/.ut-inventory.json（不存在 → 首次运行）
2. 首次运行（用户提供本地 project_path）：
   → 若 session 中 `mode_0_active == true`（由 Mode 0 设置）→ **跳过环境检查的提供方解析**，直接确认图谱可用（Mode 0 已锁定本地 + 同步索引）
   → 否则 → 环境检查（references/environment-check.md）→ 框架搭建（references/framework-builder.md）
3. 有 inventory：
   a. 重新校验 MCP 提供方：确认该提供方仍可用（`list_projects()` 可调通）且目标项目仍索引 ready。
      若提供方已失联（如远端断开），重新走环境检查解析
   b. git rev-parse HEAD → 当前 commit
   c. 与 inventory.base_sha 比较
   d. 不同 → 源码已变更：
      - **Freshness 检测（仅远端提供方，见 mcp-providers.md §2/§5）：**
        若 `mcp_provider_type == "remote"` 且 `mode_0_active == false`：
        · 有未推送 commit（`git log @{upstream}..HEAD --oneline` 非空，或无 upstream）
          → 远端图谱**必然**过时（结构性边界，等待无意义）→ **硬终止**并输出统一指引
          （push 后等待远端同步重试，或显式触发 Mode 0），**不回退本地**
        · 已全部推送 → 落入下方索引等待 / 新鲜度验证逻辑
        · `mode_0_active == true` → 本地提供方，跳过远端检测（Mode 0 已同步）
      - index_status(project) 若 "indexing" → 等待到 "ready"
      - 长时间不 ready（硬超时 300 秒 / 5 分钟）：
        · 本地提供方（仅 Mode 0 路径）→ index_repository(mode="fast") 推一下
        · 远端提供方 → 等待远端 watcher 自动同步；超时则**硬终止**：
          「[FATAL] 远端索引未同步。请手动刷新远端，或显式触发 Mode 0 使用本地图谱。」
          （不回退本地）
      - 索引 ready 后验证新鲜度：query_graph 查一个已知类，
        若返回的 file_path 对应的 git log 与当前 HEAD 一致则索引已同步；
        若不一致 → 同上按提供方类型处理（本地可 index_repository 刷新，远端只能等待/提醒）
      - 执行 inventory 对账（更新 inventory 本身 + 产出 diff 报告）：
          python3 scripts/fetch-mcp-data.py \
            --project <project_name> --file-pattern "src/**" \
            --output {test_dir}/.ut-inventory.json \
            --base-sha <HEAD> \
            --incremental --existing {test_dir}/.ut-inventory.json --summary
        · 全量重建 methods（图谱最新为准）+ 回写旧 inventory 的人工标记
          （source=manual 的 level、review_status=confirmed、usecase_count）
        · 方法删除直接清理（不留墓碑，不做改名软匹配）
        · 产出 {test_dir}/.ut-inventory-diff.md：新增/删除/签名变更/level 变化
        · base_sha 由脚本写入新 inventory（= --base-sha）
        · 原地覆盖自动备份 .ut-inventory.json.bak
        详见 references/incremental-inventory.md
      - 读 diff 报告驱动后续 Mode 2 动作（上层职责，非对账脚本自身）：
        · 新增方法 → 增量补全（references/incremental-updater.md）
        · 签名/体变更 → 测试生成（重新生成该类）→ 编译验证 → 自检
        · 方法删除 → **主动清理测试用例**（references/stale-test-cleanup.md）
          - 对账阶段立即执行，不等编译报错
          - 读 diff 报告的 `removed` 列表，逐方法定位引用它的 TEST_F/TEST_P
          - 注释或删除这些用例（加 `// Removed: method deleted from source`）
          - TEST_P 连带移除对应 INSTANTIATE_TEST_SUITE_P
          - 从 .ut-inventory.json 对应类的 methods 中更新 usecase_count
          - 不视为源码缺陷（正常的代码演进）
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
- 本地提供方（仅 Mode 0 路径）超时处理：`index_repository(mode="fast")` 推一下
- 远端提供方超时处理：**硬终止** + 统一指引（手动刷新远端，或显式触发 Mode 0；不回退本地）

## 本地提供方索引同步

> 本地索引同步仅在 Mode 0 路径执行，见 `references/dev-preflight.md` Step 3（项目查找、freshness 检查、首次/增量索引、等待 ready 全套）。Mode 1-5 远端唯一，不触碰本地 MCP。

## Freshness 检测辅助函数

### get_graph_head_sha(provider, project_name, project_path, fallback_sha)

获取图谱记录的 HEAD commit SHA（近似推断）。**仅 Mode 0 / 本地提供方使用**——
本地 MCP 的 `list_projects` 不返回 git 元数据，需间接推断；
远端提供方直接用其 `git.head_sha` 元数据，不用本函数。

> 实验注记：本地图谱有 `Branch` 节点原生携带 `head_sha`（见 local-first-graph.md §4.1），
> `MATCH (b:Branch) RETURN b.head_sha` 一条查询即得，比下面的采样推断更可靠，优先使用。

策略（Branch 节点缺失时的兼容手段）：
1. `search_graph(label="Class", limit=5)` 取若干类的 `file_path`
2. 对每个 `file_path` 执行 `git log -1 --format=%H -- <file_path>` 取最新 commit
3. 取所有 commit 中拓扑最新的一个作为 `graph_head` 近似值（**不可**按 SHA 字典序取 max）
4. 均不可用 → 返回 `fallback_sha`（视为 fresh，不回退）

> ⚠️ **近似推断的保守策略**：git log 取的是「文件最后一次被修改的 commit」，
> 可能偏旧（误判过时）。**宁可误判过时**（触发一次 fast 增量索引，秒级完成），
> 也不要漏判（用过时图谱生成测试）。因此：
> - **有未 push commit 时，无论 freshness 结果如何，都执行一次 `index_repository(mode="fast")`**
> - 无未 push commit 时，才依赖 freshness 判断

### git_unpushed_commits(project_path)

```bash
git -C <project_path> log @{upstream}..HEAD --oneline
```

- 有输出 → 返回 commit 列表（非空）
- `@{upstream}` 不存在（无远程追踪分支）→ git 返回错误，`has_upstream = false`
  此时远端图谱天然不可能同步本地 → 同样视为「图谱必然过时」→ 硬终止 + 指引 Mode 0

## 分支切换处理

1. 检查每个类的 `file_path` 是否在当前分支仍存在
2. 不存在 → 标记 `status="stale"`，从 CMakeLists 移除对应 `add_subdirectory`
3. 保留 stale 类的测试文件（不删除，供切回原分支后恢复）
4. 记录 `stale_classes` 列表（内存变量）
5. 新分支重新对账 → 新增/变更的类正常闭环
6. 更新内存变量 `branch` + `inventory.base_sha` + `stale_classes`
