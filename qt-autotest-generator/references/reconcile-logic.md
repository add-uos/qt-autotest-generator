# Reconcile（对账）逻辑

> 每次技能触发后必先执行 reconcile，判断源码是否变更，再决定执行哪个步骤。

GitNexus 单栈下对账有**两个轴**：

- **图谱轴**：inventory.base_sha（= 上次 fetch 时的图谱 lastCommit）vs 当前
  `list_repos` 返回的 lastCommit —— 检测平台是否重新索引过（图谱内容变了）
- **本地轴**：本地 HEAD vs 图谱 lastCommit（check_drift）—— 检测本地是否有
  图谱看不到的代码（未 push / 未提交）

## 流程

```
1. 读 {test_dir}/.ut-inventory.json（不存在 → 首次运行）
2. 首次运行（用户提供本地 project_path）：
   → 若 session 中 `mode_0_active == true`（由 Mode 0 设置）→ **跳过环境检查的提供方解析**，
     直接确认图谱可用（Mode 0 已确认索引 + 量化漂移）
   → 否则 → 环境检查（references/environment-check.md）→ 框架搭建（references/framework-builder.md）
3. 有 inventory：
   a. 重新校验 GitNexus：`list_repos` 可调通且目标项目仍在册。
      失联/不在册 → **硬终止** + 统一指引（`mcp-providers.md` §3）
   b. 读当前 `lastCommit`（图谱基线）与 `branch`
   c. 与 inventory.base_sha 比较（图谱轴）：
      不同 → 平台已重新索引（源码变更已同步进图谱）：
      - 执行 inventory 对账（更新 inventory 本身 + 产出 diff 报告）：
          python3 scripts/mcp-scan.py fetch \
            --project <project_name> --file-pattern "src/**" \
            --repo-root <repo_root> \
            --output {test_dir}/.ut-inventory.json \
            --incremental --existing {test_dir}/.ut-inventory.json --summary
        · 省略 --base-sha：脚本默认取 list_repos.lastCommit（图谱基线），
          不传本地 HEAD（本地领先图谱时传 HEAD 会导致行号错位）
        · 全量重建 methods（图谱最新为准）+ 回写旧 inventory 的人工标记
          （source=manual 的 level、review_status=confirmed、usecase_count）
        · 方法删除直接清理（不留墓碑，不做改名软匹配）
        · 产出 {test_dir}/.ut-inventory-diff.md：新增/删除/签名变更/level 变化
        · base_sha 由脚本写入新 inventory（= 当前 lastCommit）
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
   d. 图谱轴相同（lastCommit 未变）→ 图谱内容与上次一致：
      - 本地轴漂移检查（check_drift）：本地 HEAD vs lastCommit
        · 本地领先 → `git log --name-only lastCommit..HEAD` 列受影响文件：
          涉及待测模块 → **硬终止**等待平台同步（GitNexus 无本地索引，不可补索引；
          图谱看不到新方法，无法生成正确测试）；
          仅无关文件 → **带警告继续**（方法体以本地切片为准）
        · 本地落后 → 提示拉取最新代码后重试（切片行号以本地为准，落后会错位）
        · 工作区 dirty → 不一票否决：方法体一律本地切片，dirty 改动即刻反映；
          但新方法图谱不可见，同样受「本地领先」规则约束
      - 看当前状态决定下一步
   e. 分支切换检测：`git branch --show-current` 与内存变量记录的分支比较，
      同时与 `list_repos` 返回的 `branch` 比对：
      · 本地切换了分支但图谱仍索引原分支 → 图谱关系网与本地代码不同源，
        **硬终止**（等待平台切换索引分支；不静默混用两个分支的数据）
      · 图谱与本地分支一致但与上次会话记录不同 → 正常走对账（图谱轴驱动），
        重新对账前，检查每个类的 file_path 是否在当前分支仍存在：
        - 不存在 → 标记该类 status="stale"，从 CMakeLists 移除对应 add_subdirectory（避免编译失败）
        - 保留 stale 类的测试文件（不删除，供切换回原分支后恢复），记录 stale_classes 列表
        - 新分支重新对账 → 新增/变更的类正常闭环
        - 更新内存变量 branch + inventory.base_sha + stale_classes
```

## 图谱基线获取（唯一精确途径）

### get_graph_last_commit(project)

```python
repo = next(r for r in list_repos_pages() if r["name"] == project)
graph_last_commit = repo["lastCommit"]   # 平台索引的精确 commit SHA
```

- `list_repos` 分页遍历（limit ≤ 200）；项目不在册 → 硬终止（未索引）
- **不做任何间接推断**——GitNexus 直接给出 lastCommit，无采样、无 Branch 节点查询

> 🚫 **已废弃：采样推断**。旧方案用「search_graph 采样 file_path → `git log -1` →
> `max(commits)`」近似 graph_head，双重缺陷（SHA 无字典序时间语义、采样可漏判过时），
> 已随 codebase-memory-mcp 退役删除。

## 本地漂移辅助函数

### check_drift（mcp-scan.py 内建）

```python
drift = check_drift()   # 本地 HEAD vs list_repos.lastCommit；fetch 前自动执行并警告
```

### git_unpushed_commits(project_path)

```bash
git -C <project_path> log @{upstream}..HEAD --oneline
```

- 有输出 → 返回 commit 列表（非空）→ 本地领先图谱，进入漂移决策（受影响文件判定）
- `@{upstream}` 不存在 → `has_upstream = false`；GitNexus 从远端 git 同步，
  无 upstream 的本地提交**永远进不了图谱** → 涉及待测模块即硬终止

### git_worktree_dirty(project_path)

```bash
git -C <project_path> status --porcelain
```

- 非空（含 `??` untracked）→ dirty。
  GitNexus 双源架构下 dirty **不再一票否决**：方法体/复杂度/宏扫描以本地切片为准，
  未提交改动即刻反映；但**新方法**图谱不可见（CALLS 边、qn 都缺），
  待测模块有新增代码时仍受「本地领先」硬终止规则约束。

## 分支切换处理

1. 图谱 `branch` 与本地分支不一致 → 硬终止（平台索引分支切换请求）
2. 一致时，检查每个类的 `file_path` 是否在当前分支仍存在
3. 不存在 → 标记 `status="stale"`，从 CMakeLists 移除对应 `add_subdirectory`
4. 保留 stale 类的测试文件（不删除，供切回原分支后恢复）
5. 记录 `stale_classes` 列表（内存变量）
6. 新分支重新对账 → 新增/变更的类正常闭环
7. 更新内存变量 `branch` + `inventory.base_sha` + `stale_classes`
