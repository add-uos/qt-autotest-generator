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
│   ├── 3rdparty/stub/         # stub-ext（templates/stub-ext/ 的冻结副本，见下文）
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

## stub-ext 副本说明

`autotests/3rdparty/stub/` 是 `templates/stub-ext/` 的**字节级冻结副本**。保留副本（而非运行时从 templates 复制）是为了让 `sample-qt-project/` 可被独立复制后直接构建运行。更新 stub-ext 时两处必须同步，发布前运行校验：

```bash
bash scripts/check-stub-sync.sh
```

退出码 `0` 表示一致；`1` 表示存在漂移（脚本会打印差异与修复命令）。

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
