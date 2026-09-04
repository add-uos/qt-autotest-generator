# Mode 6 · 测试质量审查（Test Review）设计文档

> 状态：已实施
> 关联：`doc/mode2-script-offload-design.md`（脚本下沉先例）、`doc/mode4-mutation-design.md`（可选增强模式先例）、`doc/scoring-review-proposal.md`（评分体系调研沉淀）
> 产物脚本：`qt-autotest-generator/scripts/test-review.py`（子命令 `resolve` / `review`）

---

## 1. 背景与目标

qt-autotest-generator 已有 Mode 0~5：预检 / 建表 / 生成 / 覆盖率 / 变异 / 缺陷导出。
生成侧的质量把关是**生成期自检**（self-checker，二元 pass/fail，内部消费）；
`qt-autotest-scorer` 技能提供了生成后的数值评分卡。

缺口：用户经常想问——

1. **"这次 commit 里的单元测试写得怎么样？"**（审查一次本地提交，commit 可能未 push，图谱/inventory 都可能对不上）
2. **"这批没入册的测试文件质量如何？"**（未缓存的单元测试文件：磁盘上有 `test_*.cpp`，但 `.ut-inventory.json` 没登记——手写的、别的工具生成的、或历史遗留）

Mode 6 解决这两个场景：给定输入 → 只读审查 → **MD 审查报告**。
不生成、不修改测试/源码、不编译、不跑测试（与 Mode 3/5 同构的只读模式）。

## 2. 两种输入场景

### 场景 A · commit 审查（`--commit <sha>` / `--commit <shaA>..<shaB>`）

```
git diff-tree --no-commit-id --name-status -r --root -m <sha>   # 找变更测试文件
git show <sha>:<path>                                           # 提取该版本内容到审查工作区
```

- 只审查**测试文件**（basename 匹配 `test_*.cpp` / `*_test.cpp`），其余变更仅计数入报告元信息。
- 工作区：`<outdir>/review-workspace/<label>/<原相对路径>`，label = commit 短 SHA 或 `range`。
  **不 checkout、不改工作区、不动 git 状态**。
- `D`（删除）的测试文件无法取内容 → 跳过审查，在报告"跳过清单"记录。
- merge commit 用 `-m`（对每个 parent 出 diff），报告中说明。
- range 场景：`git diff-tree <a> <b>` 取累计差异，元信息记录 `git rev-list --count`。

### 场景 B · 未缓存测试（`--uncached` / `--files f1 f2...`）

- `--uncached`：`os.walk` 扫描 `--test-dir`（缺省依次探测 `autotests/`、`tests/`），
  basename 匹配测试文件模式，与 inventory 登记集合做差集。
- **归一化**：inventory `methods[].test_files` 的登记格式可能是仓库相对路径 / test_dir 相对路径 /
  纯 basename → 集合同时收入三种形态，按"basename 命中即算已登记"判定（宽松，宁可漏报不误报）。
- inventory 不存在 → 磁盘上全部测试文件视为未缓存。
- `--files`：显式指定，跳过扫描；`managed` 标记仍按 inventory 判定。
- 场景 B 审查**原路径文件**（不复制），`review_path == source_path`。

## 3. 复用与分层（不重复造轮子）

Mode 6 独有的只有三块：**输入解析、编排、裁决+报告**。质量评价全部复用既有固化脚本：

| 环节 | 复用 | 依赖 | 失败/缺失时 |
|------|------|------|------------|
| 规范检查 | `self-check-structural.py -f <file> -o <json>` | 无（纯正则） | 视为 ERROR，单文件标记后继续 |
| 分支白盒 | `mcp-scan.py extract-branches --project P --test-file F --inventory I` | MCP + inventory + 类已入册 | 降级：维度标记 `skipped` + 原因 |
| 数值评分 | `qt-autotest-scorer/scripts/score.py -f F -s structural.json [-b branch.json] [-i inventory]` | scorer 技能安装 | 降级：纯规则裁决（报告标注） |

**scorer 发现顺序**：`--scorer-path` > `$QTAG_SCORER_PATH` > 从脚本目录向上探测
`{repo}/.pi/skills/qt-autotest-scorer/scripts/score.py`。scorer 为**可选依赖**，不硬绑跨技能路径。

**Layer 与评价依据**：

- **Layer 1（永远可用）**：规则裁决。依据本技能编写规范（self-checker.md §2/§2b/§3/§4/§5、
  test-code-gen.md、Iron Laws）——已固化为 structural JSON 的 7 类检查 + 分支白盒差集。
- **Layer 2（可选叠加）**：scorer 8 维加权 rubric（coverage25/assertion20/branch15/sufficiency12/
  structure10/isolation10/naming5/compliance3 + 硬门禁封顶）。`--coverage`/`--mutation` 可把
  Mode 3/4 产物喂给 score.py，维度越全评分越可信。

## 4. 裁决模型（verdict）

| verdict | 条件 |
|---------|------|
| **FAIL** | 任一 critical 规则：`EMPTY_ASSERT` / `SOLE_NO_FATAL` / `SOLE_GMOCK` / `SOLE_GMOCK_EXPECT` / `BRANCH_NOT_MAPPED` / `FN_COVERAGE_LT_100` |
| **WARN** | 无 critical，但有 error 级违规（命名、LOW_ASSERT、MISSING_AAA 等，可修复的质量问题） |
| **PASS** | 仅 warning 或干净 |
| **ERROR** | structural 检查本身失败（工具错误，不计入质量结论） |

与 scorer 的关系：规则裁决回答"有没有致命/规范问题"，数值评分回答"整体多好"。
scorer 的 `pass`（合格线 70 + 硬门禁）与 Mode 6 verdict **并列展示、互不覆盖**。

## 5. 编排流程（review 子命令）

```
resolve（场景 → targets.json）
  └─ 逐目标：
       1. structural   self-check-structural.py → <label>/<base>.structural.json
       2. branch       mcp-scan.py extract-branches → <label>/<base>.branch.json（可降级）
       3. score        score.py -s/-b/-i/-c/-m → scorecard-<Class>.json（可降级）
       4. verdict      derive_verdict(structural, branch)
       5. recs         scorer recommendations 优先，否则 RULE_ROUTES 映射
  └─ 聚合 → test-review-<label>.md + .json
```

- 退出码：默认恒 0（审查完成≠测试失败）；`--strict` 时存在 FAIL 则退出 1（可挂 CI 门禁）。
- 产物目录：`-o`（默认 `.reports/`），与 scorer 同目录约定。

## 6. 报告结构（test-review-<label>.md）

```
# 单元测试质量审查报告（Mode 6）
> 元信息：场景 / 仓库 / commit / 时间 / 数据源与降级说明 / 只读声明
## 1. 总览          —— 表：文件 | 状态 | 用例数 | 错误 | 警告 | 规则裁决 | 评分(等级)；
                       裁决分布 PASS/WARN/FAIL
## 2. 逐文件明细     —— 每文件：违规表(级别|规则|用例|行|说明)、分支白盒结果（或降级原因）、
                       评分卡摘要（等级/得分/硬门禁）、改进建议（P0/P1/P2 → 路由）
## 3. 改进路由汇总   —— 按优先级聚合，指回 qt-autotest-generator Mode 2 对应 reference
## 附录             —— 跳过清单 / 降级清单 / 产物路径
```

JSON（`.json` 同名）：机读全量，`meta / summary / files[]`，每文件含完整 structural、
branch、score 子对象，供 dashboard/CI 消费。

## 7. 测试策略（硬要求：脚本必须有单测）

沿用仓库 `test/` 的 pytest 约定（conftest 按文件路径加载连字符脚本模块）：

| 测试文件 | 覆盖 |
|----------|------|
| `test/test_review_resolve.py` | `is_test_file` / `parse_name_status`（-z 格式：M/A/D/R/C、含重命名）/ 删除跳过 / `load_managed_files` 归一化 / `collect_uncached`（tmp_path 真实目录树 + 合成 inventory）/ commit·range 规格解析 / **`extract_commit_files` 用真实临时 git 仓库**（init→commit→modify→commit，验证旧版本内容与缺文件报错）/ targets JSON 往返 |
| `test/test_review_report.py` | `derive_verdict` 四态（合成 structural/branch JSON）/ critical 识别 / scorer JSON 宽松摄取与缺失降级 / `RULE_ROUTES` 覆盖所有 critical 规则 / `render_report_md` 必备段落 / **CLI 端到端**（`--files` + 真实 self-check-structural 子进程 + `--no-branch --no-scorer` 降级路径，断言退出码与产物） |

纯逻辑全部抽成模块级函数（不埋在 main 里），CLI 边界才 subprocess/importlib。

## 8. 与其他模式/技能的关系

- **Mode 2**：审查建议路由回去修；不回写 inventory、不触发 reconcile。
- **Mode 3/4**：产物（`coverage_by_level.json` / `.ut-mutation.json`）作为 score.py 可选输入（`--coverage`/`--mutation`）。
- **qt-autotest-scorer**：Mode 6 是"输入解析+编排+审查报告"的上层；scorer 是"数值评分引擎"。
  scorer 单独用适合"给已知文件打分"；Mode 6 适合"从 commit/未入册集合出发的批量审查"。
- **Iron Laws**：#1 图谱门禁仅约束"分支白盒"维度（缺图谱该维度降级并标注，不阻塞 Layer 1 规则裁决）；
  #7 不修源码天然满足（只读）；#4 编译运行验证不适用（明确不编译不跑）。

## 9. 已知限制

- commit 审查时分支白盒用的是**图谱当前状态**的源码分支，与 commit 时刻可能漂移（未 push 场景必然）；
  报告元信息标注该 caveat，不作为 FAIL 依据之外强解释。
- 未缓存文件的类多半不在 inventory 中 → extract-branches 无方法可查 → 分支维度自动降级（预期行为）。
- 路径含特殊字符时 git 输出已用 `-z`（NUL 分隔）规避引号转义问题。
