# 项目准备

> 前置条件：收到仓库地址（`repo_url`）和分支名（`branch`）。

## 概述

从用户提供的仓库地址和分支名拉取代码到独立 worktree，校验基线，安装构建依赖，验证项目可编译。此阶段只做项目准备，不生成测试、不修改源码、不跑测试。

## 工作步骤

### 1. 推断项目名

从 `repo_url` 提取项目名：

```bash
# https://github.com/foo/bar.git → bar
# git@github.com:foo/bar.git → bar
project_name=$(basename "$repo_url" .git)
```

### 2. 拉取代码

> **注意**：必须新建独立 worktree，禁止复用旧目录、禁止浅克隆。

**主路径**：

```bash
WT=~/UT/worktrees/${project_name}-$(date +%s)
mkdir -p "$(dirname "$WT")"
git clone --no-checkout "$repo_url" "$WT"
cd "$WT"
git checkout "$branch"
```

成功 → 记录 `拉取方式=git_clone`，继续。
失败 → fallback。

**fallback（bare 缓存 + worktree）**：

```bash
BARE=~/UT/${project_name}.git
[ -d "$BARE" ] || git clone --bare "$repo_url" "$BARE"
git --git-dir="$BARE" fetch --prune origin 2>/dev/null || true
WT=~/UT/worktrees/${project_name}-$(date +%s)
mkdir -p "$(dirname "$WT")"
git --git-dir="$BARE" worktree add -B ut-${project_name}-run "$WT" "$branch"
```

**网络兜底**：github 拉取失败且无 `https_proxy` 时：

```bash
export https_proxy=http://proxy02.uniontech.com:3128 http_proxy=http://proxy02.uniontech.com:3128
```

重试一次（仅一次）。Gerrit 鉴权失败不重试。

**完成判定**（四条全中才进 §3）：
- WT 本次新建
- 分支匹配 `branch`
- 已记录拉取方式
- 失败不得旁路继续

### 3. 基线校验

```bash
cd "$WT"
[ -z "$(git status --porcelain)" ] || 停止报告"工作区不干净"
git log -1 --format='%h %s (%cd)' --date=short
```

记录基线：`<branch> @ <short-sha> "<title>" (<date>)`。

### 4. 项目类型校验

确认根目录存在 `CMakeLists.txt`：

```bash
[ -f "$WT/CMakeLists.txt" ] || 停止报告"非 CMake 项目，不支持"
```

从 `CMakeLists.txt` 读取：
- 项目名：`project(...)`
- Qt 版本：`find_package(Qt5/Qt6 ...)`
- C++ 标准：`CMAKE_CXX_STANDARD`（默认 17）
- 第三方依赖：DTK、boost、nlohmann_json、spdlog 等

### 5. 安装构建依赖

检查 `CMakeLists.txt` 中的 `find_package` 声明，安装缺失的系统依赖：

```bash
# 常见 Qt 项目依赖
dpkg -l | grep -q qtbase5-dev || sudo apt install -y qtbase5-dev
dpkg -l | grep -q libgtest-dev || sudo apt install -y libgtest-dev

# Qt6
dpkg -l | grep -q qt6-base-dev || sudo apt install -y qt6-base-dev

# DTK 依赖（按需）
dpkg -l | grep -q libdtkcore-dev || sudo apt install -y libdtkcore-dev
dpkg -l | grep -q libdtkwidget-dev || sudo apt install -y libdtkwidget-dev
```

检查其他依赖文件：
- `requirements.txt` → `pip install -r requirements.txt`
- `package.json` → `npm install`
- `go.mod` → `go mod download`

失败记录原因，不停止（部分依赖可能已预装）。

### 6. 验证构建环境

执行最小构建验证，确认项目可编译：

```bash
cd "$WT"
mkdir -p build-verify && cd build-verify
cmake .. -DCMAKE_BUILD_TYPE=Debug 2>&1
cmake --build . -j$(nproc) 2>&1
```

**构建失败处理**：
- 分析错误：缺依赖 → 回 §5 补装；CMake 语法错 → 记录不修；缺 Qt 模块 → 记录
- 重试 3 次仍失败 → 标记 `build_env=failed`，记录错误摘要，停止流程
- **不修改源码**，疑似源码缺陷只记录

> **注意**：构建验证失败时必须停止，不得继续后续阶段。

构建成功 → 删除 `build-verify/` 目录（不污染项目）：

```bash
cd "$WT" && rm -rf build-verify
```

### 7. 初始化 session 文件

在 `$WT/{test_dir}/.ut-session.json` 写入初始状态（`test_dir` 默认为 `autotests`，最终值由 environment_check 阶段探测确定）：

```json
{
  "project_path": "<WT 绝对路径>",
  "project_name_in_graph": null,
  "test_dir": "autotests",
  "repo_url": "<repo_url>",
  "branch": "<branch>",
  "baseline_commit": "<short-sha>",
  "baseline_date": "<date>",
  "baseline_title": "<commit-title>",
  "qt_version": null,
  "pull_method": "git_clone | git_worktree_fallback",
  "build_env": "verified | failed",
  "classes": [],
  "last_phase": "project_preparer",
  "overall_status": "incomplete"
}
```

## 关键约束

- 不修改源码：构建失败只记录原因，不修
- 每次拉取必须新建独立 worktree，禁止复用旧目录
- 禁止浅克隆（`--depth` 禁用），需要完整 git 历史做基线校验
- 必须确认项目可编译才进入后续阶段
- 构建验证只做 cmake + build，不跑 ctest
- 项目名从 repo_url 推断或从 CMakeLists.txt 读取，不假设
