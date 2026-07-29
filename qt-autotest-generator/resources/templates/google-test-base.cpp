// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
// SPDX-License-Identifier: GPL-3.0-or-later

#include <gtest/gtest.h>
#include "stubext.h"
#include "{header_file}"

{Namespace}

class {ClassName}Test : public ::testing::Test {
protected:
    static void SetUpTestSuite() {
        // GUI 类用 QCoreApplication，不用 QApplication（避免 X11/Wayland 崩溃）
        // 非 GUI 类此函数可省略，由 test_writer 按需删除
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

{TestCases}

{NamespaceEnd}
