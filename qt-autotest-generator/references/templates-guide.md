# 模板文件指南（`templates/`）

技能内置的两类资产合并存放于根级 `templates/` 目录：**代码生成模板**（带占位符，读取后替换）和 **stub-ext 库**（vendored 第三方库，整目录原样复制）。

## 代码生成模板（平铺于 `templates/`）

| 文件 | 用途 | 使用阶段 | 占位符 |
|------|------|---------|--------|
| `google-test-base.cpp` | GTest 测试夹具基类骨架：TEST_F 类结构、SetUp/TearDown、stub 声明、SPDX 头 | 测试代码生成（每类） | `{ClassName}` `{header_file}` `{SPDX_YEAR}` `{SetUpTestSuite}` `{TestCases}` 等 |
| `stub-patterns.cpp` | 常用 stub 模式速查：UI 显示/尺寸、信号监听、虚函数、文件 IO、网络、定时器等 19 节模式 | 依赖追踪 + 测试代码生成（参考用，不直接复制） | `{ClassName}` `{MethodName}` `{SignalName}`（示例占位符，参考用） |
| `cmake-autotests.txt` | 测试根 `CMakeLists.txt` 模板：GTest 依赖、覆盖率标志、子目录挂载 | 框架搭建 | `{THIRD_PARTY_PACKAGES}` `{ADD_SUBDIRECTORIES}` |
| `cmake-submodule.txt` | 测试子模块 `CMakeLists.txt` 模板：可执行目标、stub-shadow 链接、Qt 版本、include 路径 | 测试代码生成（每模块） | `{QT_VERSION}` `{PROJECT_LIBRARIES}` `{QT_EXTRA_LIBS}` `{module_name}` `{test_dir}` `{source_module_path}` |

## stub-ext 库（`templates/stub-ext/`）

vendored [stub-ext](https://github.com/guyongling/stub-ext) 库源码，用于运行时函数 stub（替换虚函数/私有方法/系统调用）。框架搭建时**整目录原样复制**到项目 `{test_dir}/3rdparty/stub/`，不从网络下载。

| 文件 | 说明 |
|------|------|
| `stubext.h` | 库主头文件，测试代码 `#include "stubext.h"` 入口 |
| `stub.h` | Stub 核心实现（函数地址替换） |
| `stub-shadow.h` / `stub-shadow.cpp` | Shadow 机制（堆栈上对象 stub），必须编入 test target |
| `addr_any.h` / `addr_pri.h` | 内存地址工具（私有成员访问） |
| `elfio.hpp` | ELF 解析（内联第三方头，用于符号定位） |
