# Autotests

由 qt-autotest-generator 技能自动生成的单元测试框架。

## 目录结构

```
autotests/
├── 3rdparty/stub/       # stub-ext（从 resources/stub/ 复制）
├── cmake/               # CMake 工具脚本
├── core/                # 测试模块（按源码模块分组）
│   ├── CMakeLists.txt
│   └── test_calculator.cpp
├── report_generator/    # 报告生成器
├── run-ut.sh             # 测试运行脚本
└── CMakeLists.txt        # 测试根 CMake
```

## 构建与运行

```bash
mkdir build && cd build
cmake .. -DBUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build . -j$(nproc)
ctest --output-on-failure
```

或直接用运行脚本：

```bash
cd autotests && ./run-ut.sh
```
