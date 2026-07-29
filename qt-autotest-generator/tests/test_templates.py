# SPDX-FileCopyrightText: 2025 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for template files: syntax checks, placeholder presence, SPDX headers.
"""

import re
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).parent.parent / "resources" / "templates"


class TestGoogleTestBase:
    """Tests for google-test-base.cpp template"""

    def test_has_spdx_header(self):
        content = (TEMPLATES_DIR / "google-test-base.cpp").read_text()
        assert "SPDX-FileCopyrightText" in content
        assert "SPDX-License-Identifier" in content

    def test_has_placeholders(self):
        content = (TEMPLATES_DIR / "google-test-base.cpp").read_text()
        assert "{header_file}" in content
        assert "{ClassName}" in content
        assert "{Namespace}" in content
        assert "{NamespaceEnd}" in content
        assert "{SetUpStubs}" in content
        assert "{TestCases}" in content

    def test_has_setuptestuite_for_gui(self):
        content = (TEMPLATES_DIR / "google-test-base.cpp").read_text()
        assert "SetUpTestSuite" in content
        assert "{SetUpTestSuite}" in content

    def test_has_set_up_object_placeholder(self):
        content = (TEMPLATES_DIR / "google-test-base.cpp").read_text()
        assert "{SetUpObject}" in content
        assert "{TearDownObject}" in content

    def test_inherits_testing_test(self):
        content = (TEMPLATES_DIR / "google-test-base.cpp").read_text()
        assert "::testing::Test" in content


class TestCmakeAutotests:
    """Tests for cmake-autotests.txt template"""

    def test_has_find_package_gtest(self):
        content = (TEMPLATES_DIR / "cmake-autotests.txt").read_text()
        assert "find_package(GTest REQUIRED)" in content

    def test_has_placeholders(self):
        content = (TEMPLATES_DIR / "cmake-autotests.txt").read_text()
        assert "{THIRD_PARTY_PACKAGES}" in content
        assert "{ADD_SUBDIRECTORIES}" in content

    def test_no_qt_test_reference(self):
        content = (TEMPLATES_DIR / "cmake-autotests.txt").read_text()
        assert "Qt::Test" not in content
        assert "QtTest" not in content


class TestCmakeSubmodule:
    """Tests for cmake-submodule.txt template"""

    def test_has_placeholders(self):
        content = (TEMPLATES_DIR / "cmake-submodule.txt").read_text()
        assert "{QT_VERSION}" in content
        assert "{module_name}" in content
        assert "{PROJECT_LIBRARIES}" in content
        assert "{source_module_path}" in content

    def test_no_double_brace_syntax(self):
        content = (TEMPLATES_DIR / "cmake-submodule.txt").read_text()
        assert "${{" not in content

    def test_no_qt_test_link(self):
        content = (TEMPLATES_DIR / "cmake-submodule.txt").read_text()
        assert "Qt::Test" not in content
        assert "QtTest" not in content

    def test_links_gtest(self):
        content = (TEMPLATES_DIR / "cmake-submodule.txt").read_text()
        assert "GTest::gtest" in content
        assert "GTest::gtest_main" in content

    def test_links_qt_core_and_widgets(self):
        content = (TEMPLATES_DIR / "cmake-submodule.txt").read_text()
        assert "Qt${QT_VERSION}::Core" in content
        assert "Qt${QT_VERSION}::Widgets" in content

    def test_has_gtest_discover_tests(self):
        content = (TEMPLATES_DIR / "cmake-submodule.txt").read_text()
        assert "gtest_discover_tests" in content


class TestStubPatterns:
    """Tests for stub-patterns.cpp template"""

    def test_has_ui_stubs(self):
        content = (TEMPLATES_DIR / "stub-patterns.cpp").read_text()
        assert "QWidget::show" in content
        assert "QWidget::hide" in content
        assert "VADDR(QDialog, exec)" in content

    def test_has_vaddr_pattern(self):
        content = (TEMPLATES_DIR / "stub-patterns.cpp").read_text()
        assert "VADDR" in content

    def test_has_static_cast_pattern(self):
        content = (TEMPLATES_DIR / "stub-patterns.cpp").read_text()
        assert "static_cast" in content

    def test_has_file_stubs(self):
        content = (TEMPLATES_DIR / "stub-patterns.cpp").read_text()
        assert "QFile::open" in content
        assert "QFile::readAll" in content

    def test_has_timer_stubs(self):
        content = (TEMPLATES_DIR / "stub-patterns.cpp").read_text()
        assert "QTimer::start" in content
        assert "QTimer::stop" in content
