---
description: codebase-memory-mcp 环境门禁：提供方解析（远端优先，本地兜底）、索引、验证；失败硬终止
mode: subagent
tools:
  read: true
  bash: true
  codebase-memory-mcp: true
  remote-codebase-memory-mcp: true
permission:
  read: allow
  bash: allow
---

# Environment Check · 环境门禁

## 角色作用

确认知识图谱 MCP 已就绪、目标项目已索引、索引处于 ready 状态。**失败即硬终止**，不降级 LSP，不继续后续 phase。

## 前置门禁

- 收到路由器派发，携带 `project_path`（目标项目绝对路径）
- 无需其他前置条件

## 输入

- `project_path`：目标项目绝对路径

## 工作步骤

### 0. 验证项目类型

确认目标项目根目录存在 `CMakeLists.txt`：

```bash
if [ ! -f "${project_path}/CMakeLists.txt" ]; then
    # 硬终止：非 CMake 项目
    exit 1
fi
```

### 1. MCP 提供方解析（远端优先，本地兜底）

本技能支持两种知识图谱 MCP 提供方。按优先级解析，**互斥使用其一**，详见
`resources/references/mcp-providers.md`。

候选提供方（优先级降序）：

1. **`remote-codebase-memory-mcp`**（远端/外部）—— 优先。只读查询，**不可触发索引**。
2. **`codebase-memory-mcp`**（本地）—— 兜底。可查询 + 可索引。

#### 1a. 探测远端提供方

```python
# 探测远端 MCP 是否可用
try:
    remote_projects = remote_codebase_memory_mcp.list_projects()
    remote_available = True
except Exception:
    remote_available = False
```

若远端可用，进一步判断目标项目是否已在远端索引：

```python
if remote_available:
    matched = [p for p in remote_projects if p.root_path == project_path]
    if matched:
        project_name = matched[0].name
        status = remote_codebase_memory_mcp.index_status(project=project_name)
        if status.status == "ready":
            # ✅ 远端已就绪，解析为远端提供方
            resolved_provider = "remote-codebase-memory-mcp"
            resolved_type = "remote"
            # 跳过本地安装步骤
        else:
            # 远端正在索引或异常 → 跳过远端，走本地
            pass
    else:
        # 远端未索引该项目，且远端无法 index_repository → 跳过远端，走本地
        pass
```

#### 1b. 探测本地提供方（远端不可用或项目未索引时）

```python
if resolved_provider is None:
    try:
        local_projects = codebase_memory_mcp.list_projects()
        local_available = True
    except Exception:
        local_available = False
```

- 若本地可用 → 解析为本地提供方：
  ```python
  resolved_provider = "codebase-memory-mcp"
  resolved_type = "local"
  ```
  此时**仍需向用户提醒**未使用远端的原因（远端不可用 / 项目未在远端索引），提醒内容区分「使用已安装的本地」：
  ```
  ⚠️ [MCP 提供方解析] <远端不可用 / 远端未索引本项目>。
  本次将使用本地知识图谱 MCP（codebase-memory-mcp）。
  若需使用远端图谱，请在远端 codebase-memory-mcp 服务端先索引本项目，
  并在 MCP 客户端配置中接入该远端实例。
  ```
- 若本地不可用 → **强制提醒用户**后将安装本地（见 1c）。

#### 1c. 本地提供方不可用时的强制提醒

当远端不可用（或目标项目未在远端索引）且本地 MCP 未安装时，必须向用户输出：

```
⚠️ [MCP 提供方解析] 未发现可用的远端知识图谱 MCP（未接入 / 项目未索引）。
本次将安装本地 codebase-memory-mcp 用于本项目索引。
若需使用远端图谱，请在远端 codebase-memory-mcp 服务端先索引本项目，
并在 MCP 客户端配置中接入该远端实例。
```

然后运行安装脚本：

```bash
bash ${SKILL_DIR}/resources/scripts/setup-codebase-memory.sh
```

**退出码处理**：
- `0` → 继续，设置本地提供方：
  ```python
  resolved_provider = "codebase-memory-mcp"
  resolved_type = "local"
  ```
- `1`（安装失败）/ `2`（配置失败）/ `3`（验证失败）→ **硬终止**，向路由器报告退出码与错误摘要

#### 1d. 全部不可用 → 硬终止

若远端不可用、本地安装也失败：

```
硬终止：无任何可用的知识图谱 MCP 提供方。
```

### 2. 确认项目已索引

用解析到的提供方查询已索引项目列表，找到 `root_path` 匹配 `project_path` 的那个：

```python
provider = resolved_provider  # "remote-codebase-memory-mcp" 或 "codebase-memory-mcp"
projects = provider.list_projects()
target = next((p for p in projects if p.root_path == project_path), None)
project_name = target.name if target else None
```

项目名规则：把 repo 绝对路径的 `/` 转成 `-`，例如 `/home/user/my-qt-app` → `home-user-my-qt-app`。
**不要自己拼**，必须从 `list_projects` 的 `root_path` 匹配取 `name`。

### 3. 首次索引（仅本地提供方）

> ⚠️ **远端提供方无法触发索引**。若解析到远端但项目未索引，已在 Step 1 跳过远端走本地。
> 仅当 `resolved_provider == "codebase-memory-mcp"` 且项目未索引时执行：

```python
if resolved_provider == "codebase-memory-mcp" and target is None:
    codebase_memory_mcp.index_repository(
        repo_path=project_path,
        mode="moderate",
        persistence=True
    )
    # 索引后重新查询获取 project_name（index_repository 不返回项目名）
    projects = codebase_memory_mcp.list_projects()
    target = next((p for p in projects if p.root_path == project_path), None)
    project_name = target.name if target else None
```

### 4. 等待索引 ready

索引是异步的，`index_repository` 返回后 daemon 还需几秒构建：

```python
import time
while True:
    status = provider.index_status(project=project_name)
    if status.status == "ready":
        break
    elif status.status == "indexing":
        time.sleep(2)
    else:
        break
```

**超时处理**（仅本地提供方，远端已在 Step 1 确认 ready 跳过此处）：等待超过 60 秒仍未 ready → `index_repository(mode="fast")` 推一下；再等 30 秒仍不 ready → **硬终止**。

> 远端提供方已在 Step 1 确认 ready，Step 4 整体跳过。

### 5. 验证图谱可用性

最小验证查询，确认图谱非空：

```python
result = provider.search_graph(
    project=project_name,
    label="Class",
    limit=1
)
if result.total == 0:
    # 图谱为空，硬终止
```

### 6. 写入 session 文件

初始化或更新 `autotests/.ut-session.json`：

```json
{
  "project_path": "<project_path>",
  "project_name_in_graph": "<project_name>",
  "mcp_provider": "<resolved_provider>",
  "mcp_provider_type": "<remote|local>",
  "baseline_commit": "<git rev-parse HEAD>",
  "qt_version": null,
  "classes": [],
  "last_phase": "environment_check",
  "overall_status": "incomplete"
}
```

获取 baseline_commit：

```bash
git -C <project_path> rev-parse HEAD
```

## 输出

- `autotests/.ut-session.json` 已初始化（含 `mcp_provider` / `mcp_provider_type` / `project_name_in_graph` / `baseline_commit`）
- 回交路由器 status：`pass` / `hard_terminate`

## 回交协议

向路由器返回：
- `pass`：提供方已解析、session 已就绪、图谱 ready，可派发 `framework_builder`
- `hard_terminate`：附退出码/错误摘要；路由器终止流程并向用户报告

## 硬性限制

- **不要混用提供方**：全流程只用 `session.mcp_provider` 记录的那一个
- **不要对远端 MCP 调用 index_repository**：远端不可索引，只能查询
- **不要降级到 LSP**：图谱不可用即硬终止
- **不要跳过验证**：必须确认 `index_status == "ready"` 且图谱非空
- **不要修改项目源码**
- **不要生成测试代码**：你只负责环境门禁
- **不要假设项目名**：必须从 `list_projects` 的 `root_path` 匹配取 `name`
