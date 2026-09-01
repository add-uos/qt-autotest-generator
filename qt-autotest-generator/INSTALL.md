# 安装说明

本技能遵循 AgentSkills 常见布局：仓库根目录即技能根目录，内含 `SKILL.md`。

## opencode

在 **git 仓库根目录** 下安装：

```bash
mkdir -p .opencode/skills
git clone <本仓库 URL> .opencode/skills/qt-autotest-generator
```

或使用本地路径复制到 `.opencode/skills/qt-autotest-generator`。

运行时环境通常会设置 **`SKILL_DIR`** 指向该技能目录；`SKILL.md` 中的 `${SKILL_DIR}/templates/...` 和 `${SKILL_DIR}/references/...` 即解析到此路径。

## Claude Code

```bash
mkdir -p .claude/skills
git clone <本仓库 URL> .claude/skills/qt-autotest-generator
```

运行时环境通常会设置 **`CLAUDE_SKILL_DIR`** 指向该技能目录。

## Cursor

Cursor 支持 Agent Skills 约定：每个技能是一个子文件夹，内含根级 `SKILL.md`（`name` 字段须与文件夹名一致，本仓库为 `qt-autotest-generator`）。

### 用户主目录（全局）

| 系统 | 推荐路径 |
|------|----------|
| Windows | `%USERPROFILE%\.cursor\skills\qt-autotest-generator\` |
| macOS / Linux | `~/.cursor/skills/qt-autotest-generator/` |

```bash
mkdir -p ~/.cursor/skills
git clone <本仓库 URL> ~/.cursor/skills/qt-autotest-generator
```

### 项目目录（仅当前仓库）

将本技能放在当前工作区下的：

`<项目根>/.cursor/skills/qt-autotest-generator/`

## 必需依赖安装

### Ubuntu / Debian

```bash
# CMake + 编译器
sudo apt install cmake build-essential

# Qt5
sudo apt install qtbase5-dev

# 或 Qt6
sudo apt install qt6-base-dev

# Google Test
sudo apt install libgtest-dev
cd /usr/src/gtest && sudo cmake . && sudo make && sudo mv lib/libgtest* /usr/lib/

# Python 3
sudo apt install python3

# 可选：覆盖率
sudo apt install lcov
```

### codebase-memory-mcp（知识图谱）

本技能支持两种知识图谱 MCP 提供方，**互斥使用，不自动回退**：

| 提供方 | 说明 |
|--------|------|
| `remote-codebase-memory-mcp` | 远端 MCP，Mode 1-5 的**唯一**提供方。只读查询，**远端无法触发索引**，项目须已在远端索引且 ready；图谱过时（工作区 dirty / 有未推送 commit）时硬终止并指引 |
| `local-codebase-memory-mcp` | 本地 MCP，**仅经 Mode 0（Dev Preflight）显式使用**：探测/安装、为本机项目建立索引并同步到本地 HEAD |

提供方解析在 `environment_check` 阶段完成（Mode 0 则在 `references/dev-preflight.md`），
结果记录为内存变量 `mcp_provider`，详见 `references/mcp-providers.md`。

本地开发（有未 push 代码 / 工作区有未提交改动）请显式触发 Mode 0（说「dev preflight /
本地模式」），技能会安装并使用本地 `local-codebase-memory-mcp`：

```bash
bash scripts/setup-codebase-memory.sh
```

### 验证安装

```bash
# 验证 codebase-memory-mcp
codebase-memory-mcp --version  # >= 0.8.0

# 验证 GTest
pkg-config --modversion gtest  # 应有输出

# 验证 Qt
qmake --version  # 或 qmake6 --version
```

## 可选依赖

| 依赖 | 安装 | 说明 |
|------|------|------|
| lcov | `sudo apt install lcov` | 代码覆盖率 HTML 报告 |
| ccache | `sudo apt install ccache` | 加速重复编译 |
| AddressSanitizer | gcc 内置 | 编译时加 `-fsanitize=address` |

## GitHub 网络受限时设置代理

```bash
# 设置代理（按实际环境修改）
export https_proxy="${QTAG_PROXY:-http://proxy02.uniontech.com:3128}"
export http_proxy="${QTAG_PROXY:-http://proxy02.uniontech.com:3128}"
```

远端 codebase-memory-mcp 若走公网同样受此代理影响；本地 codebase-memory-mcp 不受影响。

## 常见问题

### GTest 找不到

```
CMake Error: Could not find GTest
```

解决：确认 `libgtest-dev` 已安装且编译了库文件（见上方「Google Test」节）。

### codebase-memory-mcp 索引不 ready

```
index_status 返回 "indexing" 超过 60 秒
```

解决：手动推一下 `codebase-memory-mcp index_repository --repo-path <path> --mode fast`，等待 ready。

### Qt 模块缺失

```
Could not find Qt6::Widgets
```

解决：`sudo apt install qt6-base-dev`（Qt5 用 `qtbase5-dev`）。

### stub-shadow.cpp 链接错误

```
undefined reference to stub_ext::freeWrapper
```

解决：确认 `{test_dir}/3rdparty/stub/stub-shadow.cpp` 已编入 test target（CMakeLists 检查）。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `QTAG_MCP_URL` | `http://10.8.12.80:13626/mcp` | 远端 MCP HTTP 端点，`mcp-scan.py` 使用 |
| `QTAG_CBM_INSTALL_URL` | GitHub 官方 install.sh | codebase-memory-mcp 安装脚本 URL，内网可设为镜像 |
| `QTAG_PROXY` | `http://proxy02.uniontech.com:3128` | HTTP/HTTPS 代理地址 |
| `QTAG_GIT_EMAIL` | `autotest@uniontech.com` | 自动提交 git 回退邮箱 |
| `CBM_INSTALL_SHA256` | _(空)_ | install.sh SHA256 校验值，设置后启用完整性校验 |
