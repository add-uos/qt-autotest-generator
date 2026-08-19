# qt-autotest-generator 豁免改造说明

> 本文档说明如何把现有 `qt-autotest-generator`（位于 `../../qt-autotest-generator/SKILL.md`）改造为单元测试小队的「广度补全」能力载体，使其支持「阈值=100 + 统计豁免」。
>
> 改造原则：**最小侵入**，不破坏现有 12 条 Iron Law 与 12 个 subagent 流水线，只新增豁免相关机制。

---

## 改造目标

现有 skill 的门禁是「函数覆盖率不低于阈值（默认 80%）」。小队广度角色需要它：
1. 默认阈值改为 100（由 session.targets 注入，而非硬编码）。
2. 新增「豁免机制」：测不到的函数标豁免候选，门禁判**有效**覆盖率而非原始覆盖率。
3. 新增「断言有效性」前置意识（虽然 lint 在 verifier，但广度阶段提前规避）。

## 具体修改点

### 修改 1：核心原则新增第 13 条（豁免机制）

在 `SKILL.md` 的「核心原则（Iron Laws）」末尾，原则 12 之后新增：

```md
13. **不可测函数标豁免候选** —— 确属不可测的函数（GUI 事件槽、DBus 外部依赖、硬件、入口），
    写豁免候选到 `autotests/.ut-exemptions.json`，含 function / file / reason / category
    （仅 gui_event / ipc_extern / hardware / entry_only 四类）/ approved=false。
    门禁判有效函数覆盖率 = passed / (total − exempted) × 100%，exempted 仅计 approved 项；
    广度角色只提候选，不自行 approved，由人工在 Issue 一次性确认。
```

### 修改 2：self_checker 增加豁免统计

在 `agent/self_checker.md` 的单类自检项中，新增：
- 若该类含未覆盖方法，检查是否已写豁免候选（否则不算广度完成）。
- 豁免 category 必须是四类之一，reason 非空。

### 修改 3：incremental_updater 增加豁免候选生成

在 `agent/incremental_updater.md` 中，补全函数时：
- 若某方法经多轮重试仍无法覆盖，判断是否属豁免类别（GUI/DBus/硬件/入口），是则写豁免候选而非标记 failed。
- 豁免候选写 `autotests/.ut-exemptions.json`（新建文件，结构见下）。

### 修改 4：路由队长 reconcile 增加豁免对账

在 `SKILL.md` 的「reconcile 逻辑」中，源码变更对账后：
- 若新增方法属豁免类别，直接写豁免候选，不强制补测。

### 修改 5：覆盖率门禁判定口径

在 `agent/self_checker.md` 与 `report_generator.md` 中，门禁判定从：

```
function_coverage >= coverage_threshold  （原始覆盖率）
```

改为：

```
effective_coverage = passed / (total - approved_exempt) * 100
effective_coverage >= coverage_threshold  （有效覆盖率）
```

> 注意：广度角色读到的 `.ut-exemptions.json` 中 approved 字段在人工确认前都是 false；
> 广度阶段门禁判定的 exempted 应取「候选豁免」（即 function 级标记），因为人工确认是
> 在广度之后、深度之前的关卡。实际实现：广度完成判定 = 未覆盖函数集 ⊆ 豁免候选集
> （无论 approved），然后回交队长触发人工确认。

---

## 新增文件：`autotests/.ut-exemptions.json`

```json
{
  "baseline_commit": "abc1234",
  "generated_at": "2026-08-14T10:00:00+08:00",
  "exemptions": [
    {
      "function": "ImageViewer::onImageLoaded",
      "qualified_name": "deepin-image-viewer.ImageViewer.onImageLoaded",
      "file": "src/src/viewer/imageviewer.cpp",
      "category": "gui_event",
      "reason": "GUI 渲染槽，依赖 QImage 实际绘制与 QPaintEvent，单测环境无法触发真实绘制",
      "approved": false
    },
    {
      "function": "DBusService::handleScreenshotRequest",
      "file": "src/src/dbus/screenshot_adaptor.cpp",
      "category": "ipc_extern",
      "reason": "DBus 信号 handler，依赖远端 screenshot 服务注册，需集成测试而非单测",
      "approved": false
    }
  ],
  "summary": {
    "total_candidates": 2,
    "by_category": {"gui_event": 1, "ipc_extern": 1, "hardware": 0, "entry_only": 0}
  }
}
```

---

## 触发词调整（SKILL.md 顶部）

在现有触发条件中补充：
- 「补全到 100% 覆盖率」「全函数覆盖」 → coverage_threshold=100，启用豁免机制。
- 由小队队长注入 session.targets.function_coverage_threshold，覆盖默认 80。

## 注意事项

- **不改动测试框架约定**（Google Test only / autotests/ / 命名规范）。
- **不改动 MCP 提供方机制**（远端优先本地兜底）。
- **不改动批次提交逻辑**（code_committer / self_checker commit_check）。
- 改造后现有 80% 阈值的旧用法仍兼容（阈值由调用方注入），向后兼容。

## 验收

改造后，对一个真实项目（如 deepin-image-viewer）跑广度角色，应能：
1. 对可测函数补测到 100%。
2. 对不可测函数产出 `.ut-exemptions.json` 候选清单。
3. 原始覆盖率 < 100%，但有效覆盖率（含豁免候选）= 100%。
4. 队长拿到候选清单后在 Issue 贴出，等人工 approve。
