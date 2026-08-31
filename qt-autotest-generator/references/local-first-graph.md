# 本地优先设计：代码是事实，图谱是缓存（Local-First Graph）

> 状态：**设计提案 + 实验实证**（2025-01，基于 codebase-memory-mcp 0.10.8 实测）
> 本文档替代现行 reconcile freshness fallback（mcp-providers.md §7）+ Mode 0（dev-preflight.md）的整套设计。
> 实验数据见 §6，全部结论有一线实测支撑。

---

## 1. 动机：现行设计的复杂度根源

现行设计隐含假设：**图谱是权威，代码要向图谱对账**。由此衍生出一整套
"freshness 探测 → 发现过时 → fallback → 状态机跟踪"机制
（`get_graph_head_sha` 间接推断、`session["freshness_stale"]`、`fallback_*`、`mode_0_active`、哨兵 `-1`），
并存在已确认的逻辑缺陷：

| # | 缺陷 | 根因 |
|---|------|------|
| 1 | `max(commits)` 按字典序取"最新 SHA" | Git SHA 无字典序语义 |
| 2 | 远端 freshness 用本地间接推断 | 方法答非所问（测的是"本地文件何时改过"）|
| 3 | `ensure_local_project_ready` 变量先用后定义、违反自家保守策略 | 状态散落在 session 变量 |
| 4 | 首次运行 freshness 标记无人消费 | fallback 嵌在 reconcile 3.d，首运不经过 |
| 5 | base_sha==HEAD 时绕过检测 | 检测与 inventory 路径耦合 |
| 6 | dirty 工作区完全没防住 | 所有比较都是 commit SHA 级 |

## 2. 核心原则：三个反转

1. **权威反转**：本地 git 工作区是唯一事实源；图谱是派生索引缓存（类比 build 产物）。
   缓存语义 = 用前廉价校验、未命中廉价重算（fast 增量，实测秒级）、永不与图谱"协商"。
2. **触发反转**：远端看不到未 push / 未提交代码是**结构性能力边界**，不是"过时"瞬态。
   路由决策在任何 MCP 查询之前、纯靠本地 git 事实一次性做出（前置分类器），没有运行时 fallback 状态机。
3. **状态反转**：失效状态不进 session 内存变量，用图谱**原生自记的水位**（见 §4），
   路径无关、幂等，任何模式入口走同一条规则。

**不变式**：`G = f(working_tree)`。graph 是工作区的函数，失效检查是本地廉价查询，重算是 fast 增量索引。

## 3. Step 0：本地状态分类器（零 MCP 调用）

```python
def classify(project_path) -> "remote_ok" | "local_only":
    if git_status_porcelain():        # dirty（含 untracked、不含 ignored）
        return "local_only"           # 远端 watcher 从远端仓库同步，永远看不到工作区
    if not has_upstream():
        return "local_only"
    if git_rev_list("@{upstream}..HEAD"):
        return "local_only"           # 未 push 内容远端必然没有
    return "remote_ok"
```

| 本地 git 状态 | 远端状态 | 路由 |
|---|---|---|
| dirty / 无 upstream / 有 unpushed | —— | **FATAL + 指引 Mode 0**（远端结构性看不到；本地图谱仅经 Mode 0 显式进入）|
| clean + pushed | ready 且 `git.head_sha == @{upstream}` | remote |
| clean + pushed | ready 但 head_sha 落后 | 有界等待 watcher → 超时 → FATAL + 指引 Mode 0 |
| clean + pushed | 不可用 / 未索引 | **FATAL + 指引 Mode 0**（不自动回退本地）|

用户显式"用本地"偏好 = 分类器的一个 override 变量（即 Mode 0）。

## 4. 图谱原生能力（实测确认，替代自建机制）

**关键发现：现行设计自建的三套机制，本地 MCP 0.10.8 已原生提供。**

### 4.1 commit 级水位：`Branch.head_sha`（替代 `get_graph_head_sha` 整套间接推断）

图谱原生记录 git 元数据，一条 Cypher 即得：

```
MATCH (b:Branch) RETURN b.branch, b.head_sha, b.base_sha
```

实测：图谱 `head_sha = 121cf38...` 与 `git rev-parse HEAD` **精确一致**。
现行设计前提"本地 MCP 拿不到图谱 SHA、需 search_graph→git log 采样推断（含 max() 字典序 bug）"**不成立**。

### 4.2 文件级水位：`detect_changes`（覆盖 dirty 工作区 + 白送 delta）

```
codebase-memory-mcp cli detect_changes --project <name>
→ changed_files: [src/utils.cpp, src/utils.h, ...]
→ seed_symbols: 5          # 变化符号
→ impacted_total: 6        # 受影响下游符号（含调用方与测试）
```

实测对**未提交的工作区改动**精准命中（commit SHA 不变也能检出）——这补上了
`Branch.head_sha` 比较覆盖不了的 dirty 场景。`impacted` 直接就是 delta 驱动开发的输入。

### 4.3 测试关联：`TESTS` 边 + `is_test` 属性（替代 stale 清理的 grep）

实测：删除 `Utils::isAtoE` 后，图中源码符号消失但
`TEST_F_UtilsTest_IsAtoE_ChecksHexChar` 可查——孤儿测试用例从图里直接定位，
stale-test-cleanup 无需 grep。

### 4.4 两级失效检查（组合即完整）

| 层级 | 手段 | 覆盖场景 |
|------|------|---------|
| commit 级 | `Branch.head_sha` vs `git rev-parse HEAD` | unpushed / 切分支 / rebase |
| 文件级 | `detect_changes` | dirty 工作区（最强"本地优先"场景）|

自建 `state_token` 水位文件、session 变量、哨兵 `-1` 全部不需要。

## 5. 新流程（本地优先最小闭环）

```
① classify()（本地 git 三问，秒回）
     └─ local_only → 锁定本地提供方（探测→必要时安装，一次）
② detect_changes（~1s）
     └─ 无源码变更 且 Branch.head_sha == HEAD → 直接用图，跳过索引
③ 有变更 → index_repository(mode="fast")   # 实测 ~10s（CLI 冷启动）
④ delta = detect_changes.impacted（或 git diff --name-only 交叉）
     └─ 新增/变更符号 → get_code_snippet 取函数体 + CALLS 边查调用方
     └─ 删除符号     → TESTS 边反查孤儿用例 → 清理
⑤ 生成/更新测试 → 编译验证 → 自检（现有 Mode 2 闭环不变）
```

与现行流程的关系：
- reconcile 的 base_sha 对账保留（inventory 语义），但其**图谱同步输入**与 ④ 同源，不再有两套对账；
- environment-check §1-§4（提供方解析/等待 ready）保留 remote_ok 分支；§0b/§4a 删除；
- mcp-providers §7 fallback 状态机删除，替换为 §3 路由表；
- dev-preflight.md（Mode 0）塌缩为分类器 + override 说明。

## 6. 实验记录（deepin-calculator，2025-01 实测）

项目：`/home/zhy/demo/deepin-calculator`（图谱 `home-zhy-demo-deepin-calculator`，
baseline 4781 nodes / 13959 edges，moderate 索引）。
实验改动：`src/utils.{h,cpp}` 新增 `experimentEcho` / 修改 `stringIsDigit`（+3 行）/ 删除 `isAtoE`（调用点内联替换）。

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | 图谱自动感知磁盘变化 | ❌ 不感知——新函数查无、已删函数仍在旧行号（缓存语义确认，必须有失效检查）|
| 2 | `detect_changes` 发现改动 | ✅ changed_files 13→15 精准命中 `src/utils.{cpp,h}`；seed_symbols 5；impacted 6（含 `memorywidget`、`prolistmodel`、`test_utils`）|
| 3 | fast 增量索引耗时 | ✅ **9.85s / 12.5s** 两次实测（CLI 冷启动含进程创建）——"立刻"量级 |
| 4 | 新增函数进图 | ✅ `experimentEcho` 行号 359-362 精确 |
| 5 | 删除函数出图 | ✅ `isAtoE` 消失；孤儿测试 `TEST_F_UtilsTest_IsAtoE_ChecksHexChar` 可从图中定位 |
| 6 | 修改函数同步 | ✅ 行号 142-157→142-160（+3 精确对应）；`get_code_snippet` 返回**修改后的函数体**（内容级同步，非仅符号）|
| 7 | fast 是否丢调用链 | ✅ 不丢：CALLS 边 1141 条保留，`formatThousandsSeparators` 8 个调用方全返回且带 confidence（0.38-0.95）|
| 8 | 图谱原生水位 | ✅ `Branch.head_sha` 与 `git rev-parse HEAD` 一致（121cf38...）；且 dirty 改动后 head_sha 不变 → 印证 4.4 两级互补 |
| 9 | 回退 + fast 重索引闭环 | ✅ `git checkout` + fast → 图谱恢复原状（isAtoE 回到 355-358，experimentEcho 消失，changed_files 复原）|

**注意（fast vs moderate 的节点差异）**：fast 重建后 nodes 4781→2621、edges 13959→8449
（语义边/富节点减少），但对单测生成的核心查询（search_graph / get_code_snippet / CALLS / TESTS）
实测无影响。首次索引仍用 moderate；需要 semantic 边的深度分析时再补 moderate。

## 7. 对简化"编写单元测试流程"的回答

**仍然用图谱**（硬门禁"读源码只走 MCP"不变——fast 同步确定性秒级后，没有绕过图谱的理由），
但流程从「reconcile → 环境检查 → 提供方解析 → freshness → fallback」五段
塌缩为「**detect_changes → (fast) → 按 impacted 符号生成**」三步（§5）。
本地开发（dirty/unpushed）下，这就是最简可行流程。

## 8. 迁移清单（待实施）

- [ ] `environment-check.md`：删 §0b、§4a；§1 增加 Step 0 分类器前置门
- [x] `mcp-providers.md`：已落地——远端唯一 + 硬终止指引（本地仅 Mode 0，无 fallback 状态机）
- [ ] `reconcile-logic.md`：删 freshness 检测块与 `get_graph_head_sha`/`git_unpushed_commits`/`probe_local_mcp_available` 辅助函数；`ensure_local_project_ready` 塌缩为 §5 ②③
- [ ] `dev-preflight.md`：重写为分类器 override（用户显式本地偏好）薄说明
- [ ] `SKILL.md`：Mode 0 触发词并入"本地模式"；删除 `mode_0_active`/`fallback_*` 相关检查清单项
- [ ] `codebase-memory-guide.md`：补记 `detect_changes` / `Branch.head_sha` / `TESTS` 边用法
- [ ] stale-test-cleanup 流程改为 TESTS 边反查（去 grep 化）

## 9. 边界与代价

- **开发中反复 fast 索引**：dirty 下每次树变都要 ~10s。可用 detect_changes 无源码变更时跳过（§5 ②）缓解；嫌频繁可改懒同步（首次真正查询时才索引）。
- **唯一保留的异步等待**：clean+pushed + 远端 watcher 延迟，有界 120s，超时一次性单向切 local（一行日志，无状态机）。
- **fast 节点丰富度低于 moderate**：语义边相关深度分析需 moderate；单测生成核心查询不受影响（§6 注）。
- **水位与 daemon 脱节**：图谱 Branch 节点缺失（如空库）→ 视为不匹配 → fast 重索引，失败方向安全。
