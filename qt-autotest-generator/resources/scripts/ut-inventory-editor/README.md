# UT Inventory Editor

`.ut-inventory.json` 的可视化编辑器，让开发者直观调节函数测试优先级、复核待审条目、管理覆盖率门禁。

## 使用方式

直接在浏览器中打开 `index.html`，或拖放 `.ut-inventory.json` 文件到页面。

### 数据来源

由 `fetch_mcp_data.py` + `scan_inventory.py` 生成：

```bash
python3 fetch_mcp_data.py --project <id> --output .ut-inventory.json
```

## 功能

| 功能 | 说明 |
|------|------|
| 三栏布局 | 左侧筛选+Class树 / 中间方法表格 / 右侧详情面板 |
| Level 筛选 | 🌟 high / ⚖ mid / 💤 low / 🚫 exempt 复选框 |
| Level 编辑 | 点击 Level 色标 → 弹出选择器，或键盘 1-4 |
| 豁免切换 | testable 开关 → 豁免/取消豁免，双向切换 |
| Review Queue | 逐条复核待审条目，确认 high/mid/low |
| 批量操作 | 多选 → 批量设 Level / 豁免 / 取消豁免 |
| 因子筛选 | 点击 Factor pill → 筛选含该因子的所有方法 |
| Class 树 | 按类分组，点击类名过滤 |
| 搜索 | 方法名/类名/文件路径模糊搜索 |
| 虚拟滚动 | IntersectionObserver，支持 12000+ 方法 |
| 撤销 | Ctrl+Z，最多 50 步 |
| 保存 | 写回 `.ut-inventory.json` |
| CSV 导出 | 导出当前筛选结果 |
| 拖放导入 | 拖放 JSON 文件到页面 |
| 暗色/亮色主题 | 自动检测系统偏好，手动切换 |

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `1` | 设为 🌟 high |
| `2` | 设为 ⚖ mid |
| `3` | 设为 💤 low |
| `4` | 设为 🚫 exempt（弹出原因输入） |
| `Ctrl+Z` | 撤销 |
| `Ctrl+S` | 保存 |

## 覆盖率门禁

| Level | 行覆盖 | 分支覆盖 | 函数覆盖 |
|-------|--------|----------|----------|
| 🌟 high | ≥90% | ≥80% | =100% |
| ⚖ mid | ≥60% | — | =100% |
| 💤 low | 无硬性要求 | — | — |
| 🚫 exempt | 豁免 | 豁免 | 豁免 |

## 技术栈

- 纯 HTML + Vanilla JS（零构建依赖，单文件可分发）
- Tailwind CSS (CDN)
- Lucide Icons (CDN)
- Google Fonts: JetBrains Mono + IBM Plex Sans
