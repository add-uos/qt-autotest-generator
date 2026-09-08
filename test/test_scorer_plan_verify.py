# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""R4：ut-plan report 聚合 + scorer 消费 .ut-plan.json last_verify 证据。

覆盖：
- ut-plan.build_report / cmd_report（模块聚合、状态计数、test_passed 汇总）
- score.load_plan_verifications（套名索引、ts 去重、缺字段容忍）
- score.score_file 的 plan_verify 注入（不进权重、失败标注）
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_UP = _ROOT / "qt-autotest-generator" / "scripts" / "ut-plan.py"
_SCORE = _ROOT / ".pi" / "skills" / "qt-autotest-scorer" / "scripts" / "score.py"

# ut_plan 已由 test_ut_plan.py 注册；独立跑本文件时兜底加载
if "ut_plan" in sys.modules:
    up = sys.modules["ut_plan"]
else:
    _ga = _ROOT / "qt-autotest-generator" / "scripts" / "graph-access.py"
    if "graph_access" not in sys.modules:
        _spec = importlib.util.spec_from_file_location("graph_access", _ga)
        _m = importlib.util.module_from_spec(_spec)
        sys.modules["graph_access"] = _m
        _spec.loader.exec_module(_m)
    _spec = importlib.util.spec_from_file_location("ut_plan", _UP)
    up = importlib.util.module_from_spec(_spec)
    sys.modules["ut_plan"] = up
    _spec.loader.exec_module(up)

_spec = importlib.util.spec_from_file_location("score", _SCORE)
score = importlib.util.module_from_spec(_spec)
sys.modules["score"] = score
_spec.loader.exec_module(score)


def _plan(tmp_path, blocks):
    plan = {
        "version": up.PLAN_VERSION, "repo": "demo",
        "survey": {}, "quantile": {},
        "blocks": blocks,
        "stats": {"blocks": len(blocks), "methods": 0, "high": 0, "mid": 0, "low": 0},
    }
    path = tmp_path / "plan.json"
    up.save_plan(plan, path)
    return path


def _block(bid, cluster, status="pending", high=1, mid=0, methods=None, lv=None):
    return {"block_id": bid, "kind": "class", "node_id": None, "name": bid,
            "file_path": f"{cluster}/x.h", "cluster": cluster,
            "methods": methods or [{"id": f"M{bid}", "file_path": f"{cluster}/x.h"}],
            "level_summary": {"high": high, "mid": mid},
            "priority": 1.0, "context_bytes": 0, "status": status,
            **({"last_verify": lv} if lv else {})}


# ── ut-plan report ──────────────────────────────────────────────────

class TestBuildReport:
    def test_module_aggregation(self, tmp_path):
        plan = _plan(tmp_path, [
            _block("B1", "src/dfm-base", status="done", high=2, mid=1,
                   lv={"run": {"passed": 10, "failed": 0}}),
            _block("B2", "src/dfm-base", status="failed", high=1,
                   lv={"run": {"passed": 3, "failed": 1}}),
            _block("B3", "src/plugins", status="selected", high=1, mid=2),
        ])
        rep = up.build_report(up.load_plan(plan))
        by_mod = {m["module"]: m for m in rep["modules"]}
        assert set(by_mod) == {"src/dfm-base", "src/plugins"}
        dfb = by_mod["src/dfm-base"]
        assert dfb["blocks"] == 2 and dfb["methods"] == 2
        assert dfb["high"] == 3 and dfb["mid"] == 1
        assert dfb["done"] == 1 and dfb["failed"] == 1
        assert dfb["test_passed"] == 13 and dfb["test_failed"] == 1
        plugins = by_mod["src/plugins"]
        assert plugins["selected"] == 1 and plugins["test_passed"] == 0
        # 排序：按方法数降序；total 汇总
        assert rep["modules"][0]["module"] == "src/dfm-base"
        assert rep["total"]["blocks"] == 3 and rep["total"]["test_passed"] == 13
        assert rep["total"]["failed"] == 1

    def test_empty_plan(self, tmp_path):
        rep = up.build_report(up.load_plan(_plan(tmp_path, [])))
        assert rep["modules"] == [] and rep["total"]["blocks"] == 0

    def test_cli_json_and_table(self, tmp_path, capsys):
        plan = _plan(tmp_path, [_block("B1", "src/dfm-base", status="done",
                                       lv={"run": {"passed": 10, "failed": 0}})])
        rep = up.cmd_report(str(plan), as_json=True)
        out = capsys.readouterr().out
        assert json.loads(out)["total"]["done"] == 1
        rep2 = up.cmd_report(str(plan), top=1)
        out2 = capsys.readouterr().out
        assert "src/dfm-base" in out2 and "repo=demo" in out2

    def test_cli_route(self, tmp_path, capsys):
        plan = _plan(tmp_path, [])
        assert up.main(["report", str(plan), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["repo"] == "demo"


# ── scorer plan_verify ──────────────────────────────────────────────

class TestLoadPlanVerifications:
    def test_index_by_suite_and_dedupe(self, tmp_path):
        plan = _plan(tmp_path, [
            _block("B1", "src/a", status="done",
                   lv={"suite": "FooTest", "ts": "2026-01-01 10:00:00",
                       "run": {"passed": 5, "failed": 0}}),
            _block("B2", "src/a", status="done",
                   lv={"suite": "FooTest", "ts": "2026-01-02 09:00:00",
                       "run": {"passed": 7, "failed": 0}}),
            _block("B3", "src/b", status="failed",
                   lv={"suite": "BarTest", "ts": "2026-01-03 09:00:00",
                       "run": {"passed": 1, "failed": 2}}),
        ])
        ev = score.load_plan_verifications(str(plan))
        # 键 = 套名去 Test 后缀的小写 token，与 class_token_from_file 对齐
        assert ev["foo"]["passed"] == 7              # 保留 ts 更新的一条
        assert ev["foo"]["block_id"] == "B2"
        assert ev["bar"]["status"] == "failed"
        assert ev["bar"]["suite"] == "BarTest"

    def test_missing_or_empty(self, tmp_path):
        assert score.load_plan_verifications(None) == {}
        assert score.load_plan_verifications(str(tmp_path / "no.json")) == {}
        plan = _plan(tmp_path, [_block("B1", "src/a")])  # 无 last_verify
        assert score.load_plan_verifications(str(plan)) == {}

    def test_skips_incomplete_records(self, tmp_path):
        plan = _plan(tmp_path, [
            _block("B1", "src/a", lv={"suite": "NoRunTest", "run": None}),
            _block("B2", "src/a", lv={"run": {"passed": 1}}),  # 无 suite
        ])
        assert score.load_plan_verifications(str(plan)) == {}


class TestScoreFilePlanVerify:
    def _write_test(self, tmp_path, suite="SqliteHelperTest"):
        f = tmp_path / "test_sqlitehelper.cpp"
        f.write_text(
            "// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.\n"
            "// SPDX-License-Identifier: GPL-3.0-or-later\n"
            "#include <gtest/gtest.h>\n"
            f"TEST({suite}, A) {{ EXPECT_EQ(1, 1); }}\n"
            f"TEST({suite}, B) {{ ASSERT_TRUE(true); }}\n")
        return str(f)

    def test_verify_evidence_attached(self, tmp_path):
        ev = {"block_id": "B1010", "ts": "2026-09-08 19:29:25",
              "passed": 10, "failed": 0, "status": "done"}
        s = score.score_file(self._write_test(tmp_path), None, None, None, None,
                             None, dict(score.DEFAULT_WEIGHTS), True, 70,
                             plan_verify={"sqlitehelper": ev})  # token 键
        assert s["plan_verify"]["passed"] == 10
        assert "实测通过" in s["plan_verify"]["note"]
        assert s["inputs_used"]["plan_verify"] is True
        # 证据不进权重：与无证据时同分
        s2 = score.score_file(self._write_test(tmp_path), None, None, None, None,
                              None, dict(score.DEFAULT_WEIGHTS), True, 70)
        assert s2["score"] == s["score"] and s2["plan_verify"] is None

    def test_failed_run_flagged(self, tmp_path):
        ev = {"block_id": "B1", "ts": "2026-09-08 19:29:25",
              "passed": 3, "failed": 1, "status": "failed"}
        s = score.score_file(self._write_test(tmp_path), None, None, None, None,
                             None, dict(score.DEFAULT_WEIGHTS), True, 70,
                             plan_verify={"sqlitehelper": ev})
        assert "修复" in s["plan_verify"]["note"]

    def test_no_match_suite_leaves_none(self, tmp_path):
        s = score.score_file(self._write_test(tmp_path), None, None, None, None,
                             None, dict(score.DEFAULT_WEIGHTS), True, 70,
                             plan_verify={"othertoken": {"passed": 1}})
        assert s["plan_verify"] is None

    def test_cli_end_to_end(self, tmp_path, capsys, monkeypatch):
        """CLI：--plan 传入后评分卡含证据且 summary 表格带 verify passed 列。"""
        f = tmp_path / "test_sqlitehelper.cpp"
        f.write_text(self._write_test(tmp_path))
        plan = _plan(tmp_path, [_block("B1", "src/dfm-base", status="done",
                                       lv={"suite": "SqliteHelperTest",
                                           "ts": "2026-09-08 19:29:25",
                                           "run": {"passed": 10, "failed": 0}})])
        monkeypatch.chdir(tmp_path)
        # 裸文件（无 inventory/coverage）评分 <70 → rc=1，属正常业务语义
        rc = _run_cli_main(["-f", str(f), "--plan", str(plan),
                            "-o", str(tmp_path / "rep")])
        assert rc == 1
        out = capsys.readouterr().out
        assert "plan verify 10 passed" in out
        assert "合格 否" in out
        card = json.load(open(tmp_path / "rep" / "scorecard-Sqlitehelper.json"))
        assert card["plan_verify"]["passed"] == 10


def _run_cli_main(argv):
    """score.main 用 argparse 默认 sys.argv；临时替换后调用。"""
    old = sys.argv
    sys.argv = ["score.py"] + argv
    try:
        return score.main()
    finally:
        sys.argv = old
