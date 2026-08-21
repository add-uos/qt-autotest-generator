# Mode 2 · 机械步骤固化设计方案

> 状态：**全部落地**（§2.1–§2.5 五个固化脚本均已实现并实测，reference 已接入「首选方式」提示框，旧手动路径保留为兜底）。执行以现行 `references/` 落地文档为准。
> 配套：Mode 4 设计见 `doc/mode4-mutation-design.md`；Mode 5 设计见 `doc/defect-export-design.md`
> 调研范围：`qt-autotest-generator` v3.3.0 Mode 2（`references/test-writer.md` 主流程 + 子步骤 references）

---

## 0. 动机

Mode 2 逐类闭环中，大量 token 花在**模型执行机械步骤**上：伪代码分组排序、读完整编译 log、正则回读自己刚写的文件、数 TEST_F 写回 JSON、填模板拼 commit message。这些步骤**零判断力需求**，却和"用例设计/stub 策略/缺陷判定"这类核心判断共享同一个上下文，既贵又慢还易错（大项目上模型做 12000 方法的分组排序可靠性存疑）。

原则与 skill 既有架构同构——**脚本做机械活，模型做判断**（先例：`scan-inventory.py` / `fetch-mcp-data.py` 已把 Mode 1 评分全固化）。本方案把同一哲学推进到 Mode 2 主流程。

**收益预期**：Mode 2 总 token 约省 20–40%（大项目更明显；编译 log 是按失败轮数线性烧的，是最大单项）。

**落地策略：渐进迭代**——脚本单独放 `scripts/`，一次落一个，模型侧 reference 对应小节逐步从"伪代码"改为"跑脚本、消费输出"，旧路径（手动执行）保留为兜底，不做一次性切换。

---

## 1. Mode 2 全流程固化筛查表

| 步骤 | 现状 | 固化判定 | 说明 |
|------|------|---------|------|
| reconcile 对账 | ✅ 已固化 | — | `fetch-mcp-data.py --incremental` + `stale-test-cleanup.py` |
| 过时测试清理 | ✅ 已固化 | — | `stale-test-cleanup.py` |
| **§4 确定待测类列表** | ✅ 已固化 | **✅ 已落地** | `scripts/plan-test-classes.py`，见 §2.1 |
| 依赖追踪 | 半固化 | ⚠️ 拆两半 | MCP 调用+原始数据收集可固化（trace_path 批量拉）；**stub 策略决策留模型** |
| 测试代码生成（用例设计） | ❌ | ❌ 核心判断 | 等价类/边界值/期望值——模型主战场，不固化 |
| **编译验证·执行** | ✅ 已固化 | **✅ 已落地** | `scripts/verify-build.py`，见 §2.2，**最大单项节省** |
| 编译验证·修复决策 | ❌ | ❌ 判断 | 错误→改哪里需要推理 |
| **自检·结构性检查** | ✅ 已固化 | **✅ 已落地** | `scripts/self-check-structural.py`，见 §2.3 |
| 自检·语义检查 | ❌ | ❌ 判断 | "断言是否名实相符"需要理解 |
| **§6 usecase_count 回写** | ✅ 已固化 | **✅ 已落地** | `scripts/update-usecase-count.py`，见 §2.4 |
| **§8 批次提交拼装** | ✅ 已固化 | **✅ 已落地** | `scripts/compose-commit.py`，见 §2.5 |

固化后模型在 Mode 2 只保留三件事：

1. **用例设计**（输入空间建模、期望值推导）
2. **stub 策略与修复决策**（含 build 摘要后的"改哪里"）
3. **缺陷判定**（测试侧还是源码侧、是否标红落盘）

---

## 2. 五个固化点详细设计

### 2.1 `plan-test-classes.py`（固化 test-writer §4，✅ 已落地）

**现状**：`test-writer.md` §4 用伪代码让模型在上下文里做：按 `class_qn` 分组 → 类 level 取方法最高级 → `level_rank` 排序 → is_gui 短名匹配 → 自由函数按 `file_path` 归组。

**目标形态**：

```
输入：{test_dir}/.ut-inventory.json
输出：{test_dir}/.reports/testable-classes.json
```

输出结构（模型直接消费，不再读 inventory 全量）：

```json
{
  "classes": [
    {
      "name": "FileView", "qualified_name": "project.src.FileView",
      "level": "high", "is_gui": true,
      "file_path": "src/lib/ui/fileview.cpp",
      "methods": ["<原 methods 条目数组，按 level 降序>"]
    }
  ],
  "free_function_groups": [
    { "file_path": "src/utils.cpp", "module": "utils",
      "functions": ["<原 methods 条目数组>"] }
  ]
}
```

要点：
- 12000 方法的 inventory 全量塞上下文既贵又易错；脚本毫秒级完成分组排序
- 类间排序保持现有语义：**类粒度优先级**（类 level = 方法最高级），同类内所有 testable 方法一次闭环，不跨类按方法穿插（与 `test-writer.md` §4 现有澄清一致）
- 同名类消歧（`A/Manager.h` vs `B/Manager.h`）沿用 `test-code-gen.md` §3 的模块路径拆分规则，脚本按 `file_path` 预生成 `module_path_flattened`
- 模型消费：读 `testable-classes.json`，逐类进入闭环，类内方法已按 level 降序排好

**已实现**：`scripts/plan-test-classes.py` + `test/test_plan_test_classes.py`（33 用例）

**实现要点**（含实测踩坑）：

| 要点 | 原因 |
|------|------|
| **双 schema 字段兼容** | 真实存量数据（deepin-image-viewer）用 `qn`/`file`/短名 `class_qn`，schema 文档用 `qualified_name`/`file_path`/全名 `class_qn`；`_field()` 依次查找两种字段名 |
| **分组键 = class_qn 本身** | class_qn 全名天然区分不同类；短名碰撞时追加 `@file_path` 消歧（同名类不同模块不合并，test-code-gen §3） |
| **class_qn 短名与全名** | scan-inventory.py 产出短名（如 `ApplicationAdaptor`），手写/旧数据可能全名（如 `proj.src.Calculator`）；类名统一取 `rsplit(".",1)[-1]`，类全名从方法 qn 剥最后一节推导 |
| **is_gui 用短名匹配** | `classes[].name` 是短名，`methods[].class_qn` 无论长短都取最后一段后匹配 |
| **level 缺失/非法归 low** | testable=true 但 level=null/非法时 `_level()` 归一化为 "low"，不影响排序；方法 entry 保持原字段不改写 |
| **module = file_path 最后一段目录** | test-code-gen §3 "source_dirs 最后一段"语义；无目录段 → "common" |
| **空 classes 列表容错** | 两份真实 inventory 的 `classes[]` 均为空 → `is_gui` 全 false，不崩溃 |
| **自由函数含 node_type=Method 无 class_qn** | test-writer §4 伪代码：class_qn 为空即入自由函数收集，不看 node_type |

**真实项目验证**：

- sample-qt-project：1 类（Calculator，9 方法），0 自由函数组
- **deepin-image-viewer**：40 类（2 high / 21 mid / 17 low，350 方法），34 自由函数组（238 函数含 QML）
  - 3 组同名类正确消歧（OcrInterface .cpp/.h、PathViewRangeHandler、RotateImageHelper）
  - 双 schema 字段自动兼容

```bash
python3 scripts/plan-test-classes.py --inventory tests/.ut-inventory.json
# [PLAN] classes: 40 (high=2, mid=21, low=17) | class methods: 350 | free-function groups: 34 (238 funcs)
```

**尚未接入 reference**（渐进策略）：`test-writer.md` §4 仍是伪代码形态；下一步在 §4 前加"首选方式：跑 `plan-test-classes.py` 消费清单，伪代码仅兜底"。


### 2.2 `verify-build.py`（固化 build-verifier 执行层，✅ 已落地）

**已实现**：`scripts/verify-build.py`，双项目实测：
- sample-qt-project：五条路径（正常 / 头文件缺失 / 类无成员 / 链接错误 / 断言失败）
- **deepin-image-viewer（真实大型项目）**：单巨型 target（`deepin-image-viewer-test`，全量 src+tests 单可执行），Qt6+DTK6+OCR 依赖，全新构建目录从零 configure + 全量编译 + 运行，`465 tests, 465 pass`；配套单元测试 46 个（`test/test_verify_build.py`）

**固化范围**：`build-verifier.md` §1–§4 的「执行」——cmake configure → 编译目标 → timeout 运行 → gtest XML 解析 → 错误正则预分类 → 结构化摘要。**模型保留**：修复决策、迭代计数（Iron Law #10）、源码缺陷判定。

**CLI**：

```bash
python3 scripts/verify-build.py --project <path> --class Calculator [--module core]
    [--target test_core]           # 显式指定 target（优先于自动推导）
    [--test-dir autotests]         # 默认自动探测 autotests/tests
    [--build-dir <path>]           # 默认 {project}/build-{test_dir}
    [--timeout 120] [--jobs N] [--skip-run]
```

**输出样例**（真实实测，stdout ≤ 8 行，退出码 0=全通过）：

```
# 编译失败
[VERIFY] Calculator | build: FAIL | run: skipped
errors:
  E1 undefined_reference | Calculator::extra() const
  E2 no_such_file | no_such_header.h | test_calculator.cpp:7
gtest: n/a
hint: E1 → trace_path 重查传递依赖或补 target_link_libraries；E2 → 补 target_include_directories
log: autotests/.results/build-calculator.log

# 运行失败
[VERIFY] Calculator | build: PASS | run: FAIL
errors:
  E1 assert_failure | Add_PositiveNumbers_ReturnsCorrectSum | test_calculator.cpp:35 | Expected equality of these values:
gtest: 15 tests, 14 pass, 1 fail (0.00s)
xml: autotests/.results/test-calculator.xml
hint: E? → 检查测试逻辑；逻辑正确而断言恒失败 → 疑似源码缺陷

# 全通过
[VERIFY] Calculator | build: PASS | run: PASS
errors: (none)
gtest: 15 tests, 15 pass, 0 fail (0.00s)
```

**实现要点**（含实测踩坑，后续脚本复用）：

| 要点 | 原因 |
|------|------|
| **注入 `LC_ALL=C`** 跑所有编译命令 | gcc/ld 错误文本会被本地化（中文系统输出"没有那个文件或目录"），正则匹配不到英文原文；强制 C locale 保证分类稳定 |
| **configure 传 `-DCMAKE_BUILD_TYPE=Debug`** | 存量项目（deepin-image-viewer）用 `CMAKE_BUILD_TYPE=Debug` 作为 `add_subdirectory(tests)` 开关，只传 `-DBUILD_TESTS=ON` 会导致 tests 子目录根本不加载 → 永远 no_target；且覆盖率标志仅 Debug 启用（framework-builder.md 既有实践） |
| **configure source 用项目绝对路径** | `cmake ..` 假设 build 目录在项目内（默认 `{project}/build-{test_dir}`），`--build-dir` 指到项目外（如 /tmp）时 `..` 指错源；实测曾因旧 cache 掩盖此错 |
| **hint 查表必须带默认值** | `no_target` / `assert_failure` / `runtime_*` 等运行期类别不在 ERROR_PATTERNS 表内，裸 `next()` 无默认值抛 StopIteration 直接崩溃（实测踩坑） |
| 错误分类表 = `build-verifier.md` §2 原样映射 | `stub_ext_freewrapper` / `vtable` / `undefined_reference` / `no_such_file` / `stub_signature` / `primary_expression` / `cmake_error`，**特殊模式在前**（vtable 本身也是 undefined reference） |
| 通用兜底 `compile_error` 模式 | 分类表外的编译错误（如 `'class X' has no member named 'y'`）不能静默丢弃——实测曾出现 build FAIL 但 `errors: (none)` 的误导输出 |
| make `help` 输出解析需剥 `... ` 前缀 | make 的 target 列表每行前导 `...`，ninja 是 `name: ...`，两种格式都要兼容 |
| target 解析：`test_<class>` 优先、`test_<module>` 回退、`--target` 显式指定 | 项目 target 粒度三种都存在：按类（`test_calculator`）、按模块（`test_core` glob）、单巨型（deepin-image-viewer 的 `deepin-image-viewer-test`，需 `--target` 显式指定） |
| 原始 log 落盘 `{test_dir}/.results/build-<class>.log` | 摘要不截断丢失信息；模型需要细节时定向回读，平时不进上下文 |
| gtest XML 失败简报 | message 首行是 `path:line`（部分版本带 `: Failure` 后缀），提取 `basename:line + 首行断言描述`，去绝对路径 |
| 运行期分类 | rc=124 → timeout；rc<0 或 134/139/136 → crash（hint 指向补 stub / 标红路径） |
| `QT_QPA_PLATFORM=offscreen` 注入运行环境 | 与 sample CMakeLists 的 gtest_discover_tests 保持一致；deepin-image-viewer 的 GUI 类用例同样跑通 |

**真实项目验证命令**（deepin-image-viewer）：

```bash
python3 scripts/verify-build.py \
  --project /home/zhy/debug/deepin-image-viewer \
  --class ImageViewer --target deepin-image-viewer-test \
  --test-dir tests --build-dir /tmp/div-build-probe --timeout 300
# [VERIFY] ImageViewer | build: PASS | run: PASS
# gtest: 465 tests, 465 pass, 0 fail (1.09s)
```

**尚未接入 reference**（渐进策略）：`build-verifier.md` §1–§3 仍是手动命令形态；下一步在 §1 前加"首选方式：跑 `verify-build.py` 消费摘要，手动命令仅兜底"，并把 §1 的 configure 命令补上 `-DCMAKE_BUILD_TYPE=Debug`（存量项目开关）。

### 2.3 `self-check-structural.py`（固化 self-checker 结构性检查，✅ 已落地）

**已实现**：`scripts/self-check-structural.py` + `test/test_self_check_structural.py`（57 用例）

固化 self-checker.md §2/§2b/§3/§4/§5/§5b 的纯文件正则检查（无图谱依赖），Python 实现块切分（不依赖 awk，跨平台可单测）。六类检查：

| 检查项 | 规则 | 违规码 |
|--------|------|--------|
| spdx | 前 5 行有 Copyright + GPL-3.0 License | — |
| naming | 用例名 ≥2 下划线分段 + 禁轮数批次号 `R\d+`/`Round\d+`/`Batch\d+` | TOO_FEW_SEGMENTS / ROUND_BATCH / MEANINGLESS |
| assertion | 每用例 ≥2 有效 EXPECT_*（排除 NO_FATAL/NO_THROW/EXPECT_CALL） | EMPTY_ASSERT / SOLE_NO_FATAL / SOLE_GMOCK_EXPECT / LOW_ASSERT / SOLE_BOOL_ASSERT(warn) |
| structure | 继承 `::testing::Test` + SetUp/TearDown 存在 | — |
| stub | set_lamda 出现须有 clear；clear 须在 TearDown | STUB_NOT_CLEARED / STUB_CLEAR_NOT_IN_TEARDOWN(warn) |
| env | 硬编码绝对路径（排除 QTemporaryDir）/ qputenv-qunsetenv 不平衡 / 真实外部资源（含时间/随机依赖，排除 stub） / 用户目录访问（QDir::homePath/QStandardPaths，warning） | HARDCODED_PATH / ENV_UNBALANCED / REAL_EXTERNAL_CALL / HOME_PATH_ACCESS(warn) |

**实现要点**：
- 块切分 `split_test_blocks` 按大括号深度判定边界，等价 awk 行为；局限（字符串字面量内 `{}` 干扰）与 awk 相同，注释说明
- `extract_tested_names` 提取用例名首段 PascalCase，供方法名差集（§1a）用——图谱侧拉全量仍需 MCP，留模型
- 语义检查（断言名实相符、AAA 结构、期望值正确性、副作用/返回值断言缺失的源码侧判断）**留模型**，需 trace_path 图谱
- **自审修正**：env 检查的 stub 排除正则初版用 `stub`（过宽），会吞掉注释中含 'stub' 的真实外部调用行；后收窄为 `stub\.set_lamda|__DBG_STUB_INVOKE__`，与 reference grep 严格一致（grep 仅排除这两种形式）
- **二轮修正**：`EXTERNAL_CALL_RE` 初版缺 §5b 时间/随机模式（`QDateTime::currentDateTime`/`QTime::currentTime`/`QRandomGenerator::system`/`srand`/`qsrand`），对照 reference 补齐；新增 `HOME_PATH_RE` 检测 `QDir::homePath()`/`QStandardPaths::writableLocation()` 用户目录访问（标 warning，语义留给模型复核是否已重定向到临时目录）

**真实项目验证**：sample-qt-project 的 `test_calculator.cpp` 跑出 15 个 LOW_ASSERT + 3 个 SOLE_BOOL_ASSERT——如实反映该文件每用例仅 1 断言的密度不足，脚本正确（spdx/naming/structure/stub/env 全 pass）。

### 2.4 `update-usecase-count.py`（固化 test-writer §6，✅ 已落地）

**已实现**：`scripts/update-usecase-count.py` + `test/test_update_usecase_count.py`（22 用例）

固化 test-writer.md §6 的纯机械操作：统计 TEST_F 用例数 → 按方法名匹配（首段 PascalCase vs 方法名 camelCase 小写归一化）→ 增量写回 usecase_count。

**实现要点**：
- 匹配规则：`TEST_F(Fixture, {MethodName_PascalCase}_...)` 首段小写 == `method.name.lower()`
- 双 schema 字段兼容（与 plan-test-classes 一致：`qn`/`file` vs `qualified_name`/`file_path`）
- class_qn 匹配：`--class` 短名匹配（全名取最后一段）；同名歧义用 `--class-qn` 精确匹配
- 失败安全：匹配不到的方法保持原 usecase_count；只改当前类 testable 方法，不覆盖其他类
- `--dry-run` 只打印不写回
- **自审修正**：`--class` 短名匹配多个不同 class_qn（同名类歧义）时会静默串改；后加 `detect_ambiguous_class` 检测，命中 >1 个 class_qn 时打印 WARNING 并提示用 `--class-qn`（不中断，但模型可据此重跑）

**真实项目验证**：sample-qt-project `test_calculator.cpp`（15 用例）正确分配到 9 方法——add:3、divide:2、sum:2、findMax:3、subtract:1、multiply:1、isEmpty:1、pushValue:1、clear:1。

### 2.5 `compose-commit.py`（固化 code-committer 信息拼装，✅ 已落地）

**已实现**：`scripts/compose-commit.py` + `test/test_compose_commit.py`（18 用例）

固化 code-committer.md §5 的纯模板渲染：从 classes_status 统计本批次/累计数据 → 按 git-commit-workflow test 类型 Log/Influence 格式生成提交信息到 stdout。

**实现要点**：
- 输入：`--status-file` 读 JSON（模型把内存变量 dump 出来：classes_status/batch_classes/baseline_commit/branch_name/project_name/project_path/test_dir/pms_no/issue_no）
- `--git-dir` 提供时用 `git log` 查 baseline title/date；不提供时 title 用 `"(no --git-dir)"` 占位（不暴露完整 sha）
- 项目名：优先 `project_name`，缺省时从 `project_path` 取 basename（对齐 code-committer §5 的 `split('/')[-1]`）
- 标题行 ≤ 80 字符（超长截断）；body 行 ≤ 80 字符
- 退出码 0=有 done 类可提交 / 2=无可提交类（跳过 commit，code-committer §1 空集处理）
- 模型保留：精确暂存（§4）、staged diff 复核（§6）、执行提交（§7）——跳过人工确认的规则不变
- **自审修正**：初版只接 `project_name`，但 reference 内存变量是 `project_path`，模型按内存传参会把完整路径写进标题；后加 `_derive_project_name` 兼容两者
- **二轮修正**：`git log` 失败（sha 不存在等）时 title 回退原用完整 sha；后改为 `"(git log failed)"` 占位，与 `"(no --git-dir)"` 占位逻辑一致

**端到端验证**：3 类（2 done + 1 failed），batch 含 1 done + 1 failed → 正确输出“新增 1 个类”、累计 2/3 classes、PMS 行。

---

## 3. 落地进度与顺序

| 优先级 | 项 | 状态 |
|--------|----|------|
| P0 | §2.2 verify-build.py | **✅ 已落地 v1.1**（`scripts/verify-build.py` + 46 单测；sample 五路径 + deepin-image-viewer 真实项目全流程实测；reference `build-verifier.md §1` 已接入首选方式） |
| P0 | §2.1 plan-test-classes.py | **✅ 已落地**（`scripts/plan-test-classes.py` + 33 单测；sample + deepin-image-viewer 双项目验证；reference `test-writer.md §4` 已接入首选方式） |
| P1 | §2.3 self-check-structural.py | **✅ 已落地**（`scripts/self-check-structural.py` + 57 单测；sample 验证如实报出 15 LOW_ASSERT；reference `self-checker.md` 已接入首选方式） |
| P2 | §2.4 update-usecase-count.py | **✅ 已落地**（`scripts/update-usecase-count.py` + 22 单测；sample 15 用例正确分配到 9 方法；reference `test-writer.md §6` 已接入首选方式） |
| P2 | §2.5 compose-commit.py | **✅ 已落地**（`scripts/compose-commit.py` + 18 单测；端到端验证多类多状态；reference `code-committer.md §5` 已接入首选方式） |

每项落地时：对应 reference 小节从"伪代码"改为"跑脚本、消费输出"（旧手动路径保留为兜底），并在 §状态传递 表中把对应内存变量改为脚本产物路径。Iron Laws 不变——脚本只是执行者，调度语义（3 轮上限、单类失败不阻塞、只 commit 不 push）仍在模型侧。

---

## 4. 不固化的红线（模型保留职责）

1. **用例设计**：等价类划分、边界值、期望值推导、TEST_P 参数化组织
2. **stub 策略与修复决策**：mock 深度分析（test-code-gen §4.0）、错误→修复动作映射
3. **缺陷判定**：source_defect_{compile,runtime,logic} 分类、标红、`.ut-defects.json` 落盘时机
4. **所有 Iron Laws 的遵守责任**在模型——脚本不承担铁律检查，只提供事实
