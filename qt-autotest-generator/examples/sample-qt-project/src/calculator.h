// SPDX-FileCopyrightText: 2025 UnionTech Software Technology Co., Ltd.
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <QString>
#include <QList>

class Calculator
{
public:
    Calculator();
    ~Calculator();

    int add(int a, int b) const;
    int subtract(int a, int b) const;
    int multiply(int a, int b) const;
    double divide(int a, int b) const;

    bool isEmpty() const;
    void pushValue(int value);
    int sum() const;
    void clear();

    int findMax(const QList<int> &values) const;

private:
    QList<int> m_values;
};
