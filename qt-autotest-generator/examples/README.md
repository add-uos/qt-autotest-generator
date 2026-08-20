# 示例项目

本目录包含一个示例 Qt 项目，展示 qt-autotest-generator 的典型产物。

## 目录结构

```
sample-qt-project/
├── src/
│   ├── CMakeLists.txt
│   └── calculator.h          # 示例 Qt 类（仅头文件，内联实现）
├── CMakeLists.txt             # 项目根 CMake（含 BUILD_TESTS 开关）
├── autotests/                 # 技能生成的测试框架
│   ├── 3rdparty/stub/         # stub-ext（从 templates/stub-ext/ 复制）
│   ├── cmake/UnitTestUtils.cmake
│   ├── run-ut.sh
│   ├── CMakeLists.txt          # 测试根 CMake
│   ├── .ut-inventory.json       # inventory 分级表示例
│   ├── .results/              # gtest XML 输出
│   └── core/
│       ├── CMakeLists.txt     # 模块 CMake
│       └── test_calculator.cpp # 生成的测试文件
└── build-autotests/           # Mode 5 导出产物示例（非构建产物）
    ├── defects.json           # 机读缺陷清单
    └── defects-summary.md     # 人读标红清单（md 内链接跳转源码行）
```

## Mode 5 导出产物示例

`build-autotests/` 目录存放 Mode 5（源码缺陷导出）的示例产物，**不是**构建中间产物：

- `defects.json`：机读缺陷清单（`scripts/export-defects.py export` 产出）
- `defects-summary.md`：人读标红清单，Markdown 内链接可跳转源码行

实际使用时，Mode 5 缺陷实时落盘到 `{test_dir}/.ut-defects.json`（本地，不入 git），导出时生成到构建目录下。

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
