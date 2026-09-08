# 基于代码知识图谱的单元测试技能架构设计

> 版本 v2.3（白盒修订：基于 GitNexus 源码 /home/zhy/source/GitNexus，v1.6.10 核实；
> **只保留秒级路径**——数据面单一 REST 通道，不可用即硬终止，不降级分钟级 MCP 通道）。
> 替代旧 `.ut-inventory.json` 全量清单模式，可不兼容。
> 依据：《new_代码图谱MCP_使用文档.md》+ dde-file-manager 真机实测（2804 类 / 25041 方法 / 4084 文件 / 16894 调用边）+ 源码白盒核实。

---

## 1. 架构目标与约束

**目标**：以知识图谱为唯一事实源，产出可量化、可复现、可断点续跑的单元测试工作流。

**真机实测约束**（架构必须内建，不是可选项）：

| # | 约束 | 实测证据 | 架构对策 |
|---|------|----------|----------|
| C1 | MCP 服务端单会话敏感，并发触发全线 504 | `--parallel 2` 全失败 | 串行访问 + 客户端退避重试（已落地） |
| C2 | MCP 单页请求固定 ~40s 开销；**REST `/api/query` 通道存在且免分页**（实测全量骨架 23307 行 4.5s、聚合 0.7s） | MCP list_repos 一页 42s | **规划数据走 REST**（无会话无分页）；语义工具走 MCP；统计下推服务端聚合 |
| C3 | cypher 结果为 markdown 表格，多行文本截到首行 | 7607 字符文件只剩首行 78 字符 | **数据面不走 MCP cypher**：内容一律 REST 纯 JSON（天然免截断）；哨兵转义仅存量 mcp-scan.py 保留 |
| C4 | 符号 `content` 在**索引写入时**截断（源码 `csv-generator.ts`: `MAX_SNIPPET=5000` + `\n... [truncated]` = 5016，永久性）；但 **`File.content` 全量存储不截断**（源码注释 "intentionally NOT length-capped"） | 266 行方法 content 只回 46 行 | 行数用 startLine/endLine（永远精确）；**方法体完整重建：File.content + 行切片**（含无本地仓场景，cc_proxy 永远可精确） |
| C5 | 内容级全量不可行（业务方法内容总量 **9.9MB**，单方法内容库内截 5016 字符）；**骨架级全量可行**（REST 一次 4.5s / 5.7MB） | 23307 方法 content 合计 9.9MB；同查询去 content 仅 5.7MB 一次拉回 | **骨架级全量 + 内容级按需**（候选方法才拉 content） |
| C6 | 边全部存于 `CodeRelation` 单表，按 `type` 区分 | `-[:HAS_METHOD]->` 独立边表语法直接报错 | 统一用 `-[r:CodeRelation]-> WHERE r.type='…'`（官方文档 §4 的简写形式在本服务端**不可用**，属文档勘误） |

---

## 2. 总体架构：六层分层

```
┌─────────────────────────────────────────────────────────────┐
│ L5 质量度量层   report · 评分卡 · 覆盖对照 · 失败重试队列        │
├─────────────────────────────────────────────────────────────┤
│ L4 生成执行层   块上下文组装 → 用例生成 → 编译 → 运行 → 状态回写  │
├─────────────────────────────────────────────────────────────┤
│ L3 规划层       测绘 → 指标 → 分级 → 分块 → .ut-plan.json      │
├─────────────────────────────────────────────────────────────┤
│ L2 语义分析层   impact · trace · detect_changes ——「测什么、
│                 先测谁」；Community/Process 节点（聚类+流程）     │
├─────────────────────────────────────────────────────────────┤
│ L1 图谱访问层   数据面单通道：REST /api/query（免分页免 markdown  │
│                 截断，不可用即硬终止）+ MCP 仅 impact/trace/      │
│                 detect_changes 三语义工具；统一重试/缓存/错误分类 │
├─────────────────────────────────────────────────────────────┤
│ L0 接入层       REST 直连（HTTP POST，无会话）+ MCPClient         │
│                 （仅语义工具：单会话串行、退避重试）              │
└─────────────────────────────────────────────────────────────┘
                    全部状态落盘于 .ut-plan.json
```

分层规则：**L2 只回答问题不产数据，L3 只做决策不碰内容，L4 只按单取料**。
每层只与相邻层通信，任何一层可独立替换（如换图数据库只改 L0/L1）。

---

### 3.1 部署差异勘误（真机 tools/list + resources/list 实测）

本部署服务端与官方文档存在出入，架构以实测为准：

| 官方文档 | 实测 | 对策 |
|---|---|---|
| 17+ 工具（含 query/explain/pdg_query） | 17 个工具：cypher、context、search、impact、trace、detect_changes、list_repos + `api_impact/group_list/group_sync/route_map/tool_map/shape_check/check/rename/explain/pdg_query/query` | 契约/编排类工具（group_*、api_impact、route_map）属微服务场景，测试工作流不涉及 |
| 资源 `gitnexus://repo/{n}/clusters` / `processes` | MCP 资源不存在，**但数据在库**：`Community`（功能聚类）1169 个、`Process`（执行流程）275 个，cypher 直查可用；关联边 `MEMBER_OF` 5684、`STEP_IN_PROCESS` 946（真机实测） | 聚类/流程作为**图谱节点**纳入架构（§3.3），不经资源通道 |
| detect_changes / trace / impact 参数 | 实测齐全：`detect_changes(scope, base_ref, worktree, repo, branch)`、`trace(from_uid, to_uid, maxDepth, includeTests, repo…)`、`impact(target, direction, maxDepth, includeTests, repo…)`；**仅 MCP 暴露，REST 无对应端点（404 实测）** | 直接可用，语义分析走 MCP 通道 |
| REST `/api/query`（官方文档未提） | 实测存在：POST `{"cypher": …, "repo": …}` → `{"result":[…]}` 纯 JSON，**免分页、免 markdown 表格截断**；请求字段名是 `cypher` 非 `statement`（400 报错提示确认）；GET 形式 405 | 规划数据**唯一**数据通道（v2.3 起：REST 不可用即硬终止，不降级 MCP 分钟级通道） |
| REST `/api/graph` | 源码存在（`app.get('/api/graph')`，含 includeContent/stream 参数），本部署 **405**（网关或版本差异） | 不依赖；用 `/api/query` 等价实现（骨架/内容均可查） |
| Kuzu 函数子集 | `percentile_cont` / `split()` / `char()` / 一元 `round()` **不存在**；`MATCH (n:Macro)` 表不可用（500 实测）；可用：`count/sum/size/avg/coalesce/replace`、`File/Class/Method/Function/Community/Process/Struct` 等节点表 | 分位数与模块聚合**本地计算**；宏信息不走图谱节点表 |

## 3.2 工具选用矩阵（实测修正后）

官方 17+ 工具中，测试工作流选用 9 个，按「角色 × 成本档」定位。
成本档：● 聚合/元查询（秒级）｜◐ 分页行查询（页级 40s）｜○ 内容查询（单次，注意截断）。

| 工具 | 在测试流程中的角色 | 阶段 | 成本 |
|---|---|---|---|
| `list_repos` | 仓库在册确认、`lastCommit` 固化为 base 版本 | preflight | ● |
| `cypher`（仅 REST） | 骨架采集、指标计算、覆盖统计、**Community/Process 聚类与流程查询**——**唯一数据通道**：REST 免分页免截断（骨架全量 4.5s）；MCP 同名工具不用于数据面 | plan/select | ● |
| `context` | 单符号 360° 视图（定义+引用+所属流程）——生成期的语义包装 | generate | ○ |
| `search` | 测试骨架定位、符号消歧 | generate | ● |
| `impact` | 修改影响面 → 变更驱动模式的选块依据 | select（增量） | ● |
| `trace` | 被测方法 → 入口的最短调用链，用于判断「值得测/怎么测」 | plan/generate | ● |
| `detect_changes` | git 变更影响圈定 → 增量模式入口 | select（增量） | ● |
| `trace` | 被测方法 → 入口的最短调用链，用于判断「值得测/怎么测」 | plan/generate | ● |
| 目录前缀本地聚合 | 模块维度（`filePath` 顶层目录），块的分组与报告聚合依据（永不失效的兑底） | plan/report | 本地 |
| `Community` 节点 | 功能聚类（1169 个，`MEMBER_OF` 关联 5684）：聚类内高内聚（cohesion ≥0.9 的聚类优先建测）| plan/select | ● |
| `Process` 节点 | 执行流程（275 个，`STEP_IN_PROCESS` 946）：跨类流程场景提示——流程上的方法值得集成级用例 | plan/generate | ● |

**明确不选用**：`explain`（生成期 LLM 自身可解释代码）、`rename`/`pdg_query`（超出测试职责）。

设计要点：
- L2 的三个决策工具（impact/trace/detect_changes）把「测什么、先测谁、按什么增量」
  从**启发式规则**升级为**图谱语义查询**——这是相比旧 inventory 模式的本质增强；
- Community/Process 虽未暴露为 MCP 资源，但作为**图谱节点**始终可查（cypher 直查，
  REST 唯一数据通道）——聚类给「按功能域分组攻坚」，流程给「跨类集成场景」提示；
- 模块分组不依赖服务端：目录前缀聚合是纯本地确定性计算，作为聚类不可用时的兑底。

---

## 4. 规划数据模型 `.ut-plan.json`

唯一落盘状态。机器可读、断点续跑、全程可量化。**所有规模度量用字节与条目数等
确定性单位，不依赖任何模型侧估算。**

```jsonc
{
  "version": "2.1",
  "repo": "dde-file-manager",
  "base_commit": "76ad91358a21…",        // preflight 固化的图谱版本
  "survey": {                             // 测绘快照（survey 阶段）
    "classes": 2804, "methods": 25041, "files": 4084,
    "call_edges": 16894,
    "modules": {"src/dfm-base": 5210, "src/plugins": 11020, "…": 0},   // 目录前缀聚合：各模块方法数
    "top_in_degree": ["DConfigManager.instance(296)", "…"],           // 枢纽方法快照
    "top_clusters": [{"label": "Views", "cohesion": 0.92, "symbols": 108}],  // 高内凝聚类 top
    "top_processes": [{"label": "Blocker → X", "steps": 7}]            // 执行流程 top
  },
  "quantile": {                           // 分级阈值（plan 阶段算定，全程冻结 → 可复现）
    "lines":  {"p50": 6, "p75": 14, "p90": 31},
    "cc":     {"p50": 3, "p75": 9,  "p90": 22},
    "in_deg": {"p50": 1, "p75": 4,  "p90": 11}
  },
  "blocks": [                             // 块 = 分块单位（见 §6）
    {
      "block_id": "B0007",
      "kind": "class",                    // class | standalone | test-file
      "node_id": "Class:src/…/urlroute.cpp:UrlRoute#0",
      "name": "UrlRoute",
      "file_path": "src/…/urlroute.cpp",
      "cluster": "dfm-base-utils",        // 模块归属 = filePath 顶层目录（本地聚合）
      "methods": [
        {
          "id": "Method:src/…/urlroute.cpp:UrlRoute.regScheme#0",  // 主键，自描述
          "name": "regScheme",
          "lines": 42,                    // 图谱结构，精确
          "cc_proxy": 17,                 // 分支计数（File.content+行切片精确计算）
          "body_source": "file-slice",    // body 来源：file-slice | symbol-snippet（截断降级）
          "in_degree": 9, "out_degree": 3,
          "param_count": 4, "is_public": true,
          "tested": {"files": ["autotests/…/test_urlroute.cpp"], "cases": 12},
          "level": "high",                // high | mid | low
          "score": 0.83,                  // §5 公式，权重固化在脚本常量
          "content_bytes": 1712,          // cypher size() 实测，上下文组装依据
          "status": "pending"             // pending | done | failed | skipped
        }
      ],
      "level_summary": {"high": 2, "mid": 3, "low": 4},
      "priority": 0.61,                   // §5.3 排序键
      "context_bytes": 11200,             // Σ content_bytes + 邻接清单 ≈ 组装规模
      "status": "pending"
    }
  ],
  "stats": {"blocks": 2804, "methods": 25041,
            "high": 2100, "mid": 6800, "low": 16141}
}
```

模型决策：

- **主键 = 图谱节点 id**（`Method:<filePath>:<Class.name>#<重载序号>`，自描述、确定性、
  重载安全）。旧 qn 归一化（双轴调和的最大 bug 源）整体废弃；
- `content_bytes` 用 cypher `size(coalesce(n.content,''))` 服务端聚合取得——上下文组装
  规模在 plan 阶段就是**精确数**，不需要估算；
- 生成会话的容量控制按 `context_bytes`：**单块组装上限 48KB 文本（≈单次生成会话的
  稳妥容量）**，超限拆子块（每子块 ≤6 方法）。容量数字写在脚本常量区，可调可审。

---

## 5. 函数分级与量化体系

### 5.1 指标来源（全部零本地依赖，可复现）

| 指标 | 来源 | 性质 |
|---|---|---|
| `lines` | `endLine - startLine + 1` | 图谱结构，精确 |
| `cc_proxy` | 方法体内 `if/for/while/case/catch/&&/‖/?:` 计数 +1 | **精确**：方法体 = File.content（全量存储）按 startLine/endLine 本地行切片；符号 content 仅作切片失败时的降级（REST 拉取；源码白盒：符号级截断 5000 字符） |
| `in_degree/out_degree` | 调用边计数（服务端聚合） | 精确 |
| `param_count / is_public` | 节点属性 | 精确 |
| `tested` | 测试目录方法经调用边指向本方法（服务端聚合） | 精确 |

### 5.2 分级公式

```text
score = 0.35·s(lines) + 0.25·s(cc_proxy) + 0.25·s(in_degree)
      + 0.10·is_public + 0.05·min(param_count,5)/5

s(x) = 项目内分位归一（P50→0.25，P75→0.5，P90→0.75，≥P90→1.0，线性内插；本地计算）

level = high   score ≥ 0.55
      | low    存取器（^(get|set|is|has) 且 lines≤3 且 cc_proxy≤1）
      |        或（非 public 且 in_degree≤1 且 lines≤5）
      | mid    其余
```

分位数在 plan 阶段一次算定并冻结进 `quantile` → 同一 plan 内结果可复现；
跨项目随分布自适应。`in_degree` 让 `DConfigManager.instance`（入度 296）这类
枢纽方法自动浮出。

### 5.3 块优先级（资源投向依据）

```text
priority = high 方法占比 × log2(1 + 方法数)      // 高价值密集、体量适中的块先行
```

排序生成，保证有限资源始终花在刀刃上。旧「P75 入度单因子评分」废弃。

---

## 6. 分块设计

**一块 = 一个类（含其全部方法）**；无类独立函数按文件聚合为 `standalone` 块；
`autotests/` 下的类标 `test-file`，默认 skip。

为什么按类而不是按文件：类是 Qt 测试的天然单位（一个测试类对应一个被测类），
粒度均匀、失败隔离好、断点恢复代价小。

**上下文组装**（generate 阶段，每块一次生成会话）：

| 组成 | 来源 | 规模参考 |
|---|---|---|
| 块内方法体（File.content 行切片，REST 纯 JSON **免截断**，实测 7607 字符完整往返） | REST 按 file_path 批量 | ≈ Σ body_bytes |
| 一跳邻接清单（谁调我/我调谁：名字+路径，**不带内容**） | 调用边（REST 聚合） | ~60B/条 × ≤30 条 |
| 既有测试骨架（该类已有测试则给桩） | 测试文件边（REST，确定性） | 0–4KB |
| 固定提示词模板 | 本地 | ~4KB |

超 48KB 的块拆子块；**邻接只给清单不给内容**是控制规模的关键一手。

---

## 7. 三种运行模式（同一 plan 文件驱动）

| 模式 | 入口 | 选块逻辑 | 典型场景 |
|---|---|---|---|
| **建档模式** | `ut-plan run --mode full` | priority 降序全量推进 | 首次为仓库建测试资产 |
| **变更驱动模式** | `ut-plan run --mode delta` | `detect_changes` 圈定变更 → `impact`（upstream）扩到受影响方法 → 映射到块 | 日常提交后定向补测 |
| **单模块模式** | `ut-plan run --module <dir>` | 目录前缀（模块）选块 | 集中攻坚某功能域 |

三种模式共用同一份 blocks 状态与同一套分级——差异只在 select 阶段的选块策略。
变更驱动模式是旧方案完全没有的能力：图谱让「这次提交动了哪些可测逻辑、波及了谁」
变成一次 `detect_changes` + 一次 `impact` 查询。

---

## 8. 流水线阶段详设

```
preflight → survey → plan → select ⇄ generate（逐块循环）→ verify → report
```

### Phase 0 `preflight`
`list_repos` 确认在册、固化 `lastCommit` 为 `base_commit`；未在册/重试耗尽 → 终止。

### Phase 1 `survey`（测绘，聚合查询为主）

REST `/api/query` 聚合拿类/方法/文件/边计数（0.7s 实测）；目录前缀本地聚合出
模块清单与各模块方法数；`Community` 高内聚类（cohesion ≥0.9）与 `Process`
流程 top 各一查。产出 `survey` 节 + 报告头。**秒级。**

### Phase 2 `plan`（骨架 + 分级，REST 3-4 次请求）
1. 方法骨架全量（REST `/api/query` **一次拉回 23307 行，实测 4.5s**，含 id/name/filePath/
   startLine/endLine/parameterCount/isExported + `size(content)` 体量）；
2. 类清单同上一次拉回；
3. 调用入度聚合（`RETURN t.id, count(r) ORDER BY count(r) DESC`，REST 0.7s）；
4. 测试覆盖聚合（测试目录方法经边指向被测方法，瞬时）；
5. 候选方法的**方法体精确重建**：对「非 low 且 lines≥8」（约 6900 个）拉所在
   `File.content`（REST，按文件去重约 2000 次，纯 JSON 免截断）→ 按
   startLine/endLine 本地行切片得完整方法体 → cc_proxy 精确计算。
   符号级 `content` 库内截断 5000 字符（源码 `MAX_SNIPPET`），仅作切片失败时降级。

本地计算指标（分位数本地算——服务端无 percentile 函数）→ 分级 → 分块 → 写
`.ut-plan.json`。**此阶段零生成成本，纯计算，总耗时分钟级。**

### Phase 3 `select`
三种模式选块（§7）；默认跳过 `done` 且 `base_commit` 未变的块（增量幂等）。

### Phase 4 `generate`（逐块）
按 §6 组装上下文 → 生成用例 → 立即本地编译；通过 → 方法/块状态 `done`，
失败 → `failed` + 错误摘要（进重试队列）。**每块结束即原子写盘**，任意时刻可中断续跑。

### Phase 5 `verify` / Phase 6 `report`
跑测试、统计；report 全部从 plan 出数：进度（块/方法）、分级覆盖
（high 已测率 / mid 已测率 / 高优无覆盖清单）、按模块（目录前缀）聚合的模块热力、
失败队列。scorer 评分卡消费同一 plan。

---

## 9. 失效模式与降级链

| 失效 | 降级链 |
|---|---|
| 服务端 504/空响应 | 指数退避重试 → 仍败则该块标 `failed` 续跑下一块（不阻塞整批） |
| REST 通道不可用（404/网络策略变更） | **硬终止**并给出排查提示（v2.3 决策：只保留秒级路径，不降级分钟级 MCP 通道——与「仓库未索引硬终止」同一哲学） |
| 符号 `content` 截断（库内 5000 字符，源码实锤） | 方法体一律 File.content + startLine/endLine 行切片（REST 实测全量）；切片失败才降级符号 content 并标 `body_source` |
| 仓库未索引 | preflight 硬终止（不静默降级为空结果——旧方案的最大教训） |
| 图谱版本漂移（base_commit ≠ 当前 lastCommit） | report 标注漂移；select 拒绝复用旧块状态，提示重跑 plan |
| 本地仓库缺失 | 方法体走图谱重建（File.content REST 行切片，实测全量）；无本地则跳过编译，仅产出待检用例 |
| 服务端语法版本差异 | L1 统一封装边表写法（`CodeRelation`+type），屏蔽单表/独立边表差异（C6） |

---

## 10. 与旧 `.ut-inventory.json` 模式的对照

| 旧概念 | v2.1 处置 | 理由 |
|---|---|---|
| `qualified_name` 拼接归一 + 双轴调和 | **废弃** → 图谱节点 id | 归一化是历史最大 bug 源 |
| indexer 提供的 `complexity/cognitive` | `lines + cc_proxy` 双代理（方法体 File.content 行切片精确计算） | 图谱无此属性（真机确认 0/581） |
| P75 入度单因子评分 | 多因子分位归一（§5.2） | 单因子埋没冷门复杂方法 |
| `usecase_count`（docstring 用例） | `tested.cases`（调用边聚合） | 新图谱无 docstring |
| 一次性全量清单 | `.ut-plan.json` 块状态机 | 断点、量化、三种模式 |
| 「高/中/低分级 + testable」 | **保留**（公式升级） | 旧设计精神的核心延续 |
| 覆盖回写（test_cover_count） | 保留，改为节点 id 直连 | 消除匹配损耗 |

---

## 11. 落地路线

| 步骤 | 交付 | 验收标准 |
|---|---|---|
| R1 | L1 封装：REST 直连（唯一数据通道）+ MCP 语义工具客户端 + 聚合下推（沉淀为 `graph-access.py`） | dde-file-manager 规划数据采集 <2 分钟（骨架 4.5s 已实测） |
| R2 | L3：`ut-plan.py plan`（survey+plan 两阶段 → .ut-plan.json） | 25041 方法全部分级；分级分布合理（high ≤15%） |
| R3 | `select/generate/verify`：单块闭环（挑 1 个 high 块端到端） | 用例编译通过、plan 状态正确回写 |
| R4 | report + scorer 消费 v2 字段 | 报告可按模块（目录前缀）聚合 |
| R5 | 变更驱动模式（detect_changes+impact 选块） | 人为改动后仅生成受影响块的用例 |

R2 完成即可做**分级合理性评审**：抽 30 个 high/low 方法人工核对，通过后再进 R3。
