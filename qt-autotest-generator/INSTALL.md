# 安装说明

本技能遵循 AgentSkills 常见布局：仓库根目录即技能根目录，内含 `SKILL.md`。

## opencode

在 **git 仓库根目录** 下安装：

```bash
mkdir -p .opencode/skills
git clone <本仓库 URL> .opencode/skills/qt-autotest-generator
```

或使用本地路径复制到 `.opencode/skills/qt-autotest-generator`。

运行时环境通常会设置 **`SKILL_DIR`** 指向该技能目录；`SKILL.md` 中的 `${SKILL_DIR}/resources/...` 即解析到此路径。

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

### codebase-memory-mcp

首次使用时，技能的 `environment_check` subagent 会自动调用 `setup-codebase-memory.sh` 安装。也可手动预装：

```bash
bash resources/scripts/setup-codebase-memory.sh
```

退出码：
- `0` → 成功
- `1` → 安装失败
- `2` → 配置失败
- `3` → 验证失败

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
