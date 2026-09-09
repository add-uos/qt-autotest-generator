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

### GitNexus 代码图谱 MCP（唯一数据面）

本技能基于 **GitNexus 代码图谱 MCP 单栈**，无本地索引概念：仓库由平台统一索引，技能侧不能自行触发索引。

- 端点/认证经环境变量配置（见下方环境变量表），`mcp-scan.py` 读取；命令行 `--mcp-url` 可覆盖端点。
- **单一来源不回退**：端点不可用或项目未索引 → 硬终止并给出指引（详见 `references/mcp-providers.md` §3），不降级 LSP / 文件扫描。
- 图谱从远端 git 同步，看不到本地未 push/未提交代码；本地 HEAD 领先图谱 lastCommit 即漂移，硬终止并等待平台同步。

### 验证安装

```bash
# 验证 GitNexus 连通性与项目索引（端点不通/未索引时 SystemExit(2) 并输出指引；
# file-pattern 最小化采集作轻量探测）
python3 scripts/mcp-scan.py fetch --project <仓库名> --file-pattern 'zz_probe_*' -o /tmp/gn-probe.json

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

远端 GitNexus 端点若走公网同样受此代理影响。

## 常见问题

### GTest 找不到

```
CMake Error: Could not find GTest
```

解决：确认 `libgtest-dev` 已安装且编译了库文件（见上方「Google Test」节）。

### GitNexus 报项目未索引 / 图谱漂移

```
SystemExit(2): 项目未在 GitNexus 平台索引
```

解决：GitNexus 由平台统一索引，技能侧无法自行触发。确认仓库名与平台登记一致，联系平台管理员；本地 HEAD 领先图谱时 push 后等待平台同步（详见 `references/mcp-providers.md` §3）。

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
| `QTAG_MCP_URL` | `https://codegraph.uniontech.com/api/mcp` | GitNexus MCP HTTP 端点，`mcp-scan.py` 使用 |
| `QTAG_MCP_HEADERS` | 内置 Basic 认证头 | 额外请求头（JSON 字符串），与 `QTAG_MCP_API_KEY` 二选一 |
| `QTAG_MCP_API_KEY` | _(空)_ | `X-API-Key` 认证头 |
| `QTAG_PROXY` | `http://proxy02.uniontech.com:3128` | HTTP/HTTPS 代理地址 |
| `QTAG_GIT_EMAIL` | `autotest@uniontech.com` | 自动提交 git 回退邮箱 |
