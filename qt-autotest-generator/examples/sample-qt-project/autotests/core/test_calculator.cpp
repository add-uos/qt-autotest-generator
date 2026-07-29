// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
// SPDX-License-Identifier: GPL-3.0-or-later

#include <gtest/gtest.h>
#include "stubext.h"
#include "calculator.h"

class CalculatorTest : public ::testing::Test {
protected:
    void SetUp() override {
        stub.clear();
        obj = new Calculator();
    }

    void TearDown() override {
        delete obj;
        stub.clear();
    }

    stub_ext::StubExt stub;
    Calculator *obj = nullptr;
};

// === add ===

TEST_F(CalculatorTest, Add_PositiveNumbers_ReturnsCorrectSum) {
    // Arrange
    int a = 5;
    int b = 3;

    // Act
    int result = obj->add(a, b);

    // Assert
    EXPECT_EQ(result, 8);
}

TEST_F(CalculatorTest, Add_NegativeNumbers_ReturnsCorrectSum) {
    // Arrange
    int a = -5;
    int b = -3;

    // Act
    int result = obj->add(a, b);

    // Assert
    EXPECT_EQ(result, -8);
}

TEST_F(CalculatorTest, Add_ZeroValues_ReturnsZero) {
    // Arrange
    int a = 0;
    int b = 0;

    // Act
    int result = obj->add(a, b);

    // Assert
    EXPECT_EQ(result, 0);
}

// === subtract ===

TEST_F(CalculatorTest, Subtract_PositiveNumbers_ReturnsCorrectDifference) {
    // Arrange
    int a = 10;
    int b = 4;

    // Act
    int result = obj->subtract(a, b);

    // Assert
    EXPECT_EQ(result, 6);
}

// === multiply ===

TEST_F(CalculatorTest, Multiply_PositiveNumbers_ReturnsCorrectProduct) {
    // Arrange
    int a = 6;
    int b = 7;

    // Act
    int result = obj->multiply(a, b);

    // Assert
    EXPECT_EQ(result, 42);
}

// === divide ===

TEST_F(CalculatorTest, Divide_ValidDivisor_ReturnsCorrectQuotient) {
    // Arrange
    int a = 10;
    int b = 2;

    // Act
    double result = obj->divide(a, b);

    // Assert
    EXPECT_DOUBLE_EQ(result, 5.0);
}

TEST_F(CalculatorTest, Divide_ZeroDivisor_ReturnsZero) {
    // Arrange
    int a = 10;
    int b = 0;

    // Act
    double result = obj->divide(a, b);

    // Assert
    EXPECT_DOUBLE_EQ(result, 0.0);
}

// === isEmpty / pushValue / sum / clear ===

TEST_F(CalculatorTest, IsEmpty_InitialState_ReturnsTrue) {
    // Act
    bool result = obj->isEmpty();

    // Assert
    EXPECT_TRUE(result);
}

TEST_F(CalculatorTest, PushValue_ValidValue_IncreasesSize) {
    // Arrange
    obj->pushValue(10);
    obj->pushValue(20);

    // Act
    bool empty = obj->isEmpty();

    // Assert
    EXPECT_FALSE(empty);
}

TEST_F(CalculatorTest, Sum_MultipleValues_ReturnsCorrectTotal) {
    // Arrange
    obj->pushValue(10);
    obj->pushValue(20);
    obj->pushValue(30);

    // Act
    int result = obj->sum();

    // Assert
    EXPECT_EQ(result, 60);
}

TEST_F(CalculatorTest, Sum_EmptyList_ReturnsZero) {
    // Act
    int result = obj->sum();

    // Assert
    EXPECT_EQ(result, 0);
}

TEST_F(CalculatorTest, Clear_AfterPush_MakesEmpty) {
    // Arrange
    obj->pushValue(10);
    obj->pushValue(20);

    // Act
    obj->clear();

    // Assert
    EXPECT_TRUE(obj->isEmpty());
}

// === findMax ===

TEST_F(CalculatorTest, FindMax_MultipleValues_ReturnsMaximum) {
    // Arrange
    QList<int> values = {3, 7, 2, 9, 5};

    // Act
    int result = obj->findMax(values);

    // Assert
    EXPECT_EQ(result, 9);
}

TEST_F(CalculatorTest, FindMax_EmptyList_ReturnsZero) {
    // Arrange
    QList<int> values;

    // Act
    int result = obj->findMax(values);

    // Assert
    EXPECT_EQ(result, 0);
}

TEST_F(CalculatorTest, FindMax_SingleElement_ReturnsThatElement) {
    // Arrange
    QList<int> values = {42};

    // Act
    int result = obj->findMax(values);

    // Assert
    EXPECT_EQ(result, 42);
}
