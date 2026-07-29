# 环境搭建指南

## 1. 系统要求

- Linux（推荐 Ubuntu 22.04+ / Debian 12+ / deepin V20+）
- CMake >= 3.16
- gcc/g++ 支持 C++17
- Qt 5 或 Qt 6
- Google Test
- Python 3.8+
- codebase-memory-mcp >= 0.8.0

## 2. 安装步骤

### 2.1 基础工具链

```bash
sudo apt update
sudo apt install cmake build-essential git python3
```

### 2.2 Qt

```bash
# Qt5
sudo apt install qtbase5-dev

# 或 Qt6
sudo apt install qt6-base-dev
```

### 2.3 Google Test

```bash
sudo apt install libgtest-dev
cd /usr/src/gtest
sudo cmake CMakeLists.txt
sudo make
sudo cp lib/*.a /usr/lib/
sudo mkdir -p /usr/local/lib/cmake/GTest
sudo cp CMakeLists.txt /usr/local/lib/cmake/GTest/
```

或从源码编译：

```bash
git clone https://github.com/google/googletest.git
cd googletest
mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=/usr/local
make -j$(nproc)
sudo make install
```

### 2.4 codebase-memory-mcp

技能首次运行时自动安装。也可手动：

```bash
bash <技能目录>/resources/scripts/setup-codebase-memory.sh
```

验证：

```bash
codebase-memory-mcp --version
codebase-memory-mcp cli list_projects
```

### 2.5 可选：覆盖率工具

```bash
sudo apt install lcov
```

## 3. 验证环境

```bash
# 一键检查
cmake --version && \
g++ --version | grep -o 'C++.*' && \
python3 --version && \
pkg-config --modversion gtest && \
codebase-memory-mcp --version
```

## 4. 常见问题

### 4.1 GTest 找不到

```
CMake Error: Could not find GTest
```

解决：确认 `libgtest-dev` 已安装且编译了库文件（见 2.3）。

### 4.2 codebase-memory-mcp 索引不 ready

```
index_status 返回 "indexing" 超过 60 秒
```

解决：手动推一下 `codebase-memory-mcp index_repository --repo-path <path> --mode fast`，等待 ready。

### 4.3 Qt 模块缺失

```
Could not find Qt6::Widgets
```

解决：`sudo apt install qt6-base-dev`（Qt5 用 `qtbase5-dev`）。

### 4.4 stub-shadow.cpp 链接错误

```
undefined reference to stub_ext::freeWrapper
```

解决：确认 `autotests/3rdparty/stub/stub-shadow.cpp` 已编入 test target（CMakeLists 检查）。
