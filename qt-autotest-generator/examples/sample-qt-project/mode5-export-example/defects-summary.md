# 源码缺陷清单 · sample-qt-project

> 基线: a1b2c3d · 生成: 2026-08-20 05:59:36 UTC

## 统计摘要

- 共 **2** 个缺陷（open 2 / fixed 0 / reopened 0）
- 严重度: 高 1 / 中 1 / 低 0
- 类型: 编译 0 / 运行 1 / 逻辑 1 / 待排查 0
- 影响: 2 个方法 / 1 个类

## 🔴 高危 (1)

| 类 | 方法 | 用例 | 文件:行 | 类型 | 证据 | 建议 |
|----|------|------|---------|------|------|------|
| Calculator | divide | [Divide_ByZero_ShouldThrow](../autotests/core/test_calculator.cpp) | [src/calculator.cpp:18](../src/calculator.cpp#L18) | runtime | segfault at line 18, stub fully applied, source has no zero-… | divide 方法未对 b==0 做检查，应抛异常或返回错误码 |

## 🟡 中危 (1)

| 类 | 方法 | 用例 | 文件:行 | 类型 | 证据 | 建议 |
|----|------|------|---------|------|------|------|
| Calculator | add | [Add_LargeNumbers_ShouldNotOverflow](../autotests/core/test_calculator.cpp) | [src/calculator.cpp:7](../src/calculator.cpp#L7) | logic | ASSERT 恒失败: add(INT_MAX, 1) 返回 INT_MIN，未处理溢出 | 大数相加应做溢出检查或用更大类型 |

## ✅ 已修复 (0)

无

## 下一步建议

- 优先处理 1 个高危运行/编译缺陷（阻塞测试推进）
- 0 个 needs_manual 需人工排查根因
- 0 个已修复记录保留，关注回归
