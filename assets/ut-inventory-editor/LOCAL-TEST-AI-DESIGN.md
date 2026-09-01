# UT Inventory Editor — 本地单测看板 & 触发运行 & 覆盖率内嵌 设计方案

> 日期: 2026-08-31 (v2，根据反馈修订)
> 状态: 草案，待讨论
> 实验项目: `~/debug/deepin-image-viewer`, `~/debug/deepin-calculator`

---

## 0. 背景与范围

### 0.1 问题

现有看板只展示**函数优先级分布**（来自 MCP 知识图谱 + `scan-inventory.py` 评分），核心指标是高/中/低优方法数、待复核数、无覆盖缺口数。这些都是**静态分析**结果，与项目本地实际单元测试状况完全脱节。

### 0.2 inventory.json 来源多样性（重要前提）

`.ut-inventory.json` 的来源不止一处，设计时必须考虑：

| 来源 | 路径 | 特点 |
|------|------|------|
| MCP 批量采集 | `mcp-projects/<name>/.ut-inventory.json` | server 默认数据源，26 项目全量，函数图谱+评分 |
| 手动导入 | 拖放/文件选择 | 用户本地的 inventory 文件 |
| 本地项目自带 | `{source.path}/.ut-inventory.json` | 开发者自己跑 `scan-inventory.py` 生成 |

**设计原则**：本地测试结果展示与 inventory 来源解耦——只要项目在 `projects.json` 里登记了 `source.path`，就能展示本地测试状况，**不强制要求 inventory 也来自本地**。一个项目可以「MCP 提供函数图谱 + 本地提供测试结果」并存。

### 0.3 本期范围

| # | 功能 | 状态 |
|---|------|------|
| 1 | **本地单测结果展示** | ✅ 本期实施 |
| 2 | **触发单测运行（可并行）** | ✅ 本期实施 |
| 3 | **覆盖率 HTML 报告 iframe 内嵌** | ✅ 本期实施 |
| 4 | **AI 补全单元测试** | 📌 保留设计，暂缓实施（见第 5 节） |

---

## 1. 本地单测结果展示

### 1.1 数据源分析

实验项目已有的测试基础设施：

```
deepin-image-viewer/
├── tests/
│   ├── CMakeLists.txt          # 测试构建配置（cmake + gtest + coverage）
│   ├── src/                    # 26 个 ut_*.cpp 测试文件
│   ├── gen-ut-summary.py       # 解析 gtest XML + lcov → ut-summary.json
│   └── test-prj-running.sh     # 一键构建+运行+采集脚本
└── build-ut/
    ├── ut-summary.json          # ← 测试结果摘要
    ├── report/
    │   └── report_deepin-image-viewer.xml   # gtest XML 详细报告
    └── html/                    # ← LCOV 覆盖率 HTML 报告（本期重点）
        ├── index.html (隐含, 实际是 index-sort-f.html)
        ├── index-sort-f.html    # 按文件排序总览
        ├── index-sort-l.html    # 按行覆盖率排序
        ├── index-sort-b.html    # 按分支覆盖率排序
        ├── cov_deepin-image-viewer.html
        ├── gcov.css             # 已有 UTIE 匹配主题 gcov-utie.css
        ├── *.png                # 图标资源
        └── src/
            ├── baseutils.cpp.gcov.html      # 源码级覆盖率（逐行高亮）
            ├── baseutils.cpp.func.html      # 函数级覆盖率
            └── baseutils.cpp.func-sort-c.html

deepin-calculator/
├── tests/
│   ├── CMakeLists.txt
│   ├── src/                    # 测试源文件
│   └── test-prj-running.sh     # 同样的构建脚本
└── build-ut/                   # （当前为空，未构建过）
```

**`ut-summary.json` 结构**（已有标准，`gen-ut-summary.py` 生成）：

```json
{
  "test_cases": { "total": 465, "passed": 465, "failed": 0 },
  "line_coverage": { "total": 4721, "passed": 3879, "failed": 842, "coverage": "82.20%" },
  "function_coverage": { "total": 422, "passed": 420, "failed": 2, "coverage": "99.50%" }
}
```

**gtest XML 报告** 包含每个 test case 的详细结果（文件、行号、耗时、失败信息）。

**LCOV HTML 报告** 是自包含的静态站点（相对路径引用 css/png），可直接 iframe 内嵌。

### 1.2 数据采集方案

#### 新增端点：`GET /api/test-results/<name>`

dashboard-server.py 读取本地项目的测试结果：

```python
# 逻辑：
# 1. 查 projects.json 获取 source.path（本地路径）
# 2. 探测 build_dir（build-ut / build-test / build-ut-m3 / build）
# 3. 读 {source.path}/{build_dir}/ut-summary.json → 摘要
# 4. 读 {source.path}/{build_dir}/report/report_*.xml → 用例详情
# 5. 检测 {source.path}/{build_dir}/html/index.html → 标记覆盖率报告可用
# 6. 与 inventory 交叉分析高优缺口（test_cover_count 字段已有）

# 返回结构：
{
  "project": "deepin-image-viewer",
  "local_path": "/home/zhy/debug/deepin-image-viewer",
  "build_dir": "build-ut",
  "last_run": "2026-08-13T15:21:01",     # 来自 XML timestamp
  "test_summary": {
    "total": 465, "passed": 465, "failed": 0,
    "line_coverage": "82.20%",
    "function_coverage": "99.50%"
  },
  "test_suites": [                         # 来自 gtest XML
    { "name": "ut_baseutils", "tests": 29, "failures": 0, "time": "0.05",
      "cases": [
        { "name": "Hash_WhenSameInput_ReturnsSameMd5Hex",
          "status": "completed", "time": "0.001",
          "file": "tests/src/ut_baseutils.cpp", "line": 23 }
      ]
    }
  ],
  "coverage_html_available": true,         # html/ 目录是否存在
  "coverage_html_path": "build-ut/html",   # 相对路径，供 iframe URL 拼装
  "failed_cases": [                        # 失败用例（便于抽屉直接展示）
    { "suite": "ut_foo", "name": "BarTest", "time": "0.003",
      "file": "...", "line": 42, "failure": "Expected true, got false" }
  ]
}
```

#### `build_dir` 的发现策略

不同项目的构建目录名不统一（`build-ut`、`build-test`、`build`），需探测：

```python
BUILD_DIR_CANDIDATES = ["build-ut", "build-test", "build-ut-m3", "build"]

def find_build_dir(project_path):
    for d in BUILD_DIR_CANDIDATES:
        p = Path(project_path) / d
        if p.is_dir():
            # 优先有 ut-summary 或 report 的
            if (p / "ut-summary.json").exists() or any(p.glob("report/*.xml")):
                return d
    # 退而求其次：任意存在的 build*
    for d in BUILD_DIR_CANDIDATES:
        if (Path(project_path) / d).is_dir():
            return d
    return None
```

候选列表从 `config.json` 的 `test.build_dir_candidates` 读取，可配置。

### 1.3 看板卡片增强

**现有卡片**：

```
┌────────────────────────────────┐
│ deepin-image-viewer       XL   │
│ ▓▓▓░░░░ 7923 可测              │
│ 🟢31 ⚖333 💤249                │
│ ⚠ 高优无覆盖: 31               │
│ 覆盖 0/7923 (0%)   ← MCP 理论  │
└────────────────────────────────┘
```

**增强后卡片**（双行覆盖信息）：

```
┌─────────────────────────────────────────┐
│ deepin-image-viewer          XL    🏠   │  ← 🏠 表示有本地路径
│ ▓▓▓░░░░ 7923 可测                       │
│ 🟢31 ⚖333 💤249                         │
│ ⚠ 高优无覆盖: 31 (MCP)                  │
│ ──────── 本地单测 ────────               │
│ ✅ 465/465 通过  |  📊 行覆盖 82.2%     │  ← 实际测试结果
│ 🕐 上次运行: 8月13日 15:21              │
│ [▶ 运行测试]  [📊 覆盖率]              │  ← 新增操作按钮
└─────────────────────────────────────────┘
```

**状态标识**：

| 状态 | 图标 | 含义 |
|------|------|------|
| 有本地路径 + 有测试结果 | 🏠 + ✅ | 本地项目，测试通过 |
| 有本地路径 + 有失败 | 🏠 + ❌ | 本地项目，有失败用例 |
| 有本地路径 + 未构建 | 🏠 + ⏸ | 本地项目，未运行测试 |
| 有本地路径 + 运行中 | 🏠 + 🔄 | 正在编译/测试 |
| 无本地路径 | — | 远程 MCP 项目，只有理论数据 |

### 1.4 项目下钻抽屉增强

```
┌──────────────────────────────────────────────┐
│ deepin-image-viewer                       ✕  │
├──────────────────────────────────────────────┤
│ 🟢31 ⚖333 💤249  ⏳ 待复核 20               │
│ ┌──────────┬──────────┐                      │
│ │Level 饼图 │ Top 风险类│                     │
│ └──────────┴──────────┘                      │
│                                              │
│ ──── 🏠 本地测试结果 ────                     │
│ ✅ 465/465 通过   行覆盖: 82.2%  函数: 99.5%│
│ 🕐 2026-08-13 15:21:01                       │
│                                              │
│ ──── 失败用例 (如果有) ────                   │
│ ❌ ut_foo::BarTest (0.003s)                  │
│    Expected true, got false at line 42       │
│                                              │
│ ──── 测试套件列表 ────                        │
│ ut_baseutils.cpp      29 cases   ✅          │
│ ut_applicationadpator.cpp  7 cases  ✅       │
│ ut_commandparser.cpp  12 cases   ✅          │
│ ... [展开全部 26 个]                          │
│                                              │
│ ──── 高优无覆盖 Top 10 ────                   │
│ BaseUtils::hash  ·  src/baseutils.cpp        │
│                                              │
│ [▶ 运行测试]  [📊 查看覆盖率]  [编辑器→]      │
└──────────────────────────────────────────────┘
```

### 1.5 统计卡片增强

顶部统计区新增两个卡片：

```
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ 总可测   │ │ 🟢高优   │ │ ⚠待复核 │ │ MCP缺口  │ │ ✅本地通过│ │ ❌本地失败│
│ 24,302  │ │  1,955   │ │  321    │ │ 2,014   │ │  465     │ │    0     │
└──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘
```

---

## 2. 覆盖率 HTML 报告 iframe 内嵌

### 2.1 核心思路

项目已有的 LCOV HTML 报告（`build-ut/html/`）内容非常详细：
- 总览页（按文件/行/分支排序）
- 源码级覆盖率（逐行高亮，命中次数）
- 函数级覆盖率（每个函数的行覆盖）
- 已有 `gcov-utie.css` 主题与 UTIE 匹配

**不自己解析 lcov**，直接用 server 代理这些静态 HTML，在前端用 iframe 内嵌，避免跳转到不同浏览器标签页。

### 2.2 server 静态代理端点

新增路由，把本地项目的 `html/` 目录映射成同源 URL：

```
GET /api/coverage/<name>/              → 重定向到 index-sort-f.html
GET /api/coverage/<name>/index-sort-f.html
GET /api/coverage/<name>/gcov.css
GET /api/coverage/<name>/glass.png
GET /api/coverage/<name>/src/baseutils.cpp.gcov.html
GET /api/coverage/<name>/src/baseutils.cpp.func.html
```

**实现**（复用现有 `_file()` 方法）：

```python
def serve_coverage(self, name, sub_path):
    """代理本地项目 LCOV HTML 报告"""
    # 1. 查 projects.json 获取 source.path + build_dir
    proj = self._find_project(name)
    if not proj or not proj.get("source", {}).get("path"):
        self._json({"error": "无本地路径"}, 404)
        return
    
    build_dir = find_build_dir(proj["source"]["path"])
    if not build_dir:
        self._json({"error": "未找到构建目录"}, 404)
        return
    
    html_root = Path(proj["source"]["path"]) / build_dir / "html"
    if not html_root.is_dir():
        self._json({"error": "无覆盖率报告，请先运行测试"}, 404)
        return
    
    # 2. 路径安全：sub_path 不允许 .. 穿越
    if ".." in sub_path or sub_path.startswith("/"):
        self._json({"error": "bad path"}, 400)
        return
    
    # 3. 根路径 → 默认页
    if not sub_path:
        sub_path = "index-sort-f.html"
    
    # 3.5 主题注入（零侵入）：拦截 gcov.css → 返回 server 自带 UTIE 主题
    #     LCOV 每个页面都 <link href="gcov.css">，替换这一个文件即全站换肤
    #     不改项目源码、不改 test-prj-running.sh、不改 LCOV 生成产物
    if sub_path == "gcov.css":
        theme = self.coverage_theme  # 来自 config.test.coverage.theme_css
        if theme and theme.get("override") and Path(theme["css"]).is_file():
            self._file(Path(theme["css"]), "text/css; charset=utf-8")
            return
    
    target = (html_root / sub_path).resolve()
    if not str(target).startswith(str(html_root.resolve())):
        self._json({"error": "bad path"}, 400)
        return
    
    if not target.is_file():
        self._json({"error": "not found"}, 404)
        return
    
    # 4. 按扩展名设 mime
    mime = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".png": "image/png",
        ".gif": "image/gif",
        ".js": "text/javascript; charset=utf-8",
    }.get(target.suffix, "application/octet-stream")
    
    self._file(target, mime)
```

**关键点**：
- LCOV 报告内所有资源用**相对路径**引用（`gcov.css`、`glass.png`、`src/xxx.html`），iframe 加载 `/api/coverage/<name>/index-sort-f.html` 后，浏览器自动按同前缀解析相对路径 → 无需改写 HTML
- 路径穿越防护：拒绝 `..`、校验 resolve 后仍在 `html_root` 内
- `X-Frame-Options`：server 默认不设此头（或设 `SAMEORIGIN`），允许同源 iframe 嵌入

### 2.3 主题不侵入注入

#### 原理

LCOV 生成的每个 HTML 页面（总览页、源码级 `.gcov.html`、函数级 `.func.html`）都通过相对路径引用同一个样式表：

```html
<link rel="stylesheet" type="text/css" href="gcov.css">
<!-- 子目录页面: -->
<link rel="stylesheet" type="text/css" href="../gcov.css">
```

无论页面在哪层目录，浏览器最终都请求 `/api/coverage/<name>/gcov.css`。**server 代理这一个文件时拦截替换**，全部页面自动套用新主题——无需触碰项目源码、`test-prj-running.sh`、`genhtml` 参数或 LCOV 生成产物。

#### 三种方案对比

| 方案 | 侵入性 | 说明 |
|------|--------|------|
| A. 改 `genhtml --css-file` | ❌ 侵入 | 需改每个项目的 `test-prj-running.sh`，且要在项目里放 css |
| B. server 拦截 `gcov.css` | ✅ 零侵入 | 代理时替换这一个文件，全站换肤（**采用**） |
| C. iframe 内 JS 注入 | ❌ 不可靠 | sandbox 禁脚本时失效，跨域限制 |

#### UTIE 主题 css 的存放

主题 css 不放在项目里，放在 **ut-inventory-editor 自己的 assets** 下，server 直接托管：

```
ut-inventory-editor/
├── assets/
│   └── coverage-themes/
│       ├── utie-light.css     ← 默认主题（匹配 UTIE light）
│       ├── utie-dark.css      ← 匹配 UTIE dark
│       └── gcov-original.css  ← 原始 LCOV 样式（兜底/对照）
```

`utie-*.css` 完整重写 LCOV 的 class 规则（LCOV class 名是固定的：`.headerItem`、`.headerCovTableEntryHi`、`.lineCov`、`.lineNoCov` 等），实现与 UTIE 设计系统一致的配色。

#### 配置驱动

`config.json` 控制主题行为，设置界面提供切换：

```json
"coverage": {
  "theme": "utie-auto",
  "themes": {
    "utie-auto":  {"css": "assets/coverage-themes/utie-light.css", "override": true},
    "utie-dark":  {"css": "assets/coverage-themes/utie-dark.css",  "override": true},
    "original":   {"css": null, "override": false}
  }
}
```

- `override: true` → 拦截 `gcov.css` 返回指定 css
- `override: false`（`original`）→ 不拦截，透传项目自带的 `gcov.css`
- `theme: utie-auto` → 跟随 UTIE 当前主题（light/dark），server 读取请求 cookie 或前端传 `?theme=` 参数动态选 css

#### 设置界面入口

```
┌─ 全局设置 ─────────────────────────────────┐
│ ...                                        │
│ ── 📊 覆盖率报告 ──                         │
│ 主题: [UTIE 自动 ▾]                        │
│        ○ UTIE 自动 (跟随看板主题)           │
│        ○ UTIE 浅色                         │
│        ○ UTIE 深色                         │
│        ○ LCOV 原始                          │
│        ○ 自定义 CSS 文件... [选择]          │
│ ☑ 生成报告时注入 UTIE css (不修改项目)     │
└────────────────────────────────────────────┘
```

「自定义 CSS 文件」允许用户指向自己的 css，server 拦截时返回它。

#### 与「触发测试运行」的衔接

运行测试时 `genhtml` 仍用项目原有命令（不加 `--css-file`），生成的 `html/gcov.css` 是 LCOV 原始样式。server 代理时拦截替换 → **项目产物零修改，主题仅在浏览器查看时生效**。换个项目、换个机器，LCOV 报告本身永远干净。

### 2.4 前端 iframe 内嵌 UI

**方案：抽屉内嵌 iframe**（点击「📊 查看覆盖率」展开）：

```
┌─ 抽屉：deepin-image-viewer ──────────────────────────┐
│ ✅ 465/465 | 行覆盖 82.2%   [▶ 运行] [📊覆盖率] [✕]  │
├──────────────────────────────────────────────────────┤
│ ← 点击「📊 查看覆盖率」后展开 ↓                       │
├──────────────────────────────────────────────────────┤
│                                                       │
│  ┌─ iframe: LCOV 报告 ───────────────────────────┐  │
│  │ LCOV - code coverage report                    │  │
│  │ Lines: 3883/4721  82.2%   Functions: 420/422  │  │
│  │ ┌─────────────────────────────────────────┐   │  │
│  │ │ Directory    Line Cov   Func Cov         │   │  │
│  │ │ src/         82.2%      99.5%            │   │  │
│  │ │   baseutils  95.0%      100%             │   │  │
│  │ │   filecontrol 78.3%     98.1%            │   │  │
│  │ │   ...                                    │   │  │
│  │ └─────────────────────────────────────────┘   │  │
│  │                                                │  │
│  │ 点击文件名 → iframe 内跳转到源码级覆盖率页     │  │
│  └────────────────────────────────────────────────┘  │
│                                                       │
│ [在新窗口打开 ↗]  [刷新报告]                         │
└──────────────────────────────────────────────────────┘
```

**交互细节**：
- iframe 占满抽屉剩余高度（`height: calc(100% - header)`）
- iframe 内的链接点击**在 iframe 内导航**（LCOV 默认行为，不跳出），用户在抽屉里就能层层下钻到源码级
- 提供「在新窗口打开 ↗」按钮（`window.open('/api/coverage/<name>/')`）作为备选
- 运行测试完成后，iframe 自动 reload 刷新报告

**独立覆盖率视图**（可选，Phase 2）：

在顶部 tab 增加独立的覆盖率大图视图，全屏 iframe + 项目切换下拉：

```
[📋 编辑器] [📊 看板] [🧪 测试] [📈 覆盖率] [⚙ 设置]
                                ↑
                          全屏 iframe + 项目选择器
```

### 2.5 LCOV 报告深度链接

最有价值的场景：**从看板的方法详情直接跳到该方法在 LCOV 里的覆盖率页**。

LCOV 的函数级报告 URL 规律：
```
/api/coverage/<name>/src/<file>.func.html          # 函数列表
/api/coverage/<name>/src/<file>.func-sort-c.html   # 按覆盖率排序
/api/coverage/<name>/src/<file>.gcov.html          # 源码逐行
```

inventory 里有 `file_path`（如 `src/baseutils.cpp`），可拼装：

```js
function coverageUrlForMethod(project, filePath) {
  return `/api/coverage/${project}/src/${filePath}.func.html`;
}
// → /api/coverage/deepin-image-viewer/src/baseutils.cpp.func.html
```

编辑器详情面板里，给每个方法加「📊 覆盖率」链接，点击在 iframe 里打开对应文件的函数级覆盖率。

---

## 3. 触发单元测试（可并行）

### 3.1 为什么不直接调项目脚本

两个实验项目都自带 `tests/test-prj-running.sh`，但它会 **`rm -rf build` 全量重编译**：

```bash
# deepin-image-viewer/tests/test-prj-running.sh（节选）
rm -rf build      # ← 清理构建目录
rm -rf build-ut
cd ../build
cmake ... ..       # ← 重新配置
make -j8           # ← 全量重编译（超级大项目耗时极长）
./tests/xxx-test ...
lcov ... && genhtml ...
```

对 deepin-pdfium（XL，7900+ 可测方法）这种超大项目，**只想跑一下单测和报告时，没必要每次都清理重编译**——增量编译复用已有 `.o` 文件即可秒级完成。

**决定：server 自己分阶段控制，不调用项目脚本**。分离的好处：
- 不清理 `build_dir`，增量编译
- 各阶段可单独触发（只跑测试、只出覆盖率）
- 超大项目跑测试时跳过 configure/build，几秒出结果

### 3.2 分阶段流程（增量、不清理）

把测试链路拆成 5 个独立阶段，按需组合：

| 阶段 | 命令 | 是否清理 | 何时跳过 |
|------|------|----------|----------|
| `configure` | `phases.configure`（如 `cmake ... ..`） | 不清理；build_dir 不存在才创建 | build_dir 已有 `CMakeCache.txt` 时默认跳过 |
| `build` | `phases.build`（如 `make -j<N>`） | **不清理**，增量编译 | 用户选 `test-only` 模式时跳过 |
| `test` | `phases.test`（如 `./tests/<binary> --gtest_output=...`） | 清理 `report/`（纯产物） | —（核心阶段） |
| `coverage` | `phases.coverage`（`lcov ... && genhtml ...`） | 清理 `html/`（纯产物） | 用户选 `test-only` 或 `coverage-only` 反向时跳过 |
| `summary` | `phases.summary`（如 `python3 gen-ut-summary.py`） | 不清理 | `phases.summary` 为空时跳过 |

> **只清理产物目录（report/html），不清理 build_dir**——编译缓存得以保留，二次跑测试只需 `test + summary` 两步。

#### 触发模式（前端可选）

| 模式 | 阶段序列 | 场景 |
|------|----------|------|
| `full`（默认） | configure(若需) → build → test → coverage → summary | 首次 / 改了源码 |
| `test-only` | test → summary | 超大项目、已编译过、只验证用例通过 |
| `test+coverage` | test → coverage → summary | 改了源码想看覆盖率 |
| `build+test` | build(增量) → test → summary | 改了源码、不要覆盖率 |
| `coverage-only` | coverage → summary | 不重跑测试，用已有 XML 重算覆盖率（如换主题后） |

```bash
# server 实际执行（伪代码，cwd = source.path/build_dir）

# configure：仅 build_dir 不存在或无 CMakeCache.txt 时
if not (build_dir / 'CMakeCache.txt').exists():
    run(phases['configure'], cwd=build_dir)

# build：增量，make 自动跳过未变更文件
run(phases['build'], cwd=build_dir)            # make -j8

# test：清 report/ 纯产物，跑测试
shutil.rmtree(build_dir / 'report', ignore_errors=True)
run(phases['test'], cwd=build_dir)             # ./tests/xxx-test --gtest_output=xml:./report/...

# coverage：清 html/ 纯产物，lcov + genhtml
shutil.rmtree(build_dir / 'html', ignore_errors=True)
run('lcov -d . -c -o coverage.info', cwd=build_dir)
run('genhtml -o html coverage.info', cwd=build_dir)   # 不加 --css-file，主题靠 server 代理注入

# summary：生成 ut-summary.json
if phases.get('summary'):
    run(phases['summary'], cwd=build_dir)      # python3 ../tests/gen-ut-summary.py
```

#### 不同项目命令差异的吸收点

项目脚本间的差异（测试二进制名、CMake 参数、Qt5/6、ctest vs 直跑），全部落到 `projects.json` 的 `build.phases` 字段里，server 不硬编码：
- 测试二进制名不统一 → `phases.test` 里写死
- CMake 参数不同 → `phases.configure` 里写死
- `ctest` vs 直跑 → `phases.test` 里写 `ctest --output-on-failure` 或 `./tests/xxx-test ...`
- `gen-ut-summary.py` 路径 → `phases.summary` 里写死

### 3.3 项目注册表 `projects.json` 扩展

`build` 字段改为分阶段命令结构，server 按阶段执行：

```json
{
  "name": "deepin-image-viewer",
  "source": {
    "type": ["mcp", "local"],
    "mcp_name": "home-uos-service-codebase-repos-deepin-image-viewer",
    "path": "/home/zhy/debug/deepin-image-viewer"
  },
  "build": {
    "system": "cmake",
    "framework": "gtest",
    "build_dir": "build-ut",
    "phases": {
      "configure": "cmake -DCMAKE_BUILD_TYPE=Debug -DCMAKE_SAFETYTEST_ARG=CMAKE_SAFETYTEST_ARG_ON ..",
      "build":     "make -j8",
      "test":      "./tests/deepin-image-viewer-test --gtest_output=xml:./report/report_deepin-image-viewer.xml",
      "coverage":  "lcov -d . -c -o coverage.info && lcov --extract coverage.info '*/src/*' -o coverage.info && lcov --remove coverage.info '*/tests/*' -o coverage.info && genhtml -o html coverage.info",
      "summary":   "python3 ../tests/gen-ut-summary.py"
    },
    "env": {
      "QT_QPA_PLATFORM": "offscreen",
      "ASAN_OPTIONS": "detect_leaks=1",
      "LSAN_OPTIONS": "suppressions=tests/lsan_suppressions.txt"
    },
    "timeout": 600
  }
}
```

> **`source.type` 改为数组**：一个项目可同时来自 MCP（函数图谱）和本地（测试结果/源码）。现有 `type: "mcp"` 向后兼容（单值视为数组）。
>
> **`coverage` 阶段不加 `--css-file`**：LCOV 生成原始 `gcov.css`，主题由 server 代理时拦截注入（见 2.3），项目产物零修改。

**`phases` 字段缺失时的自动推断**（任一阶段为空，server 按以下策略探测）：

```python
def infer_phases(project_path, build_dir, project_name):
    """自动发现各阶段命令（字段为空时回退）"""
    build_path = Path(project_path) / build_dir
    phases = {}
    
    # configure: cmake 标准命令（build_dir 相对源码根）
    if not phases.get('configure'):
        phases['configure'] = 'cmake -DCMAKE_BUILD_TYPE=Debug ..'
    
    # build: make -j
    if not phases.get('build'):
        nproc = os.cpu_count() or 8
        phases['build'] = f'make -j{min(nproc, 16)}'
    
    # test: 探测测试二进制
    if not phases.get('test'):
        # 策略1: tests/ 下的可执行文件
        for f in (build_path / 'tests').glob('*-test'):
            if f.is_file() and os.access(f, os.X_OK):
                phases['test'] = f'./tests/{f.name} --gtest_output=xml:./report/report_{project_name}.xml'
                break
        # 策略2: ctest
        if 'test' not in phases and (build_path / 'CTestTestfile.cmake').exists():
            phases['test'] = 'ctest --output-on-failure'
        # 策略3: 顶层 ut_*
        if 'test' not in phases:
            for f in build_path.glob('ut_*'):
                if f.is_file() and os.access(f, os.X_OK):
                    phases['test'] = f'./{f.name} --gtest_output=xml:./report/report_{project_name}.xml'
                    break
    
    # coverage: lcov + genhtml 标准命令
    if not phases.get('coverage'):
        phases['coverage'] = "lcov -d . -c -o coverage.info && genhtml -o html coverage.info"
    
    # summary: 探测 gen-ut-summary.py
    if not phases.get('summary'):
        for cand in ['tests/gen-ut-summary.py', '../tests/gen-ut-summary.py']:
            if (Path(project_path) / cand).exists():
                phases['summary'] = f'python3 {cand}'
                break
    
    return phases
```

**与项目脚本的关系**：`phases` 字段的值可直接从项目 `test-prj-running.sh` 里抽取（命令几乎一致，只去掉 `rm -rf` 清理和 `cp` 搬运）。设置界面提供「从项目脚本导入」按钮，解析 `test-prj-running.sh` 自动填充各阶段命令。

### 3.4 多项目并行运行

**不做环境隔离**（直接在本机运行），但支持多项目并行，并发数在设置界面配置。

#### 配置（`config.json`）

```json
{
  "test": {
    "max_concurrent": 2,
    "default_timeout": 600,
    "build_dir_candidates": ["build-ut", "build-test", "build-ut-m3", "build"]
  }
}
```

#### 设置界面增强

```
┌─ 全局设置 ─────────────────────────────────┐
│ MCP 地址:    [http://10.8.12.80:13626/mcp]│
│ GitHub 组织: [linuxdeepin              ]  │
│ 服务端口:    [8765] (重启生效)            │
│ 同步并发:    [1]                          │
│                                            │
│ ── 🧪 测试运行 ──                          │
│ 最大并行项目数: [2]   ← CPU 负荷权衡       │
│ 默认超时(秒):    [600]                     │
│ 构建目录候选:    [build-ut,build-test]     │
└────────────────────────────────────────────┘
```

`max_concurrent` 建议：
- 1 = 串行（最安全，CPU 满载一个项目）
- 2 = 默认（多数开发机 8 核+ 可承受）
- 3-4 = 高配机器（16 核+）

#### 并行实现（`ThreadPoolExecutor`）

```python
class TestRunner:
    def __init__(self, config):
        self.max_concurrent = config.get("test", {}).get("max_concurrent", 2)
        self.executor = ThreadPoolExecutor(max_workers=self.max_concurrent)
        self.running = {}    # project_name → {future, process, state, log}
        self.lock = threading.Lock()
    
    def run(self, project_name, mode='full'):
        """提交一个项目测试任务
        mode: full | test-only | test+coverage | build+test | coverage-only
        """
        with self.lock:
            if project_name in self.running:
                return {"error": "该项目已在运行"}
            if len(self.running) >= self.max_concurrent:
                return {"error": f"已达最大并行数 {self.max_concurrent}", "queued": True}
        
        future = self.executor.submit(self._run_project, project_name)
        with self.lock:
            self.running[project_name] = {
                "future": future, "state": "queued",
                "log": [], "started_at": time.time()
            }
        return {"started": True}
    
    def _run_project(self, project_name, mode='full'):
        """按 mode 执行选定阶段（增量、不清理 build_dir）"""
        # 按 mode 选择阶段序列，每阶段 Popen 流式输出
        # 更新 self.running[name] 的 phase/progress/log_tail
        ...
    
    def status(self, project_name):
        """单个项目状态"""
        ...
    
    def status_all(self):
        """所有运行中项目状态（看板多卡片同时刷新用）"""
        ...
    
    def stop(self, project_name):
        """终止指定项目（kill 进程组）"""
        ...
```

### 3.5 服务端新增端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/test/run/<name>` | POST | 启动测试，body `{mode}` 选阶段 |
| `/api/test/status/<name>` | GET | 单项目运行状态（含当前阶段） |
| `/api/test/status` | GET | 所有运行中项目状态（批量轮询） |
| `/api/test/stop/<name>` | POST | 终止单项目 |
| `/api/test/results/<name>` | GET | 读取测试结果（见 1.2） |
| `/api/coverage/<name>/*` | GET | 代理 LCOV HTML（见 2.2） |

**`POST /api/test/run/<name>`** body：

```json
{ "mode": "full" }   // full | test-only | test+coverage | build+test | coverage-only
```

server 按 mode 选择阶段序列（见 3.2 触发模式表），每个阶段 Popen 流式执行，更新 `self.running[name].phase`。

**`GET /api/test/status`**（批量，减少轮询次数）：

```json
{
  "running": [
    {
      "project": "deepin-image-viewer",
      "state": "building",
      "phase": "compiling",
      "progress": "60%",
      "started_at": "2026-08-31T14:00:00",
      "log_tail": "[ 72%] Building CXX object..."
    },
    {
      "project": "deepin-calculator",
      "state": "configuring",
      "phase": "cmake",
      "progress": "10%",
      "started_at": "2026-08-31T14:01:00",
      "log_tail": "-- The CXX compiler..."
    }
  ],
  "max_concurrent": 2,
  "slots_used": 2
}
```

### 3.6 前端交互

**卡片上的「▶ 运行测试」按钮**（带模式下拉）：

```
┌─────────────────────────────────────────┐
│ ...                                     │
│ [▶ 运行 ▾]  [📊 覆盖率]                │
│         ├─ 完整 (编译+测试+覆盖率)      │
│         ├─ 仅测试 (用已有编译)    ← 快   │
│         ├─ 测试+覆盖率                  │
│         ├─ 编译+测试                    │
│         └─ 仅覆盖率 (用已有测试结果)     │
└─────────────────────────────────────────┘
```

1. 点击「▶ 运行」直接用默认 mode（`full`，超大项目可设默认 `test-only`）
2. 点箭头展开模式菜单，选其他模式
3. `POST /api/test/run/<name>` body 带 `{mode}`
4. 卡片进入运行状态：
   - 按钮变为 `⏹ 停止`
   - 显示当前阶段（按 mode 过滤）：`⚙ 配置…` → `🔨 编译…` → `🧪 测试…` → `📊 采集…`
   - `test-only` 模式下直接从 `🧪 测试…` 开始
   - 进度条
5. 轮询 `/api/test/status`（2s 间隔，一次拉所有运行中项目）
6. 完成后：
   - 卡片自动刷新显示新测试结果
   - Toast 提示：`✅ deepin-image-viewer: 465/465 通过, 行覆盖 82.2%`
7. 失败时：
   - 卡片显示 `❌ 失败` + 失败阶段
   - 抽屉中显示失败用例详情

**批量运行**（看板工具栏）：

```
┌─ 看板工具栏 ──────────────────────────────────────────┐
│ ●server已连接  [▶ 运行所有本地项目] [🔄刷新MCP] [📥] │
│                   ↑ 批量提交，受 max_concurrent 限制   │
│                   并行槽满的项目自动排队               │
└──────────────────────────────────────────────────────┘
```

- 点击「运行所有本地项目」→ 所有 `source.path` 非空的项目提交
- 超过 `max_concurrent` 的项目排队（state=`queued`）
- 每完成一个，队列自动推进

**抽屉中的详细运行面板**：

```
┌─── 运行测试 ────────────────────────────┐
│                                          │
│  阶段: 🔨 编译中 (60%)                  │
│  ⏱ 已用: 45s                            │
│                                          │
│  ┌── 日志 ──────────────────────────┐   │
│  │ [ 72%] Building CXX object...    │   │
│  │ [ 75%] Linking deepin-viewer-test│   │
│  │ [ 80%] ...                       │   │
│  └──────────────────────────────────┘   │
│                                          │
│  [⏹ 停止]                               │
│                                          │
│  ─── 完成后显示 ───                      │
│  ✅ 465/465 通过                         │
│  📊 行覆盖 82.2%  函数覆盖 99.5%        │
│  ⏱ 耗时: 2m 15s                         │
│  [📊 查看覆盖率(iframe)]  [在新窗口打开] │
└──────────────────────────────────────────┘
```

### 3.7 安全与资源

- **不引入 Docker 隔离**，直接在本机运行
- **只允许运行 `projects.json` 中 `source.path` 非空的项目**
- **不清理 build_dir**：只清理 `report/`、`html/` 纯产物目录，保留编译缓存
- **命令执行限定在项目目录内**（`cwd=source.path/build_dir`）
- **超时保护**：默认 600s（可配），超时自动 kill 进程组
- **并行限制**：`max_concurrent` 防止 CPU 过载（太多并行都慢）
- **不暴露任意命令执行**：只运行 `phases` 里预设的命令
- **运行前提示**：检查 `git status` 是否有未提交修改（提醒用户，不强制阻断）
- **进程组 kill**：`os.killpg(os.getpgid(pid), SIGTERM)` 确保子进程全终止

---

## 4. 前端架构扩展

### 4.1 UI 设计一致性规范

新增的所有 UI 必须与现有看板/编辑器设计语言一致，核心原则：**零新颜色、零新字号、零新圆角**，全部复用 `styles.css` 已有的 CSS 变量和组件类。

#### 4.1.1 组件复用映射表

每个新 UI 元素都映射到一个现有组件类，不另起炉灶：

| 新 UI 元素 | 复用现有类 | 备注 |
|-----------|-----------|------|
| 看板卡片「本地测试」区 | `.dash-card` 内嵌区块 | 卡片整体不新建类，只加一段子内容 |
| 本地状态徽章 🏠✅❌⏸🔄 | `.level-badge` 风格 | 同 padding/border-radius/font-size；配色见 4.1.3 |
| 测试通过数行 `465/465` | `.dash-cover` | 现有「覆盖 X/Y」行样式 |
| 行覆盖率进度条 | `.bar-track` + `.bar-fill` | 现有进度条，不加新类 |
| 运行阶段进度条 | `.bar-fill.warn` | 复用 warn 色 |
| 阶段模式下拉「▶ 运行 ▾」 | `.export-menu-wrap` + `.export-dropdown` + `.export-dropdown-item` | 与看板排序/规模下拉完全同款 |
| 「▶ 运行」按钮 | `.btn .btn-sm .btn-primary` | 现有主按钮 |
| 「⏹ 停止」按钮 | `.btn .btn-sm`（border-color: `var(--danger)`） | 复用 .btn-sm，仅换边框色 |
| 运行实时日志 | `.sync-log` | 现有同步日志：mono + 暗底 + scroll |
| 多项目运行浮层 | `.sync-float` + `.spin` | 现有同步浮层同款 spinner |
| 覆盖率抽屉 | `.dash-drawer` + `.dash-drawer-overlay` | 复用看板抽屉，不新建面板 |
| 覆盖率 iframe 容器条 | `.gh-panel-header` 风格 | 复用 GitHub 面板 header 布局 |
| 新统计卡（✅本地通过/❌失败） | `.stat-card` | 现有统计卡，仅换 `.v` 的 color |
| 失败用例行 | `.gap-item` | 现有抽屉 gap-item 同款 |
| 测试套件列表行 | `.class-method` 风格 | 现有类树方法行：缩进 + mono 名 |
| 空状态（无本地项目/无报告） | `.empty-state` | 现有空状态 |
| 设置页测试/覆盖率配置区 | `.cfg-grid` + `.cfg-field` + `.cfg-input` | 现有设置表单 |
| 路径选择按钮 | `.btn .btn-sm .btn-ghost` | 现有幽灵按钮 |

#### 4.1.2 新增 CSS 集中管理

确实需要的新类（如 `.coverage-iframe-wrap`、`.test-phase-row`），**统一加到 `styles.css` 末尾的对应区块**，且必须只引用现有变量：

```css
/* ✅ 正确：复用现有变量 */
.coverage-iframe-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg);          /* 不是 #0f172a */
  border-top: 1px solid var(--border-soft);
}
.coverage-iframe-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;             /* 与 .gh-panel-header 一致 */
  background: var(--bg-elev2);
  border-bottom: 1px solid var(--border-soft);
}

/* ❌ 错误：硬编码颜色/字号 */
.coverage-iframe-wrap {
  background: #0f172a;            /* 不允许 */
  font-size: 13px;                /* 不允许，要用 calc(var(--fs)*.xxx) */
}
```

#### 4.1.3 状态色规范（复用语义色，不自创新色）

本地测试的 5 种状态全部映射到现有语义变量：

| 测试状态 | 图标 | 用色变量 | 现有出处 |
|---------|------|----------|----------|
| 通过 | ✅ | `--accent` / `--high` | level-high、bar-fill |
| 失败 | ❌ | `--danger` / `--exempt` | dash-gap.risk、level-exempt |
| 运行中 | 🔄 | `--info` | bar-fill.info、code-link |
| 排队/待运行 | ⏸ | `--text-muted` / `--low` | level-low |
| 编译阶段 | 🔨 | `--warn` / `--mid` | bar-fill.warn、level-mid |

状态徽章统一用 `.level-badge` 的尺寸规格（`padding:2px 8px; border-radius:6px; font-size:calc(var(--fs)*0.786)`），只换 background/color/border 的变量：

```css
.status-pass  { background: rgba(34,197,94,.18);  color: var(--high);    border-color: rgba(34,197,94,.3) }   /* = level-high */
.status-fail  { background: rgba(239,68,68,.18);  color: var(--exempt);  border-color: rgba(239,68,68,.3) }   /* = level-exempt */
.status-run   { background: rgba(56,189,248,.18); color: var(--info);    border-color: rgba(56,189,248,.3) }
.status-queue { background: rgba(100,116,139,.18);color: var(--low);     border-color: rgba(100,116,139,.3) }  /* = level-low */
.status-build { background: rgba(245,158,11,.18); color: var(--mid);     border-color: rgba(245,158,11,.3) }   /* = level-mid */
```

> 这些类的色值与现有 `.level-*` 完全一致，只是语义名不同（测试状态 vs 方法优先级），便于 JS 按语义赋类。

#### 4.1.4 字号 / 过渡 / 圆角 统一

全部沿用现有系数，不引入新值：

| 用途 | 系数 | 现有出处 |
|------|------|----------|
| 区块标题 | `1.071`–`1.143` | `.detail-heading` / `.detail-title` |
| 正文 | `0.929` | `.detail-body` |
| 按钮/次要 | `0.857` | `.btn` / `.dash-levrow` |
| 徽章/元信息 | `0.786` | `.level-badge` / `.dash-cover` |
| 极小标注 | `0.714`–`0.643` | `.factor-pill` / `.dash-meta` |

过渡时长：hover `.15s`–`.18s`，进度条 `.3s`–`.4s`，抽屉 `.3s`。圆角：按钮 `6px`–`8px`，卡片 `12px`，徽章 `5px`–`6px`——与现有逐级一致。

#### 4.1.5 覆盖率主题 css 对齐 UTIE 配色（特例）

`assets/coverage-themes/utie-light.css` / `utie-dark.css` 是注入到 iframe 内的 LCOV 主题，**iframe 里没有 UTIE 的 `:root` 变量**，所以这两份 css 必须用**硬编码色值**，但色值要与 `styles.css` 的变量值严格一致：

```css
/* utie-light.css —— 色值取自 :root[data-theme='light'] */
body { color: #0f172a; background: #f8fafc; }            /* = --text / --bg */
td.headerCovTableEntryHi { color: #16a34a; }             /* = --high */
td.headerCovTableEntryMed { color: #d97706; }            /* = --mid */
td.headerCovTableEntryLo  { color: #dc2626; }            /* = --danger */
/* ... LCOV 固定 class 名，色值对齐 UTIE light */
```

这样 iframe 内的 LCOV 报告与外层 UTIE 看板视觉融为一体，切换 light/dark 主题时 server 按 `coverage.theme` 返回对应 css。

#### 4.1.6 一致性自检清单

提交前逐条核对：

- [ ] 没有出现 `#xxxxxx` 硬编码色（`styles.css` 内），都用 `var(--xxx)`
- [ ] 没有出现 `font-size: Npx`，都用 `calc(var(--fs)*系数)`
- [ ] 没有新建独立 css 文件（覆盖率主题 css 除外，它是 iframe 内用的特例）
- [ ] 新按钮都带 `.btn` 基类 + 尺寸类（`.btn-sm`）
- [ ] 新徽章尺寸与 `.level-badge` 一致
- [ ] 新下拉复用 `.export-dropdown`，不自建
- [ ] 新进度条复用 `.bar-track`/`.bar-fill`
- [ ] 新空状态用 `.empty-state`
- [ ] hover 过渡时长在 `.12s`–`.18s` 区间
- [ ] light/dark 主题切换后新组件表现正常（无硬编码导致的反色 bug）

### 4.2 视图规划

近期新增一个「🧪 测试」视图 tab，覆盖率主要在抽屉内嵌 iframe：

```
[📋 编辑器]  [📊 看板]  [🧪 测试]  [⚙ 设置]
                          ↑
                    本地测试总览 + 运行控制
```

> 独立「📈 覆盖率」全屏 tab 为 Phase 2 可选项。

### 4.3 状态扩展

```javascript
S.test = {
  // 按项目测试结果
  results: new Map(),       // projectName → test-results JSON
  
  // 运行状态（多项目并行）
  running: new Map(),       // projectName → {state, phase, progress, log_tail, started_at}
  maxConcurrent: 2,
  
  // 轮询
  pollTimer: null,
}
```

### 4.4 新增 JS 模块

```
js/
├── core.js          # 不变
├── editor.js        # 不变
├── github.js        # 不变
├── dashboard.js     # 小改（卡片增强 + 本地数据 + 运行状态）
├── settings.js      # 小改（build 配置 + 并行数配置）
├── test-runner.js   # 🆕 测试运行管理 + 覆盖率 iframe
└── app.js           # 路由扩展
```

### 4.5 iframe 组件

```html
<!-- 抽屉内的覆盖率 iframe 区 -->
<div class="coverage-iframe-wrap" id="coverage-iframe-wrap" hidden>
  <div class="coverage-iframe-bar">
    <span class="cov-title">📊 覆盖率报告</span>
    <button class="btn btn-sm btn-ghost" id="cov-refresh">↻ 刷新</button>
    <button class="btn btn-sm btn-ghost" id="cov-newtab">↗ 新窗口</button>
    <button class="btn btn-sm btn-ghost" id="cov-close">✕</button>
  </div>
  <iframe id="coverage-iframe" 
          class="coverage-iframe"
          src="" 
          sandbox="allow-same-origin allow-popups">
  </iframe>
</div>
```

配套 CSS（加到 `styles.css` 末尾，只引现有变量，遵循 4.1.2）：

```css
.coverage-iframe { width: 100%; flex: 1; border: 0; background: var(--bg); }
```

- `sandbox="allow-same-origin allow-popups"`：允许同源加载（访问 `/api/coverage/*`），允许新窗口打开，但禁脚本执行（LCOV 报告不需要 JS）
- 实测 LCOV HTML 是纯静态表格，无 JS，sandbox 安全

---

## 5. AI 补全单元测试（保留设计，暂缓实施）

> 本节为设计储备，**本期不实施**，待本地测试链路稳定后再启动。

### 5.1 核心场景

用户在看板/编辑器中看到高优无覆盖的方法，希望一键让 AI 生成 GTest 用例：

```
  BaseUtils::hash  ·  high  ·  无测试覆盖
  ─────────────────────────────────────
  点击 [🤖 AI 补全] →

  AI 分析：
  1. 读取方法签名（从 inventory: qn, signature）
  2. 读取源码上下文（源文件 + 头文件，依赖本地 source.path）
  3. 读取已有测试文件模式（tests/src/ut_*.cpp 的风格）
  4. 生成 GTest 用例代码
  5. 用户审核 → 写入测试文件 → 重新运行测试
```

### 5.2 数据需求

| 数据 | 来源 | 说明 |
|------|------|------|
| 方法签名 + 修饰 | inventory `qn`, `signature` | 函数名、参数、返回值 |
| 所属类 | inventory `class_qn` | 确定测试文件归属 |
| 源码 | `source.path` + `file_path` | 实现逻辑、边界条件 |
| 头文件 | 同目录 `.h` | 公共 API、类型定义 |
| 已有测试风格 | `tests/src/ut_*.cpp` | 保持命名、mock、stub 一致 |
| 项目依赖 | `CMakeLists.txt` | 可用库和框架 |

### 5.3 端点（储备）

```
POST /api/ai/suggest-tests          → 批量生成建议
GET  /api/ai/task/<id>              → 轮询生成进度
POST /api/ai/apply-suggestion       → 应用建议（写入测试文件）
```

### 5.4 调用方案（储备）

- **单方法快速建议**：dashboard-server.py 直连 LLM API（DeepSeek-Coder-V2 等），快，适合预览
- **批量高质量生成**：subprocess 调 pi agent（复用 `qt-unittest-make` skill，含编译验证闭环）

### 5.5 Prompt 模板（储备）

```python
AI_TEST_PROMPT = """你是一个 Qt/C++ 单元测试专家。请为以下方法生成 GTest 用例。

## 项目信息
- 项目: {project_name}
- 框架: Google Test (GTest)
- 构建: CMake + Qt{qt_version}
- 测试目录: {test_dir}/src/

## 目标方法
- 全限定名: {qn}
- 签名: {signature}
- 源文件: {file_path}:{line_number}
- 优先级: {level} (原因: {factors_summary})

## 源码上下文
```cpp
{source_code_context}
```

## 头文件
```cpp
{header_content}
```

## 项目已有测试风格参考
```cpp
{existing_test_style}
```

## 要求
1. 遵循项目已有的测试命名风格（如 `ClassName_MethodName_Scenario_ExpectedResult`）
2. 使用项目的 stub/mock 模式（参考已有 ut_*.cpp）
3. 每个方法生成 3-5 个测试用例，覆盖：正常路径、边界条件、异常输入
4. 如需访问私有成员，使用 `-fno-access-control`（项目已启用）
5. 如需 mock DTK/Qt 依赖，使用项目的 stub.h 模式
6. 添加 SPDX 版权头
7. 输出可直接追加到 {target_test_file} 的代码片段

## 输出格式
仅输出 C++ 代码，不要 markdown 包裹，不要解释。以 TEST() 宏开头。
"""
```

### 5.6 验证闭环（储备）

```
AI 生成建议 → 用户审核代码 diff → 确认应用 → 写入 tests/src/ut_*.cpp
  → 自动触发「▶ 运行测试」→ 新用例编译+运行
  → 结果反馈：✅ 全部通过 / ❌ N 个失败
  → 失败用例可一键回退（备份原文件）
```

---

## 6. 服务端架构扩展

### 6.1 端点汇总

| 端点 | 方法 | 状态 | 说明 |
|------|------|------|------|
| `/api/test/results/<name>` | GET | ✅ 本期 | 读取本地测试结果 |
| `/api/test/run/<name>` | POST | ✅ 本期 | 启动本地测试 |
| `/api/test/status` | GET | ✅ 本期 | 所有项目运行状态（批量轮询） |
| `/api/test/status/<name>` | GET | ✅ 本期 | 单项目运行状态 |
| `/api/test/stop/<name>` | POST | ✅ 本期 | 终止测试 |
| `/api/coverage/<name>/*` | GET | ✅ 本期 | 代理 LCOV HTML 报告 |
| `/api/ai/suggest-tests` | POST | 📌 储备 | AI 生成测试建议 |
| `/api/ai/task/<id>` | GET | 📌 储备 | AI 任务进度 |
| `/api/ai/apply` | POST | 📌 储备 | 应用 AI 建议 |

### 6.2 dashboard-server.py 扩展

```python
# ── 测试运行器（新增模块）──
class TestRunner:
    """分阶段执行测试链路（增量、不清理 build_dir），支持多项目并行"""
    def __init__(self, config, registry):
        self.max_concurrent = config.get("test", {}).get("max_concurrent", 2)
        self.executor = ThreadPoolExecutor(max_workers=self.max_concurrent)
        self.running = {}
        self.lock = threading.Lock()
    
    def run(self, project_name, mode='full'):
        """提交测试任务（受 max_concurrent 限制，超出的排队）"""
        ...
    
    def _run_project(self, project_name, mode='full'):
        """按 mode 执行选定阶段（configure/build/test/coverage/summary）
        只清理 report/html 纯产物，不清理 build_dir"""
        ...
    
    def status_all(self):
        """所有运行中项目状态"""
        ...
    
    def stop(self, project_name):
        """kill 进程组"""
        ...

# ── 覆盖率静态代理（新增路由）──
def serve_coverage(handler, name, sub_path):
    """代理本地项目 LCOV HTML 报告；拦截 gcov.css 注入 UTIE 主题（见 2.3）"""
    ...
```

### 6.3 配置扩展 `config.json`

```json
{
  "server": {"port": 8765, "host": "127.0.0.1"},
  "mcp_url": "http://10.8.12.80:13626/mcp",
  "github": {"org": "linuxdeepin"},
  "sync": {"concurrency": 1},
  "test": {
    "max_concurrent": 2,
    "default_timeout": 600,
    "build_dir_candidates": ["build-ut", "build-test", "build-ut-m3", "build"]
  },
  "coverage": {
    "theme": "utie-auto",
    "themes": {
      "utie-auto":  {"css": "assets/coverage-themes/utie-light.css", "override": true},
      "utie-dark":  {"css": "assets/coverage-themes/utie-dark.css",  "override": true},
      "original":   {"css": null, "override": false}
    }
  },
  "ai": {
    "enabled": false,
    "provider": "deepseek",
    "api_url": "",
    "api_key": "",
    "model": "deepseek-coder-v2",
    "max_tokens": 4096,
    "temperature": 0.3,
    "context_lines": 80,
    "max_cases_per_method": 5,
    "timeout": 120
  }
}
```

> `ai` 段本期不启用（`enabled: false`），设置界面可预留入口但置灰。

---

## 7. 实施阶段

### Phase 1: 本地单测结果展示 + 覆盖率 iframe（3-4 天）

**目标**：看板卡片显示本地测试结果，抽屉可内嵌 LCOV 报告

- [ ] `projects.json`：实验项目（image-viewer, calculator）填入 `source.path`，`source.type` 改数组
- [ ] `dashboard-server.py`：`/api/test/results/<name>` + `build_dir` 发现
- [ ] `dashboard-server.py`：`/api/coverage/<name>/*` 静态代理（复用 `_file`，路径穿越防护）
- [ ] `dashboard-server.py`：`gcov.css` 拦截注入（见 2.3），按 `coverage.theme` 返回 UTIE 主题
- [ ] `assets/coverage-themes/utie-light.css` / `utie-dark.css`（色值对齐 4.1.5）
- [ ] 前端 `dashboard.js`：卡片增强（复用 `.dash-card`/`.dash-cover`/`.bar-track`，按 4.1.1 映射表）
- [ ] 前端抽屉：测试结果详情（`.gap-item` 失败用例）+ iframe 覆盖率区（`.dash-drawer` 复用）
- [ ] 编辑器详情面板：方法「📊 覆盖率」深度链接到 `src/<file>.func.html`
- [ ] **UI 一致性自检**：跑一遍 4.1.6 清单，light/dark 双主题验证无反色
- [ ] 验证：image-viewer 的 `ut-summary.json` + `html/` 正确读取展示

### Phase 2: 触发单测运行（分阶段、可并行）（3-4 天）

**目标**：从看板一键运行测试，支持多项目并行、分阶段触发

- [x] `dashboard-server.py`：`TestRunner` 类 + `ThreadPoolExecutor` 并行
- [x] `/api/test/run`（带 `mode` 参数）、`/api/test/status`（批量）、`/api/test/stop` 端点 + `/api/test/phases` 探测端点
- [x] `projects.json`：`build` 字段新增 `phases` 结构（configure/build/test/coverage/summary）+ `build_dir`
- [x] `phases` 自动推断逻辑（`infer_phases` 探测测试二进制、ctest、gen-ut-summary.py）
- [ ] 设置界面：「从项目脚本导入」按钮（解析 test-prj-running.sh 填充 phases）— 待后续迭代
- [x] 设置界面：`max_concurrent` / `default_timeout` 配置（全局配置区新增 2 字段）
- [x] 增量编译逻辑（build_dir 无 CMakeCache.txt 才 configure，不清理 build_dir，仅清理 report/html）
- [x] 前端卡片「▶ 运行 ▾」下拉（5 模式）+ 运行状态徽章（`.test-phase-badge`）+ 日志面板（`.test-log-panel`）
- [x] drawer 内「运行测试」面板（模式选择 + 运行/停止 + 进度条 + 实时日志）
- [x] 完成后自动刷新测试结果 + 卡片状态更新
- [x] **UI 一致性自检**：复用 CSS 变量，新类符合 4.1.6 清单（0 新颜色/字号/圆角）
- [x] deepin-calculator 验证（无 build_dir → test-only 友好错误提示「请先用 full 模式编译」）
- [x] deepin-image-viewer 增量验证（已有 build/ → test-only 模式 1.3s 出结果 465/465）
- [x] stop 功能验证（终止后 state=stopped，不覆盖为 failed）
- [x] `ut-summary.json` 自动生成（summary 阶段无脚本时 server 从 gtest XML + coverage.info 聚合）
- [x] `collect_test_results` 增强：ut-summary.json 缺失时从 gtest XML 聚合测试数 + 从 coverage.info 解析覆盖率

### Phase 3（储备）: AI 补全单测

暂缓，待 Phase 1-2 稳定后启动。设计见第 5 节。

---

## 8. 待讨论点

### 8.1 已决定（根据反馈）

| 问题 | 决定 |
|------|------|
| 测试运行是否 Docker 隔离 | ❌ 不隔离，直接本机运行 |
| 覆盖率展示方式 | ✅ iframe 内嵌项目已有 LCOV HTML，不自解析 lcov |
| 覆盖率主题 | ✅ server 代理拦截 `gcov.css` 注入 UTIE 主题，不侵入项目源码 |
| 触发测试方式 | ✅ server 分阶段控制，**不调项目脚本**（它会 rm -rf 全量重编译） |
| 构建缓存 | ✅ 不清理 build_dir，增量编译；只清 report/html 纯产物 |
| 阶段触发 | ✅ 支持 full / test-only / test+coverage / build+test / coverage-only |
| 多项目并行 | ✅ 支持，设置界面配 `max_concurrent`，默认 2 |
| AI 补全 | 📌 保留设计，本期不实施 |
| UI 一致性 | ✅ 新组件零新色/零新字号，全部复用现有 CSS 变量与组件类（见 4.1） |

### 8.2 待确认

**Q1：`source.type` 数组化兼容性**
现有 `projects.json` 里 26 个项目都是 `type: "mcp"`（单值字符串）。改为数组后，向后兼容处理：
- 读到字符串 → 视为 `[该字符串]`
- 读到数组 → 直接用
这样对不对？还是直接批量改一遍数据文件？

**Q2：iframe sandbox 策略**
LCOV 报告实测无 JS，但 `genhtml` 高版本可能带排序脚本。`sandbox` 设 `allow-same-origin allow-popups`（禁脚本）最安全；如果某些 LCOV 功能依赖 JS，需放开 `allow-scripts`。先用禁脚本版本，遇到功能缺失再调整？

**Q3：测试运行前的 git 状态检查**
运行 `make` 会在 `build-ut/` 产生文件（已在 `.gitignore` 里通常），但 `cmake` 偶尔会在源码目录写东西。要不要运行前检查 `git status` 并提示用户 stash？还是完全信任 `.gitignore`？

**Q4：失败用例的 gtest XML 解析容错**
不同项目 gtest XML 格式略有差异（有的 `<testsuite>` 是根，有的 `<testsuites>` 是根）。`gen-ut-summary.py` 已有容错逻辑，我直接复用它的 `parse_gtest_xml` 函数，还是 server 里重新实现一份？

**Q5：「运行所有本地项目」是否默认勾选实验项目？**
批量运行时，是只运行有 `source.path` 的项目，还是用户可勾选要运行的项目集合？建议：默认所有本地项目，工具栏提供项目多选筛选。

---

## 9. 线框总览

```
┌───────────────────────────────────────────────────────────────────────────┐
│ 🔬 UT Inventory Editor           ●server已连接   [🌙] [S M L]          │
├───────────────────────────────────────────────────────────────────────────┤
│ [📋 编辑器] [📊 看板] [🧪 测试] [⚙ 设置]                              │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐     │
│  │ 总可测 │ │ 🟢高优 │ │ ⚠待复核│ │ MCP缺口│ │ ✅本地  │ │ ❌失败 │     │
│  │ 24,302│ │ 1,955  │ │  321   │ │ 2,014 │ │  465   │ │   0    │     │
│  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘     │
│                                                                           │
│  工具栏: [▶ 运行所有本地项目] [🔄刷新MCP] [搜索___]   并行:2/2           │
│                                                                           │
│  ┌───────────────────────────┐  ┌───────────────────────────┐            │
│  │ deepin-image-viewer XL 🏠│  │ deepin-calculator    M 🏠│            │
│  │ 🟢31 ⚖333 💤249          │  │ 🟢12 ⚖89 💤156           │            │
│  │ ⚠ 高优无覆盖: 31         │  │ ⚠ 高优无覆盖: 12         │            │
│  │ ✅ 465/465 | 行覆盖 82%  │  │ 🔄 编译中 60%             │            │
│  │ 🕐 8月13日 15:21         │  │ ⏱ 已用 45s                │            │
│  │ [▶ 运行▾] [📊覆盖率]     │  │ [⏹ 停止]                  │            │
│  └───────────────────────────┘  └───────────────────────────┘            │
│                                                                           │
│  ┌───────────────────────────┐  ┌───────────────────────────┐            │
│  │ deepin-pdfium        XL   │  │ dde-grand-search      L   │            │
│  │ 🟢521 ⚖2327 💤5075       │  │ 🟢139 ⚖817 💤1388        │            │
│  │ ⚠ 高优无覆盖: 521        │  │ ⚠ 高优无覆盖: 139        │            │
│  │ (仅 MCP 数据)             │  │ (仅 MCP 数据)             │            │
│  └───────────────────────────┘  └───────────────────────────┘            │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

抽屉展开（覆盖率 iframe）:
┌─ deepin-image-viewer ──────────────────────────────────────────┐
│ ✅ 465/465 | 行覆盖 82.2%   [▶运行▾] [📊覆盖率] [编辑器→] [✕]  │
├────────────────────────────────────────────────────────────────┤
│ 📊 覆盖率报告  [↻刷新] [↗新窗口] [✕]                           │
├────────────────────────────────────────────────────────────────┤
│ ┌─ iframe: /api/coverage/deepin-image-viewer/index-sort-f.html┐│
│ │ LCOV - code coverage report                                 ││
│ │ Lines: 3883/4721 82.2%   Functions: 420/422 99.5%          ││
│ │ ┌──────────────────────────────────────────────────────┐    ││
│ │ │ Directory    Line Cov   Func Cov                      │    ││
│ │ │ src/         82.2%      99.5%                         │    ││
│ │ │   baseutils  95.0%      100%       ← 点击下钻源码级   │    ││
│ │ │   filecontrol 78.3%     98.1%                          │    ││
│ │ └──────────────────────────────────────────────────────┘    ││
│ └─────────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────┘
```
