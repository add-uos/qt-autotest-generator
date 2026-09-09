# 技能复盘报告（qt-autotest-generator）

> 生成时间：2026-09-09 09:56:50 ｜ backlog：/home/zhy/demo/utest/skills/qt-autotest-generator/retro/backlog.json
> 共 14 项：open 2 / resolved 12

## 分类统计

| 分类 | open | resolved |
|---|---|---|
| G 文档缺口 | 0 | 2 |
| R 规则缺陷 | 0 | 2 |
| S 脚本缺陷 | 1 | 4 |
| Q 生成质量 | 1 | 0 |
| E 工具链坑 | 0 | 2 |
| T 触发路由 | 0 | 2 |

## P1（效率受损，open）

### S-003 argparse choices 与 mode 实现漂移（edit 静默未生效）

- 证据：R5 给 cmd_select 加 changed 模式时，argparse --mode choices 漏改，CLI 测试才兜住（invalid choice）；edit 工具曾报成功但目标文本未匹配
- 修复建议：改 CLI 路由时 argparse choices 必须与实现分支同一次提交核对；CLI 层测试覆盖每个 mode 值
- 备注：缓解：test_r5_changed CLI 层测试覆盖每个 mode；彻底修需技能发版时 choices 与实现 diff 校验脚本


## P2（体验瑕疵，open）

### Q-001 high 方法下限公式对重载同名方法重复计数分母

- 证据：plan 里 typeString 出现 high/mid 两条（重载），sufficiency 按名字分组分母重复；评分偏严但语义可解释
- 修复建议：scorer 按名字分组时同名方法取 max(level) 去重分母，或 plan 分块时合并同名重载的 level

## 已解决（本实战沉淀）

- **S-002** gtest --gtest_filter 空格分隔形式静默 exit 0（假绿） —— ut-plan.py cmd_verify 强制等号形式 f'--gtest_filter={suite}.*'；test_ut_plan 守卫断言 calls[-1] 含等号串
- **S-004** scorer sufficiency 按去重首段集合计数，用例数被系统性低估 —— _sufficiency_for_methods 改用完整用例名列表 case_names 计数，回退首段集合；回归测试 test_case_names_counted_not_deduped
- **S-005** score_project 空目录 UnboundLocalError —— 提前 return 路径补 files = [] 或调整分支顺序
- **G-002** 验证语义未写明：跑测 0 例 ≠ 通过 —— build-verifier.md 增加『跑测结果解析』小节：PASSED 计数缺失/0 例/FAILED>0 三态判定 + gtest filter 等号形式示例
- **G-003** Plan 驱动工作流（ut-plan.py）入口缺失 —— SKILL.md v3.5.0：模式表+触发条件+快速参考+检查清单四处加 Plan 驱动入口；新增 references/ut-plan-workflow.md
- **R-001** verify 漂移语义：无 base_commit 记录，无法判断测试对应哪个源码版本 —— plan 记 base_commit、verify 回写 last_verify.base_commit + --base 漂移检查（标注不阻断）；scorer 透传 base_drift
- **R-002** Iron Law #12 与骨架降级三态冲突：none 态拿不到方法体时禁 read 源码会卡死生成 —— 明确三态自适应规则（file-slice→symbol-snippet→none），none 态以行为约定写用例不臆造实现；写入 ut-plan-workflow.md 硬规则 5
- **E-001** Kuzu/Ladybug 方言：无 signature/className 属性、CALLS 挂头文件声明节点 —— gitnexus-guide.md 固化方言清单（单表 CodeRelation/本地分位数/IN 500/属性名表），新查询先查属性再写 Cypher
- **E-002** TypeString 非法分支 Debug 下 assert abort，不可测 —— test-types.md 增加『Debug assert 分支』条目：识别 assert 类分支→用例标 GTEST_SKIP 或注明不测原因，防 verify 崩溃误判 failed
- **T-001** 『本地开发』字样误路由 Mode 0（应为 Mode 2） —— 保持 description 注意项；trigger-evals 补一条『本地开发的类补个测试』应触发 Mode 2 的用例
- **T-002** Plan 驱动入口触发词未进 trigger-evals —— trigger-evals.json should_trigger 补 plan-driven 4 条 + 不应触发补 1 条（只跑测试不生成）
- **S-006** _next_id 按 count 计数：删除中间项后 add 复用 id 造成冲突 —— _next_id 改为现有最大序号+1（isdigit 校验），单测 test_add_no_id_reuse_after_manual_delete 守卫
