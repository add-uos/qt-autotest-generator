# Mode 0 · Dev Preflight（开发预检）

> Mode 0 是显式的本地开发入口。用户主动选择使用本地图谱，
> 技能自动完成本地 MCP 可用性探测、项目索引、freshness 同步。
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

### Step 0: 未 push commit 检测与展示

```bash
git -C <project_path> log @{upstream}..HEAD --oneline 2>/dev/null
```

| git 返回 | 含义 | 处理 |
|----------|------|------|
| 非空输出 | 有 N 个未 push commit | 记录 `unpushed_count`，展示列表，继续 |
| 空输出 | 已全部 push / 无新 commit | 提示「未检测到未推送 commit，远端图谱可能已是最新」，但**仍继续**（用户可能想用本地模式的其他原因） |
| 错误（无 upstream） | 无远程追踪分支 | 记录 `has_upstream = false`，继续（远端图谱必然无法同步） |

**输出示例**（有未 push commit）：

```
🔍 [Mode 0] 检测到 3 个未推送 commit：
  a1b2c3d feat(ui): add new sidebar widget
  e4f5a6b fix(core): resolve crash on empty input
  c7d8e9f refactor(data): extract parser to separate class
```

### Step 1: 本地 MCP 可用性探测

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
| 1 | 安装失败 | **硬终止**：`[FATAL] 本地 codebase-memory-mcp 安装失败，无法执行 Dev Preflight` |
| 2 | 配置失败 | **硬终止**：同上 |
| 3 | 验证失败 | **硬终止**：同上 |

> **重试上限**：安装成功（退出码 0）后重新探测，若仍不可用，最多重试 **2 次**
> （每次间隔 3 秒等待 daemon 就绪）。超过上限仍未可用 → **硬终止**：
> `[FATAL] 本地 codebase-memory-mcp 安装后反复探测不可用，请检查 daemon 状态`
>
> ```python
> max_probe_retries = 2  # 安装成功后最多再探测 2 次
> probe_attempt = 0
> while not probe_local_mcp():
>     if probe_attempt >= max_probe_retries:
>         HARD_FATAL("本地 codebase-memory-mcp 安装后反复探测不可用")
>     time.sleep(3)  # 等待 daemon 就绪
>     probe_attempt += 1
> ```

### Step 2: 强制锁定本地提供方

**跳过远端探测**，直接设置：

```python
mcp_provider = "codebase-memory-mcp"
mcp_provider_type = "local"
mode_0_active = True  # 标志位，供后续 reconcile/Mode 1/2 识别
```

**输出**：

```
✅ [Mode 0] 提供方：本地 codebase-memory-mcp（已跳过远端探测）
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

```python
import subprocess

# 获取本地 HEAD
local_head = subprocess.run(
    ["git", "-C", project_path, "rev-parse", "HEAD"],
    capture_output=True, text=True, timeout=10
).stdout.strip()

# 获取图谱 HEAD
graph_head = get_graph_head_sha(
    provider=codebase_memory_mcp,
    project_name=project_name,
    project_path=project_path,
    fallback_sha=local_head
)
```

**`get_graph_head_sha` 实现**（Agent 内联执行）：

> 本地 MCP 的 `list_projects` **不返回 git 元数据**（远端 MCP 才有 `git.head_sha` 字段），
> 因此本地提供方需用间接策略推断图谱新鲜度。

```python
def get_graph_head_sha(provider, project_name, project_path, fallback_sha):
    """获取图谱记录的 HEAD SHA（本地 MCP 间接推断）。"""
    # 策略: 从图谱取若干已知符号的 file_path，
    #        对每个 file_path 执行 git log 取最新 commit，
    #        取其中最新的作为 graph_head 的近似值。
    #        如果所有 file_path 的 git log 都指向 local_head，则图谱大概率 fresh。
    try:
        result = provider.search_graph(
            project=project_name,
            label="Class",
            limit=5
        )
        results = result.get("results", []) if isinstance(result, dict) else getattr(result, "results", [])
        if not results:
            return fallback_sha  # 图谱可能为空，交给 Step 5 处理

        import subprocess
        commits = []
        for r in results:
            file_path = r.get("file_path", "")
            if not file_path:
                continue
            # file_path 是相对项目根的路径
            r2 = subprocess.run(
                ["git", "-C", project_path, "log", "-1", "--format=%H", "--", file_path],
                capture_output=True, text=True, timeout=10
            )
            if r2.returncode == 0 and r2.stdout.strip():
                commits.append(r2.stdout.strip())

        if not commits:
            return fallback_sha

        # 取所有 file_path 最新 commit 中的最大值（最新那个）
        graph_head = max(commits)  # SHA 字符串可按字典序比较
        return graph_head
    except Exception:
        return fallback_sha
```

> ⚠️ **此方法是近似推断**：git log 取的是「该文件最后一次被修改的 commit」，
> 如果图谱索引时某些文件刚被修改，近似值接近真实 graph_head；
> 如果文件长时间未变，近似值会偏旧（误判为过时）。
> **保守策略**：宁可误判过时（触发一次 fast 增量索引，秒级完成），
> 也不要漏判（用过时图谱生成测试）。因此：
> - **有未 push commit 时，无论 freshness 结果如何，都执行一次 `index_repository(mode="fast")`**
> - 无未 push commit 时，才依赖 freshness 判断是否需要同步

| 比较结果 | 处理 |
|----------|------|
| `local_head == graph_head` 且 `unpushed_count == 0` | 图谱 fresh → **跳过索引**，直接进入 Step 5 |
| `local_head != graph_head` 或 `unpushed_count > 0` | 图谱过时（或保守起见） → 执行增量同步（下方） |

**增量同步**：

```python
codebase_memory_mcp.index_repository(
    repo_path=project_path,
    mode="fast",           # 已有索引用 fast 增量
    persistence=True
)
```

**输出**：

```
🔄 [Mode 0] 图谱过时（graph: {graph_head[:8]} → local: {local_head[:8]}），执行增量同步 (mode=fast)...
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

max_wait = 300  # 硬超时 300 秒
start = time.time()
while True:
    elapsed = time.time() - start
    if elapsed > max_wait:
        print(f"[FATAL] [Mode 0] 本地索引 {max_wait} 秒未 ready，daemon 可能异常")
        break  # 硬终止
    
    status = codebase_memory_mcp.index_status(project=project_name)
    if status.status == "ready":
        break
    elif status.status == "indexing":
        if elapsed > 60:
            # 超过 60 秒仍未 ready → 推一下
            codebase_memory_mcp.index_repository(
                repo_path=project_path,
                mode="fast",
                persistence=True
            )
        time.sleep(2)
    else:
        # error / 其他状态
        break
```

### Step 5: 验证图谱可用性

```python
result = codebase_memory_mcp.search_graph(
    project=project_name,
    label="Class",
    limit=1
)
if result.total == 0:
    print("[FATAL] [Mode 0] 图谱为空，索引可能失败")
    # 硬终止
```

### Step 6: 交接

**写入 session**：

```python
session["mcp_provider"] = "codebase-memory-mcp"
session["mcp_provider_type"] = "local"
session["project_name"] = project_name
session["base_sha"] = local_head
session["unpushed_count"] = unpushed_count
session["mode_0_active"] = True
```

**输出摘要**：

```
✅ [Mode 0] Dev Preflight 完成
   提供方：本地 codebase-memory-mcp
   项目：<project_name>
   本地 HEAD：<local_head[:8]>
   未推送 commit：<unpushed_count> 个
   图谱状态：fresh / 已同步
   
   → 自动进入 Mode 1（首次） / Mode 2（增量）
```

**自动路由**：

```python
inventory_path = f"{project_path}/{test_dir}/.ut-inventory.json"
if os.path.exists(inventory_path):
    # 走 Mode 2（reconcile 会检测 mcp_provider_type=="local"，跳过远端相关逻辑）
    进入_Mode_2()
else:
    # 走 Mode 1
    # Mode 1 的环境检查步骤会检测 mode_0_active，跳过提供方解析
    进入_Mode_1()
```

## 与 reconcile 的交互

Mode 0 完成后进入 Mode 1/2，这些 Mode 的 reconcile 阶段会：

1. 检测 `mcp_provider_type == "local"` → **跳过远端 freshness 检测**（已在 Mode 0 确认 fresh）
2. 检测 `mode_0_active == True` → **跳过环境检查的提供方解析**（已在 Mode 0 锁定）
3. 正常执行 reconcile 对账逻辑（比较 HEAD 与 inventory.base_sha）

## 约束

- Mode 0 **不生成测试代码、不编译、不运行**
- Mode 0 锁定本地提供方后，后续 Mode 1-5 **不触碰远端 MCP**
- Mode 0 不执行框架搭建（`references/framework-builder.md`），框架搭建在 Mode 2 按需触发
- 互斥原则仍然有效：Mode 0 选择了本地，全流程只用本地
