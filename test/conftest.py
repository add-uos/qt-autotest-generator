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

# 模块名 → 文件名映射（模块名用下划线，便于测试 import）
_SCRIPT_FILES = {
    "fetch_mcp_data": "fetch-mcp-data.py",
    "scan_inventory": "scan-inventory.py",
    "export_defects": "export-defects.py",
    "mutation_score": "mutation-score.py",
    "stale_test_cleanup": "stale-test-cleanup.py",
    "collect_coverage_report": "collect-coverage-report.py",
    "coverage_by_level": "coverage-by-level.py",
}


def _load_module(mod_name: str, file_name: str):
    """按文件路径加载脚本为模块，注册到 sys.modules 避免重复加载。"""
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = SCRIPTS_DIR / file_name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def scripts_dir():
    """scripts/ 目录绝对路径。"""
    return SCRIPTS_DIR


# ── 每个脚本一个 session 级 fixture ──
@pytest.fixture(scope="session")
def fetch_mcp_data():
    return _load_module("fetch_mcp_data", "fetch-mcp-data.py")


@pytest.fixture(scope="session")
def scan_inventory():
    return _load_module("scan_inventory", "scan-inventory.py")


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
def collect_coverage_report():
    return _load_module("collect_coverage_report", "collect-coverage-report.py")


@pytest.fixture(scope="session")
def coverage_by_level():
    return _load_module("coverage_by_level", "coverage-by-level.py")
