// SPDX-FileCopyrightText: {SPDX_YEAR} UnionTech Software Technology Co., Ltd.
// SPDX-License-Identifier: GPL-3.0-or-later
//
// 用例计数声明（self-check-structural 验证此块）：
// | method | level | factors | min | actual |
// |--------|-------|---------|-----|--------|
// | method1 | high | complexity:25 | 3 | ? |
// | method2 | mid  | - | 2 | ? |
// ─── 生成后填入 actual 列，低于 min 即违规 ───
//
// 最小清单完成情况（test-code-gen §最小清单）：
// 1. 每个公开方法 ≥ 1 用例: [ ]
// 2. 每个输入维度按等价类划分 ≥ 1 用例/类: [ ]
// 3. 每个等价类的边界值显式覆盖: [ ]
// 4. 同质 ≥ 3 组用 TEST_P: [ ]
// 5. 分支清单 → 用例映射已列出: [ ]
// 6. 每条 if/switch/throw/early-return 有触发用例: [ ]
// 7. 异常路径 EXPECT_THROW 精确匹配: [ ]
// 8. 负面场景有专门用例: [ ]
// 9. 负面用例验证强异常安全: [ ]
// 10. stub_ext vs gMock 选择正确: [ ]

#include <gtest/gtest.h>
// #include <QApplication>       // GUI 类需要此 include，由生成流程按需取消注释
// #include <QCoreApplication>    // 非 GUI 类需要此 include，由生成流程按需取消注释
// #include <gmock/gmock.h>     // gMock 需要此 include，由生成流程按需追加
#include "stubext.h"
#include "{header_file}"

{BranchList}

{Namespace}

class {ClassName}Test : public ::testing::Test {
protected:
    static void SetUpTestSuite() {
        // GUI 类用 QApplication（QWidget 子类需要 QApplication），配合 QT_QPA_PLATFORM=offscreen 避免 X11/Wayland 崩溃
        // 非 GUI 类此函数可省略，由生成流程按需删除
        {SetUpTestSuite}
    }

    static void TearDownTestSuite() {
        {TearDownTestSuite}
    }

    void SetUp() override {
        stub.clear();
        {SetUpObject}
        {SetUpStubs}
    }

    void TearDown() override {
        {TearDownObject}
        stub.clear();
    }

    stub_ext::StubExt stub;
    {ClassName} *obj = nullptr;
};

// ═══════════════════════════════════════════════════════════════
// ⚠️ 以下每个 TEST_F 必须包含 // Arrange / // Act / // Assert 三段注释
// ⚠️ 缺少任一段 → self-check-structural 报 MISSING_AAA 违规
// ⚠️ 每段至少有 1 行实质内容（空段也算违规）
// ═══════════════════════════════════════════════════════════════

{TestCases}

{NamespaceEnd}
