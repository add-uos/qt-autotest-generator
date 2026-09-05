# GitNexus 代码图谱 MCP 使用指南

> 本文取代原 codebase-memory-guide.md（旧提供方已下线）。假设 MCP 端点已配置（默认
> `https://codegraph.uniontech.com/api/mcp`，可用 `QTAG_MCP_URL` 覆盖）。

GitNexus 是**平台统一索引的仓库级代码图谱**：整个仓库预解析成一张图（文件/类/方法/函数/
调用边/继承边），毫秒级查询调用链与传递依赖。与旧 codebase-memory-mcp 的根本差异：

- **单栈**：无 remote/local 双提供方，无本地索引概念。仓库由平台索引与同步，
  技能侧**不能**自行触发索引；本地 HEAD 领先图谱 lastCommit 即为漂移，只能等待同步。
- **双源**：图谱只当**索引**（定位类/方法/边）；方法体、复杂度、签名、Qt 宏扫描一律从
  `--repo-root` 本地检出**切片/计算**。图谱的 content 字段在 5016 字符截断，仅作降级。
- **硬门禁**：仓库未索引（`list_repos` 查不到）→ 硬终止不回退（mcp-scan.py 内建校验）。

## 1. 连接与认证

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `QTAG_MCP_URL` | `https://codegraph.uniontech.com/api/mcp` | MCP HTTP 端点 |
| `QTAG_MCP_HEADERS` | 内置 Basic 认证头 | 额外请求头，JSON 字符串 |
| `QTAG_MCP_API_KEY` | 空 | `X-API-Key` 认证头（与 HEADERS 二选一） |

## 2. MCP 工具（仅三个）

### 2.1 `list_repos` —— 索引确认与 base_sha

```json
{"tool": "list_repos", "args": {"limit": 100, "offset": 0}}
```

- 返回 `{"repositories": [{"name", "lastCommit", "branch", "indexedAt"}, ...]}`
- **`lastCommit` 即 base_sha**：inventory.base_sha / reconcile 的对账基准
- 服务端 limit 上限 200；仓库多时分页遍历（mcp-scan.py `find_repo` 已封装）
- 仓库不存在 → 硬终止，指引平台索引，**不得**降级文件扫描

### 2.2 `cypher` —— 任意图查询

```json
{"tool": "cypher", "args": {"statement": "MATCH (m:Method) WHERE m.filePath STARTS WITH 'src/' RETURN m.name AS name SKIP 0 LIMIT 10", "repo": "<项目名>"}}
```

**方言限制（实测）：**
- `repo` 参数**必带**（服务端多仓库索引；缺省报 "Multiple repositories indexed"）
- `SKIP` 必须在 `LIMIT` **前**
- **不支持标签析取**：`(t:Method OR t:Function)`、`(t:Method|t:Function)` 均报 Parser
  exception → 分开查两条，或 CALLS 类查询直接省略标签谓词（CALLS 只指向可调用节点）
- 属性名 camelCase：`filePath` / `startLine` / `endLine` / `name`；关系类型在边属性 `r.type` 上（无 `type()` 函数）
- `AS` 别名、`IN` 列表、`CONTAINS`、`STARTS WITH` 可用；`type(r)` 函数不存在
- 返回结构化 `{markdown, row_count, cols, rows, total}`

**图模型**：节点 `File` / `Class` / `Method` / `Function` / `Property` / `Field`；
边统一为 `[:CodeRelation {type: '...'}]`（关系类型存在边属性 `r.type` 上；实测集合：
`HAS_METHOD`（Class→Method）/ `EXTENDS` / `IMPLEMENTS` / `CALLS` / `IMPORTS` /
`CONTAINS` / `DEFINES` / `ACCESSES` / `HAS_PROPERTY` / `MEMBER_OF` /
`METHOD_OVERRIDES` / `STEP_IN_PROCESS`）。

**常用查询：**

```cypher
-- 类的全部方法（边类型用 HAS_METHOD）
MATCH (c:Class {name: 'UrlRoute'})-[r:CodeRelation]->(m:Method) WHERE r.type = 'HAS_METHOD'
RETURN m.name AS name, m.filePath AS filePath, m.startLine AS startLine, m.endLine AS endLine

-- 方法出向调用（依赖追踪）
MATCH (src)-[r:CodeRelation {type:'CALLS'}]->(t)
WHERE src.filePath = 'src/dfmutils/urlroute.cpp' AND src.name = 'regScheme'
RETURN DISTINCT t.filePath AS filePath, t.name AS name

-- 方法入向调用（in_degree / 影响面）
MATCH (src)-[r:CodeRelation {type:'CALLS'}]->(t)
WHERE t.filePath = 'src/dfmutils/urlroute.cpp' AND t.name = 'regScheme'
RETURN src.filePath AS filePath, src.name AS name

-- 继承边（DBus/GUI/并发基类判定）
MATCH (c:Class)-[r:CodeRelation {type:'EXTENDS'}]->(base) RETURN c.name, base.name
```

### 2.3 `context` —— 降级内容读取

按节点取上下文内容；**content 在 5016 字符截断**，只用于降级查看，方法体一律走本地切片。

## 3. `scripts/mcp-scan.py` 子命令（日常主入口）

四个子命令共用 `--project`（list_repos 中的仓库名）、`--mcp-url`、`--repo-root`
（本地检出，方法体切片/复杂度/宏扫描的数据源；缺省从输出文件或测试文件路径的 git
顶层推导）：

| 子命令 | 用途 | 关键输出 |
|---|---|---|
| `fetch` | 端到端采集：方法/继承/DBus/宏扫描/CALLS 评分 + test_* 回写 | `.ut-inventory.json`（`--keep-dump` 另存 mcp_dump） |
| `scan` | 仅采集图谱度量并评分（不跑 CALLS/test_* 全流程） | mcp_dump JSON |
| `extract-branches` | 分支清单交叉验证：图谱定位方法 → 本地切片提取 if/switch/for/while/throw | 测试文件的分支 JSON（self-checker §2c） |
| `test-mapping` | CALLS 边 → 被测方法回写 test_cover_count | 更新 inventory（`--dry-run` 只出报告） |

test_* 采集逻辑：`discover_test_modules`（测试目录 File 节点，`ut_*.cpp` 与 `test_*.cpp`
命名均兼容）→ `collect_all_calls`（测试模块发出的 CALLS 边）→ `fetch_test_cases`
（本地解析 TEST_F/TEST 宏）→ 按 qn 回写。

## 4. qualified_name（qn）语义变化

旧图谱 qn 带命名空间前缀；GitNexus 无命名空间属性，mcp-scan.py 按以下规则分配
（`_assign_qualified_names`）：

- `Method` → `Class.name`；`Function` → 裸名
- 同基名撞名 → 追加 `@文件名（去扩展名）`；再撞 → 追加 `@行号`
- 同文件重载（同参数个数）→ 追加 `@行号`
- 下游 `_tm_normalize_qn` 对新格式天然兼容（剥 `-` 前导段）

## 5. 新旧工具映射对照

| 旧 codebase-memory-mcp | GitNexus 等价 |
|---|---|
| `search_graph(label="Method", file_pattern=...)` | `mcp-scan.py fetch`（进 inventory）或 `cypher`（示例见 §2.2） |
| `search_graph(label="Class", qn_pattern=...)` | `cypher`：`MATCH (c:Class) WHERE c.name CONTAINS 'X'` |
| `get_code_snippet(qualified_name=...)` | mcp-scan.py 内建「图谱定位 + 本地行切片」；降级 `context`（5016 截断） |
| `trace_path` | `cypher` CALLS 边查询（出向/入向见 §2.2） |
| `query_graph` | `cypher`（统一 `[:CodeRelation {type:...}]` 边） |
| `index_status` | `list_repos`（lastCommit = base_sha） |
| `index_repository` / 本地索引 | **无**——平台索引，本地只能 check_drift 等同步 |

## 6. 版本漂移（reconcile 依赖）

- 图谱版本 = `list_repos.lastCommit`；本地 HEAD 与之比对即 check_drift
  （`mcp-scan.py` 内建，fetch 前自动警告）
- 本地 HEAD 领先 → 图谱缺新方法/边：**硬终止并等待平台同步**（无本地索引可补），
  或确认漂移范围仅涉及无关文件后带警告继续（reconcile 按 diff 路由）
- 本地 HEAD 落后 → 切到 lastCommit 再采集，或接受图谱为最新基线
