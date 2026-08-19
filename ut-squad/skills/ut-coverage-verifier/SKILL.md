---
name: ut-coverage-verifier
description: "单元测试覆盖率统计与门禁验证：封装项目 tests/test-prj-running.sh + gen-ut-summary.py，解析 ut-summary.json 做双门禁（有效函数覆盖率=100% / 行覆盖率≥90%）、回归、ASAN 核验，断言有效性 lint，生成基于流程基线的 gzip 全量 patch。支持 baseline（现状）、gate（门禁）、patch（出 patch）三种模式。"
version: "1.0.0"
user-invocable: true
argument-hint: "[mode: baseline|gate|patch] [project-path]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

# UT Coverage Verifier

封装项目自身的覆盖率统计工具链，提供统一口径的度量与门禁。**不另造统计口径**，所有判定只读项目 `tests/` 下脚本产出的 `ut-summary.json`。

## 核心原则

1. **只读项目自身工具链** —— 统计只走 `tests/test-prj-running.sh` + `tests/gen-ut-summary.py`，不改写、不旁路。
2. **双门禁硬判** —— 有效函数覆盖率=100%（含豁免）/ 行覆盖率≥90% / `test_case.failed`=0 / ASAN 无错误，四者全过才算通过。
3. **有效覆盖率公式** —— `passed / (total − exempted) × 100%`，exempted 取 `.ut-exemptions.json` 中 `approved=true` 的项；未 approved 不计入豁免。
4. **断言有效性 lint** —— 扫描 `autotests/**`，命中禁止模式即判不过。
5. **patch 全量 gzip** —— 基于流程基线，排除编译产物/源码修改/session/缓存。
6. **不修源码不提交** —— 只统计、只生成 patch，不 commit、不 push。

## 三种模式

### mode=baseline（跑现状覆盖率）

队长在 intake 阶段调用，了解补测前的起跑线。

```bash
# 1. 确认项目脚本存在
ls tests/test-prj-running.sh tests/gen-ut-summary.py

# 2. 跑全流程（脚本内部：cmake+ASAN+gtest+lcov+genhtml+gen-ut-summary）
cd tests && ./test-prj-running.sh; cd ..

# 3. 读摘要
cat build-ut/ut-summary.json
```

读出 `function_coverage` 与 `line_coverage` 的原始值，写入 session。**不判门禁**，只记录现状。

### mode=gate（门禁核验）

验证角色在广度+深度产物上调用。跑完 `test-prj-running.sh` 后按四道门禁判定：

```python
# 伪代码：gate 判定逻辑
import json
summary = json.load(open("build-ut/ut-summary.json"))
exemptions = json.load(open("autotests/.ut-exemptions.json"))
approved_exempt = [e for e in exemptions["exemptions"] if e["approved"]]

func = summary["function_coverage"]
line = summary["line_coverage"]
tc = summary["test_case"]

# 门禁1：有效函数覆盖率
total, passed = func["total"], func["passed"]
exempted = len(approved_exempt)
effective = passed / (total - exempted) * 100 if (total - exempted) > 0 else 100
gate_func = effective >= 100.0

# 门禁2：行覆盖率
gate_line = float(line["coverage"].rstrip("%")) >= 90.0

# 门禁3：回归
gate_regression = tc["failed"] == 0

# 门禁4：ASAN —— test-prj-running.sh 以 exit code 透传（set -e + test_exit_code）
# 脚本 exit 0 即 ASAN/UBSAN 无致命错误；额外 grep asan.log 确认无 "ERROR: AddressSanitizer"
```

门禁结果写入 `session.verify_status`（passed/failed）。失败时附缺口清单：

- 函数缺口 = 未覆盖函数集 − 豁免函数集 → 回退广度。
- 行缺口函数 = line_coverage<100% 的函数 → 回退深度。

### mode=patch（出 patch）

四道门禁 + 断言 lint 全过后调用。

```bash
# 1. 断言有效性 lint（先跑，不过不出 patch）
#    扫描 autotests/**/*.cpp，命中禁止模式即 fail
grep -rnE 'EXPECT_TRUE\(true\)|SUCCEED\(\)' autotests/ && FAIL=trivial_assert
# 详见下方「断言有效性 lint 规则」

# 2. 确认基线
BASE_SHA=$(jq -r '.baseline_commit' autotests/.ut-session.json)
git rev-parse --verify "$BASE_SHA"

# 3. 生成 gzip 全量 patch（从基线到当前 working tree 的测试代码差异）
ISSUE_ID=$(jq -r '.issue_identifier' autotests/.ut-session.json)
git diff --binary --full-index "$BASE_SHA" -- autotests/ \
  | gzip > "${ISSUE_ID}-ut-v1.patch.gz"

# 4. 排除清单（diff 前自检，确保这些不进 patch）
#    - build/, build-ut/, *.o, *.gcno, *.gcda
#    - 任何 src/ 下的源码改动（小队不改源码）
#    - autotests/.ut-session.json, autotests/.ut-exemptions.json（session 文件）
```

交付评论模板：

```text
base commit SHA：<sha>
patch 类型：全量（gzip）
应用命令：gzip -dc <file>.patch.gz | git apply --3way -（注意：不是 git am）
```

## 断言有效性 lint 规则

扫描 `autotests/**`，命中即判不过，输出 `文件:行:模式`：

| 禁止模式（grep） | 判定 |
|---|---|
| `EXPECT_TRUE(true)` / `ASSERT_TRUE(true)` / `SUCCEED()` | trivial 凑数 |
| `EXPECT_FALSE(false)` / `ASSERT_FALSE(false)` | trivial 凑数 |
| `EXPECT_NO_THROW(...)` 作为该 TEST 唯一断言 | 只验不崩 |
| 整个 TEST 块内无 `EXPECT_`/`ASSERT_` 引用被测对象成员或返回值 | 无可观测断言 |

> lint 实现提示：按 `TEST(...)` / `TEST_F(...)` 块切分，每块内统计断言数量与断言是否引用了被测对象（类成员 / 函数返回值 / 传入变量）。全为常量操作数的断言判为假断言。

## 状态文件交互

读写 `autotests/.ut-session.json`：

| 字段 | mode=baseline | mode=gate | mode=patch |
|---|---|---|---|
| `function_coverage_raw` | 写 | 写 | 读 |
| `line_coverage` | 写 | 写 | 读 |
| `function_coverage_effective` | — | 写 | — |
| `verify_status` | — | 写(passed/failed) | — |
| `verify_gates` | — | 写(四道门禁明细) | — |

## 红旗（出现即停）

- 项目无 `tests/test-prj-running.sh` 或 `tests/gen-ut-summary.py` → 停止，提示队长该项目未接入统计工具链。
- 改动 `tests/` 下脚本或 `gen-ut-summary.py`（小队不改统计口径）。
- `test-prj-running.sh` exit ≠ 0 且非已知测试失败（ASAN 致命错误）→ 停止报告。
- 门禁未全过就生成 patch。
- patch 包含 `src/` 源码改动或编译产物。
- 用增量场景的局部覆盖冒充全量门禁通过。
