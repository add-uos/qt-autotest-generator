# Mode 0 · Dev Preflight（开发预检）

> Mode 0 是显式的本地开发入口。用户主动选择使用本地图谱，
> 技能自动完成本地 MCP 可用性探测、项目索引（本地模式默认图谱即最新工作区）。
> Mode 0 完成后自动路由到 Mode 1（首次）或 Mode 2（已有 inventory）。

## 适用场景

- 本地有未 push 的 commit，远端图谱必然不包含最新代码
- 无远程追踪分支（`@{upstream}` 不存在），远端图谱天然无法同步
- 需要立即对本地最新代码写测试，不想等 push + 远端同步
- 远端 MCP 不可用或远端项目未索引（远端路径会硬终止，Mode 0 是唯一本地入口）

## 前置条件

- `project_path` 已就绪（项目根目录存在 CMakeLists.txt）
- 由 SKILL.md 触发条件路由到 Mode 0

## 执行步骤

### Step 0: 本地 git 状态检测与展示

```bash
git -C <project_path> rev-parse HEAD                                # 本地 HEAD（交接用）
git -C <project_path> log @{upstream}..HEAD --oneline 2>/dev/null   # 未推送 commit
git -C <project_path> status --porcelain                            # 工作区脏检测（含 untracked）
```

| 检测 | 返回 | 含义 | 处理 |
|------|------|------|------|
| 未推送 | 非空输出 | 有 N 个未 push commit | 记录 `unpushed_count = N`，展示列表，继续 |
| 未推送 | 空输出 | 已全部 push / 无新 commit | `unpushed_count = 0`，提示「未检测到未推送 commit，远端图谱可能已是最新」，但**仍继续**（用户可能想用本地模式的其他原因） |
| 未推送 | 错误（无 upstream） | 无远程追踪分支 | 记录 `has_upstream = false`，继续（远端图谱必然无法同步） |
| 脏检测 | porcelain 非空 | 工作区有未提交改动（含 untracked） | 记录 `dirty = true`，继续（本地模式默认工作区即最新，见 Step 3a） |
| 脏检测 | porcelain 为空 | 工作区干净 | `dirty = false` |

**输出示例**（有未 push commit）：

```
🔍 [Mode 0] 检测到 3 个未推送 commit：
  a1b2c3d feat(ui): add new sidebar widget
  e4f5a6b fix(core): resolve crash on empty input
  c7d8e9f refactor(data): extract parser to separate class
```

### Step 1: 本地 MCP 可用性探测

> 📍 **本地 HTTP 端点与认证**：`mcp-scan.py` 通过 HTTP JSON-RPC 直连 MCP，
> 本地模式需把 `QTAG_MCP_URL` 指向本地端点。pi 环境可从 `~/.pi/agent/mcp.json`
> 找到 `local-codebase-memory-mcp` 的 `url` 与 `headers`：
>
> ```bash
> export QTAG_MCP_URL="<mcp.json 中的 url>"
> export QTAG_MCP_API_KEY="<headers.X-API-Key 的值>"   # 部分部署需要；无认证则省略
> # 或一次性透传任意请求头：
> export QTAG_MCP_HEADERS='{"X-API-Key":"<key>"}'
> ```
>
> 不同部署认证方式不同：有些本地端点无需 API Key，有些必须；按实际 `mcp.json` 配置。

调用本地 `codebase_memory_mcp.list_projects()` 探测可用性：

```python
def probe_local_mcp():
    try:
        result = codebase_memory_mcp.list_projects()
        return True
    except Exception:
        return False
```

| 结果 | 处理 |
|------|------|
| 可用 | 进入 Step 2 |
| 不可用 | 进入 Step 1a：安装 |

#### Step 1a: 安装本地 MCP

```bash
bash ${SKILL_DIR}/scripts/setup-codebase-memory.sh
```

| 退出码 | 含义 | 处理 |
|--------|------|------|
| 0 | 安装成功 | 回到 Step 1 重新探测（受下方重试上限约束） |
| 1 | 安装失败 | **硬终止**：`[FATAL] 本地 local-codebase-memory-mcp 安装失败，无法执行 Dev Preflight` |
| 2 | 配置失败 | **硬终止**：同上 |
| 3 | 验证失败 | **硬终止**：同上 |

> **重试上限**：安装成功（退出码 0）后重新探测，若仍不可用，最多重试 **2 次**
> （每次间隔 3 秒等待 daemon 就绪）。超过上限仍未可用 → **硬终止**：
> `[FATAL] 本地 local-codebase-memory-mcp 安装后反复探测不可用，请检查 daemon 状态`
>
> ```python
> max_probe_retries = 2  # 安装成功后最多再探测 2 次
> probe_attempt = 0
> while not probe_local_mcp():
>     if probe_attempt >= max_probe_retries:
>         HARD_FATAL("本地 local-codebase-memory-mcp 安装后反复探测不可用")
>     time.sleep(3)  # 等待 daemon 就绪
>     probe_attempt += 1
> ```

### Step 2: 强制锁定本地提供方

**跳过远端探测**，直接设置：

```python
mcp_provider = "local-codebase-memory-mcp"
mcp_provider_type = "local"
mode_0_active = True  # 标志位，供后续 reconcile/Mode 1/2 识别
```

**输出**：

```
✅ [Mode 0] 提供方：本地 local-codebase-memory-mcp（已跳过远端探测）
```

### Step 3: 项目索引检查与同步

#### Step 3.0: 查找已索引项目

```python
import os
projects = codebase_memory_mcp.list_projects()
project_basename = os.path.basename(project_path.rstrip('/'))
target = next(
    (p for p in projects 
     if os.path.basename(p.root_path.rstrip('/')) == project_basename),
    None
)
project_name = target.name if target else None
```

| 结果 | 路径 |
|------|------|
| 找到（`target` 非 None） | → Step 3a: Freshness 检查 |
| 未找到 | → Step 3b: 首次索引 |

#### Step 3a: Freshness 检查（已有索引）

> **本地模式默认即最新**：Mode 0 面向活跃开发场景，当前工作区即最新代码。
> 已索引项目**不做过时判定**，直接视为 fresh（跳过 `Branch.head_sha` 比对与 dirty 门禁），
> 进入 Step 5。

| 场景 | 处理 |
|------|------|
| 已有索引 | 默认 fresh → 直接进入 Step 5 |
| 怀疑索引落后（刚大批改码 / 索引异常） | 手动执行一次增量同步（下方）即可，不阻断 |

> 💡 不同本地 MCP 的 fresh 信号（`Branch.head_sha` 查询 / `index_status` 的 git 字段）
> 实现不统一，强行比对反而误判。本地模式下工作区即真相，故不再依赖该信号。

**可选增量同步**（仅当需要强制对齐工作区时执行）：

```python
codebase_memory_mcp.index_repository(
    repo_path=project_path,
    mode="fast",           # 已有索引用 fast 增量
    persistence=True
)
```

#### Step 3b: 首次索引

```python
codebase_memory_mcp.index_repository(
    repo_path=project_path,
    mode="moderate",       # 首次用 moderate（含 semantic 边）
    persistence=True
)

# 索引后重新查询获取 project_name
projects = codebase_memory_mcp.list_projects()
project_basename = os.path.basename(project_path.rstrip('/'))
target = next(
    (p for p in projects 
     if os.path.basename(p.root_path.rstrip('/')) == project_basename),
    None
)
project_name = target.name if target else None
```

**输出**：

```
📦 [Mode 0] 项目未索引，执行首次索引 (mode=moderate)...
```

### Step 4: 等待索引 ready

```python
import time

max_wait = 300        # 硬超时 300 秒
push_deadline = 60    # 60 秒仍未 ready 才推
pushed = False        # 只推一次：反复重发会打断进行中的增量索引，可能永远到不了 ready
start = time.time()
while True:
    elapsed = time.time() - start
    if elapsed > max_wait:
        HARD_FATAL(f"[Mode 0] 本地索引 {max_wait} 秒未 ready，daemon 可能异常")
        # 真正终止，不 break 后继续走 Step 5/6

    status = codebase_memory_mcp.index_status(project=project_name)
    if status.status == "ready":
        break
    elif status.status == "indexing":
        if elapsed > push_deadline and not pushed:
            # 推一次且只推一次
            codebase_memory_mcp.index_repository(
                repo_path=project_path,
                mode="fast",
                persistence=True
            )
            pushed = True
        time.sleep(2)
    else:
        # error / 未知状态 → 真正终止（半残图谱不得交接给 Mode 1/2）
        HARD_FATAL(f"[Mode 0] 索引状态异常：{status.status}，请检查 daemon 日志")
```

### Step 5: 验证图谱可用性

```python
try:
    result = codebase_memory_mcp.search_graph(
        project=project_name,
        label="Class",
        limit=1
    )
except Exception as e:
    HARD_FATAL(f"[Mode 0] 图谱查询失败：{e}")   # 查询报错 ≠ 图谱为空，同样终止
if result.total == 0:
    HARD_FATAL("[Mode 0] 图谱为空，索引可能失败")
```

### Step 6: 交接

**写入 session**：

```python
session["mcp_provider"] = "local-codebase-memory-mcp"
session["mcp_provider_type"] = "local"
session["project_name"] = project_name
session["base_sha"] = local_head
session["unpushed_count"] = unpushed_count
session["has_upstream"] = has_upstream
session["dirty"] = dirty
session["mode_0_active"] = True
```

**输出摘要**：

```
✅ [Mode 0] Dev Preflight 完成
   提供方：本地 local-codebase-memory-mcp
   项目：<project_name>
   本地 HEAD：<local_head[:8]>
   未推送 commit：<unpushed_count> 个
   工作区：干净 / 有未提交改动
   图谱状态：fresh / 已同步
   
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
    # 走 Mode 2（reconcile 会检测 mcp_provider_type=="local"，跳过远端相关逻辑）
    进入_Mode_2()
else:
    # 走 Mode 1
    # Mode 1 的环境检查步骤会检测 mode_0_active，跳过提供方解析；
    # test_dir 的正式命名/沿用规则由该阶段一次性探测确定
    进入_Mode_1()
```

## 与 reconcile 的交互

Mode 0 完成后进入 Mode 1/2，这些 Mode 的 reconcile 阶段会：

1. 检测 `mcp_provider_type == "local"` → **跳过远端 freshness 检测**（已在 Mode 0 确认 fresh）
2. 检测 `mode_0_active == True` → **跳过环境检查的提供方解析**（已在 Mode 0 锁定）
3. 重新校验本地提供方仍可用（`codebase_memory_mcp.list_projects()` 可调通）→ 失联则**硬终止**：重启本地 daemon 后重跑 Mode 0（不重新解析提供方、不触碰远端）
4. 正常执行 reconcile 对账逻辑（比较 HEAD 与 inventory.base_sha）

## 约束

- Mode 0 **不生成测试代码、不编译、不运行**
- Mode 0 锁定本地提供方后，后续 Mode 1-5 **不触碰远端 MCP**
- Mode 0 不执行框架搭建（`references/framework-builder.md`），框架搭建在 Mode 2 按需触发
- 互斥原则仍然有效：Mode 0 选择了本地，全流程只用本地
