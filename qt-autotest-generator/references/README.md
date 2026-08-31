# references/ 目录索引

本目录包含技能的详细参考文档，Agent 按需读取（不在触发时全量加载）。

| 文件 | 用途一句话 | 被谁引用 |
|------|-----------|---------|
| `inventory.md` | Mode 1 全量扫描→评分→产出 .ut-inventory.json | SKILL.md Mode 1, test-writer |
| `inventory-schema.md` | .ut-inventory.json 完整 JSON Schema | inventory.md, test-writer, assets/editor |
| `test-writer.md` | Mode 2 主流程：逐类闭环→编译验证→自检→提交 | SKILL.md Mode 2 |
| `test-code-gen.md` | 测试代码生成：模板替换、用例数下限、AAA、命名 | test-writer 子步骤 |
| `test-types.md` | 用例设计方法论：等价类/边界值/参数化/异常/反模式 | test-code-gen (必读前置) |
| `build-verifier.md` | 编译验证：错误分类→修复表→覆盖率信号 | test-writer 子步骤 |
| `self-checker.md` | 自检 7 项：覆盖率/命名/SPDX/stub/断言强度/环境隔离/结构 | test-writer 子步骤 |
| `failure-repairer.md` | 失败修复：根因判定→重试→标红落盘 | test-writer 子步骤 |
| `incremental-updater.md` | 增量补全：覆盖率缺口→追加用例 | test-writer 子步骤 |
| `incremental-inventory.md` | inventory 增量更新：人工标记 overlay | reconcile-logic, inventory.md |
| `dependency-tracer.md` | 依赖追踪：trace_path→stub 决策矩阵→CMake 目录 | test-writer 子步骤 |
| `framework-builder.md` | 框架搭建：目录结构/CMake/stub-ext/脚本 | test-writer 子步骤 |
| `environment-check.md` | 环境门禁：MCP 提供方解析→索引验证 | SKILL.md 所有 Mode |
| `mcp-providers.md` | MCP 提供方解析规则（唯一权威文档） | environment-check |
| `dev-preflight.md` | Mode 0 开发预检：本地提供方锁定 + 索引同步 + freshness | SKILL.md Mode 0, reconcile-logic |
| `codebase-memory-guide.md` | MCP 工具语义、参数、调用样例 | 所有需查图谱的子步骤 |
| `reconcile-logic.md` | 对账逻辑：git HEAD vs base_sha→差异路由 | SKILL.md Mode 1/2 |
| `stale-test-cleanup.md` | 过时测试清理：removed 方法→注释用例+INSTANTIATE | reconcile 后, failure-repairer 兜底 |
| `coverage-tiers.md` | 三级覆盖率分类体系定义 | self-checker, build-verifier |
| `report-generator.md` | Mode 3 覆盖率采集：collect-coverage-report.py | SKILL.md Mode 3 |
| `mutation-testing.md` | Mode 4 变异测试：源码安全四铁律→变异体→得分 | SKILL.md Mode 4 |
| `defect-exporter.md` | Mode 5 缺陷导出：upsert/mark-fixed/export | SKILL.md Mode 5, failure-repairer |
| `defect-schema.md` | .ut-defects.json 完整 JSON Schema + severity 映射 | defect-exporter, failure-repairer |
| `code-committer.md` | 批次代码提交：暂存→复核→commit | test-writer 子步骤 |
| `templates-guide.md` | templates/ 目录资产说明：模板 vs 脚本 vs stub-ext | framework-builder, test-code-gen |
