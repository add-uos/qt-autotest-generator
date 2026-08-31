# MCP 提供方解析（Provider Resolution）

> 两条互斥路径：**Mode 0 = 本地图谱（显式触发）；其余一切 = 远端图谱（唯一，不回退）**。
> 单一权威文档：所有 phase 均以本文档为提供方解析的唯一依据。

## 1. 两种部署形态

| 形态 | MCP 名称 | 能力 | 进入方式 |
|------|----------|------|---------|
| 远端 | `remote-codebase-memory-mcp` | 只读查询（search_graph / query_graph / trace_path / get_code_snippet / index_status / list_projects 等）。**不可触发 index_repository**——项目必须已在远端索引好 | Mode 1-5 默认，**唯一** |
| 本地 | `codebase-memory-mcp` | 查询 + 索引（含 index_repository / index_status）。可为本机项目建立索引 | **仅 Mode 0 显式触发**（见 `references/dev-preflight.md`）|

## 2. 路由规则（全部规则，无隐藏分支）

| session 状态 | 提供方 |
|--------------|--------|
| `mode_0_active == true` | 本地。Mode 0 已在 `references/dev-preflight.md` 完成探测、锁定与索引同步，本文档不再涉及 |
| 其余一切（Mode 1-5、reconcile、环境检查） | **远端唯一，不回退本地** |

- **互斥且单向**：一个会话自始至终只用一个提供方，不存在任何运行时切换。
- **不回退**：远端不可用 / 项目未索引 / 索引非 ready / 图谱过时，一律**硬终止**并给出指引（§5）。不静默安装本地、不切换本地。
- **结构性边界**：远端图谱从远端 git 仓库同步，看不到本地未 push / 未提交代码。这不是"过时"瞬态，而是能力边界——检测到未推送 commit 即判定图谱必然过时（见 `environment-check.md` §4a / `reconcile-logic.md`）。

## 3. 远端解析算法（Mode 1-5）

```
输入: project_path（目标项目绝对路径）

# ── 远端唯一候选 ──
IF NOT probe_available(remote):                 # list_projects 调不通
    HARD_FATAL(§5 · 情形 1)

project_name = find_project_by_basename(remote, project_path)
IF project_name IS None:                        # 远端未索引本项目
    HARD_FATAL(§5 · 情形 2)

IF remote.index_status(project_name) != "ready":
    HARD_FATAL(§5 · 情形 3)                     # 正在索引或异常

resolved_provider = remote
mcp_provider = "remote-codebase-memory-mcp"
mcp_provider_type = "remote"
```

### 3.1 探测可用性（probe_available）

```python
def probe_available(remote):
    try:
        result = remote.list_projects()   # 最轻量的可用性探针
        return True
    except Exception:
        return False
```

- 探测失败（MCP 未连接 / 工具调用抛错）→ 硬终止（§5 情形 1）。
- `list_projects` 返回空列表不算不可用，但项目匹配不到仍是情形 2。

### 3.2 远端项目已索引判定

用**项目名**匹配 `project_path`——提取路径最后一段（如 `/home/zhy/debug/deepin-picker` → `deepin-picker`），与远端返回的 `p.root_path` 最后一段比对。匹配不到即"远端未索引该项目"。

> **为什么不全路径匹配**：远端服务器路径与本地路径天然不同（如远端 `/home/uos/service/codebase/repos/deepin-picker` vs 本地 `/home/zhy/debug/deepin-picker`），图谱内容以项目名为锚，路径前缀不影响查询结果。

```python
import os
project_basename = os.path.basename(project_path.rstrip('/'))
remote_projects = remote_provider.list_projects()
matched = [p for p in remote_projects
           if os.path.basename(p.root_path.rstrip('/')) == project_basename]
if not matched:
    HARD_FATAL(§5 · 情形 2)
```

## 4. 提供方与工具前缀映射

| resolved_provider | 工具前缀 | 示例 |
|-------------------|---------|------|
| `remote-codebase-memory-mcp` | `remote_codebase_memory_mcp_*` | `remote_codebase_memory_mcp_search_graph` |
| `codebase-memory-mcp`（仅 Mode 0）| `codebase_memory_mcp_*` | `codebase_memory_mcp_search_graph` |

> ⚠️ 各 prompt 文档中的 `codebase_memory_mcp.*` 调用示例均为**概念性写法**，实际调用时替换为 `mcp_provider` 对应的前缀。

## 5. 硬终止与用户指引

远端路径任何一步失败都**硬终止**（不降级 LSP、不回退本地），并输出统一指引：

| 情形 | 条件 | 终止信息要点 |
|------|------|-------------|
| 1 | 远端 MCP 不可用（list_projects 调不通）| ⛔ 远端知识图谱 MCP 不可用 |
| 2 | 项目未在远端索引（项目名匹配不到）| ⛔ 本项目未在远端索引 |
| 3 | 远端索引非 ready（indexing / 异常）| ⛔ 远端索引未就绪 |
| 4 | 图谱必然过时（有未推送 commit / 无 upstream，见 environment-check §4a）| ⛔ 远端图谱必然落后于本地代码（远端看不到未推送内容）|

统一指引模板（附在终止信息后）：

```
本流程仅使用远端图谱，不自动回退本地。请二选一：
  1) 修复远端后在远端 codebase-memory-mcp 服务端索引/刷新本项目，再重试；
  2) 显式触发 Mode 0（Dev Preflight / 本地模式）使用本地图谱。
```

## 6. 会话记录

```
mcp_provider = "remote-codebase-memory-mcp"    # Mode 1-5；Mode 0 时为 "codebase-memory-mcp"
mcp_provider_type = "remote"                   # 或 "local"（仅 Mode 0）
project_name_in_graph = "home-demo-utest-skills"
mode_0_active = false                          # 仅用户显式触发 Mode 0 时为 true
```

- `mcp_provider` / `mcp_provider_type`：解析结果，全程只读。
- `mode_0_active`：仅 Mode 0 置 true（见 `references/dev-preflight.md`）。
- **无 fallback_* 字段**——不存在运行时回退，无需记录回退状态。

## 7. 深入参考

本文档是提供方解析的唯一权威来源。如需了解 codebase-memory-mcp 各工具的完整语义、参数与调用样例（search_graph / query_graph / trace_path / get_code_snippet / index_repository 等），`Read references/codebase-memory-guide.md`。本地模式的完整流程（探测、安装、索引同步）见 `references/dev-preflight.md`。
