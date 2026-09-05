"""test-review.py（Mode 6）裁决/建议/编排/报告渲染单元测试 + CLI 端到端。

裁决与渲染用合成数据（structural/branch/score JSON）；编排层 review_targets
通过 monkeypatch run_* 边界函数隔离子进程；CLI 端到端跑真实
self-check-structural 子进程 + --no-branch --no-scorer 降级路径。
"""
import argparse
import json
import subprocess
import sys

import pytest

from conftest import SCRIPTS_DIR

TEST_REVIEW_SCRIPT = str(SCRIPTS_DIR / "test-review.py")

# ── 合成数据 ─────────────────────────────────────────────────────────

GOOD_HEADER = (
    "// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.\n"
    "// SPDX-License-Identifier: GPL-3.0-or-later\n"
)


def _structural(violations, n_cases=2):
    return {"file": "test_x.cpp", "test_case_count": n_cases,
            "tested_names": ["Foo"], "summary": {},
            "violations": violations}


V_NAMING = {"check": "naming", "severity": "error", "rule": "TOO_FEW_SEGMENTS",
            "message": "用例名分段不足", "line": 10, "case": "Bad"}
V_CRITICAL = {"check": "assertion", "severity": "error", "rule": "SOLE_NO_FATAL",
              "message": "唯一断言为 EXPECT_NO_FATAL_FAILURE", "line": 16, "case": "Bad"}
V_TRIVIAL = {"check": "assertion", "severity": "error", "rule": "TRIVIAL_ASSERT",
             "message": "唯一有效断言为字面量布尔", "line": 18, "case": "Bad"}
V_WARN = {"check": "aaa", "severity": "warning", "rule": "EMPTY_AAA",
          "message": "AAA 段为空", "line": 10, "case": "Foo_Bar_Baz"}
V_BRANCH = {"check": "branch", "severity": "error", "rule": "BRANCH_NOT_MAPPED",
            "message": "declared=1 actual=3", "line": 0, "method": "compute"}


def _targets_doc(tmp_path, files=("autotests/test_x.cpp",)):
    targets = [{"source_path": f, "review_path": str(tmp_path / f),
                "git_status": "M", "managed": False, "class_hint": "x"}
               for f in files]
    return {"scenario": "commit", "generated_at": "T", "repo": str(tmp_path),
            "commit_spec": "abc1234", "commit": {"sha": "abc1234", "short": "abc1234",
                                                 "author": "t", "date": "D", "subject": "s"},
            "range": None, "test_dir": None, "inventory": None,
            "workspace": None, "targets": targets, "skipped": [],
            "non_test_changes": 1}


def _args(tmp_path, **kw):
    # no_scorer 默认 False（测评分支由 fake run_score 隔离）；降级场景显式传 True
    defaults = dict(outdir=str(tmp_path / ".reports"), project=None, mcp_url=None,
                    no_branch=False, no_scorer=False, scorer_path=None,
                    coverage=None, mutation=None, inventory=None)
    defaults.update(kw)
    return argparse.Namespace(**defaults)


# ── 裁决模型 ─────────────────────────────────────────────────────────

class TestDeriveVerdict:
    def test_fail_on_critical(self, test_review):
        v, crit = test_review.derive_verdict(_structural([V_CRITICAL]), None)
        assert v == "FAIL" and crit == ["SOLE_NO_FATAL"]

    def test_fail_on_branch_critical(self, test_review):
        branch = {"violations": [V_BRANCH]}
        v, crit = test_review.derive_verdict(_structural([]), branch)
        assert v == "FAIL" and crit == ["BRANCH_NOT_MAPPED"]

    def test_fail_on_trivial_assert(self, test_review):
        # 字面量布尔占位断言入 critical：触发 FAIL 且进 P0 路由
        v, crit = test_review.derive_verdict(_structural([V_TRIVIAL]), None)
        assert v == "FAIL" and crit == ["TRIVIAL_ASSERT"]
        recs = test_review.merge_recommendations(_structural([V_TRIVIAL]), None)
        assert recs[0]["rule"] == "TRIVIAL_ASSERT" and recs[0]["priority"] == "P0"
        assert recs[0]["reference"] == "self-checker.md §2b"

    def test_warn_on_error_only(self, test_review):
        v, crit = test_review.derive_verdict(_structural([V_NAMING]), None)
        assert v == "WARN" and crit == []

    def test_pass_on_warning_only(self, test_review):
        v, _ = test_review.derive_verdict(_structural([V_WARN]), None)
        assert v == "PASS"

    def test_pass_clean(self, test_review):
        v, _ = test_review.derive_verdict(_structural([]), None)
        assert v == "PASS"

    def test_error_on_missing_structural(self, test_review):
        v, crit = test_review.derive_verdict(None, None)
        assert v == "ERROR" and crit == []

    def test_critical_dedup(self, test_review):
        v, crit = test_review.derive_verdict(
            _structural([V_CRITICAL, dict(V_CRITICAL)]), None)
        assert v == "FAIL" and crit == ["SOLE_NO_FATAL"]


# ── 建议路由 ─────────────────────────────────────────────────────────

class TestMergeRecommendations:
    def test_priority_mapping(self, test_review):
        recs = test_review.merge_recommendations(
            _structural([V_CRITICAL, V_NAMING, V_WARN]), None)
        by_rule = {r["rule"]: r for r in recs}
        assert by_rule["SOLE_NO_FATAL"]["priority"] == "P0"
        assert by_rule["TOO_FEW_SEGMENTS"]["priority"] == "P1"
        assert by_rule["EMPTY_AAA"]["priority"] == "P2"
        # 排序：P0 在最前
        assert recs[0]["rule"] == "SOLE_NO_FATAL"

    def test_route_known(self, test_review):
        recs = test_review.merge_recommendations(_structural([V_NAMING]), None)
        assert recs[0]["reference"] == "test-code-gen.md §用例命名"
        recs = test_review.merge_recommendations(_structural([V_WARN]), None)
        assert recs[0]["reference"].startswith("self-checker.md")

    def test_branch_violation_routed(self, test_review):
        recs = test_review.merge_recommendations(
            _structural([]), {"violations": [V_BRANCH]})
        assert recs[0]["priority"] == "P0"
        assert "§2c" in recs[0]["reference"]

    def test_dedupe_same_rule_case(self, test_review):
        recs = test_review.merge_recommendations(
            _structural([V_NAMING, dict(V_NAMING)]), None)
        assert len([r for r in recs if r["rule"] == "TOO_FEW_SEGMENTS"]) == 1

    def test_unknown_rule_fallback_by_check(self, test_review):
        v = {"check": "spdx", "severity": "error", "rule": None, "message": "缺 SPDX"}
        recs = test_review.merge_recommendations(_structural([v]), None)
        assert recs[0]["reference"] == "self-checker.md §3"

    def test_all_critical_rules_have_route(self, test_review):
        for rule in test_review.CRITICAL_RULES:
            assert rule in test_review.RULE_ROUTES, f"{rule} 缺路由"


# ── scorer 摘要宽松摄取 ──────────────────────────────────────────────

class TestSummarizeScore:
    def test_full(self, test_review):
        score = {"grade": "B", "score": 83, "pass": True, "raw_score": 83,
                 "capped_by": None, "triggered_hardgates": [], "min_pass": 70,
                 "dimensions": [{"name": "coverage", "score": 25, "weight": 25,
                                 "status": "pass"}, "bad-entry"],
                 "recommendations": [{"priority": "P1", "dimension": "naming",
                                      "action": "重命名", "route": "test-code-gen.md"}],
                 "inputs_used": {"structural": True}}
        s = test_review.summarize_score(score)
        assert s["grade"] == "B" and s["pass"] is True
        assert len(s["dimensions"]) == 1  # 非法条目被过滤
        assert s["recommendations"][0]["action"] == "重命名"

    def test_empty_and_garbage(self, test_review):
        assert test_review.summarize_score(None) is None
        assert test_review.summarize_score("nope") is None
        s = test_review.summarize_score({})
        assert s["grade"] is None and s["dimensions"] == []


# ── 编排层（monkeypatch run_* 边界）───────────────────────────────────

class TestReviewTargets:
    def _run(self, test_review, monkeypatch, tmp_path, structural, branch_status="skipped",
             branch_data=None, score_status="not_found", score_data=None):
        calls = {}

        def fake_structural(path, out_json):
            calls["structural"] = path
            with open(out_json, "w") as f:
                json.dump(structural, f)
            return structural, None

        def fake_branch(path, inv, project, url, out_json, repo_root=None):
            calls["branch"] = (path, inv, project)
            if branch_data is not None:
                with open(out_json, "w") as f:
                    json.dump(branch_data, f)
            return branch_status, branch_data, "no inv" if branch_status != "ok" else None

        def fake_score(path, sjson, bjson, inv, cov, mut, sdir, scorer):
            calls["score"] = (path, bjson)
            if score_data is not None:
                import os
                os.makedirs(sdir, exist_ok=True)
                with open(os.path.join(sdir, "scorecard-X.json"), "w") as f:
                    json.dump(score_data, f)
            return score_status, score_data, "not found" if score_status != "ok" else None

        monkeypatch.setattr(test_review, "run_structural", fake_structural)
        monkeypatch.setattr(test_review, "run_branch", fake_branch)
        monkeypatch.setattr(test_review, "run_score", fake_score)
        report = test_review.review_targets(_targets_doc(tmp_path), _args(tmp_path))
        return report, calls

    def test_orchestration_and_verdict(self, test_review, monkeypatch, tmp_path):
        report, calls = self._run(test_review, monkeypatch, tmp_path,
                                  _structural([V_CRITICAL, V_NAMING]),
                                  branch_status="ok", branch_data={"checked": 3, "violations": []},
                                  score_status="ok",
                                  score_data={"grade": "C", "score": 72, "pass": False,
                                              "min_pass": 70, "capped_by": "SOLE_NO_FATAL",
                                              "triggered_hardgates": ["SOLE_NO_FATAL"],
                                              "dimensions": [], "recommendations": []})
        assert calls["structural"].endswith("test_x.cpp")
        assert report["summary"] == {"total": 1, "pass": 0, "warn": 0, "fail": 1,
                                     "error": 0, "scored": 1, "branch_ok": 1}
        f = report["files"][0]
        assert f["verdict"] == "FAIL" and f["critical"] == ["SOLE_NO_FATAL"]
        assert f["score"]["summary"]["capped_by"] == "SOLE_NO_FATAL"
        assert f["test_case_count"] == 2
        assert report["meta"]["readonly"] is True
        assert report["meta"]["caveats"]  # commit 场景带 caveat

    def test_degraded_marks(self, test_review, monkeypatch, tmp_path):
        report, _ = self._run(test_review, monkeypatch, tmp_path,
                              _structural([]), branch_status="skipped",
                              score_status="not_found")
        f = report["files"][0]
        assert f["verdict"] == "PASS" and f["score"]["status"] == "not_found"
        assert f["branch"]["status"] == "skipped"
        assert any("数值评分" in d for d in report["degraded"])
        assert any("分支白盒" in d for d in report["degraded"])

    def test_structural_failure_is_error(self, test_review, monkeypatch, tmp_path):
        monkeypatch.setattr(test_review, "run_structural",
                            lambda p, o: (None, "boom"))
        report = test_review.review_targets(_targets_doc(tmp_path), _args(tmp_path))
        assert report["files"][0]["verdict"] == "ERROR"
        assert report["summary"]["error"] == 1
        # ERROR 不跑 branch/score（structural 为 None 时跳过）
        assert report["files"][0]["branch"] is None

    def test_render_contains_required_sections(self, test_review, monkeypatch, tmp_path):
        report, _ = self._run(test_review, monkeypatch, tmp_path,
                              _structural([V_CRITICAL, V_NAMING, V_WARN]),
                              score_status="ok",
                              score_data={"grade": "C", "score": 72, "pass": False,
                                          "min_pass": 70, "capped_by": "SOLE_NO_FATAL",
                                          "triggered_hardgates": ["SOLE_NO_FATAL"],
                                          "dimensions": [], "recommendations": [
                                              {"priority": "P0", "dimension": "assertion",
                                               "action": "重写断言", "route": "self-checker.md §2b"}]})
        md = test_review.render_report_md(report)
        for frag in ["# 单元测试质量审查报告", "## 1. 总览", "## 2. 逐文件明细",
                     "## 3. 改进路由汇总", "## 附录", "| 测试文件 |", "test_x.cpp",
                     "❌ FAIL", "SOLE_NO_FATAL", "test-code-gen.md",
                     "72（C）", "只读", "abc1234"]:
            assert frag in md, f"报告缺少片段: {frag}"
        # 模式独立性守卫：报告不得提及其他模式（只允许 Mode 6 自我标识）
        import re
        assert not re.search(r"Mode\s*[0-5]\b", md), "报告提及了其他模式，违反独立性"
        # 降级与跳过清单出现
        md2 = test_review.render_report_md(
            {**report, "skipped": [{"path": "del.cpp", "reason": "deleted"}],
             "degraded": ["分支白盒[x]: no inv"]})
        assert "del.cpp" in md2 and "降级说明" in md2


# ── CLI 端到端（真实 structural 子进程 + 降级路径）────────────────────

def _write_bad_test(tmp_path):
    """含 SOLE_NO_FATAL critical 的测试文件 → 预期 FAIL。"""
    p = tmp_path / "test_bad.cpp"
    p.write_text(GOOD_HEADER + """
#include <gtest/gtest.h>
class BadTest : public ::testing::Test {
protected:
    void SetUp() override {}
    void TearDown() override { stub.clear(); }
    stub_ext::StubExt stub;
};
TEST_F(BadTest, Add_Positive_ReturnsSum) {
    EXPECT_NO_FATAL_FAILURE({ int x = 1 + 1; });
}
""")
    return p


class TestCliE2E:
    def test_review_files_fail(self, tmp_path):
        bad = _write_bad_test(tmp_path)
        outdir = tmp_path / ".reports"
        rc = subprocess.run(
            [sys.executable, TEST_REVIEW_SCRIPT, "review", "--files", str(bad),
             "--no-branch", "--no-scorer", "-o", str(outdir)],
            capture_output=True, text=True,
            cwd=str(tmp_path)).returncode
        assert rc == 0  # 默认审查完成即 0
        md = (outdir / "test-review-files.md").read_text()
        data = json.loads((outdir / "test-review-files.json").read_text())
        assert data["summary"]["fail"] == 1
        f = data["files"][0]
        assert f["verdict"] == "FAIL" and "SOLE_NO_FATAL" in f["critical"]
        assert f["structural"] is not None  # 真实 structural JSON 已摄取
        assert "❌ FAIL" in md and "SOLE_NO_FATAL" in md
        assert "test-review-files.md" not in md  # 自引用 sanity

    def test_review_strict_exit1(self, tmp_path):
        bad = _write_bad_test(tmp_path)
        proc = subprocess.run(
            [sys.executable, TEST_REVIEW_SCRIPT, "review", "--files", str(bad),
             "--no-branch", "--no-scorer", "--strict", "-o", str(tmp_path / ".r")],
            capture_output=True, text=True, cwd=str(tmp_path))
        assert proc.returncode == 1
        assert "--strict" in proc.stderr

    def test_review_warn_not_fail(self, tmp_path):
        """仅命名 error（TOO_FEW_SEGMENTS）→ WARN，--strict 不触发。"""
        p = tmp_path / "test_warn.cpp"
        p.write_text(GOOD_HEADER + """
#include <gtest/gtest.h>
class WarnTest : public ::testing::Test {
protected:
    void SetUp() override {}
    void TearDown() override { stub.clear(); }
    stub_ext::StubExt stub;
};
TEST_F(WarnTest, Ok) {
    EXPECT_EQ(2, 1 + 1);
}
""")
        proc = subprocess.run(
            [sys.executable, TEST_REVIEW_SCRIPT, "review", "--files", str(p),
             "--no-branch", "--no-scorer", "--strict", "-o", str(tmp_path / ".r")],
            capture_output=True, text=True, cwd=str(tmp_path))
        assert proc.returncode == 0
        data = json.loads((tmp_path / ".r" / "test-review-files.json").read_text())
        assert data["summary"]["warn"] == 1 and data["summary"]["fail"] == 0

    def test_review_missing_file_exit2(self, tmp_path):
        rc = subprocess.run(
            [sys.executable, TEST_REVIEW_SCRIPT, "review", "--files", str(tmp_path / "nope.cpp"),
             "-o", str(tmp_path / ".r")],
            capture_output=True, text=True, cwd=str(tmp_path)).returncode
        assert rc == 2

    def test_review_requires_input(self, tmp_path):
        rc = subprocess.run(
            [sys.executable, TEST_REVIEW_SCRIPT, "review", "-o", str(tmp_path / ".r")],
            capture_output=True, text=True, cwd=str(tmp_path)).returncode
        assert rc == 2
