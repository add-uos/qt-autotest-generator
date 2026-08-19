# 示例项目

本目录包含一个示例 Qt 项目，展示 qt-autotest-generator 的典型产物。

## 目录结构

```
sample-qt-project/
├── src/
│   ├── CMakeLists.txt
│   └── calculator.h          # 示例 Qt 类（仅头文件，内联实现）
├── CMakeLists.txt             # 项目根 CMake（含 BUILD_TESTS 开关）
└── autotests/                 # 技能生成的测试框架
    ├── 3rdparty/stub/         # stub-ext（从 resources/stub/ 复制）
    ├── cmake/UnitTestUtils.cmake
    ├── run-ut.sh
    ├── report_generator/
    ├── CMakeLists.txt          # 测试根 CMake
    ├── .ut-inventory.json       # inventory 分级表示例
    ├── .results/              # gtest XML 输出
    ├── .reports/              # HTML/CSV 报告
    └── core/
        ├── CMakeLists.txt     # 模块 CMake
        └── test_calculator.cpp # 生成的测试文件
```

## 使用

1. 将 `sample-qt-project/` 复制到独立目录
2. 构建并运行测试：
   ```bash
   cd sample-qt-project
   mkdir build && cd build
   cmake .. -DBUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Debug
   cmake --build . -j$(nproc)
   ctest --output-on-failure
   ```
3. 或在 Agent 中触发：「为 sample-qt-project 生成单元测试」
