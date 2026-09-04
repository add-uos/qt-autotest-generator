# Mode 6 · 测试质量审查（只读，可选）

> 脚本：`scripts/test-review.py`（纯标准库，无 MCP 硬依赖）
> 定位：对**已存在**的单元测试做质量审查与裁决，产出 MD + JSON 审查报告。
> 铁律：**只读** —— 不生成/不修改测试与源码、不编译、不运行测试；审查对象文件的 `git status` 必须保持干净（工作区快照写入 `<outdir>/review-workspace/`，不 checkout）。

---

## 1. 适用场景

| 场景 | 触发 | 说明 |
|------|------|------|
| **A · commit 审查** | `--commit <sha>` 或 `--commit <shaA>..<shaB>` | 审查某次（段）提交涉及的测试文件；删除（D）的测试文件记入 skipped，不审查 |
| **B · 未缓存文件** | `--uncached` | 扫描测试目录中**未登记**在 `.ut-inventory.json` 的测试文件（basename 归一化比对）；inventory 不存在时全部视为未缓存 |
| **B' · 显式文件** | `--files f1 f2 ...` | 用户点名指定，跳过扫描与登记比对 |

两者都用 `--files` 语义兜底；`--commit` / `--uncached` / `--files` 互斥，同时给出按 A > B > B' 优先级取一。

**典型用途**：
- code review 阶段审他人/自己某笔提交的测试质量
- 接手历史项目，审查"野生"未入册测试（不重新生成、不入册，只出报告）
- CI 门禁（`--strict`）

---

## 2. 执行步骤

### 第 1 步：解析输入（resolve，可选）

```bash
python3 scripts/test-review.py resolve \
  --repo <项目根> --commit <sha|a..b> \
  [--test-dir autotests] [--inventory .ut-inventory.json] \
  -o targets.json
```

产出 `targets.json`：`{scenario, repo, commit, workspace, targets[{source_path, review_path, git_status, managed, class_hint}], skipped, non_test_changes, _label}`。

- commit 场景把每个目标在**该 commit 时刻**的内容提取到 `<outdir>/review-workspace/<label>/`（`git show sha:path`，非工作区状态）；提取失败的文件记入 skipped
- `managed` 标记该文件是否已在 inventory 登记过（basename 归一化）
- `class_hint` 从文件名推断（`test_file_view.cpp` → `FileView`，与 scorer 大小写风格一致），仅作展示提示
- 路径含 `..` 分量 / 绝对路径一律拒绝提取（防目录穿越）

> 两步法适合想先人工看一眼"会审哪些文件"再放行的场景；通常直接进第 2 步。

### 第 2 步：编排审查（review）

```bash
python3 scripts/test-review.py review \
  --repo <项目根> --commit <sha|a..b>        # 场景 A
  # 或 --uncached / --files a.cpp b.cpp      # 场景 B / B'
  # 或 --targets targets.json                # 消费第 1 步产物
  [--outdir .reports] [--project <MCP项目名>] [--inventory <path>]
  [--mcp-url URL] [--scorer-path PATH]
  [--no-branch] [--no-scorer]
  [--coverage coverage_by_level.json] [--mutation .ut-mutation.json]
  [--strict]
```

对每个审查目标依次执行三个维度，聚合裁决后产出：

```
<outdir>/
├── test-review-<label>.md        # 人读审查报告（最终交付物）
├── test-review-<label>.json      # 机读报告（同构数据）
└── review-artifacts/<label>/     # 每文件工件
    ├── 00_test_x.cpp.structural.json
    ├── 00_test_x.cpp.branch.json
    └── 00_test_x.cpp/scorecard-*.json
```

`<label>` = 短 sha（如 `3fc6c0cc`）、`<a8>..<b8>`、`uncached` 或 `files`。

### 三维度编排

| 维度 | 工具 | 数据流 | 失败行为 |
|------|------|--------|----------|
| 结构规范 | `self-check-structural.py -f <file> --json` | 违规清单（spdx/naming/assertion/aaa/structure/stub/env） | 该文件 ERROR，不参与后续维度 |
| 分支白盒 | `mcp-scan.py extract-branches --project P --test-file F --inventory I` | `BRANCH_NOT_MAPPED` 等违规，与 structural 同构 | 记 degraded + 原因，不阻塞 |
| 数值评分 | qt-autotest-scorer `score.py`（可选依赖） | scorecard JSON → 总览"评分（等级）"列 | 记 degraded + 原因，不阻塞 |

scorer 探测顺序：`--scorer-path` > `$QTAG_SCORER_PATH` > `{repo}/.pi/skills/qt-autotest-scorer/scripts/score.py` 兄弟目录探测。找不到只降级不报错。

`--project` 缺省时自动读 `inventory.project`；无 inventory 或用户 `--no-branch` 时跳过分支白盒（commit 场景强烈建议保留——见 §5 已知限制）。

---

## 3. 裁决模型

规则裁决（self-checker 语义）与 scorer 合格线**并列展示、互不覆盖**：

| 裁决 | 条件 |
|------|------|
| ❌ **FAIL** | 存在任一 critical 规则（下表） |
| ⚠️ **WARN** | 无 critical，但有其他 error 级违规 |
| ✅ **PASS** | 仅 warning 或干净 |
| 💥 **ERROR** | structural 工具故障（文件读不了/JSON 无效） |

**Critical 规则**（一票否决）：

| 规则 | 含义 | 修复路由 |
|------|------|----------|
| `EMPTY_ASSERT` | 空断言，等于没测 | self-checker.md §2b |
| `TRIVIAL_ASSERT` | 唯一断言为字面量布尔（`EXPECT_TRUE(true)`），占位断言未验证任何行为 | self-checker.md §2b |
| `SOLE_NO_FATAL` | 唯一断言为 `EXPECT_NO_FATAL_FAILURE`，逻辑全错也能过 | self-checker.md §2b |
| `SOLE_GMOCK_EXPECT` / `SOLE_GMOCK` | 纯 gMock 期望无传统断言，未验证 SUT 自身行为 | self-checker.md §2b |
| `BRANCH_NOT_MAPPED` | 声明分支 < 真实分支，漏测 | self-checker.md §2c |
| `FN_COVERAGE_LT_100` | 函数覆盖率 < 100%（Iron Law #3） | build-verifier.md |

非 critical 的 error（如 `TOO_FEW_SEGMENTS`、`STUB_NOT_CLEARED`）→ WARN；warning（如 `EMPTY_AAA`）→ PASS 但计入警告列。

### 退出码

| 退出码 | 条件 |
|--------|------|
| 0 | 审查完成（无论 PASS/WARN/FAIL）——审查报告本身就是交付物 |
| 1 | `--strict` 且存在 FAIL/ERROR（CI 门禁用） |
| 2 | 硬错误：仓库无效、commit 不存在、找不到测试文件/目录、指定文件不存在 |

---

## 4. 报告结构（test-review-<label>.md）

```markdown
# 单元测试质量审查报告（Mode 6 · 测试质量审查）
> 生成时间 / 工具版本 / 场景 / 审查对象 / 仓库
> 模式：**只读审查**（未修改/生成任何测试或源码，未编译未运行）

## 1. 总览
| 测试文件 | 状态 | 用例数 | 错误 | 警告 | 规则裁决 | 评分（等级） |
**裁决分布**：PASS n · WARN n · FAIL n · ERROR n ｜ 已评分 n ｜ 分支白盒可用 n

## 2. 逐文件明细
### 2.1 `path/test_x.cpp` — ❌ FAIL
违规表（check/severity/rule/行号/case）→ 分支白盒结果（或降级原因）
→ 评分卡摘要 → 建议清单（P0 critical / P1 error / P2 warning，附路由）

## 3. 改进路由汇总
规则 → 规范文档（self-checker.md §x / test-code-gen.md §y）→ 按建议修复后重跑本审查验证

## 附录
跳过清单（删除文件/提取失败）· 降级说明 · 非测试文件变更数
```

建议优先级：**P0** = critical（一票否决）→ **P1** = error（规范问题）→ **P2** = warning（提示性）。每条建议带 `reference` 路由，指向规范文档的对应小节（规则定义与修复范式所在）——**本模式只出报告，不改任何代码**。

---

## 5. 已知限制（报告自动标注 caveat）

1. **commit 场景分支漂移**：`extract-branches` 反查的是知识图谱**当前状态**的源码分支，与 commit 时刻可能不一致（未 push 的本地 commit 必然漂移）。报告 meta.caveats 固定标注；对本地未推送 commit 的白盒结论要打折看。
2. **未缓存文件缺上下文**：场景 B 的文件多半不在 inventory → 无 `methods` 映射 → 分支白盒/数值评分自动降级，只剩结构规范维度（这正是"野生测试"的预期审查深度）。
3. **rename 折叠已规避**：git 调用强制 `--no-renames`，D+A 不会被折叠成 R，删除信号不丢失。
4. **scorer 是可选依赖**：缺失时报告只有规则裁决列，无评分列——不是故障，是设计好的降级。

---

## 6. 报告之后的行动（本模式不参与）

- **修复**：按报告建议直接修改测试代码（或交还测试作者/负责测试的流程）；P0 优先，逐条对应规范文档小节。本模式不修改任何文件。
- **闭环**：修复后重跑本审查，验证裁决从 FAIL → WARN → PASS 逐步收敛；`--strict` 模式下退出码变 0 即达门禁。
- **数据增强（可选）**：手头有 coverage_by_level.json（覆盖率采集产出）或 .ut-mutation.json（变异测试产出）时，用 `--coverage` / `--mutation` 喂给脚本，评分维度更完整；没有则纯规则裁决，不影响可用性。
- **CI 门禁**：`--strict` 模式存在 FAIL/ERROR 时退出码 1，可直接接入流水线。
