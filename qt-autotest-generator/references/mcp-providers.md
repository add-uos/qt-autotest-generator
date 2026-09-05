# GitNexus 单栈说明（原 MCP 提供方解析）

> 旧 remote/local 双提供方已退役。GitNexus 代码图谱 MCP 是**唯一**图谱后端，
> 无本地索引概念：仓库由平台统一索引，技能侧不能自行触发索引。

## 1. 解析规则（无隐藏分支）

```
输入: project（GitNexus 仓库名，通常 = 仓库目录名）

# ── GitNexus 唯一候选 ──
IF NOT probe_available():                       # list_repos 调不通
    HARD_FATAL(§3 · 情形 1)

IF NOT find_repo(project):                      # list_repos 遍历分页匹配不到
    HARD_FATAL(§3 · 情形 2)                     # 平台未索引本项目

resolved = "gitnexus-mcp"
```

- **单一来源**：一个会话自始至终只用 GitNexus 端点，不存在运行时切换。
- **不回退**：端点不可用 / 项目未索引 → 一律**硬终止**并给出指引（§3）。
  不降级 LSP / 文件扫描。
- **结构边界**：GitNexus 从远端 git 同步，看不到本地未 push/未提交代码。
  本地 HEAD 领先图谱 lastCommit 即漂移（check_drift），只能等待平台同步（无本地索引可补）。

## 2. 连接配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `QTAG_MCP_URL` | `https://codegraph.uniontech.com/api/mcp` | MCP HTTP 端点 |
| `QTAG_MCP_HEADERS` | 内置 Basic 认证头 | 额外请求头（JSON 字符串） |
| `QTAG_MCP_API_KEY` | 空 | `X-API-Key` 认证头（与 HEADERS 二选一） |

mcp-scan.py 读取上述变量；命令行 `--mcp-url` 可覆盖端点。

## 3. 硬终止与用户指引

| 情形 | 条件 | 终止信息要点 |
|------|------|-------------|
| 1 | MCP 端点不可用（list_repos 调不通） | ⛔ GitNexus MCP 不可用 |
| 2 | 项目未索引（list_repos 匹配不到） | ⛔ 本项目未在 GitNexus 索引 |
| 3 | 图谱漂移（本地 HEAD 领先 lastCommit） | ⛔ 图谱落后于本地代码，等待平台同步（fetch 会警告，reconcile 按 diff 路由） |

统一指引模板（附在终止信息后）：

```
本流程仅使用 GitNexus 图谱，不降级文件扫描。请联系平台确认本项目已纳入 GitNexus 索引，
或等待索引同步完成后重试（可用 list_repos 查 lastCommit 判断同步进度）。
```

## 4. 会话记录

```
mcp_provider = "gitnexus-mcp"      # 唯一值
project = "dde-file-manager"       # GitNexus 仓库名（list_repos 中的 name）
mode_0_active = false              # 仅用户显式触发 Mode 0 时为 true
```

## 5. 深入参考

GitNexus 工具语义（`cypher` / `list_repos` / `context`）、cypher 方言限制、
mcp-scan.py 子命令与新旧工具映射对照：`Read references/gitnexus-guide.md`。
Mode 0 流程（list_repos 确认 + check_drift 漂移检查）见 `references/dev-preflight.md`。
