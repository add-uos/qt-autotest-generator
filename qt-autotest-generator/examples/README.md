# 示例项目

> **定位**：本目录是面向人类的可运行演示，展示 qt-autotest-generator 的典型产物与目录布局。Agent 运行时**不读取**本目录——技能自带资源在 `templates/` / `references/` / `scripts/`，本目录仅作参考。

## 目录结构

```
sample-qt-project/
├── src/
│   ├── calculator.h          # 示例 Qt 类声明
│   └── calculator.cpp        # 示例 Qt 类实现
├── CMakeLists.txt             # 项目根 CMake（含 BUILD_TESTS 开关）
├── autotests/                 # 技能生成的测试框架
│   ├── 3rdparty/              # 构建时由 run-ut.sh 从 templates/stub-ext/ 同步（不随附）
│   ├── cmake/UnitTestUtils.cmake
│   ├── run-ut.sh              # 构建+运行+覆盖率一键脚本
│   ├── CMakeLists.txt          # 测试根 CMake
│   ├── .ut-inventory.json       # inventory 分级表示例（Mode 1 产物）
│   └── core/
│       ├── CMakeLists.txt     # 模块 CMake
│       └── test_calculator.cpp # 生成的测试文件
└── mode5-export-example/      # Mode 5 导出产物示例（人工查阅）
    ├── defects.json           # 机读缺陷清单
    └── defects-summary.md     # 人读标红清单（md 内链接跳转源码行）
```

> 构建产物（`build-autotests/`）、gtest 运行输出（`.results/`）、运行态缺陷记录（`.ut-defects.json`）均为本地生成、**不入 git**，见 `sample-qt-project/.gitignore`。本仓库只随附可读的 curated 示例产物（`.ut-inventory.json` 与 `mode5-export-example/`）。

## stub-ext 说明

`autotests/3rdparty/stub/` **不随附**——为避免与 `templates/stub-ext/` 重复导致漂移，示例构建时自动同步：

- `run-ut.sh` 步骤 1 自动从 `templates/stub-ext/` 复制到 `autotests/3rdparty/stub/`
- 手动构建前执行：`cp -r <skill>/templates/stub-ext/ autotests/3rdparty/stub/`

`3rdparty/stub/` 为构建产物，已 gitignore。

## Mode 5 导出产物示例

`mode5-export-example/` 存放 Mode 5（源码缺陷导出）的示例产物：

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
