# GitNexus MCP 使用文档
## 概述


GitNexus 是一个代码知识图谱引擎，通过 MCP (Model Context Protocol) 协议为 AI Agent 提供代码库的深度理解和分析能力。它将代码库索引为知识图谱，包含依赖关系、调用链、聚类和执行流程等信息。



* * *

## 1\. Agent 配置方法
### 1.1 OpenCode 配置


OpenCode 支持两种配置方式：



#### 方式一：全局配置（推荐）



配置文件路径：`~/.config/opencode/opencode.json` 或 `~/.config/opencode/opencode.jsonc`

```plain
{
  "$schema": "https://opencode.ai/schema.json",
  "mcp": {
    "gitnexus": {
      "type": "remote",
      "url": "https://codegraph.uniontech.com/api/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "Basic Z2l0bmV4dXM6Z2l0bmV4dXMuMTEyMg=="
      }
    }
  }
}
```

#### 方式二：项目级配置



配置文件路径：项目根目录下的 `.opencode/opencode.json` 或 `opencode.json`

```plain
{
  "mcp": {
    "gitnexus": {
      "type": "remote",
      "url": "https://codegraph.uniontech.com/api/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "Basic Z2l0bmV4dXM6Z2l0bmV4dXMuMTEyMg=="
      }
    }
  }
}
```

**注意**：OpenCode 配置中的 MCP 服务器配置在 `mcp` 对象下，而不是 `mcpServers`。

### 1.2 Cursor 配置


在 Cursor 的 `.cursor/mcp.json` 或全局配置中添加：

```plain
{
  "mcpServers": {
    "gitnexus": {
      "url": "https://codegraph.uniontech.com/api/mcp",
      "headers": {
        "Authorization": "Basic Z2l0bmV4dXM6Z2l0bmV4dXMuMTEyMg=="
      }
    }
  }
}
```### 1.3 Cline / Roo Code 配置


在 VS Code 的 `settings.json` 中配置 MCP：

```plain
{
  "cline.mcpServers": {
    "gitnexus": {
      "type": "remote",
      "url": "https://codegraph.uniontech.com/api/mcp",
      "headers": {
        "Authorization": "Basic Z2l0bmV4dXM6Z2l0bmV4dXMuMTEyMg=="
      }
    }
  }
}
```### 1.4 通用 HTTP MCP 配置


对于支持 HTTP MCP 协议的任意 Agent：

```plain
# config.yaml
mcp_servers:
  gitnexus:
    type: remote
    url: https://codegraph.uniontech.com/api/mcp
    headers:
      Authorization: "Basic Z2l0bmV4dXM6Z2l0bmV4dXMuMTEyMg=="
```

* * *

## 2\. MCP 功能说明


GitNexus MCP 服务器提供 **17+ 个工具**，分为以下几类：

### 2.1 仓库管理工具


| 工具名称 | 功能描述 |
|---|---|
| `list_repos` | 列出所有已索引的代码仓库（支持分页） |




### 2.2 代码查询工具


| 工具名称 | 功能描述 |
|---|---|
| `query` | 混合搜索（BM25 + 语义搜索），返回按进程分组的执行流程 |
| `cypher` | 执行 Cypher 图查询语言，进行复杂结构查询 |
| `context` | 360度符号视图 - 查看符号的完整引用关系和所属进程 |
| `search` | 代码符号搜索（函数、类、变量等） |




### 2.3 代码分析工具


| 工具名称 | 功能描述 |
|---|---|
| `impact` | 影响范围分析 - 查看修改某符号会影响哪些代码 |
| `trace` | 追踪两个符号之间的最短调用路径 |
| `detect_changes` | 检测 Git 变更的影响范围 |
| `explain` | 解释代码行为（支持安全分析、性能分析等） |
| `pdg_query` | 程序依赖图查询（需要 `--pdg` 索引模式） |




### 2.4 代码重构工具


| 工具名称 | 功能描述 |
|---|---|
| `rename` | 安全的符号重命名（跨文件） |




### 2.5 资源访问


GitNexus 还提供以下资源 URI 供读取：



| 资源 URI | 描述 |
|---|---|
| `gitnexus://repos` | 所有仓库列表 |
| `gitnexus://repo/{name}/context` | 仓库概览和统计信息 |
| `gitnexus://repo/{name}/clusters` | 代码功能聚类（模块划分） |
| `gitnexus://repo/{name}/processes` | 执行流程列表 |
| `gitnexus://repo/{name}/process/{name}` | 具体执行流程详情 |
| `gitnexus://repo/{name}/schema` | 知识图谱 Schema |




### 2.6 提示词模板


| 提示词名称 | 用途 |
|---|---|
| `detect_impact` | 分析当前变更的影响范围 |
| `generate_map` | 生成架构文档 |






* * *

## 3\. Agent 提示词调用示例
### 3.1 基础查询场景
```markdown
请帮我查找项目中处理用户认证的代码。

请使用 gitnexus MCP 工具：
1. 首先调用 list_repos 查看可用的仓库
2. 使用 query 工具搜索 "user authentication" 或 "login"
3. 对于找到的关键函数，使用 context 工具查看完整的引用关系
```### 3.2 代码理解场景
```markdown
我想了解 UserService 类的实现和调用关系。

请使用 gitnexus MCP：
1. 使用 context 工具查询 UserService 的完整上下文
2. 使用 impact 工具分析修改 UserService 会影响哪些代码（direction: "upstream"）
3. 使用 cypher 工具查询 UserService 的所有方法：
   MATCH (c:Class {name: "UserService"})-[r:CodeRelation {type: 'HAS_METHOD'}]->(m:Method)
   RETURN m.name, m.parameterCount
```### 3.3 变更影响分析场景
```plain
我准备修改 validateUser 函数，请帮我分析影响范围。

请使用 gitnexus MCP：
1. 使用 impact 工具分析 validateUser 的上游影响：
   - target: "validateUser"
   - direction: "upstream"
   - depth: 3
2. 使用 detect_changes 查看当前工作目录的变更影响
3. 对于高风险调用者，使用 context 工具查看其详细引用关系

请输出：
- 直接受影响的函数列表
- 跨进程的调用链
- 风险评估和建议
```### 3.4 代码重构场景
```markdown
我需要将函数名 oldFunctionName 重命名为 newFunctionName。

请使用 gitnexus MCP：
1. 首先使用 impact 工具分析 oldFunctionName 的调用范围
2. 使用 rename 工具执行重命名：
   - old_name: "oldFunctionName"
   - new_name: "newFunctionName"
   - file_path: "src/utils/helpers.js"
3. 使用 detect_changes 验证重命名结果
```### 3.5 复杂查询场景（Cypher）
```markdown
请帮我分析代码中的依赖注入模式。

请使用 gitnexus MCP 的 cypher 工具执行以下查询：

1. 查找所有被注入的服务：
```cypher
MATCH (c:Class)-[r:CodeRelation {type: 'INJECTS'}]->(provider)
RETURN c.name as Consumer, provider.name as Provider, r.reason as InjectionType
```
1.  查找所有继承关系：
```cypher
MATCH (child:Class)-[r:CodeRelation {type: 'EXTENDS'}]->(parent:Class)
RETURN child.name, parent.name
```
1.  查找特定函数的调用链：
```cypher
MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(callee:Function {name: "targetFunction"})
RETURN caller.name, caller.filePath
```### 3.6 架构分析场景
```markdown
请帮我生成当前项目的架构文档。

请使用 gitnexus MCP：
1. 读取 gitnexus://repo/{repo_name}/context 获取仓库概览
2. 读取 gitnexus://repo/{repo_name}/clusters 查看功能聚类
3. 读取 gitnexus://repo/{repo_name}/processes 查看执行流程
4. 对于前 5 个重要进程，读取 gitnexus://repo/{repo_name}/process/{process_name} 获取详细信息

请生成包含以下内容的架构文档：
- 项目整体结构
- 主要功能模块划分
- 核心执行流程
- 模块间依赖关系图（使用 mermaid 语法）
```### 3.7 安全分析场景
```markdown
请帮我分析代码中可能存在的安全问题。

请使用 gitnexus MCP：
1. 使用 query 工具搜索 "authentication", "authorization", "validate", "sanitize"
2. 使用 explain 工具分析安全相关的函数：
   - name: "suspiciousFunction"
   - analysis_type: "security"
3. 使用 cypher 查询潜在的安全问题：
```cypher
// 查找没有输入验证的 API 端点
MATCH (r:Route)-[:CodeRelation {type: 'HANDLED_BY'}]->(f:Function)
WHERE NOT (f)-[:CodeRelation {type: 'CALLS'}]->(:Function {name: "validate"})
RETURN r.path, f.name
```

* * *

## 4\. 常用 Cypher 查询示例
### 4.1 查找函数调用者
```cypher
MATCH (caller)-[:CodeRelation {type: 'CALLS'}]->(callee:Function {name: "targetFunction"})
RETURN caller.name, caller.filePath
```### 4.2 查找类的所有方法
```cypher
MATCH (c:Class {name: "UserService"})-[r:CodeRelation {type: 'HAS_METHOD'}]->(m:Method)
RETURN m.name, m.parameterCount, m.returnType
```### 4.3 查找方法重写关系
```cypher
MATCH (winner:Method)-[r:CodeRelation {type: 'METHOD_OVERRIDES'}]->(loser:Method)
RETURN winner.name, winner.filePath, loser.filePath, r.reason
```### 4.4 查找依赖注入
```cypher
MATCH (c:Class)-[r:CodeRelation {type: 'INJECTS'}]->(provider)
RETURN c.name, provider.name, r.reason
```### 4.5 查找继承关系
```cypher
MATCH (child:Class)-[:CodeRelation {type: 'EXTENDS'}]->(parent:Class)
RETURN child.name, parent.name
```### 4.6 检测菱形继承
```cypher
MATCH (d:Class)-[:CodeRelation {type: 'EXTENDS'}]->(b1),
      (d)-[:CodeRelation {type: 'EXTENDS'}]->(b2),
      (b1)-[:CodeRelation {type: 'EXTENDS'}]->(a),
      (b2)-[:CodeRelation {type: 'EXTENDS'}]->(a)
WHERE b1 <> b2
RETURN d.name, b1.name, b2.name, a.name
```

* * *

## 5\. 最佳实践
### 5.1 查询策略

1.  **先使用** `**list_repos**` 确认可用的仓库
2.  **使用** `**query**` **进行语义搜索** 快速定位相关代码
3.  **使用** `**context**` **深入理解** 特定符号的完整上下文
4.  **使用** `**impact**` **分析影响** 在修改前评估风险
### 5.2 性能优化

*   使用 `limit` 参数控制返回结果数量
*   使用 `max_symbols` 限制每个进程的符号数
*   对于大型仓库，使用分页参数遍历结果
### 5.3 安全建议

*   在生产环境使用只读模式 (`GITNEXUS_MCP_READ_ONLY=1`)
*   使用仓库策略限制可访问的仓库范围
*   定期更新索引以获取最新代码状态


* * *

## 6\. 故障排除
### 6.1 连接问题

*   检查 MCP 服务器 URL 是否正确
*   验证 Authorization Header 是否有效
*   确认网络可以访问服务器地址
### 6.2 查询问题

*   确保仓库已正确索引
*   检查 `repo` 参数是否正确指定
*   对于 Cypher 查询，先读取 Schema 了解图谱结构
### 6.3 性能问题

*   减少 `limit` 和 `max_symbols` 参数值
*   使用更具体的查询条件
*   考虑使用分页获取大量结果


* * *

## 7\. 相关资源

*   **GitNexus 官网**: [https://gitnexus.vercel.app](https://gitnexus.vercel.app)
*   **GitHub 仓库**: [https://github.com/abhigyanpatwari/GitNexus](https://github.com/abhigyanpatwari/GitNexus)
*   **Discord 社区**: [https://discord.gg/MgJrmsqr62](https://discord.gg/MgJrmsqr62)
*   **MCP 协议文档**: [https://modelcontextprotocol.io](https://modelcontextprotocol.io)


* * *



*文档版本: 1.1 | 最后更新: 2026-08-13*