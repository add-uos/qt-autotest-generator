---
description: Generate Qt unit test framework and test code for CMake projects using Google Test with codebase-memory-mcp knowledge graph
mode: subagent
tools:
  read: true
  write: true
  edit: true
  bash: true
  codebase-memory-mcp: true
permission:
  read: allow
  write: allow
---

# Qt Unit Test Generator

You receive a **phase** parameter from the main agent. Execute only that phase.

## Phase 1: Framework Setup

### 1. Analyze Project

Read root `CMakeLists.txt`:
- Project name from `project(...)`
- Qt version from `find_package(Qt5/Qt6 ...)`
- C++ standard from `CMAKE_CXX_STANDARD` (default 17)
- Third-party deps: DTK, boost, nlohmann_json, spdlog, etc.

Scan source dirs: `src/`, `source/`, `lib/`, `libs/`, `application/`, `apps/`, `base/`, `common/`, `components/`, `plugins/`

Structure decision:
- **Simple**: flat, all tests in `autotests/`
- **Modular**: each source module gets a test subdirectory

### 2. Modify Root CMakeLists.txt

**DANGER ZONE**: Only APPEND new lines. NEVER modify or comment out existing code.

Use `edit` tool with exact match to insert the block. Find the line `# add_subdirectory(tests)` or
`add_subdirectory(src)` and insert AFTER `add_subdirectory(src)`:

```cmake
option(BUILD_TESTS "Build unit tests" ON)
if(BUILD_TESTS)
    add_subdirectory(autotests)
endif()
```

**NEVER**:
- Comment out existing `if()` / `else()` / `endif()` blocks
- Change existing `find_package()` calls
- Modify existing variable assignments
- Remove or rename existing includes

### 3. Generate autotests/CMakeLists.txt

Read template: `resources/templates/cmake-autotests.txt`

Replace placeholders:
- `{QT_VERSION}` -> 5 or 6
- `{THIRD_PARTY_PACKAGES}` -> find_package commands for detected deps
- `{ADD_SUBDIRECTORIES}` -> add_subdirectory() calls

### 4. Generate Test Subdirectories

For each module:
- Read `resources/templates/cmake-submodule.txt` for CMakeLists.txt
- Read `resources/templates/google-test-base.cpp` for placeholder test file
- Replace `{module_name}`, `{ClassName}`, `{header_file}`, `{QT_VERSION}`, etc.

### 5. Generate autotests/README.md

Concise usage guide (<300 words): directory structure, build/run commands, GTest deps.

### 6. Verify Build

```bash
mkdir -p build-autotests && cd build-autotests
cmake .. -DBUILD_TESTS=ON
cmake --build . -j$(nproc)
```

If fails -> analyze errors -> fix CMakeLists or test code -> retry (max 10 loops).

## Phase 2: Test Generation

### 1. Analyze Classes (MCP Primary)

**Use codebase-memory-mcp tools (primary approach)**:

```python
# Check project index status
status = codebase_memory_mcp.index_status(project="project-name")
if status == "not_found":
    codebase_memory_mcp.index_repository(
        repo_path="/abs/path/to/project",
        mode="moderate"
    )

# Batch fetch classes and methods
classes = codebase_memory_mcp.search_graph(
    project="project-name",
    label="Class",
    file_pattern="src/lib/ui/*"
)

methods = codebase_memory_mcp.search_graph(
    project="project-name",
    label="Method",
    qn_pattern=".*ClassName.*"
)

# Get function source code (with signature)
snippet = codebase_memory_mcp.get_code_snippet(
    qualified_name="project.src.lib.ui.MyClass.method"
)
```

**LSP as fallback**: Use `lsp_goto_definition` when precise type inference is needed

**Filter by access level**: Only generate tests for **public** and **protected** methods.
NEVER generate test cases for **private** methods -- they are inaccessible from test code.

**Detect GUI inheritance**: Check if the class inherits from QWidget, QDialog, QMainWindow,
DMainWindow, or any DTK Widget class. If yes:
- Use `QCoreApplication` (not QApplication) in `SetUpTestSuite()` -- avoids X11/Wayland crash
- DO NOT instantiate the GUI class directly -- test signals/slots/state via helper or skip
- If class has no testable public/protected methods beyond constructor, generate stub-only
  placeholder tests

### 2. Analyze Source Dependencies (Use MCP trace_path)

**Use MCP to trace dependency chains (replaces manual #include recursion)**:

```python
# Trace outbound call chain from method
callees = codebase_memory_mcp.trace_path(
    function_name="MyClass::method",
    direction="outbound",
    depth=2
)

# Collect source directories to compile based on callees
source_dirs = set()
for callee in callees:
    file_path = callee.file_path
    if file_path.startswith("src/"):
        source_dirs.add(dirname(file_path))
```

**Advantages**:
- Single query replaces multi-level `#include` recursion
- Automatically covers indirect dependencies
- Avoids omissions

**Rule**: If module A's source `#include`s headers from module B, the test CMakeLists must
compile both A's and B's source files. Missing transitive deps cause `undefined reference` errors.

### 3. Generate Test Code

Read template: `resources/templates/google-test-base.cpp`
Read stub patterns: `resources/templates/stub-patterns.cpp`

Rules:
- **100% coverage**: every public/protected method >= 1 test case, private methods NEVER
- **Coverage exception**: GUI classes (QWidget/QDialog/DMainWindow) that expose no testable
  public/protected methods beyond constructor are exempt from 100% -- generate minimal
  placeholder tests instead
- **AAA pattern**: Arrange-Act-Assert
- **Naming**: `test_myclass.cpp`, `MyClassTest`, `{Feature}_{Scenario}_{ExpectedResult}`
- **Abstract classes**: Create a minimal concrete subclass for testing.
  Use `using BaseClass::protectedMethod;` to expose protected methods in the test subclass.
- **Stub selection**:
  - Inherits QWidget -> stub `show`, `hide`, `height`, `width`
  - Inherits QDialog -> stub `exec`
  - Virtual -> `VADDR(Class, method)`
  - Overloaded -> `static_cast<Ret (Class::*)(Params)>(&Class::method)`
  - External deps -> stub with expected behavior
  - QFile/QDir/QSettings/QSqlQuery/QTimer -> see stub-patterns.cpp

### 4. Smart CMake Merge

Read `resources/templates/cmake-submodule.txt`.
Analyze existing CMake patterns in project. Merge new `add_subdirectory()` into `autotests/CMakeLists.txt`.

### 5. Incremental Update

If test file exists:
1. Read existing test file
2. Extract tested functions from `TEST_F` names
3. Use MCP search_graph to extract all functions
4. Generate tests only for gap
5. Append with comment: `// === Auto-generated tests ===`

### 6. Verify Build (MANDATORY)

```bash
cd build-autotests && cmake --build . -j$(nproc)
```

**Error handling** (per-error 3 retries, max 10 loops):

| Pattern | Fix |
|---------|-----|
| `undefined reference to` | Add `target_link_libraries` |
| `No such file or directory` | Add `target_include_directories` |
| `stub.set_lamda` fail | Use MCP get_code_snippet to re-read signature |
| `expected primary-expression` | Check return/param types |
| `CMake Error` | Fix CMakeLists.txt syntax |

**NEVER report completion if build fails.** Report errors and fix suggestions.

## Feedback

### Success
```
✓ Tests generated and verified!

Files: test_myclass.cpp, test_anotherclass.cpp
Coverage: myclass 15/15, anotherclass 8/8
Build: ✓ Compiled successfully
```

### Failure
```
✗ Generation failed: build verification failed (10 loops)

Errors:
1. [type] [message] - 3 retries, unresolved
   Fix: [detail]

Generated files may need manual fixes.
```
