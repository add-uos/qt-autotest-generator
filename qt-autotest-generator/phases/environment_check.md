# 环境门禁

> 前置条件：目标项目绝对路径（`project_path`）已就绪。

> 通过 session.mcp_provider 调用知识图谱工具（详见 resources/references/mcp-providers.md）

## 概述

确认知识图谱 MCP 已就绪、目标项目已索引、索引处于 ready 状态。**失败即硬终止**，不降级 LSP，不继续后续阶段。

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

**完整解析算法（候选优先级、`probe_available` 探测、远端项目已索引判定、4 种强制提醒规则）见 [`resources/references/mcp-providers.md`](../resources/references/mcp-providers.md)，该文档为单一权威来源。** 本步骤只列执行要点：

候选提供方（优先级降序）：

1. **`remote-codebase-memory-mcp`**（远端/外部）—— 优先。只读查询，**不可触发索引**。
2. **`codebase-memory-mcp`**（本地）—— 兜底。可查询 + 可索引。

**执行步骤**：

1. **探测远端**：`remote_codebase_memory_mcp.list_projects()` 调通即远端可用；进一步用 `root_path` 匹配 `project_path`，命中且 `index_status(project=...) == "ready"` → 解析为远端提供方，写 `session.mcp_provider = "remote-codebase-memory-mcp"` / `mcp_provider_type = "remote"`，**跳过本地安装**。
2. **回退本地**：远端不可用 / 项目未在远端索引 / 远端索引中三者任一成立 → 探测本地 `codebase_memory_mcp.list_projects()`：调通则解析为本地提供方，`session.mcp_provider = "codebase-memory-mcp"` / `mcp_provider_type = "local"`，按 `mcp-providers.md` §6 输出**使用本地**提醒。
3. **本地不可用 → 安装**：本地亦不可用 → 按 `mcp-providers.md` §6 输出**安装本地**强制提醒 → 运行 `bash ${SKILL_DIR}/resources/scripts/setup-codebase-memory.sh`；退出码 `0` → 设本地提供方；`1`（安装失败）/ `2`（配置失败）/ `3`（验证失败）→ **硬终止**，报告退出码与错误摘要。
4. **全不可用 → 硬终止**：远端不可用且本地安装失败 → `硬终止：无任何可用的知识图谱 MCP 提供方。`，**不降级 LSP**（`mcp-providers.md` §7）。

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

## 关键约束

- 全流程只用 `session.mcp_provider` 记录的那一个提供方，不混用
- 不对远端 MCP 调用 `index_repository`（远端不可索引，只能查询）
- 图谱不可用即硬终止，不降级到 LSP
- 必须确认 `index_status == "ready"` 且图谱非空
- 不修改项目源码
- 项目名必须从 `list_projects` 的 `root_path` 匹配取 `name`，不假设
