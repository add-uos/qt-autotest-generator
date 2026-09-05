# 环境门禁

> 前置条件：目标项目绝对路径（`project_path`）已就绪。

> 通过 GitNexus 代码图谱 MCP 获取符号定位（详见 references/mcp-providers.md / gitnexus-guide.md）

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

### 1. GitNexus 索引确认（单栈，Mode 0 除外）

**完整规则（路由表、硬终止指引模板）见 [`references/mcp-providers.md`](mcp-providers.md)，该文档为单一权威来源。** 本步骤只列执行要点：

**Mode 0**：`mode_0_active == true` 时本步骤整体跳过（索引确认与漂移检查已由 `references/dev-preflight.md` 完成）。

**Mode 1-5**：GitNexus 是唯一图谱后端（`cypher` / `list_repos` / `context`），仓库由平台统一索引，本流程**不能触发索引、不回退文件扫描**。

**执行步骤**：

1. **探测端点**：`list_repos` 调不通 → 硬终止情形 1（MCP 不可用）。
2. **项目名匹配**：用项目名（路径最后一段）匹配 `list_repos` 返回的 `name`（分页遍历，limit ≤ 200），命中不到 → 情形 2（未索引）。
3. **记录 lastCommit**：命中即取 `lastCommit`（图谱基线，后续 base_sha 用）。
4. **任一失败 → 硬终止**：按 `mcp-providers.md` §3 输出统一指引。mcp-scan.py `open_adapter` 已内建（未索引 `SystemExit(2)`）。

### 2. 确认项目已索引

查询已索引仓库列表，按**项目名**（路径最后一段）匹配找到目标项目：

```python
import os
repos = list_repos_pages()   # 分页遍历（limit ≤ 200）：{"name","lastCommit","branch","indexedAt"}
project_basename = os.path.basename(project_path.rstrip('/'))
target = next((r for r in repos if r["name"] == project_basename), None)
project_name = target["name"] if target else None
graph_last_commit = target["lastCommit"] if target else None
```

**不要自己拼**仓库名，必须从 `list_repos` 匹配取 `name`。

### 3. 索引状态与漂移检查（check_drift）

GitNexus 无 `index_status` / 本地索引；图谱基线 = `list_repos.lastCommit`。
漂移 = 本地 HEAD 与 lastCommit 的差异（mcp-scan.py `check_drift` 内建，fetch 前自动警告）：

```python
local_head = git("-C", repo_root, "rev-parse", "HEAD")
drift = check_drift()   # lastCommit vs local_head
```

| 场景 | 处理 |
|------|------|
| `local_head == lastCommit` | 图谱即最新，继续 |
| 本地领先（未 push commit / dirty） | 图谱看不到这些代码——**列出受影响文件**（`git log --name-only lastCommit..HEAD`）：涉及待测模块 → 硬终止等待平台同步；仅无关文件 → 带警告继续（方法体以本地切片为准） |
| 本地落后 | 提示拉取最新代码（切片行号以本地为准，落后会错位） |
| 分支不一致（本地 ≠ list_repos 的 `branch`） | 硬终止：图谱关系网与本地分支不同源，CALLS/继承可能失真 |

**注意**：
- GitNexus 无本地索引可补，"等待平台同步"是唯一收敛途径；不等只能确认漂移范围不涉及待测代码
- 工作区 dirty 不再一票否决：方法体一律本地切片，dirty 改动即刻反映在切片中；漂移警告由 check_drift 承担
- Mode 0 激活时（`mode_0_active == true`）跳过提供方解析，但漂移检查照常执行

### 5. 验证图谱可用性

最小验证查询，确认图谱非空：

```python
rows = cypher("MATCH (c:Class) RETURN count(c) AS c", repo=project_name)  # repo 参数必带
if rows[0]["c"] == 0:
    # 图谱为空（平台索引异常），硬终止
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

- 全流程只用 GitNexus 单栈（`cypher` / `list_repos` / `context`），无提供方切换
- 不触发索引（GitNexus 由平台索引，技能侧无索引能力）
- 图谱不可用即硬终止，不降级到 LSP
- 必须确认项目在 `list_repos` 在册且图谱非空；`lastCommit` 记为图谱基线
- 不修改项目源码
- 项目名必须从 `list_repos` 匹配取 `name`，不自己拼
