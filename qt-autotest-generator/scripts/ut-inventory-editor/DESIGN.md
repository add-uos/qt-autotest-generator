# UT Inventory Editor — 设计规划

## 1. 产品定位

**一句话**：`.ut-inventory.json` 的可视化编辑器，让开发者直观调节函数测试优先级、复核待审条目、管理覆盖率门禁。

**目标用户**：DDE/UOS 项目的 Qt 开发者 + 测试工程师

**核心场景**：
1. 打开 inventory → 浏览 high/mid/low/exempt 分布 → 调整单个方法的 level
2. 处理 review_queue（21 条待复核）→ 逐条确认 high/mid/low
3. 批量操作：选中多个 low → 提升为 mid
4. 按类/文件/因子筛选 → 定位特定方法
5. 保存 → 写回 `.ut-inventory.json`

## 2. 信息架构

```
┌─────────────────────────────────────────────────────────────────────┐
│ Header: 项目名 | 统计摘要 | 搜索 | 主题切换 | 保存/导出           │
├──────────┬──────────────────────────────────┬───────────────────────┤
│          │                                  │                       │
│  左侧栏   │         中间主表格                │     右侧详情面板      │
│  筛选+树   │    方法列表（可排序/筛选）         │   选中方法的完整信息    │
│          │                                  │   + level 编辑器      │
│  ─────── │  ──────────────────────────────  │  ─────────────────── │
│  Level    │  ☐ | name | class | file |      │  qn (全限定名)        │
│  high  │      level | factors | source   │  signature            │
│  mid    │      review | usecase           │  file path            │
│  low   │                                  │  factors (带得分)     │
│  exempt│                                  │  gate_thresholds      │
│          │                                  │  review 操作          │
│  ─────── │                                  │                       │
│  Class树  │                                  │  ─────────────────── │
│  ▶ InputE│                                  │  批量操作栏           │
│  ▶ MainWi│                                  │  (选中N条时显示)      │
│  ▶ BasicK│                                  │                       │
│          │                                  │                       │
├──────────┴──────────────────────────────────┴───────────────────────┤
│ Status Bar: 总数 | 可测试 | high/mid/low/exempt | 已修改数 | 保存状态│
└─────────────────────────────────────────────────────────────────────┘
```

## 3. 三栏布局详解

### 3.1 左侧栏（240px，可折叠）

**Level 筛选器**（复选框组，类似 IDS Viewer 的 tab 逻辑）：
```
☑ high   (58)    ← 点击只显示 high
☑ mid    (240)   ← 可多选
☑ low    (309)
☐ exempt (484)   ← 默认不勾选（噪音大）

── 节点类型 ──
☑ Method  (1091)
☑ Function (581)    ← 自由函数（main, helpers 等）
```

**Class 树**（可折叠，按 class_qn 分组）：
```
▼ CalculatorInterface  (9)
  ▶ showWindow          
  ▶ hideWindow          
  ▶ CalculatorInterface 💤
▼ InputEdit            (23)
  ▶ CurrentCursor...   
  ▶ SetAttrRecur       
  ▶ InputEdit          💤
▼ MainWindow           (15)
  ...
```

- 点击类名 → 中间表格过滤到该类
- 方法名后带 level 色标
- 类名后显示方法数 badge

**Scope Rules 显示**（折叠区）：
```
▶ Scope Rules (12)
  3rdparty/** → 不可测试
  **/moc_*.cpp → 不可测试
  ...
```

### 3.2 中间主表格（flex-1）

**列定义**：

| 列 | 宽度 | 说明 |
|----|------|------|
| ☐ | 32px | 复选框（批量选择） |
| Level | 64px | /⚖/💤/ 色标 + 文字，**可点击切换** |
| Name | 180px | 方法名（monospace） |
| Type | 48px | M/F 标签（Method/Function） |
| Class | 120px | class_qn 短名 |
| File | 160px | 文件路径（相对） |
| Factors | auto | 因子标签（dbus_slot, complexity:12 等） |
| Source | 64px | auto/suggested/manual 标签 |
| Review | 64px | pending/auto/exempt 状态标签 |
| Usecase | 48px | 已有用例数 |

**交互**：
- 点击行 → 右侧详情面板
- 点击 Level 列 → 弹出 level 选择器（high/mid/low/exempt）
- 点击 Factors 标签 → 筛选含该因子的所有方法
- 列头可排序
- 虚拟滚动（大项目 12000+ 方法）

**Level 色标设计**：
```
high   → bg-emerald-500/20 text-emerald-400 border-emerald-500/30
mid    → bg-amber-500/20 text-amber-400 border-amber-500/30
low    → bg-slate-500/20 text-slate-400 border-slate-500/30
exempt → bg-red-500/20 text-red-400 border-red-500/30
```

**Factor 标签设计**（小 pill）：
```
dbus_slot     → 绿色 pill  (+3)
q_invokable   → 绿色 pill  (+3)
complexity:12 → 蓝色 pill  (+2)
in_degree:5   → 灰色 pill  (+1)
name_pattern  → 橙色 pill  (suggested)
destructor    → 红色 pill  (-1)
```

### 3.3 右侧详情面板（360px，可折叠）

**选中方法的完整信息**：

```
┌─ 方法详情 ─────────────────────────┐
│                                     │
│  showWindow                         │
│  CalculatorInterface                │
│                                     │
│  Level:  [ high ▾]   ← 下拉编辑  │
│  Source: auto                       │
│  Review: auto                       │
│                                     │
│  ── 签名 ──                         │
│  bool showWindow()                  │
│                                     │
│  ── 文件 ──                         │
│  src/calculatorInterface.cpp        │
│                                     │
│  ── 评分因子 ──                     │
│  dbus_slot          +3  ← 得分明细  │
│  complexity:8       +1              │
│  in_degree:3        +1              │
│  ─────────────                      │
│  总分: 5 → high (≥3)               │
│                                     │
│  ── 覆盖率门禁 ──                   │
│  行覆盖 ≥ 90%  分支覆盖 ≥ 80%      │
│  函数覆盖 = 100%                    │
│                                     │
│  ── 已有测试用例 ──                 │
│  0 个 (未测试)                      │
│                                     │
└─────────────────────────────────────┘
```

**Review Queue 模式**（当有 pending 条目时）：

```
┌─ 待复核 (21/21) ───────────────────┐
│                                     │
│  [1/21] clearItems                  │
│  SimpleListModel                    │
│                                     │
│  自动建议:  high                  │
│  原因: 方法名含 clearItems          │
│  默认: ⚖ mid                        │
│                                     │
│  你的决定:                          │
│  [ high] [⚖ mid] [💤 low]       │
│  [跳过]              [← 上一条]    │
│                                     │
│  ── 进度 ──                         │
│  ████████░░░░░░░ 8/21              │
│                                     │
└─────────────────────────────────────┘
```

## 4. 顶部 Header

```
┌─────────────────────────────────────────────────────────────────────┐
│ 🔬 UT Inventory Editor  |  deepin-calculator  |  base: 3838e80    │
│                                                                     │
│  [🔍 搜索方法名/类名/文件...]                                       │
│                                                                     │
│  58  240  309  484  |  待复核: 21  |  已修改: 3            │
│                                                                     │
│  [🌙 主题] [📥 导入] [💾 保存] [📤 导出] [⚙ 设置]                  │
└─────────────────────────────────────────────────────────────────────┘
```

- 统计摘要用色标 pill 显示，点击可快速筛选
- "已修改"计数器：跟踪未保存的变更数
- 保存按钮：写回 `.ut-inventory.json`

## 5. 批量操作

选中多条方法后，表格上方浮出操作栏：

```
┌─ 已选 12 条 ──────────────────────────────────────────────┐
│  [设为 high]  [设为 mid]  [设为 low]  [豁免]  │
│  [✅ 取消豁免]  [取消选择]                                │
└────────────────────────────────────────────────────────────┘
```

## 5b. 豁免 ↔ 非豁免切换

**核心需求**：用户应能在编辑器中切换方法的 testable 状态，
实现豁免 ↔ 非豁免的双向切换。

### 切换逻辑

| 操作 | testable | level | exempt_reason | source | review_status |
|------|----------|-------|---------------|--------|---------------|
| 非豁免 → 豁免 | true→false | → exempt | → "manual:原因" | → manual | → exempt |
| 豁免 → 非豁免 | false→true | → 重新评分 | → null | → manual | → auto |

**豁免 → 非豁免**时，需要重新评分：
- 根据 factors 重新计算 level（high/mid/low）
- 如果 factors 为空，默认 low
- source 标记为 `manual`（表示人工干预）

### UI 交互

1. **Level 列点击**：4 选项浮层增加「豁免」选项
2. **右侧详情面板**：testable 开关（toggle switch）
   - 关闭 → 弹出确认「标记为不可测试？原因：」
   - 开启 → 自动重新评分
3. **批量操作栏**：增加「豁免」和「✅ 取消豁免」按钮
4. **右键菜单**：豁免/取消豁免

### 豁免原因编辑

切换到豁免时，弹出原因输入：
```
┌─ 标记为不可测试 ──────────────────┐
│                                    │
│  原因: [手动豁免 - 不需要单测  ]   │
│                                    │
│  [取消]  [确认豁免]               │
└────────────────────────────────────┘
```

exempt_reason 存储为 `"manual:用户输入的原因"`
```

## 6. 数据流与状态管理

```javascript
// 核心状态
const state = {
  inventory: null,           // 原始 JSON 数据
  methods: [],               // 扁平化方法列表（含编辑状态）
  modified: new Map(),       // 已修改方法: qn → {field: oldValue}
  filters: {
    levels: new Set(['high', 'mid', 'low']),  // 默认不含 exempt
    classQn: null,
    search: '',
    factor: null,
    nodeType: null,           // null=all, 'Method', 'Function'
  },
  sort: { column: 'level', direction: 'desc' },
  selected: new Set(),       // 复选框选中
  activeMethod: null,        // 右侧详情
  reviewIndex: 0,            // review queue 当前索引
};
```

**编辑逻辑**：
- 修改 level → `source` 自动变为 `"manual"`，`review_status` 变为 `"confirmed"`
- 修改 testable → false：`level` 变为 `"exempt"`，`exempt_reason` 设为 `"manual:原因"`
- 修改 testable → true：`level` 根据 factors 重新评分，`exempt_reason` 设为 null
- 所有修改进入 `modified` Map（记录旧值用于撤销），保存时一次性写回

**撤销支持**：
- `Ctrl+Z` 撤销最近一次修改（从 modified Map 恢复旧值）
- 撤销栈最多 50 步

**保存逻辑**：
```javascript
function save() {
  // 1. 将 modified Map 中的变更写回 inventory.methods
  // 2. 重新计算 scan_stats
  // 3. 更新 review_queue（移除已 confirmed 的条目）
  // 4. 写入 .ut-inventory.json
  // 5. 清空 modified Map
}
```

## 7. 技术栈

| 层 | 选择 | 理由 |
|----|------|------|
| 框架 | 纯 HTML + Vanilla JS | 参考 IDS Viewer，零构建依赖，单文件可分发 |
| 样式 | Tailwind CSS (CDN) | 快速开发，与 IDS Viewer 一致 |
| 图标 | Lucide Icons | IDS Viewer 同款，轻量 SVG |
| 字体 | JetBrains Mono + IBM Plex Sans | 代码用 mono，UI 用 sans，设计系统推荐 |
| 虚拟滚动 | 自实现（IntersectionObserver） | 12000+ 方法需要 |
| 文件 | 单 HTML 文件 + 内联 CSS/JS | 拖入浏览器即用 |

## 8. 配色方案（参考 IDS Viewer + 设计系统）

```css
:root {
  /* 基底 — 与 IDS Viewer 完全一致 */
  --bg: #0f172a;
  --bg-elev: #1e293b;
  --bg-elev2: #273449;
  --bg-hover: #334155;
  --border: #334155;
  --border-soft: #1e293b;
  --text: #f8fafc;
  --text-dim: #94a3b8;
  --text-muted: #475569;

  /* 强调 — 绿色系（测试通过 = 绿） */
  --accent: #22c55e;
  --accent-hover: #16a34a;
  --accent-soft: rgba(34, 197, 94, 0.14);

  /* Level 色标 */
  --high: #22c55e;      /* emerald-500 */
  --mid: #f59e0b;       /* amber-500 */
  --low: #64748b;       /* slate-500 */
  --exempt: #ef4444;    /* red-500 */

  /* Factor 色标 */
  --factor-high: #22c55e;   /* +3/+2 得分 */
  --factor-mid: #38bdf8;    /* +1 得分 */
  --factor-suggest: #f59e0b; /* suggested */
  --factor-penalty: #ef4444; /* -1 扣分 */

  /* 状态 */
  --warn: #f59e0b;
  --danger: #ef4444;
  --info: #38bdf8;
}

/* Light theme */
:root[data-theme='light'] {
  --bg: #f8fafc;
  --bg-elev: #ffffff;
  --bg-elev2: #f1f5f9;
  --bg-hover: #e2e8f0;
  --border: #cbd5e1;
  --border-soft: #e2e8f0;
  --text: #0f172a;
  --text-dim: #475569;
  --text-muted: #94a3b8;
  --accent: #16a34a;
  --accent-hover: #15803d;
  --accent-soft: rgba(22, 163, 74, 0.16);
  --high: #16a34a;
  --mid: #d97706;
  --low: #475569;
  --exempt: #dc2626;
}
```

## 9. 关键交互细节

### 9.1 Level 快速切换
- 点击 Level 色标 → 弹出 4 选项浮层（high/mid/low/exempt）
- 键盘快捷键：选中行后 `1`=high, `2`=mid, `3`=low, `4`=exempt
- 切换后色标有 200ms 过渡动画

### 9.2 Review Queue 流程
- 右侧面板顶部显示 "待复核 N" 徽章
- 点击进入 review 模式 → 逐条展示，3 个大按钮
- 快捷键：`H`=high, `M`=mid, `L`=low, `S`=跳过
- 完成后自动更新 review_queue 和 review_status

### 9.3 搜索
- 支持方法名、类名、文件路径模糊搜索
- 搜索框与 IDS Viewer 同款（带放大镜图标，focus 时绿色边框）
- 120ms debounce

### 9.4 虚拟滚动
- 中间表格使用 `IntersectionObserver` 实现窗口渲染
- 每次渲染 150 行，滚动到底部前 200px 时追加
- 与 IDS Viewer 的 `appendVendors` / `appendDevices` 逻辑一致

### 9.5 拖放导入
- 支持拖放 `.ut-inventory.json` 文件到页面
- 与 IDS Viewer 的拖放逻辑一致（overlay + toast）

### 9.6 保存确认
- 关闭页面时如有未保存修改 → `beforeunload` 提示
- 保存后 toast "已保存 N 处修改"

## 10. 文件结构

```
scripts/ut-inventory-editor/
├── index.html      # 单文件应用（内联 CSS + JS）
├── README.md       # 使用说明
└── (无其他依赖)
```

单 HTML 文件，约 2000-3000 行，包含：
- `<style>` 内联 CSS（~400 行，参考 IDS Viewer 的 styles.css）
- `<script>` 内联 JS（~1500 行，参考 IDS Viewer 的 app.js）
- Tailwind CSS CDN
- Lucide Icons CDN
- Google Fonts CDN（JetBrains Mono + IBM Plex Sans）

## 11. 与 IDS Viewer 的对应关系

| IDS Viewer | UT Inventory Editor | 映射 |
|------------|---------------------|------|
| Vendor 树 | Class 树 | 类 = 厂商，方法 = 设备 |
| Device 列表 | Method 表格 | 方法行 = 设备行 |
| Detail 面板 | Detail 面板 | 方法详情 = 设备详情 |
| PCI/USB Tab | Level 筛选器 | high/mid/low/exempt = 4 个 tab |
| Search | Search | 同款搜索框 |
| Export | Save + Export | 保存 JSON + 导出 CSV |
| Upload .ids | 拖放 .ut-inventory.json | 同款拖放 |
| Theme toggle | Theme toggle | 同款 |
| Status bar | Status bar | 同款 |
| Checkbox + bulk | Checkbox + bulk | 同款批量操作 |

## 12. Function 节点支持

知识图谱中 `Function` 标签包含自由 C/C++ 函数（`main`, `getThemeTypeSetting`,
`Chinese2Pinyin` 等），之前完全缺失。新版 `fetch_mcp_data.py` 同时收集 Method + Function。

**噪音过滤**：Function 节点中混有大量非函数条目：
- `DGUI_USE_NAMESPACE` / `DWIDGET_USE_NAMESPACE` → using 声明
- `DArrowRectangle` / `DListView` → DTK 类型别名
- `QListWidget` / `QStyledItemDelegate` → Qt 类型别名
- `Q_OBJECT` / `Q_DECLARE_METATYPE` → Qt 宏

`scan_inventory.py` 的 `is_function_noise()` 过滤规则：
- 全大写名称 → 宏/using 声明
- `D`/`Q` 前缀 PascalCase + param_count=0 + complexity=0 → 类型别名
- PascalCase + param_count=0 + complexity=0 → 误分类的类名

**实测效果**（deepin-calculator）：
- 原始 Function 节点：637
- 噪音过滤后：581（过滤 56 个 using/宏）
- 可测试 Function：6 个（main, getThemeTypeSetting, isDigitAllowedForBase 等）

**实测效果**（dde-file-manager）：
- 原始 Function 节点：931 (src/)
- 可测试 Function：761
- 新增 high：25 个（含 complexity=272 的 dfmplugin_fileoperations）

## 13. 实现优先级

| Phase | 内容 | 预计行数 |
|-------|------|---------|
| P0 | 三栏布局 + Level 筛选 + 方法表格 + 详情面板 + 保存 | ~1800 |
| P1 | Review Queue 流程 + 批量操作 + 搜索 + 豁免切换 | ~500 |
| P2 | Class 树 + Factor 筛选 + 虚拟滚动优化 + Function 过滤 | ~400 |
| P3 | 键盘快捷键 + 拖放导入 + CSV 导出 + 撤销栈 | ~300 |
