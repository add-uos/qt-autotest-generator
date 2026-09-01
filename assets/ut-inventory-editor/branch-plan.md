# 项目远端/分支信息方案（branch-per-project）

## 背景
- 现状：GitHub 链接硬编码 `S.branch = localStorage['utie-branch'] || 'main'`，全局一个分支
- 目标：每个项目自带 **org/远端/分支**，生成 `.ut-inventory.json` 时注入；默认分支 `main` → **`master`**

## 数据源摸底（已完成）

| 来源 | 内容 | 与 26 项目交集 |
|---|---|---|
| `product_info_20260715143603.csv`（62 行） | 项目名/类型(Github,Gerrit)/分支；master×43、develop/*×10、uos×3、NA×4 | 命中 20：master×19 + deepin-scanner NA |
| downloader `PROJECT_REPOS` | 44 个 `github.com/linuxdeepin/<repo>.git` | 命中 19（与 CSV 基本重叠） |
| 两个来源都没有的 | deepin-shortcut-viewer、udisks2-qt6、com.deepin.gomoku、com.deepin.lianliankan、docparser | `gh api` 实测 5 个全部 `master` ✓ |

**结论：26 项目全部可确定分支；未命中来源的一律 fallback `master`（已实测正确）。**

## 目标数据文件：`scripts/project-branches.json`

```json
{
  "_meta": {"generated": "2026-08-27", "default_branch": "master", "org": "linuxdeepin"},
  "projects": {
    "deepin-pdfium":     {"branch": "master", "host": "github", "source": "csv"},
    "deepin-mail":       {"branch": "develop/snipe", "host": "gerrit", "source": "csv",
                          "github_mirror": "linuxdeepin/deepin-mail"},
    "com.deepin.gomoku": {"branch": "master", "host": "github", "source": "fallback"}
  }
}
```
- `branch`：CSV 非空非 NA → 用之；否则 `master`
- Gerrit 项目记录 `github_mirror`（GitHub 镜像），URL 仍统一生成 `github.com/linuxdeepin/<name>`

## 实施步骤

### ① `scripts/gen-project-info.py`（新，一次性可重跑）
- 读 CSV + 正则提取 downloader `PROJECT_REPOS`
- 合并输出 `project-branches.json`（CSV 分支优先，downloader 补 org 确认，其余 fallback master）

### ② `fetch-mcp-data.py` 注入（生成时机）
- 新增 `--branch <b>` / `--org <o>` 参数；未传时自动查 `project-branches.json`
- `.ut-inventory.json` 新增字段：
  ```json
  "git": {"org": "linuxdeepin", "branch": "master", "branch_source": "csv|arg|fallback"}
  ```
- 兼容：旧 inventory 无 `git` 字段不报错

### ③ `batch-collect.py` 传递
- `collect_project()` 查表把 branch 传给 fetch-mcp-data（或由其自查，二选一——**推荐自查**，batch 不改逻辑只加提示）
- `_summary.json` 每项目带 branch（顺手）

### ④ `dashboard-server.py` 合并
- `/api/projects`：inventory 无 `git` 字段时（旧数据），从 `project-branches.json` 补上 → **看板无需重新生成 26 份 inventory 即可生效**

### ⑤ `index.html` 前端（核心改动最小化）
分支优先级链（替换现有 `||'main'` 共 4 处）：
```
用户手动覆盖 > inventory.git.branch > project-branches(server /api/projects 缓存) > 'master'
```
- `loadInventory()`：有 `data.git?.branch` 且用户未手动改过 → 更新 `S.branch` + 状态栏显示 `🌿 <branch>`（区分来源：🔵表 / ✏️手动）
- `boot()`：`localStorage['utie-branch']` 仅当**用户手动设置**时生效（新增 `utie-branch-manual=1` 标记），否则 `master`
- 看板卡片/抽屉/下钻 GitHub 链接：用每项目自己的 branch（来自 /api/projects），不再用全局 S.branch
- 分支 popover 保留（手动覆盖场景），提示当前生效来源

### ⑥ 验证
- `gen-project-info.py` 输出 diff 人工过一遍（重点 develop/* 的 Gerrit 项）
- headless：断言默认 master、gomoku 用 master、CSV develop/snipe 项用 develop/snipe
- 重新生成 1 个项目（gomoku）确认 inventory 出现 `git` 字段

## 不做的事
- 不引 gitee URL（统一 GitHub，downloader 里 gitee 信息忽略）
- 不在浏览器请求 GitHub API 校验分支存在性（省 token，`master` 已实测）
- Gerrit-only 无镜像的项目（如有）仅记录 host 标记，链接仍生成 GitHub（可能 404，错误面板已有重试/换分支 UI）
