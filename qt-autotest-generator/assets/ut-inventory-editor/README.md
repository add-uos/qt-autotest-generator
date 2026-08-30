# UT Inventory Editor & Dashboard

单文件 HTML 的 UT（单元测试）可测性清单编辑器 + 多项目集中看板。

**独立运行，无外部依赖**（Python 仅标准库；HTML 引用 CDN 的 lucide/hljs/tailwind）。

## 目录结构

```
ut-inventory-editor/
├── index.html              # 单文件应用（编辑器 + 看板，纯显示层）
├── dashboard-plan.md       # 设计方案
├── scripts/
│   ├── dashboard-server.py # 伴随服务：托管 HTML + 调脚本刷新数据
│   ├── batch-collect.py    # 批量编排 26 个项目
│   ├── fetch-mcp-data.py   # MCP → .ut-inventory.json（依赖 scan-inventory.py）
│   ├── fetch-test-mapping.py # MCP CALLS → test-mapping.json（测试覆盖）
│   └── scan-inventory.py   # 评分逻辑（被 fetch-mcp-data.py 动态加载）
└── mcp-projects/           # 数据目录（首次「刷新」后自动生成）
    ├── _summary.json
    └── <项目名>/
        ├── .ut-inventory.json
        ├── test-mapping.json
        └── collect.log
```

## 快速开始

```bash
cd scripts
python3 dashboard-server.py          # 默认 http://localhost:8765
# 浏览器打开 http://localhost:8765/
```

1. **[📋 编辑器]** — 打开单个 `.ut-inventory.json`（拖拽/文件选择），评审 level、
   双击函数开 GitHub 右侧面板定位源码、点测试用例开左侧面板看测试文件
2. **[📊 看板]** — 首次使用点右上 **「🔄 刷新」**：
   server 后台调用 `batch-collect.py` 从 MCP（`10.8.12.80:13626`，可用环境变量
   `QTAG_MCP_URL` 覆盖）逐项目拉取数据 → 26 张项目卡渐进亮起
3. 点卡片下钻：Level 饼图 + 高优无覆盖 Top10（点条目直达 GitHub 源码）
   → 「在编辑器中打开」自动加载该 inventory

## 运行模式

| 模式 | 探测 | 能力 |
|---|---|---|
| ● server | `GET /api/status` 2xx | 刷新调脚本、下钻加载、全部功能 |
| ○ 静态 | 直接双击 index.html | 降级：手动「📥 导入」JSON（离线缓存兜底） |

## 命令行（不开看板时）

```bash
cd scripts
python3 batch-collect.py                  # 全量收集 26 项目
python3 batch-collect.py --filter camera  # 只收集匹配项目
python3 batch-collect.py --skip-fetch-mcp # 增量：已有 inventory 跳过 MCP 拉取
python3 fetch-mcp-data.py <项目>          # 单项目 MCP → inventory
```

## API（dashboard-server.py）

| 端点 | 说明 |
|---|---|
| `GET /api/status` | server/MCP/数据目录状态 |
| `POST /api/sync` | 后台调 batch-collect（防重入），返回 task_id |
| `GET /api/task/<id>` | 进度 {state, done_n/total_n, current, log_tail} |
| `GET /api/projects` | 26 项目聚合统计 + 高优缺口 Top10 |
| `GET /api/inventory/<name>` | 单项目完整 inventory |
| `GET /api/mapping/<name>` | test-mapping.json |

仅绑定 `127.0.0.1`，`--port` 可改；前端可配 server 地址
（localStorage `utie-server-url`）。

## 项目分支表（v1.2 新增）

GitHub 链接不再硬编码 `main`，每个项目自带分支，默认 `master`：

```
scripts/project-branches.json   # 77 项目分支表（CSV + downloader 整合产物）
scripts/gen-project-info.py     # 重新生成上表（可重跑）
```

- **生成时机注入**：`fetch-mcp-data.py` 写 `.ut-inventory.json` 时自动查表，
  新增 `git: {org, remote, branch, branch_source}` 字段（可用 `--branch/--org` 覆盖）
- **旧数据兼容**：`dashboard-server.py /api/projects` 对无 `git` 字段的旧 inventory
  自动从表补齐，无需重新生成
- **前端优先级**：手动覆盖（状态栏 🌿 点击）> inventory.git.branch > 分支表 > master
- 分支来源在状态栏 tooltip 与看板抽屉 `🌿` 徽标中可见
- 表数据源：`~/debug/product_info_*.csv`（62 项）+ downloader `PROJECT_REPOS`（44 项）

重新生成分支表：
```bash
python3 scripts/gen-project-info.py          # 默认取最新 CSV
python3 scripts/gen-project-info.py --csv /path/to/new.csv
```

## P1 配置化重构（v2.0）

前端拆分为**零构建经典 script 模块**（file:// 与 server 模式都可用）：

```
index.html          页面壳（标记 + 模块引用）
styles.css          全部样式
js/core.js          全局状态 S / 工具 / 分支与视图切换
js/editor.js        inventory 编辑器
js/github.js        GitHub 面板
js/dashboard.js     多项目看板
js/settings.js      配置管理界面
js/app.js           boot 启动入口
config.json         全局配置（端口 / MCP 地址 / org / 并发）
projects.json       项目注册表（权威数据源，26 项目）
scripts/sync-registry-from-mcp.py   从 MCP list_projects 同步注册表（新增/规模/分支）
```

**注册表是唯一数据源**（无内置兜底）：batch-collect 同步清单、fetch-mcp-data 的
git 注入、server /api/projects 分支合并全部只读注册表。

**从 MCP 同步项目**：设置页「🔄 从 MCP 同步」或 CLI `python3 scripts/sync-registry-from-mcp.py`。
- MCP `list_projects` 现有 40 个项目（注册表 40，26 启用）
- 规模按节点数推导：`<1K=S, <5K=M, <15K=L, ≥15K=XL`（可在同步时选"保留手工标注"）
- 分支取 MCP 真实 git 分支（如 deepin-anything → develop/snipe）
- 新增项目默认 **enabled=false**，在设置页勾选启用后才参与同步收集
- 已不在 MCP 的项目保留不动，报告中标注

**设置 tab**（第三个标签）：
- 全局设置：MCP 地址 / org / 端口（重启生效）/ 同步并发
- 项目注册表：增删行、enabled 开关、规模、分支、来源类型、**本地源码路径**、
  构建系统、自定义测试命令；🔍 按钮按本地路径**探测**构建系统（CMakeLists/pro/meson/Makefile）
- 本地路径栏带 **… 浏览按钮** → 目录浏览对话框（`GET /api/fs/list`）：🏠 主目录 / ⬆ 上级 /
  路径回车跳转 / 隐藏项开关（记忆偏好）/ ✅ 选定写回并自动把来源切为 local
- 保存写回 `config.json` / `projects.json`（自动 .bak 备份，路径不存在的项目会被拒绝）

新增 API：`GET /api/config`、`POST /api/config/global`、`POST /api/config/projects`、
`POST /api/config/detect`、`GET /api/fs/list`。

> 测试运行（P2）预留：注册表 `build` 字段即运行配置，`source.path` 指向本地源码，
> 全部 GTest + ctest 默认模板，custom 命令兜底。
