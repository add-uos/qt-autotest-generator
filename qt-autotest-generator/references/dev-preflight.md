# Mode 0 · Dev Preflight（开发预检）

> Mode 0 是显式的本地开发入口。GitNexus 由平台统一索引、技能侧**不能**触发索引，
> 因此 Mode 0 的职责是：**确认仓库已索引（list_repos）→ 校验本地检出（--repo-root）→
> 量化本地 HEAD 与图谱 lastCommit 的漂移（check_drift）**。
> Mode 0 完成后自动路由到 Mode 1（首次）或 Mode 2（已有 inventory）。

## 适用场景

- 本地有未 push 的 commit，想知道图谱是否包含最新代码（drift 量化）
- 项目初始化：确认本项目已纳入 GitNexus 索引，避免 Mode 1/2 中途硬终止
- 图谱 lastCommit 与本地 HEAD 的差异范围评估（reconcile 前置判断）

## 前置条件

- `project_path` 已就绪（项目根目录存在 CMakeLists.txt）
- `QTAG_MCP_URL` 指向 GitNexus 端点（默认 `https://codegraph.uniontech.com/api/mcp`）
- 由 SKILL.md 触发条件路由到 Mode 0

## 执行步骤

### Step 0: 本地 git 状态检测与展示

```bash
git -C <project_path> rev-parse HEAD                                # 本地 HEAD（漂移比对用）
git -C <project_path> log @{upstream}..HEAD --oneline 2>/dev/null   # 未推送 commit
git -C <project_path> status --porcelain                            # 工作区脏检测（含 untracked）
```

| 检测 | 返回 | 含义 | 处理 |
|------|------|------|------|
| 未推送 | 非空输出 | 有 N 个未 push commit | 记录 `unpushed_count = N`，展示列表，继续（图谱 lastCommit 必然 ≤ 这些 commit 的父集） |
| 未推送 | 空输出 | 已全部 push | `unpushed_count = 0` |
| 未推送 | 错误（无 upstream） | 无远程追踪分支 | 记录 `has_upstream = false`，继续 |
| 脏检测 | porcelain 非空 | 工作区有未提交改动 | 记录 `dirty = true`，继续（方法体以本地切片为准，脏改动不影响切片正确性） |
| 脏检测 | porcelain 为空 | 工作区干净 | `dirty = false` |

### Step 1: GitNexus 可用性探测与索引确认

调用 `list_repos`（分页遍历，limit ≤ 200）确认端点可用且项目已索引：

```python
repos = list_repos_pages()          # 分页聚合 {"name","lastCommit","branch","indexedAt"}
target = next((r for r in repos if r["name"] == project_name), None)
```

| 结果 | 处理 |
|------|------|
| 端点可用且项目在册 | 记录 `graph_last_commit = target["lastCommit"]`，进入 Step 2 |
| 端点不可用 | **硬终止**：`[FATAL] GitNexus MCP 不可用（QTAG_MCP_URL=<url>），请检查网络/端点` |
| 项目不在册 | **硬终止**：`[FATAL] 本项目未在 GitNexus 索引，请联系平台纳入索引（不降级文件扫描）` |

> mcp-scan.py 已内建该校验：`open_adapter` 在项目未索引时直接 `SystemExit(2)`。

### Step 2: 本地检出校验（--repo-root）

GitNexus 是双源架构：图谱当索引（定位类/方法/边），方法体/复杂度/签名/宏扫描
从 `--repo-root` 本地检出切片计算。因此本地检出的质量直接决定数据质量：

| 检查 | 命令 | 不通过时 |
|------|------|---------|
| 目录存在 | `test -d <repo_root>` | 硬终止：要求提供有效检出 |
| 是 git 仓库 | `git -C <repo_root> rev-parse --is-inside-work-tree` | 硬终止：切片与 drift 检查依赖 git |
| HEAD 与图谱分支一致 | list_repos 的 `branch` 字段 vs `git -C <repo_root> branch --show-current` | 警告：分支不同源时 CALLS/继承关系可能失真 |

### Step 3: 漂移检查（check_drift）

比对本地 HEAD 与图谱 `lastCommit`：

```python
local_head = git_rev_parse_head(repo_root)
drift = check_drift()   # mcp-scan.py 内建；fetch 前自动执行并警告
```

| 场景 | 判定 | 处理 |
|------|------|------|
| `local_head == graph_last_commit` | 无漂移 | 继续，图谱即最新 |
| `local_head` 领先（有未 push commit） | 图谱缺新代码 | **列出受影响文件**：`git log --name-only graph_last_commit..local_head`；若涉及待测模块 → 硬终止并等待平台同步（GitNexus 无本地索引，不可自行补索引）；若仅无关文件 → 带警告继续，reconcile 按 diff 路由 |
| `local_head` 落后 | 图谱更新 | 提示拉取最新代码后重试（切片行号以本地为准，落后会导致切片错位） |

**输出示例**（有漂移）：

```
⚠️ [Mode 0] 图谱漂移：lastCommit=76ad9135, 本地 HEAD=871cb5316
   落后 commit 涉及 12 个文件（src/... 8, autotests/... 4）
```

### Step 4: 验证图谱可查询性

```python
rows = cypher("MATCH (c:Class) RETURN count(c) AS c", repo=project)  # repo 参数必带
```

查询报错 ≠ 图谱为空，同样硬终止；图谱为空（count=0）→ 平台索引异常，硬终止。

### Step 5: 交接

**写入 session**：

```python
session["mcp_provider"] = "gitnexus-mcp"
session["project"] = project_name          # GitNexus 仓库名
session["repo_root"] = repo_root           # 本地检出（切片源）
session["graph_last_commit"] = graph_last_commit
session["unpushed_count"] = unpushed_count
session["has_upstream"] = has_upstream
session["dirty"] = dirty
session["mode_0_active"] = True
```

**输出摘要**：

```
✅ [Mode 0] Dev Preflight 完成
   图谱：GitNexus（<mcp_url>）
   项目：<project_name>  lastCommit：<graph_last_commit[:8]>
   本地 HEAD：<local_head[:8]>  漂移：无 / N commit（受影响文件 M 个）
   工作区：干净 / 有未提交改动
   
   → 自动进入 Mode 1（首次） / Mode 2（增量）
```

**自动路由**：

```python
# Mode 0 未跑 environment-check，test_dir 尚未确定，此处自行探测。
# 规则与 environment-check §0 一致：inventory 只可能位于 autotests/ 或 tests/。
inventory_dir = next(
    (d for d in ("autotests", "tests")
     if os.path.exists(f"{project_path}/{d}/.ut-inventory.json")),
    None
)
if inventory_dir:
    进入_Mode_2()   # reconcile 检测 mode_0_active，跳过提供方解析
else:
    进入_Mode_1()   # 环境检查检测 mode_0_active，跳过提供方解析
```

## 与 reconcile 的交互

Mode 0 完成后进入 Mode 1/2，这些 Mode 的 reconcile 阶段会：

1. 检测 `mode_0_active == True` → **跳过环境检查的提供方解析**（已在 Mode 0 确认）
2. 重新校验 GitNexus 仍可用（`list_repos` 可调通）→ 失联则**硬终止**
3. 正常执行 reconcile 对账逻辑：inventory.base_sha（= 图谱 lastCommit）与当前
   `list_repos` 返回值比对，图谱被平台重新索引后 base_sha 变化 → 按 diff 路由增量处理

## 约束

- Mode 0 **不生成测试代码、不编译、不运行、不触发索引**（GitNexus 无本地索引能力）
- 全流程只用 GitNexus 单栈，不存在提供方切换
- Mode 0 不执行框架搭建（`references/framework-builder.md`），框架搭建在 Mode 2 按需触发
