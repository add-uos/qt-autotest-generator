# GitNexus 代码图谱 MCP 适配分析报告

> 分析对象：qt-autotest-generator 全部脚本 vs 新 GitNexus MCP（`https://codegraph.uniontech.com/api/mcp`）
> 方法：真实服务探测验证（tools/list、cypher、context、list_repos 实测），非仅凭文档推断
> 日期：2026-02

---

## 0. 结论摘要

| 层面 | 结论 | 说明 |
|---|---|---|
| **传输层** | ✅ 兼容 | 现有 `MCPClient`（initialize 握手 / Mcp-Session-Id / SSE 解析）对 GitNexus **实测握手成功**；认证头走既有 `QTAG_MCP_HEADERS` 即可 |
| **工具层** | ❌ 全灭 | 脚本使用的 5 个旧工具名（`search_graph` / `query_graph` / `search_code` / `index_status` / `get_code_snippet`）在 GitNexus 17 个工具中**一个都不存在** |
| **查询方言** | ❌ 不兼容 | 旧 cypher 属性为 snake_case（`file_path`/`parent_class`/`base_classes`），GitNexus 为 camelCase（`filePath`），且 `parent_class`/`base_classes` **属性不存在**（改走 HAS_METHOD / EXTENDS 边）。实测旧查询直译报 `Binder exception` |
| **响应层** | ⚠️ 需新解析 | GitNexus 返回**字符串化 JSON**（非 dict），且查结果被包裹为 `{"markdown": "| 列名 |\n| --- |...", "row_count": N}` 表格、错误为 `{"error": ...}`+提示文本；`list_repos` 大分页时出现"多段 JSON 拼接"（需 `raw_decode`） |
| **数据完整性** | ⚠️ 5000 字符截断 | `m.content` 与 `context(include_content)` 的方法体**一律在 5016 字符截断**（实测 3 个 100+ 行方法全部恰好 5016）。长方法体必须改用 `filePath+startLine+endLine` 定位 + 本地仓库行切片 |
| **仓库覆盖** | ⚠️ 不全 | 实测 200+ 仓库中 dde-file-manager / dde-control-center / dde-session-shell 已索引 ✅；deepin-terminal / deepin-calculator / deepin-reader 未见 ❌ |

**核心判断**：传输层零改动；业务层需要一次性适配——建一个 GitNexus 数据访问层（方言翻译 + markdown 表解析 + 方法体按行切片 + graph 版本漂移校验），把 mcp-scan.py 的 16 个调用点全部收编。

---

## 1. 现状依赖盘点（脚本侧）

### 1.1 旧 MCP 工具调用点全清单（mcp-scan.py，共 16 处）

| # | 旧工具 | 调用点 | 用途 | 响应解析假设 |
|---|---|---|---|---|
| 1 | `search_graph` | L1112 | scan: 按 `label=Method` + file_pattern 分页枚举方法 | dict: `results/total/has_more/offset` 分页，行含 `qualified_name` |
| 2 | `search_graph` | L1142 | scan: `label=Function` 枚举自由函数 | 同上 |
| 3 | `query_graph` | L1187/1200 | scan: DBus Adaptor/Interface 检测（`Class.base_classes CONTAINS`） | dict: `rows` 二元组行 |
| 4 | `query_graph` | L1213/1226 | scan: 并发基类 / GUI 基类检测（同上） | 同上 |
| 5 | `query_graph` | L1265 | scan: DBus slots（`Method.parent_class CONTAINS`） | 同上 |
| 6 | `search_code` | L1299 | scan: Q_INVOKABLE 全文检索（mode=full, 返回 source） | dict: `results[].source/qualified_name` |
| 7 | `search_code` | L1322 | scan: Q_PLUGIN_METADATA 文件级检索 | dict: `files[]` |
| 8 | `index_status` | L1424 | fetch: 取图谱 git.head_sha 作为 base_sha | dict: `git.head_sha` |
| 9 | `get_code_snippet` | L1856 | extract-branches: 按 `qualified_name` 拉方法体 | dict/str：字段名轮询（body/code/source/snippet/implementation/text） |
| 10 | `search_graph` | L2282 | test-mapping: `label=Module, file_pattern=tests/**` 发现测试模块 | 同 #1，行含 `out_degree` |
| 11 | `query_graph` | L2335 | test-mapping: `Module-[:CALLS]->target` | dict: `rows` |
| 12 | `query_graph` | L2347 | test-mapping: 按 file_path 查 CALLS（v0.10.8+ 兜底） | 同上 |
| 13 | `query_graph` | L2387 | test-mapping: Module CALLS 出边 schema 探测（count） | 同上 |
| 14 | `search_graph` | L2457 | test-mapping: `Function name_pattern=TEST_F`，读 `signature` 字段 | 同 #1，行含 `signature/docstring` |

### 1.2 受影响脚本

| 脚本 | 影响方式 | 程度 |
|---|---|---|
| `mcp-scan.py`（2883 行） | 16 个调用点 + MCPClient 语义（响应是 str 不是 dict）+ 查询方言 + `qualified_name` 格式 | **重构核心** |
| `test-review.py` | `run_branch()` 子进程调 `mcp-scan.py extract-branches`，透传 `--mcp-url`；认证需经 `QTAG_MCP_HEADERS` 环境变量 | 中（入口参数需加 auth 透传） |
| `utq.py` | `test_cover_count` 消费 test-mapping 产出（间接） | 低（继承适配） |
| `stale-test-cleanup.py` | 消费 fetch 的增量 diff JSON（间接） | 低（继承适配） |
| `mutation-score.py` / `self-check-structural.py` | 仅注释提及 MCP，无实际调用 | 无 |
| scorer（`.pi/` score.py） | 消费 `.ut-inventory.json` + branch-check 产物，不直接连 MCP | 无 |
| `SKILL.md` / references | Mode 0–5 硬门禁文案写死 `codebase-memory-mcp` | 文档需同步 |

### 1.3 传输层实测证据

```
initialize 握手（protocolVersion 2024-11-05）→ 成功
tools/list（带 Mcp-Session-Id）→ 17 工具返回正常
```

GitNexus 17 工具：`list_repos, query, cypher, context, detect_changes, check, rename, impact, explain, pdg_query, route_map, tool_map, shape_check, api_impact, group_list, group_sync, trace`。与我们工作流相关的仅 4 个：**cypher（主力）、context（符号消歧）、list_repos（仓库/版本）、query（语义检索，可选）**。`impact/trace/explain/pdg_query` 为交互式分析工具，脚本批量场景价值有限。

---

## 2. 数据模型差异（旧 → 新映射）

### 2.1 图谱模型

| 维度 | 旧 codebase-memory-mcp | GitNexus（实测确认） |
|---|---|---|
| 属性命名 | snake_case：`file_path`, `qualified_name`, `parent_class`, `base_classes` | camelCase：`filePath`, `startLine`, `endLine`；**无** `parent_class`/`base_classes` 属性 |
| 节点标签 | Method / Function / Class / Module | Method / Function / Class / **File**（Module 属 additional types，测试发现改用 File+filePath） |
| 边模型 | 直接 `[:CALLS]` | 统一 `[:CodeRelation {type: 'CALLS'}]` 单表带 type 属性 |
| 类→方法 | `m.parent_class CONTAINS 'X'` | `(c:Class)-[:CodeRelation {type:'HAS_METHOD'}]->(m:Method)` ✅实测 |
| 继承检测 | `c.base_classes CONTAINS 'X'` | `(c:Class)-[:CodeRelation {type:'EXTENDS'}]->(b {name:'X'})` ✅实测 |
| 符号唯一键 | `qualified_name`（`Ns::Class::method` 风格） | `uid`（`Method:src/path.h:Class.method#N~shape:...`，#N 为重载序号） |
| 方法体 | `get_code_snippet(project, qualified_name)` 专工具 | `m.content` 属性 / `context(uid, include_content)`——**均 5016 字符截断** |
| 版本信息 | `index_status → git.head_sha` | `list_repos → lastCommit` ✅（另含 `indexedAt`、`branch/branches`） |
| 分页 | `offset` + `has_more` | cypher 仅 `SKIP n LIMIT m`（**SKIP 必须在 LIMIT 前**，实测 `LIMIT n SKIP m` 报语法错） |
| 入度/出度 | 结果行自带 `in_degree/out_degree` | 无现成字段，需 `MATCH (src)-[:CodeRelation {type:'CALLS'}]->(m) RETURN count(src)` 聚合 |
| TEST_F 用例 | `Function name_pattern=TEST_F` + `signature` 字段 | TEST_F 是 Function 节点（实测存在，有 CALLS 出边），宏参数需从 `content` 解析 |
| 方法体兜底 | 无（snippet 拉不到即 SNIPPET_FETCH_FAILED） | `filePath+startLine+endLine` + **本地仓库行切片**（完整性保障） |

### 2.2 响应形态（需统一解析层）

实测三种形态：
1. **对象结果**（context/list_repos）：字符串化 JSON → `{"status": "found", "symbol": {...}}` / `{"repositories": [...]}`；list_repos 200 条时尾部有额外数据段（`Extra data`），必须 `raw_decode` 而非 `loads`
2. **表结果**（cypher）：字符串化 `{"markdown": "| 列名 |\n| --- |\n| v1 | v2 |", "row_count": N}`，单元格为 JSON 转义字符串
3. **错误**：`{"error": "Prepare failed: Binder exception: ..."}`（查询错误，可捕获重试/降级）；`Error: LadybugDB unavailable ...`（索引重建中，需重试语义）

---

## 3. 逐流程失败分析

### 3.1 Mode 0 同步 / Mode 1 重要性（`scan`/`fetch` 子命令）

| 失败点 | 后果 | 适配 |
|---|---|---|
| `search_graph(label=Method)` 不存在 | 全量方法枚举直接挂 | cypher：`MATCH (m:Method) WHERE m.filePath STARTS WITH 'src/' RETURN m.name, m.filePath, m.startLine, m.endLine SKIP..LIMIT..`（复杂度字段需本地从 content/行切片统计） |
| `qualified_name` 格式不同 | inventory 的 `class_qn/qualified_name` 语义变化 | 以 `uid` 为准（含文件路径+重载序号），inventory schema 兼容层保留旧字段名但填新值，或升版 inventory v2 |
| `base_classes CONTAINS` 直译报 Binder exception | DBus/并发/GUI 基类检测 4 处查询全挂 | 改 EXTENDS 边（见 2.1） |
| `parent_class CONTAINS` 直译报 Binder exception | DBus slots 检测挂 | 改 HAS_METHOD 边 |
| `search_code` 不存在 | Q_INVOKABLE/Q_PLUGIN_METADATA 检测挂 | Q_INVOKABLE：Method 节点有 `annotations` 属性（schema 确认）→ `WHERE 'Q_INVOKABLE' IN m.annotations`（需实测 C++ 注解是否入 annotations）；Q_PLUGIN_METADATA：降级为本地 grep 或 Macro 节点 |
| `index_status` 不存在 | base_sha 回退 unknown → reconcile 失明 | `list_repos` 取 `lastCommit` |
| `in_degree/out_degree` 字段缺失 | P75 重要性排序失据 | cypher CALLS 入度聚合（全图一次聚合，比旧的逐节点字段更高效） |
| `has_more/offset` 分页缺失 | 大项目枚举截断 | `ORDER BY m.filePath, m.startLine SKIP {off} LIMIT n`（SKIP 前置） |

### 3.2 Mode 6 分支白盒（`extract-branches`，test-review.py 依赖）

| 失败点 | 后果 | 适配 |
|---|---|---|
| `get_code_snippet(project, qualified_name)` 不存在 | 分支白盒整个流程挂 | cypher HAS_METHOD + `m.name` 匹配取 `uid/filePath/startLine/endLine`；**方法体改为本地行切片**（`filePath:startLine-endLine`） |
| 重载歧义（#1/#2/#3…） | 拉错方法体 → 分支误报 | inventory 有 `complexity/param` 信息则按 `parameterCount` 消歧；否则取 `#1` 并在 violations 里降级说明 |
| content 5016 截断 | 长方法分支缺失（静默漏报，**最危险**） | 行切片方案天然免疫；保留 `m.content` 作为本地文件缺失时的降级（≤5000 字符方法） |
| 仓库未索引 / 索引落后本地 | 白盒拿到旧分支集合 | 前置检查：`list_repos` 的 `lastCommit` vs 本地 `git rev-parse HEAD`，不一致时告警（新能力，旧流程没有） |
| LadybugDB 重建锁（实测遇到） | 查询报错 | call_tool 重试语义 + 明确报错文案 |

### 3.3 test-mapping / utq 数据链

| 失败点 | 适配 |
|---|---|
| `label=Module, file_pattern` 不存在 | File 节点 + `WHERE f.filePath STARTS WITH 'autotests/' OR ... CONTAINS 'tests/'`；ut_* 过滤逻辑本地保留 |
| `out_degree` 预筛 | CALLS 聚合替代（`WHERE EXISTS` 或 count 分组） |
| `Function name_pattern=TEST_F` + signature | TEST_F Function 节点 `content` 解析 `(suite, case)`；行内容短无截断风险 |
| v0.10.8 Module-CALLS schema 探测逻辑 | 不再需要（GitNexus CALLS 直接可用），探测代码可简化 |

### 3.4 运维面

| 失败点 | 说明 |
|---|---|
| 认证 | `Authorization: Basic ...`（gitnexus:gitnexus.1122）——走 `QTAG_MCP_HEADERS='{"Authorization": "Basic ..."}'` 或新增 `--mcp-header` 参数（推荐，CI 友好） |
| 项目名 | 旧 `--project` 值若非 GitNexus 仓库名需映射（实测大部分同名） |
| 覆盖缺口 | deepin-terminal/calculator 等未索引 → extract-branches 优雅降级已有（无 inventory 场景实测过），但需把"未索引"从"拉取失败"提升为**一等状态**输出 |
| 多分支 | GitNexus 支持 `branch` 参数（工具普遍带），旧流程无此概念；ignore 即可，默认主分支 |

---

## 4. 适配方案（建议）

### 4.1 架构：新增 GitNexus 数据访问层，不动业务逻辑

```
mcp-scan.py
├── MCPClient          —— 保留（传输层实测兼容，仅默认 URL/headers 调整）
├── ResponseCodec      —— 新增：str→dict（raw_decode）+ markdown 表→rows + 错误分类
└── GitNexusAdapter    —— 新增：业务语义接口，内部翻成 cypher/context/list_repos
    ├── find_methods_by_file_prefix()   # 替代 search_graph(label=Method)
    ├── find_free_functions()
    ├── find_classes_extending(base)    # 替代 base_classes CONTAINS
    ├── find_methods_of_class(cls)      # 替代 parent_class CONTAINS
    ├── method_annotations(name/cls)    # 替代 search_code(Q_INVOKABLE)
    ├── calls_in_degree()               # 替代 in_degree 字段
    ├── discover_test_modules()         # 替代 Module search
    ├── collect_calls_from_tests()      # 替代 CALLS 查询
    ├── fetch_test_cases()              # TEST_F content 解析
    ├── repo_head_sha()                 # 替代 index_status
    └── fetch_method_body(repo_root, uid_info)  # 行切片为主 + content 降级
```

`extract-branches / scan / fetch / test-mapping` 四个子命令的**校验规则、输出 JSON schema、violation 格式全部不变**——下游（test-review.py、scorer、utq、stale-cleanup）零改动。

### 4.2 关键设计决定（需确认）

1. **方法体来源改为「图谱定位 + 本地行切片」**：GitNexus 的 `filePath/startLine/endLine` 精确到行（实测可信），本地仓库与图谱同源。附 drift 校验（lastCommit vs 本地 HEAD），比旧流程（纯信远端 snippet）更强。Iron Law #12 的本意（防止本地工作区漂移污染）由 drift 校验继承。
2. **`--project` 兼容**：先 `list_repos` 校验存在性，缺失时报"未索引"一等状态而非静默失败。
3. **分页统一 SKIP-before-LIMIT**，按 `filePath, startLine` 排序保证稳定翻页。
4. **MCP 传输兼容期**：GitNexusAdapter 按 URL/工具探测自动选择后端？——**不建议**双栈维护。旧 MCP 已知将退役，直接切换 + 保留旧代码路径为 fallback（`search_graph` 报 unknown tool 时提示迁移）。简化为：GitNexus 优先，旧行为仅在 `--legacy` 时启用。

### 4.3 单元测试（硬要求）

- `ResponseCodec`：三种形态 × 边界（截断 JSON、多段拼接、SSE 混入、空表、error+Next 提示）
- `GitNexusAdapter` 查询构造：EXTENDS/HAS_METHOD/CALLS/SKIP 分页的语句生成与行解析（mock 服务）
- 方法体切片：content 截断场景、重载消歧、行范围边界（startLine/endLine 偏差 ±1 实测容错）
- drift 校验：lastCommit ≠ HEAD 时输出与降级路径
- 回归：extract-branches 输出 JSON schema 不变的断言

---

## 5. 实测证据索引

| 探测 | 结果 |
|---|---|
| initialize + tools/list | ✅ 17 工具（协议兼容） |
| `list_repos` | ✅ 200+ 仓库；dde-file-manager/control-center/session-shell 在册；lastCommit 可用；响应需 raw_decode |
| `cypher` Method 全属性 | ✅ id/name/filePath/startLine/endLine/content 等（camelCase） |
| 旧查询直译 `parent_class`/`base_classes` | ❌ Binder exception（属性不存在） |
| HAS_METHOD / EXTENDS / CALLS(测试文件) | ✅ 三者实测可用，TEST_F 为 Function 节点 |
| content 完整性 | ❌ 5016 字符硬截断（3 个长方法 + context(uid) 双通道一致） |
| SKIP 分页 | ✅ `SKIP n LIMIT m`（此序）；`LIMIT n SKIP m` 语法错误 |
| `context(name=...)` | ambiguous 时返回 candidates（uid 消歧入口） |
| dde-session-shell cypher | ⚠️ 索引重建中 LadybugDB 锁（需重试语义） |

---

## 6. 实施与真机冒烟结论（feat/gitnexus-mcp 分支，2025 完成）

### 6.1 决策修订（相对 §4.2）

- §4.2 第 4 条「兼容期双栈」最终**否决**：不留旧工具路径，单栈直切（GitNexus 唯一后端）。
  旧 MCP 退役时无过渡负担；输出 JSON schema 靠字段名对齐保持不变（uuid → qn）。
- base_sha 语义确定：`resolve_base_sha` 默认取 `list_repos.lastCommit`（图谱基线），
  `--base-sha` 显式覆盖保留；check_drift 在 fetch 前自动警告本地 HEAD 与图谱的漂移。

### 6.2 冒烟发现并已修复的真机问题

| # | 问题 | 修复 |
|---|---|---|
| 1 | `cypher`/`context` 缺 `repo` 参数报 "Multiple repositories indexed" | MCPClient 在 paginate/cypher_rows/context 统一注入 `repo` |
| 2 | 标签析取不支持：`(t:Method OR t:Function)`、`(t:Method\|t:Function)` 均 Parser exception | in_degree_map / collect_all_calls 去掉标签谓词（CALLS 只指向可调用节点，语义无损） |
| 3 | `_method_row` 输出缺 `startLine` 键 → `_assign_qualified_names` 重载消歧 KeyError | `_method_row` 补 `startLine` 字段（单测 `_rows` 自带该键故未暴露，真机才炸） |
| 4 | dde-file-manager 图谱快照测试文件命名 `test_*.cpp`（旧惯例），UT_FILE_PATTERN 仅匹配 `ut_*` → 测试模块发现为 0 | 模式扩为 `(?:ut\|test)_\w+\.(?:cpp\|h)$`，两代命名兼容 |
| 5 | `type(r)` 函数不存在（"function TYPE does not exist"） | 关系类型一律走边属性 `r.type`（`[:CodeRelation {type:'...'}]`） |
| 6 | 边类型集合实测 | ACCESSES/CALLS/CONTAINS/DEFINES/EXTENDS/HAS_METHOD/HAS_PROPERTY/IMPORTS/MEMBER_OF/METHOD_OVERRIDES/STEP_IN_PROCESS；Class→Method = **HAS_METHOD** |

### 6.3 冒烟通过项（dde-file-manager @ 871cb5316，图谱 76ad9135）

- **fetch**：端到端 ✅（638 方法入册、宏扫描 3061 文件（Q_INVOKABLE 53/10 类、Q_PLUGIN_METADATA 45）、
  P75=2、test_* 回写 26 方法、drift 警告正常）
- **extract-branches**：UrlRoute 25 方法全 checked、0 violations（本地切片真实取到方法体）
- **test-mapping --dry-run**：750 测试模块（test_* 命名）、1661 CALLS 边、26/638 回写 + 报告
- **test-review.py**：`--mcp-url` 既有透传 ✅；本次补 `--repo-root` 透传

### 6.4 测试套件

- `test_gitnexus_codec.py`（codec/重试/本地度量/宏扫描）、`test_gitnexus_adapter.py`
  （FakeCypherClient + tmp_path 仓库 fixture，58 用例）、`test_mcp_normalize.py`、
  `test_fetch_mcp_data.py`、`test_fetch_test_mapping.py` 全部改写
- 全量 pytest：897 passed / 9 failed（均为迁移前基线失败：coverage fixture KeyError 'brda'，
  与本次无关）
