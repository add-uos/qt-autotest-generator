# Plan 驱动工作流（v2 graph-native）

> 基于 GitNexus REST 图谱的**全仓测试规划器** `scripts/ut-plan.py`。
> 与 inventory 模式（Mode 1/2）的关系：inventory 面向"逐类补全"（单类粒度、
> 依赖既有 `.ut-inventory.json`）；ut-plan 面向"**全仓规划 + 断点续跑 + 变更驱动**"
> （块粒度、plan 文件即状态机）。新项目初始化或需要量化进度/只做受影响块时用本工作流。
> 架构细节见 `doc/gitnexus-ut-workflow-v2.md`（v2.4，R1–R5 已全部落地）。

## 环境变量（REST 唯一数据通道）

| 变量 | 必须 | 说明 |
|---|---|---|
| `QTAG_GN_BASE_URL` | 是 | REST 端点（如 `https://codegraph.uniontech.com/api/query`） |
| `QTAG_GN_HEADERS` | 是 | 认证头 JSON（如 `{"Authorization":"Basic xxx"}`） |
| `QTAG_GN_REPO` | 是 | 仓库名（plan/select changed 子命令依赖） |

REST 不可用（404/网络/未索引）→ **硬终止，不降级**。仓库是否已索引用
`graph-access.py` 任意查询即可确认（未索引会报错）。

## 七个子命令速查

```bash
# 1) 建档：全仓 survey → 分级 → 分块（一次，全量 25041 方法约 28s）
python3 scripts/ut-plan.py plan -o .ut-plan.json

# 2) 选块（四模式）
python3 scripts/ut-plan.py select .ut-plan.json --mode full              # 全量按优先级
python3 scripts/ut-plan.py select .ut-plan.json --mode module --module src/dfm-base/base/db
python3 scripts/ut-plan.py select .ut-plan.json --mode delta             # 跳过 done
python3 scripts/ut-plan.py select .ut-plan.json --mode changed \
    --repo-root /path/to/repo                                            # 变更驱动（见下）

# 3) 生成单块上下文 → .ut-gen/<block>/context.md（锚定 plan 所在目录）
python3 scripts/ut-plan.py generate .ut-plan.json --block B1010 --repo-root /path/to/repo

# 4) 生成会话：Read context.md → 按内嵌 PROMPT_CONVENTIONS 写 test_*.cpp
#    （GTest；套名 <Class>Test；断言借 QObject 免 moc；方法体行切片可能降级 symbol——照常写）

# 5) 验证：拷入 → cmake configure+build → --gtest_filter=<Suite>.* 跑测 → 状态回写
#    可选 --base <ref>：块文件自基准起有变更时 last_verify.base_drift 标注（不阻断）
python3 scripts/ut-plan.py verify .ut-plan.json --block B1010 --repo-root /path/to/repo --base HEAD~1

# 6) 状态手工流转（跳过某块 / 重置重做）
python3 scripts/ut-plan.py update .ut-plan.json B3215 --status failed
python3 scripts/ut-plan.py update .ut-plan.json B3215 --status pending

# 7) 摘要与聚合
python3 scripts/ut-plan.py show .ut-plan.json          # 简报（--all 全部块）
python3 scripts/ut-plan.py report .ut-plan.json --top 10   # 按模块聚合状态/分级/验证
python3 scripts/ut-plan.py report .ut-plan.json --json     # 供 CI / scorer 消费
```

## 块状态机

```
pending ──select──▶ selected ──verify ok──▶ done
   ▲                    │                      
   │      verify fail   ▼                      
   └────────────────── failed ──update──▶ pending（重做）
```

- `select --block` / `update` 可手工干预；`done` 块在 delta/changed 模式下自动跳过。
- `last_verify`（ts/test_file/suite/run{passed,failed,exit}/error）是 scorer 的证据源。

## 变更驱动选块（R5）

人为改动代码后，只补受影响块的用例：

```bash
python3 scripts/ut-plan.py select .ut-plan.json --mode changed --repo-root /path/to/repo
# impact: changed_files=2 methods=24 impact_files=11 callers=9
# selected 20: B0096 B0097 …
```

链路：本地 `git diff --name-status HEAD`（含未跟踪）→ 源文件过滤 →
图谱反查方法 → 一跳 caller 文件集（外溢去重）→ 命中块选中（done 跳过）。
之后对选中的块逐个 `generate → 写用例 → verify` 即可。

## 硬规则（与 Iron Laws 同级）

1. **REST 硬终止**：plan/select(changed) 遇 REST 错误不降级 MCP。
2. **gtest filter 等号形式**：`--gtest_filter=Suite.*`（空格分隔会静默退出 0，假绿）。
3. **字节截断**：context.md 超 48KB 自动按字节截断并标注，生成会话不得要求"重读全文件"。
4. **`.ut-gen` 锚定 plan 目录**：产物与 plan 同级，不随 CWD 漂移。
5. **方法体三态**：file-slice（精确）→ symbol-snippet（降级）→ none（仅签名），
   生成会话按三态自适应，none 态用例以行为约定为主、不臆造实现细节。
6. **plan verify 证据诚实**：last_verify（含 base_commit/base_drift）只作证据标注，不改评分权重。
7. **sufficiency 降级**：scorer 无 inventory 且 `--plan` 给定时，块 methods（name/level）作充分性校核输入（`sufficiency_source=plan_methods`）。
8. **路由一致性受元测试守护**：argparse 子命令/choices 与 main 路由分支的一致性由 `test_ut_plan.py::TestRouteConsistency` 源码级校验（子命令双向相等、mode 路由 ⊆ choices）。改 CLI 路由必须同次提交更新两侧，跑测试即验——防止 edit 静默未生效造成的 choices/实现漂移（retro S-003）。
9. **重载同名方法去重**：scorer 充分性分母按方法名唯一、level 取最严（`_dedupe_overloads`）——重载共享同名用例集，重复计入分母会使 total 虚高（retro Q-001）。
