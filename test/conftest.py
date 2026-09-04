"""共享 fixtures：把 scripts/ 下带连字符的 .py 加载为模块供测试 import。

脚本文件名含连字符（如 fetch-mcp-data.py），无法用普通 import，故用
importlib.util 按文件路径加载。所有脚本入口都有 ``if __name__ == "__main__"``
保护，import 不会触发 main()。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "qt-autotest-generator" / "scripts"
# 外部工具（../assets/ut-inventory-editor）也写 .ut-inventory.json，纳入契约测试。
# ut-inventory-editor 是仓库级独立人工工具，不在 qt-autotest-generator skill 目录内。
ASSETS_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "ut-inventory-editor" / "scripts"

# 模块名 → 文件名映射（模块名用下划线，便于测试 import）
# 合并后：旧 fixture 名指向新合并文件，测试无需改名
_SCRIPT_FILES = {
    # mcp-scan.py ← scan-inventory.py + fetch-mcp-data.py
    "scan_inventory": "mcp-scan.py",
    "fetch_mcp_data": "mcp-scan.py",
    # coverage-report.py ← collect-coverage-report.py + coverage-by-level.py
    "collect_coverage_report": "coverage-report.py",
    "coverage_by_level": "coverage-report.py",
    # mode2-ops.py ← plan-test-classes.py + update-usecase-count.py + compose-commit.py
    "plan_test_classes": "mode2-ops.py",
    "update_usecase_count": "mode2-ops.py",
    "compose_commit": "mode2-ops.py",
    # 独立脚本（未合并）
    "export_defects": "export-defects.py",
    "mutation_score": "mutation-score.py",
    "stale_test_cleanup": "stale-test-cleanup.py",
    "verify_build": "verify-build.py",
    "self_check_structural": "self-check-structural.py",
    "utq": "utq.py",
    "test_review": "test-review.py",
}


def _load_module(mod_name: str, file_name: str):
    """按文件路径加载脚本为模块，注册到 sys.modules 避免重复加载。

    合并后多个旧 fixture 名可能指向同一文件（如 scan_inventory 和
    fetch_mcp_data 都加载 mcp-scan.py）。用 mod_name 做 sys.modules 缓存键，
    确保同一文件只加载一次，多个 fixture 返回同一模块对象。
    """
    cache_key = file_name  # 按文件名缓存，同一文件只加载一次
    if cache_key in sys.modules:
        return sys.modules[cache_key]
    path = SCRIPTS_DIR / file_name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = mod  # 按文件名缓存
    sys.modules[mod_name] = mod   # 按模块名注册（兼容 import 语法）
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def scripts_dir():
    """scripts/ 目录绝对路径。"""
    return SCRIPTS_DIR


# ── 每个脚本一个 session 级 fixture ──
# 合并后多个 fixture 可能加载同一文件（_load_module 按文件名缓存，返回同一模块对象）
@pytest.fixture(scope="session")
def scan_inventory():
    return _load_module("scan_inventory", "mcp-scan.py")


@pytest.fixture(scope="session")
def fetch_mcp_data():
    return _load_module("fetch_mcp_data", "mcp-scan.py")


@pytest.fixture(scope="session")
def collect_coverage_report():
    return _load_module("collect_coverage_report", "coverage-report.py")


@pytest.fixture(scope="session")
def coverage_by_level():
    return _load_module("coverage_by_level", "coverage-report.py")


@pytest.fixture(scope="session")
def plan_test_classes():
    return _load_module("plan_test_classes", "mode2-ops.py")


@pytest.fixture(scope="session")
def update_usecase_count():
    return _load_module("update_usecase_count", "mode2-ops.py")


@pytest.fixture(scope="session")
def compose_commit():
    return _load_module("compose_commit", "mode2-ops.py")


@pytest.fixture(scope="session")
def export_defects():
    return _load_module("export_defects", "export-defects.py")


@pytest.fixture(scope="session")
def mutation_score():
    return _load_module("mutation_score", "mutation-score.py")


@pytest.fixture(scope="session")
def stale_test_cleanup():
    return _load_module("stale_test_cleanup", "stale-test-cleanup.py")


@pytest.fixture(scope="session")
def verify_build():
    return _load_module("verify_build", "verify-build.py")


@pytest.fixture(scope="session")
def self_check_structural():
    return _load_module("self_check_structural", "self-check-structural.py")


@pytest.fixture(scope="session")
def utq():
    return _load_module("utq", "utq.py")


@pytest.fixture(scope="session")
def test_review():
    return _load_module("test_review", "test-review.py")


@pytest.fixture(scope="session")
def fetch_test_mapping():
    """assets/ 编辑器侧 vendored 副本（回写 test_* 字段）。

    主流程已并入 mcp-scan.py 的 update_inventory_test_mapping（Mode 1 fetch 天然
    采集）；此 fixture 加载编辑器副本用于契约对比测试，两者行为应一致。
    仅标准库依赖（urllib），import 安全；主入口有 __main__ 保护。
    """
    path = ASSETS_SCRIPTS_DIR / "fetch-test-mapping.py"
    mod_name = "fetch_test_mapping"
    cache_key = str(path)
    if cache_key in sys.modules:
        return sys.modules[cache_key]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = mod
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod
