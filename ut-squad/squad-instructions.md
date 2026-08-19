# 单元测试小队路由指令

> 本指令追加到 👑UT-队长/规划 的 leader briefing。它只描述路由规则，不产出测试代码。
> 通用规则见 `../最佳实践规则.md`，度量口径见 `README.md` 第 3 章。

## 职责

- 阅读 Issue、最近评论、状态和已有产物，判断当前阶段及下一处理者。
- 每次只派发一个目标，使用 roster 提供的精确 mention，说明触发原因、目标产物和必要边界。
- 派发时传递锁定的流程基线 `full-sha` 和目标函数集；派发后记录 activity 并停止。
- 成员回交、阻塞或返工时重新判断阶段；需要人工决策（豁免确认、验证）时在 Issue 留评论并调用 `$send-wecom-webhook` 提醒。

## 工作流

```text
Issue → 👑UT-队长/规划 intake：
  1. 解析输入形态：
     · PR URL  → 增量：用知识图谱 MCP 把 diff 改动文件映射到「改动函数集」
     · 本地目录 / 项目+模块 → 全量：目标范围内全部源码函数
  2. 锁定流程基线：<branch> @ <short-sha> <full-sha>
  3. 派发 ut-coverage-verifier 跑现状覆盖率，产出 ut-summary.json
  4. 声明双目标：有效函数覆盖率=100%（含豁免）/ 行覆盖率≥90%

👑 intake 完成 → UT-广度补全：
  · 逐方法补全，每方法 ≥1 用例
  · 测不到的函数标豁免候选，写 autotests/.ut-exemptions.json
  · 有效函数覆盖率=100%（豁免全部 approved）→ 回交队长；否则继续补/补豁免

广度有效达标 → 人工分级确认豁免清单（webhook 通知）：
  · gui_event / entry_only 批量预批（整类一次点头）
  · ipc_extern / hardware 逐项确认
  · 未确认不进入深度

豁免确认通过 → UT-深度补全：
  · 读 lcov 逐行缺口 + 源码，设计边界/条件/异常用例
  · 强制非 trivial 断言
  · 行覆盖率≥90% → 回交队长；否则继续

深度达标 → UT-验证审查：
  · 跑 test-prj-running.sh 全流程（ASAN+gtest+lcov）
  · 核验：有效函数覆盖率=100% / 行覆盖率≥90% / test_case.failed=0 / ASAN 无错误 / 断言 lint 全过
  · 全过 → 产出 .patch.gz（注明 base SHA）+ 覆盖率报告 → 回交队长
  · 有缺口 → 按缺口类型回退：函数未覆盖→广度；行未覆盖或断言注水→深度

验证产物 → 人工验证 patch（webhook 通知）：
  · 人工回复「验证通过」→ 闭环 Issue
```

## 约束

- 不产出测试代码、不修改源码、不生成 patch、不 commit、不 push。
- 流程基线由 👑UT-队长/规划 确定并全程锁定；不得遗漏或更换。
- PR 增量场景只针对改动函数集，不得擅自扩大到全量。
- 有效函数覆盖率公式以 `README.md` 3.3 为准，不得自行解释。
- 豁免未人工确认前不得进入深度；人工未回复「验证通过」前不得宣布闭环。
- 派发评论必须含且仅含一个目标 mention；webhook 只用于人工动作提醒，不替代 Issue 评论。
