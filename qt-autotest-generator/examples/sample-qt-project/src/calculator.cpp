// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "calculator.h"

Calculator::Calculator() = default;
Calculator::~Calculator() = default;

int Calculator::add(int a, int b) const
{
    return a + b;
}

int Calculator::subtract(int a, int b) const
{
    return a - b;
}

int Calculator::multiply(int a, int b) const
{
    return a * b;
}

double Calculator::divide(int a, int b) const
{
    if (b == 0) {
        return 0.0;
    }
    return static_cast<double>(a) / static_cast<double>(b);
}

bool Calculator::isEmpty() const
{
    return m_values.isEmpty();
}

void Calculator::pushValue(int value)
{
    m_values.append(value);
}

int Calculator::sum() const
{
    int total = 0;
    for (int v : m_values) {
        total += v;
    }
    return total;
}

void Calculator::clear()
{
    m_values.clear();
}

int Calculator::findMax(const QList<int> &values) const
{
    if (values.isEmpty()) {
        return 0;
    }
    int maxVal = values.first();
    for (int v : values) {
        if (v > maxVal) {
            maxVal = v;
        }
    }
    return maxVal;
}
