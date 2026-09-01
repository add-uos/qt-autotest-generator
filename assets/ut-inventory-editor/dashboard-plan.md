# UT Inventory Editor 集中式看板设计方案

> ✅ **v2 已全部实施完成**（P0+P1+P2）
> 架构：`dashboard-server.py`（伴随服务，调脚本刷数据）+ `index.html`（纯显示层）。
> 启动：`python3 scripts/dashboard-server.py` → 打开 `http://localhost:8765/` → 切「📊 看板」标签。

---

## 0. 总体架构

```
┌────────────────────────── 浏览器 ──────────────────────────┐
│  index.html（纯显示层，不跑任何采集/评分逻辑）                │
│   ├─ [📋 编辑器模式]  ← 不变                               │
│   └─ [📊 看板模式]                                          │
│        「🔄 刷新」→ POST /api/sync → 轮询进度 → 重渲染      │
└──────────────────────────┬─────────────────────────────────┘
                           │ fetch (同源,无 CORS)
┌──────────────────────────▼─────────────────────────────────┐
│  dashboard-server.py  (python3 启动, http://localhost:8765) │
│   ├─ GET  /              → 静态托管 index.html              │
│   ├─ GET  /api/status    → server/MCP/数据目录状态          │
│   ├─ POST /api/sync      → 调 batch-collect.py（后台任务）   │
│   ├─ GET  /api/task/<id> → 任务进度（日志尾部 + 完成度）      │
│   ├─ GET  /api/projects  → 聚合 26 项目 stats（实时读文件）   │
│   ├─ GET  /api/inventory/<name> → 单项目完整 inventory      │
│   │                          （看板下钻 → 编辑器直接加载！）  │
│   └─ GET  /api/mapping/<name> → test-mapping.json           │
└──────────────────────────┬─────────────────────────────────┘
                           │ subprocess / 文件
┌──────────────────────────▼─────────────────────────────────┐
│  现有脚本（零改动复用）                                       │
│   batch-collect.py      → mcp-projects/*/.ut-inventory.json │
│   fetch-mcp-data.py     →   + test-mapping.json             │
│   fetch-test-mapping.py →   + _summary.json                 │
└────────────────────────────────────────────────────────────┘
```

**关键设计：HTML 无任何数据生成逻辑，所有刷新都由服务端调脚本完成。**

---

## 1. 架构总览（前端部分）

### 1.1 模式切换：顶部 Tab

在 `app-header` 下加视图切换条：

```
[📋 编辑器]  [📊 看板]        ← S.view = 'editor' | 'dashboard'
```

- 编辑器模式：现有 3 面板，零改动
- 看板模式：`#dashboard-view` 全宽显示
- 切换只 toggle hidden，不销毁状态

### 1.2 三种运行模式（自动探测，优雅降级）

| 模式 | 探测方式 | 能力 |
|---|---|---|
| **A. server 模式**（完整） | `GET /api/status` 2xx | 刷新调脚本、下钻加载 inventory、一切功能 |
| B. 静态模式（file:// 或直接开 HTML） | status 失败 | 只能手动导入 JSON（P0 能力） |
| C. server 在但 MCP 不通 | status 返回 mcp:false | 刷新报错但历史数据仍可看 |

---

## 2. 数据流

### 2.1 刷新流程（核心：脚本干活，HTML 显示）

```
用户点「🔄 刷新」
  → POST /api/sync {filter?, size?, skip_mcp?}
  → server 起 batch-collect.py 子进程（ThreadPoolExecutor 复用其内部逻辑亦可）
  → 返回 {task_id}
  → 前端每 2s GET /api/task/<task_id>
      server 返回：{done: 5/26, current: "deepin-camera", log_tail: "..."}
  → 前端渐进渲染：每张卡片右上角转 spinner，完成一张亮一张
  → 任务完成 → GET /api/projects → 全量重渲染 + 写 localStorage 快照
```

### 2.2 服务端端点规格

```python
# dashboard-server.py —— 仅标准库（http.server + concurrent.futures）

GET  /                    # 静态托管 index.html（--root 指定目录）
GET  /api/status          # {server:true, mcp:bool(探测MCP端口), base_dir, last_summary_ts, projects_cached}
POST /api/sync            # 参数 {filter?, size?, skip_fetch_mcp?, skip_test_mapping?}
                          # → 启动后台任务, 返回 {task_id}
GET  /api/task/<id>       # {state: running|done|error, done_n, total_n,
                          #  current_project, log_tail(尾部500字), elapsed}
GET  /api/projects        # 实时聚合 mcp-projects/*/.ut-inventory.json 的 scan_stats
                          #  + _summary.json 合并 → [{name,size,stats...}]
GET  /api/inventory/<name># 完整 inventory JSON（下钻编辑器用,大文件流式）
GET  /api/mapping/<name>  # test-mapping.json
```

**进度上报实现**（不侵入现有脚本）：batch-collect.py 已把逐项目日志写进
`mcp-projects/<name>/collect.log` 且 stdout 有 `=== 开始收集 xxx ===` 标记。
server 两种实现任选：
1. 子进程 stdout 逐行读（subprocess.PIPE + readline），解析 `===` 行计进度 —— 推荐
2. 复用 `import batch_collect; collect_project()` 在进程内跑，回调更新进度字典 —— 并行更顺

### 2.3 localStorage 只存轻量缓存

```
utie-dash-projects → 项目统计摘要（server 不可用时兜底显示）
utie-dash-history  → 快照时间线 [{ts, totals}]（趋势图用）
utie-server-url    → http://localhost:8765（可改）
```

全量 inventory 永远从 `/api/inventory/<name>` 按需拉，不入 localStorage。

---

## 3. UI 布局（线框图）

### 3.1 看板总览

```
┌────────────────────────────────────────────────────────────────────────┐
│ [📊 看板]  项目总览 · 26 项目   ●server已连接     [🔄 刷新] [📥 导入] [🕐] │
├────────────────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ 总可测   │ │ 🟢高优   │ │ ⚠待复核 │ │ 无覆盖   │ │ 覆盖率   │      │
│  │ 24,302  │ │  1,955   │ │  321    │ │ 24,302  │ │   0%     │      │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
│  筛选: [全部|S|M|L|XL] [只看高优缺口] [搜索项目___]       排序: ▼高优缺口│
├────────────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────┐ ┌───────────────────────┐ ┌───────────────┐ │
│  │ deepin-pdfium      XL │ │ dde-grand-search    L │ │ deepin-camera │ │
│  │ 📁 github ↗  图谱2h前  │ │ 📁 github ↗           │ │ L             │ │
│  │ ▓▓▓░░░░░░ 7923 可测   │ │ ▓▓░░░░░░░ 2344 可测   │ │ ▓░░░░ 1171   │ │
│  │ 🟢521 ⚖2327 💤5075    │ │ 🟢139 ⚖817 💤1388    │ │ 🟢182 ⚖600  │ │
│  │ ⚠ 高优无覆盖: 521 🔴   │ │ ⚠ 高优无覆盖: 139     │ │ ⚠ 182        │ │
│  │ 覆盖 0/7923 (0%)      │ │ 覆盖 0/2344 (0%)      │ │ 0/1171       │ │
│  │ [进入编辑器 →]         │ │ [进入编辑器 →]         │ │              │ │
│  └───────────────────────┘ └───────────────────────┘ └───────────────┘ │
│  刷新中: ┌───────────────┐                                          │
│         │deepin-music ⟳ │  ← 正在同步的卡片显示 spinner            │
│         └───────────────┘                                          │
└────────────────────────────────────────────────────────────────────────┘
```

**卡片规格（复用现有变量）：**
- header: 项目名 + 规模 badge(S/M/L/XL) + GitHub 图标 + 图谱新鲜度角标
- 进度条: `--accent-soft` 底 + `--accent` 填充
- Level 分布: 复用 `level-badge`
- 风险行: N>0 红 `--danger`，=0 绿 `--accent`
- hover: border 变 `--accent` + translateY(-1px)

排序默认：**高优无覆盖数降序**。

### 3.2 项目下钻（抽屉，右滑入）

```
┌──────────────────────────────────────┬─────────────────────────────┐
│  (看板网格变暗)                       │ deepin-image-viewer      ✕ │
│                                      ├─────────────────────────────┤
│                                      │ 🟢31 ⚖333 💤249  待复核 20 │
│                                      │ ┌─────────┬─────────┐      │
│                                      │ │Level饼图│ Top风险类│      │
│                                      │ └─────────┴─────────┘      │
│                                      │ 高优无覆盖 Top 10 方法列表   │
│                                      │  [在编辑器中打开 →]          │
│                                      │   ← GET /api/inventory/     │
│                                      │     直接加载,无需文件选择器  │
│                                      └─────────────────────────────┘
```

**「在编辑器中打开」server 模式下全自动**：拉 `/api/inventory/<name>` →
走现有 `loadInventory()` → 预筛 `levels={'high'}` → 切回编辑器视图。

### 3.3 Kanban 泳道

```
[📊 总览] [🗂 Kanban] [📈 趋势]
├──────────────────┬──────────────────┬──────────────────────────────┤
│ 🔴 高优·无覆盖    │ 🟡 中优·无覆盖   │ 🟢 已有测试覆盖               │
│ (待办)           │ (排期)           │ (完成)                        │
├──────────────────┼──────────────────┼──────────────────────────────┤
│ ┌──────────────┐ │ ┌──────────────┐ │ ┌──────────────┐             │
│ │pdfium   521  │ │ │grand-search  │ │ │image-viewer  │             │
│ │🔴🔴🔴🔴🔴    │ │ │817 ⚖        │ │ │273 方法有TC   │             │
│ └──────────────┘ │ └──────────────┘ │ └──────────────┘             │
├──────────────────┴──────────────────┴──────────────────────────────┤
│ 合计: 待办 1,955 · 排期 7,593 · 完成 0        [对比上一快照 △-12]    │
└────────────────────────────────────────────────────────────────────┘
```

### 3.4 趋势（P2）

- x = 快照时间戳，y = 高优无覆盖总数
- 纯 SVG `<polyline>`，~40 行 JS，不引图表库

---

## 4. 组件规格（新增 CSS，全部走现有变量）

```css
.view-tabs / .view-tab          /* 视图切换条, .active 下划线 --accent */
.dash-view / .dash-toolbar      /* 看板容器 + 工具行 */
.dash-stats / .stat-card        /* 顶部统计卡 */
.dash-grid / .dash-card         /* bento 网格 + 项目卡 */
.size-badge(.s/.m/.l/.xl)       /* 规模徽章四档色 */
.bar-track / .bar-fill          /* 通用进度条 */
.dash-drawer                    /* 下钻抽屉(复用 gh-panel 交互) */
.kanban-cols / .kanban-col / .kanban-card / .dot-block
.sync-badge / .spin             /* 同步中状态 */
.trend-svg                      /* 趋势图容器 */
```

饼图 conic-gradient、条形 div 宽度、趋势 SVG polyline —— **零图表库**。

---

## 5. 服务端规格（dashboard-server.py）

```python
#!/usr/bin/env python3
"""UT 看板伴随服务 — 调脚本刷新数据 + 托管 index.html。仅标准库。"""
# 用法: python3 dashboard-server.py [--port 8765] [--root <html目录>]
#                                  [--base <mcp-projects目录>]

# 依赖: batch-collect.py 同目录（通过 --base 或相对路径找到）

关键实现点:
1. ThreadingHTTPServer + 简单路由 dict
2. /api/sync: task_id = uuid; ThreadPoolExecutor 提交:
     复用 batch_collect.collect_project()（import 模块,文件名含连字符
     用 importlib.util.spec_from_file_location 加载）
     每完成一个项目回调更新 TASKS[task_id]
3. /api/task/<id>: 返回 {state, done_n, total_n, current, log_tail}
4. /api/projects: 扫描 base_dir/*/.ut-inventory.json → 抽 scan_stats +
     generated_at + base_sha 合并 _summary.json 的 size 信息
5. /api/inventory/<name>: FileWrapper 流式返回（防大 JSON 阻塞）
6. 静态: / → index.html; Content-Type 正确; no-cache 头方便开发
```

**安全**：默认只绑 127.0.0.1，内网工具不暴露；`/api/sync` 加个简单防重入
（同 task 运行中再点返回当前 task_id）。

---

## 6. 状态管理（S 最小扩展）

```js
S.view: 'editor' | 'dashboard'
S.dash = {
  server: null,            // {baseUrl, status} 探测结果
  projects: [],            // /api/projects 或导入解析结果
  filter: { size: null, onlyGap: false, q: '' },
  sort: 'noCoverHigh',
  task: null,              // {id, done, total, current} 刷新进度
}
// localStorage: utie-dash-projects(兜底缓存) / utie-dash-history / utie-server-url
```

编辑器联动：看板卡片「进入编辑器」→ server 模式直接 fetch inventory →
`loadInventory()` → 预筛 high → 切视图。静态模式退化为文件选择器。

---

## 7. 实施阶段

### P0 — 伴随服务 + 看板骨架（~1 天）
- [ ] `dashboard-server.py`：/、/api/status、/api/projects、/api/inventory/<name>
- [ ] 前端：视图 tab、server 探测（●绿点/○灰点）、统计卡、项目卡网格、
      排序/筛选/搜索、localStorage 兜底、静态模式降级导入
- [ ] 卡片 → GitHub 链接（window.open，复用现有 URL 拼装）

### P1 — 同步刷新 + 下钻 + Kanban（~1 天）
- [ ] /api/sync + /api/task/<id>（复用 collect_project，进度回调）
- [ ] 前端刷新按钮 → 任务轮询 → 卡片渐进亮起 + log 浮层（可折叠）
- [ ] 下钻抽屉（Level 饼图 + 高优缺口 Top10 + 「在编辑器打开」全自动加载）
- [ ] Kanban 三泳道 + 快照对比 Δ

### P2 — 趋势 + 增强
- [ ] SVG 趋势折线（快照历史）
- [ ] /api/mapping 集成 → 卡片显示用例数、点击直达测试文件（复用左侧 GitHub 面板）
- [ ] 导出看板 CSV
- [ ] 同步参数面板（filter/size/skip 选项透传 batch-collect）

---

## 8. 技术约束与风险

| 约束 | 对策 |
|---|---|
| HTML 单文件 | 纯显示层；服务端是另一个 Python 单文件 |
| localStorage ~5MB | 只存统计摘要+快照；全量走 /api 按需 |
| batch-collect 全量耗时（26 项目可能 >10min） | 增量模式默认（inventory 已存在则跳过 fetch）；任务异步+进度显示，不阻塞 UI |
| MCP 不可达 | /api/status 探测提示；刷新报错但历史数据可看 |
| 端口冲突 | --port 可配；前端可改 server URL |
| Esc/z-index 与现有面板冲突 | drawer 层级低于 gh-overlay；Esc 先关 drawer 再关 gh 面板 |
| 旧浏览器无 File System Access | server 模式不需要它；静态模式才用文件选择器 |
