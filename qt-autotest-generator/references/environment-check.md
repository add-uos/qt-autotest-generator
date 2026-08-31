# 环境门禁

> 前置条件：目标项目绝对路径（`project_path`）已就绪。

> 通过 mcp_provider 调用知识图谱工具（详见 references/mcp-providers.md）

## 概述

确认知识图谱 MCP 已就绪、目标项目已索引、索引处于 ready 状态。**失败即硬终止**，不降级 LSP，不继续后续阶段。

> 系统级依赖（CMake ≥ 3.16、Qt5/Qt6、Google Test、lcov、c++filt、git、支持 C++17 的 gcc/g++）清单见 `requirements.txt`（纯声明文件，无 pip 包）。本阶段聚焦 MCP 门禁，系统依赖由后续编译步骤隐式校验（缺失时 cmake/build 报错即定位）。

## 工作步骤

### 0. 验证项目类型

确认目标项目根目录存在 `CMakeLists.txt`：

```bash
if [ ! -f "${project_path}/CMakeLists.txt" ]; then
    # 硬终止：非 CMake 项目
    exit 1
fi
```

### 0a. 探测测试目录

确定测试代码存放目录（`autotests/` 或 `tests/`），结果写入 `test_dir`：

```
1. 检查项目根下是否存在 autotests/ 目录
   → 存在 → test_dir = "autotests"
2. 若 autotests/ 不存在，检查是否存在 tests/ 目录且含 C++ 测试代码
   → tests/ 存在且含 CMakeLists.txt 或含 #include <gtest/ 的 .cpp 文件 → test_dir = "tests"
3. 两者都不存在 → test_dir = "autotests"（默认创建）
```

**判定规则**：
- `autotests/` 优先：若已存在，直接使用
- `tests/` 沿用条件：目录存在 **且** 含 GTest C++ 测试代码（检测到 `CMakeLists.txt` 或 `.cpp` 文件含 `#include <gtest`）；含 `#include <QtTest>` 或 Catch2 的目录不沿用，应创建 `autotests/` 代替
- 空 `tests/` 目录（无测试代码）→ 仍用 `autotests/`
- 目录选择只在本次探测确定，后续全流程从 `test_dir` 读取，不再重新判定

将 `test_dir` 值写入 session（见 Step 6）。

### 1. MCP 提供方解析（远端唯一，Mode 0 除外）

**完整规则（路由表、硬终止指引模板）见 [`references/mcp-providers.md`](mcp-providers.md)，该文档为单一权威来源。** 本步骤只列执行要点：

**Mode 0**：`mode_0_active == true` 时本步骤整体跳过（本地提供方已由 `references/dev-preflight.md` 锁定并同步）。

**Mode 1-5**：`remote-codebase-memory-mcp` 是唯一候选，只读查询、不可触发索引。
本地图谱仅经 Mode 0 进入，本流程**不安装、不回退本地**（`mcp-providers.md` §2）。

**执行步骤**：

1. **探测远端**：`remote_codebase_memory_mcp.list_projects()` 调不通 → 按硬终止情形 1 处理。
2. **项目名匹配**：用项目名（路径最后一段）匹配远端 `list_projects()` 返回的 `root_path` 最后一段，命中不到 → 情形 2。
3. **确认 ready**：`index_status(project=...) == "ready"`，否则 → 情形 3。
4. **任一失败 → 硬终止**：按 `mcp-providers.md` §5 输出统一指引（修复远端索引后重试，或显式触发 Mode 0）。不降级 LSP，不安装本地。

### 2. 确认项目已索引

用解析到的提供方查询已索引项目列表，按**项目名**（路径最后一段）匹配找到目标项目：

```python
import os
provider = resolved_provider  # "remote-codebase-memory-mcp" 或 "codebase-memory-mcp"
projects = provider.list_projects()
project_basename = os.path.basename(project_path.rstrip('/'))
target = next((p for p in projects if os.path.basename(p.root_path.rstrip('/')) == project_basename), None)
project_name = target.name if target else None
```

项目名规则：把 repo 绝对路径的 `/` 转成 `-`，例如 `/home/user/my-qt-app` → `home-user-my-qt-app`。
**不要自己拼**，必须从 `list_projects` 匹配取 `name`。

### 3. 首次索引（已并入 Mode 0）

> 首次索引仅发生在 Mode 0 路径（`references/dev-preflight.md` Step 3b）。
> Mode 1-5 远端唯一：远端不可索引，项目未索引在 Step 1 已硬终止；本地 MCP 不在本流程安装/索引。

### 4. 等待索引 ready

索引是异步的，`index_repository` 返回后 daemon 还需几秒构建：

```python
import time
max_wait = 300  # 硬超时 300 秒（5 分钟）
start = time.time()
while True:
    elapsed = time.time() - start
    if elapsed > max_wait:
        # 硬终止：远端索引 5 分钟未 ready
        print("[FATAL] 远端索引 5 分钟未 ready，请手动刷新远端，或显式触发 Mode 0 使用本地图谱")
        break
    status = provider.index_status(project=project_name)
    if status.status == "ready":
        break
    elif status.status == "indexing":
        time.sleep(2)
    else:
        break
```

**超时处理**（仅本地提供方即 Mode 0 路径，远端已在 Step 1 确认 ready 跳过此处）：等待超过 60 秒仍未 ready → `index_repository(mode="fast")` 推一下；再等 30 秒仍不 ready → **硬终止**。

> 远端提供方已在 Step 1 确认 ready，Step 4 整体跳过。

### 4a. Freshness 检查（仅远端提供方）

> 仅当 `mcp_provider_type == "remote"` 且 `mode_0_active == false` 时执行。
> 远端图谱从远端 git 仓库同步，**看不到本地未 push / 未提交代码**——这是结构性能力边界，不是可等待自愈的瞬态。

```python
if mcp_provider_type == "remote" and not mode_0_active:
    # 1) 未推送检测（定论性：远端必然没有这些代码）
    up = git("-C", project_path, "log", "@{upstream}..HEAD", "--oneline")
    if up.failed or up.stdout.strip():
        n = "无远程追踪分支" if up.failed else f"{len(up.stdout.strip().splitlines())} 个未推送 commit"
        HARD_FATAL("[FATAL] 远端图谱必然落后于本地代码（" + n + "）。" + 指引模板(mcp-providers.md §5))
        # 不回退本地；用户须 push 后等远端同步，或显式触发 Mode 0

    # 2) 已全部推送 → 用远端元数据直比（无需间接推断）
    local_head = git("-C", project_path, "rev-parse", "HEAD").stdout.strip()
    remote_head = <远端 list_projects 中本项目的 git.head_sha>   # 远端提供方携带 git 元数据
    # local_head == remote_head → 图谱 fresh，继续
    # 不等 → watcher 延迟，落入 reconcile 的索引等待逻辑（有界）
```

**注意**：
- 有未推送 commit（或无 upstream）→ **立即硬终止**：远端永远看不到这些代码，等待无意义。指引二选一：push 后等待远端同步重试；或显式触发 Mode 0。**不回退本地**（`mcp-providers.md` §2）
- 本步骤不使用 `get_graph_head_sha` 间接推断——那是本地 MCP 无 git 元数据时的手段，仅 Mode 0 使用
- Mode 0 激活时（`mode_0_active == true`）跳过本检测——Mode 0 已确保本地 fresh

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

初始化或更新 `{test_dir}/.ut-inventory.json`（若不存在则创建空表）：

```json
{
  "version": 1,
  "project": "<project_name>",
  "base_sha": "<git rev-parse HEAD>",
  "gate_thresholds": {
    "high": { "line": 90, "branch": 80, "function": 100 },
    "mid": { "line": 60, "branch": 0, "function": 100 },
    "low": { "line": 60, "branch": 0, "function": 100 }
  },
  // ⚠️ 以上为默认值。gate_thresholds 由外部确定，已有 inventory 时从 inventory 读取，不覆盖
  "scope_rules": [],
  "methods": [],
  "review_queue": []
}
```

获取 baseline_commit：

```bash
git -C <project_path> rev-parse HEAD
```

## 关键约束

- 全流程只用 `mcp_provider` 记录的那一个提供方，不混用
- 不对远端 MCP 调用 `index_repository`（远端不可索引，只能查询）
- 图谱不可用即硬终止，不降级到 LSP
- 必须确认 `index_status == "ready"` 且图谱非空
- 不修改项目源码
- 项目名必须从 `list_projects` 匹配取 `name`，不假设（远端按路径最后一段匹配，本地按全路径匹配）
