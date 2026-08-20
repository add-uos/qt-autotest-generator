# MCP 提供方解析（Provider Resolution）

> 本技能支持两种知识图谱 MCP 提供方：**远端优先**，**本地兜底**。全流程互斥使用其一，绝不混用。
> 单一权威文档：所有 phase 均以本文档为提供方解析的唯一依据。

## 1. 背景

本技能依赖 codebase-memory-mcp 知识图谱。该图谱有两种部署形态：

| 形态 | MCP 名称 | 能力 |
|------|----------|------|
| 远端 | `remote-codebase-memory-mcp` | 只读查询（search_graph / query_graph / trace_path / get_code_snippet / index_status / list_projects 等）。**不可触发 index_repository**——项目必须已在远端索引好 |
| 本地 | `codebase-memory-mcp` | 查询 + 索引（含 index_repository / index_status / ingest_traces）。可为本机项目建立索引 |

两者暴露的工具表面基本一致（仅前缀不同：`codebase_memory_mcp_*` vs `remote_codebase_memory_mcp_*`），但**远端无法触发索引**是关键差异。

## 2. 解析优先级（HIGHEST first）

1. **`remote-codebase-memory-mcp`**（远端/外部）—— 优先
2. **`codebase-memory-mcp`**（本地）—— 兜底

**互斥原则**：一旦解析到远端提供方，全流程不再触碰本地 MCP（不安装、不索引、不查询）。反之，若远端不可用或目标项目未在远端索引，才走本地路径。

## 3. 解析算法

`environment_check` 阶段在 Step 1 执行提供方解析。算法如下：

```
输入: project_path（目标项目绝对路径）

# ── 候选提供方，按优先级降序 ──
candidates = [
    { name: "remote-codebase-memory-mcp", type: "remote", can_index: false },
    { name: "codebase-memory-mcp",        type: "local",  can_index: true  },
]

resolved_provider = null
remote_attempted = false   # 标记是否尝试过远端（用于决定是否提醒用户）

FOR candidate IN candidates:
    # Step A: 探测可用性（能否调通 list_projects）
    IF NOT probe_available(candidate):
        IF candidate.type == "remote":
            remote_attempted = true   # 远端不可用
        CONTINUE

    # Step B: 远端提供方需额外确认目标项目已索引且 ready
    IF candidate.type == "remote":
        remote_attempted = true
        project_name = find_project_by_root_path(candidate, project_path)
        IF project_name IS None:
            # 远端未索引该项目 → 跳过远端
            CONTINUE
        status = candidate.index_status(project=project_name)
        IF status != "ready":
            # 远端正在索引或异常 → 跳过远端
            CONTINUE

    # Step C: 候选通过所有检查，解析为此提供方
    resolved_provider = candidate
    BREAK

# ── 回退到本地时的提醒逻辑 ──
IF resolved_provider IS NOT None AND resolved_provider.type == "local" AND remote_attempted:
    # 远端尝试过但不可用/项目未索引，回退到已安装的本地 → 提醒"使用"本地
    REMIND_USER("远端不可用/未索引本项目，将使用本地 codebase-memory-mcp")

IF resolved_provider IS None:
    # 两个候选都不可用 → 提醒"安装"本地
    FORCE_REMIND_USER("未发现可用的远端知识图谱 MCP，将安装本地 codebase-memory-mcp")
    # 运行 setup-codebase-memory.sh；成功后 resolved_provider = local
    # 安装仍失败 → 硬终止
```

### 3.1 探测可用性（probe_available）

对一个候选提供方，按下列方式探测是否可用：

```python
def probe_available(candidate):
    try:
        # list_projects 是最轻量的可用性探针
        result = candidate.list_projects()
        return True
    except Exception:
        return False
```

- 探测失败（MCP 未连接 / 工具调用抛错）→ 候选不可用，继续下一个。
- 对远端提供方，`list_projects` 返回空列表不算不可用（远端可能还没索引任何项目，但服务在线）。**但**如果远端可用却无任何项目，仍需继续判断目标项目是否已索引。

### 3.2 远端项目已索引判定

远端 MCP 的 `list_projects()` 返回的项目列表中，用 `root_path` 字段匹配 `project_path`。匹配不到则视为"远端未索引该项目"。

```
remote_projects = remote_provider.list_projects()
matched = [p for p in remote_projects if p.root_path == project_path]
if not matched:
    # 远端无法 index_repository → 远端不可用于该项目
    skip remote, continue to local
```

## 4. 提供方与工具前缀映射

解析到的提供方决定实际调用的工具前缀：

| resolved_provider | 工具前缀 | 示例 |
|-------------------|---------|------|
| `remote-codebase-memory-mcp` | `remote_codebase_memory_mcp_*` | `remote_codebase_memory_mcp_search_graph` |
| `codebase-memory-mcp` | `codebase_memory_mcp_*` | `codebase_memory_mcp_search_graph` |

> ⚠️ 各 prompt 文档中的 `codebase_memory_mcp.*` 调用示例均为**概念性写法**，实际调用时替换为 `mcp_provider` 对应的前缀。

## 5. 会话记录

解析结果记录为内存变量：

```
mcp_provider = "remote-codebase-memory-mcp"
mcp_provider_type = "remote"
project_name_in_graph = "home-demo-utest-skills"
```

- `mcp_provider`：解析到的 MCP 名称（如 `remote-codebase-memory-mcp` 或 `codebase-memory-mcp`）
- `mcp_provider_type`：`remote` 或 `local`
- 后续所有 prompt 从内存变量读取 `mcp_provider`，用对应前缀调用工具

## 6. 强制提醒规则

当远端提供方不可用或目标项目未在远端索引时，`environment_check` 必须向用户输出醒目提醒。提醒内容区分「安装本地」与「使用已安装本地」两种情况：

| 条件 | 本地状态 | 提醒内容 |
|------|---------|---------|
| 远端 MCP 可用但目标项目未在远端索引，回退本地 | 已安装 | ⚠️ 远端未索引本项目，将使用本地 codebase-memory-mcp |
| 远端 MCP 可用但目标项目未在远端索引，回退本地 | 未安装 | ⚠️ 远端未索引本项目，将安装本地 codebase-memory-mcp |
| 远端 MCP 不可用 | 已安装 | ⚠️ 未发现可用的远端知识图谱 MCP，将使用本地 codebase-memory-mcp |
| 远端 MCP 不可用 | 未安装 | ⚠️ 未发现可用的远端知识图谱 MCP，将安装本地 codebase-memory-mcp |

提醒格式（必须输出到对话）：

```
⚠️ [MCP 提供方解析] <具体原因>。
本次将<安装/使用>本地知识图谱 MCP（codebase-memory-mcp）。
若需使用远端图谱，请在远端 codebase-memory-mcp 服务端先索引本项目，
并在 MCP 客户端配置中接入该远端实例。
```

## 8. 深入参考

本文档是提供方解析的唯一权威来源。如需了解 codebase-memory-mcp 各工具的完整语义、参数与调用样例（search_graph / query_graph / trace_path / get_code_snippet / index_repository 等），`Read references/codebase-memory-guide.md`。
