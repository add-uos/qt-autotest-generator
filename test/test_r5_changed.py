# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""R5：变更驱动模式——detect_changes（本地 git）+ impact（图谱反查）+ select changed。

覆盖：
- graph_access.methods_in_files（IN 分批、IN 字面量转义）
- ut_plan.detect_changes（git diff --name-status 解析、未跟踪、非 git 报错、run 注入）
- ut_plan.compute_impact（源文件过滤、caller 文件并集、无方法短路）
- ut_plan.select_changed_blocks（done 跳过）
- cmd_select(mode="changed")（状态写回 + last_impact 摘要）与 CLI 路由
"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# 复用已注册模块（test_ut_plan/test_graph_access 先加载时）；独立跑兜底
if "graph_access" in sys.modules:
    ga = sys.modules["graph_access"]
else:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "graph_access", _ROOT / "qt-autotest-generator" / "scripts" / "graph-access.py")
    ga = importlib.util.module_from_spec(_spec)
    sys.modules["graph_access"] = ga
    _spec.loader.exec_module(ga)

if "ut_plan" in sys.modules:
    up = sys.modules["ut_plan"]
else:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "ut_plan", _ROOT / "qt-autotest-generator" / "scripts" / "ut-plan.py")
    up = importlib.util.module_from_spec(_spec)
    sys.modules["ut_plan"] = up
    _spec.loader.exec_module(up)


# ── graph-access: methods_in_files ──────────────────────────────────

class TestMethodsInFiles:
    def _client(self, queue):
        import test_graph_access as tga
        # 队列项需为 JSON 字符串（FakeResponse 接口）
        payload = json.dumps({"result": queue[0]}) if queue else None
        items = [json.dumps({"result": rows}) for rows in queue]
        client, opener = tga.TestQueryBasics._client(tga.TestQueryBasics(), items)
        client.opener = opener
        return client

    def test_batches_and_parses(self):
        # 205 个文件 → 2 次请求
        files = [f"src/a{i}.cpp" for i in range(205)]
        rows = [{"id": f"M{i}", "file": files[i]} for i in range(5)]
        c = self._client([rows, rows])
        got = c.methods_in_files(files)
        assert len(got) == 10
        # 两次请求各带 200/5 个文件
        bodies = [r[1]["cypher"] for r in c.opener.requests]
        assert bodies[0].count("src/a") == 200 and bodies[1].count("src/a") == 5

    def test_escapes_quotes(self):
        c = self._client([[{"id": "M1", "file": "src/it's.h"}]])
        c.methods_in_files(["src/it's.h"])
        cypher = c.opener.requests[0][1]["cypher"]
        assert "it\\'s.h" in cypher


# ── detect_changes ──────────────────────────────────────────────────

class TestDetectChanges:
    def test_parse_name_status(self):
        out = "M\tsrc/a.cpp\nA\tsrc/new.h\nD\tsrc/old.cpp\nR100\told.txt\tnew.txt"

        def fake_run(cmd, cwd):
            # HEAD 基准会追加 untracked 查询（返回空）
            if "--name-status" in cmd:
                return out
            return ""

        d = up.detect_changes("/repo", run=fake_run)
        assert d == {"added": ["src/new.h"], "modified": ["src/a.cpp"],
                     "deleted": ["src/old.cpp"], "renamed": ["new.txt"]}

    def test_untracked_included_for_head(self):
        calls = []

        def fake_run(cmd, cwd):
            calls.append(cmd)
            return "A\tsrc/a.cpp" if "--name-status" in cmd else "src/stray.cpp\n"

        d = up.detect_changes("/repo", base="HEAD", run=fake_run)
        assert d["added"] == ["src/a.cpp", "src/stray.cpp"]
        assert any("ls-files" in c for c in calls)

    def test_no_untracked_for_range(self):
        def fake_run(cmd, cwd):
            return "M\tsrc/a.cpp"

        d = up.detect_changes("/repo", base="main..dev", run=fake_run)
        assert d["added"] == [] and d["modified"] == ["src/a.cpp"]

    def test_git_failure_raises(self):
        def fake_run(cmd, cwd):
            raise ValueError("git 失败（exit 128）: not a repository")

        with pytest.raises(ValueError, match="not a repository"):
            up.detect_changes("/repo", run=fake_run)


# ── compute_impact ──────────────────────────────────────────────────

class FakeImpactClient:
    """methods_in_files + method_neighbors 的最小替身。"""

    def __init__(self, file_methods, neighbors, repo="demo"):
        self.repo = repo
        self.file_methods = file_methods
        self.neighbors = neighbors
        self.asked_files = None
        self.asked_ids = None

    def methods_in_files(self, files, repo=None):
        self.asked_files = list(files)
        return self.file_methods

    def method_neighbors(self, ids, repo=None):
        self.asked_ids = list(ids)
        return self.neighbors


class TestComputeImpact:
    def test_changed_files_plus_callers(self):
        client = FakeImpactClient(
            file_methods=[{"id": "M1", "file": "src/a.cpp"}],
            neighbors=[
                {"method_id": "M1", "direction": "caller", "peer": "f",
                 "peer_file": "src/b.h"},
                {"method_id": "M1", "direction": "caller", "peer": "g",
                 "peer_file": "src/a.cpp"},          # 自引用去重
                {"method_id": "M1", "direction": "callee", "peer": "h",
                 "peer_file": "src/c.h"},            # callee 不计影响面
                {"method_id": "M1", "direction": "caller", "peer": "x",
                 "peer_file": ""},                   # 空文件过滤
            ])
        imp = up.compute_impact(client, ["src/a.cpp"], "demo")
        assert client.asked_files == ["src/a.cpp"]
        assert client.asked_ids == ["M1"]
        assert imp["methods"] == 1 and imp["callers"] == 1
        assert imp["impact_files"] == {"src/a.cpp", "src/b.h"}

    def test_non_source_filtered(self):
        client = FakeImpactClient([], [])
        imp = up.compute_impact(client, ["README.md", "src/a.ui", "src/b.cpp"],
                                "demo")
        assert client.asked_files == ["src/b.cpp"]   # 非源文件不进图谱查询
        assert imp["methods"] == 0
        # 影响面 = 过滤后的源文件集合（无方法时不再外溢）
        assert imp["impact_files"] == {"src/b.cpp"}

    def test_no_methods_short_circuits(self):
        client = FakeImpactClient([], [])
        imp = up.compute_impact(client, ["src/a.cpp"], "demo")
        assert client.asked_ids is None        # 未查邻接
        assert imp["impact_files"] == {"src/a.cpp"}  # 变更文件本身仍在影响面


# ── select_changed_blocks / cmd_select(changed) ─────────────────────

def _plan_file(tmp_path, blocks):
    plan = {"version": up.PLAN_VERSION, "repo": "demo",
            "survey": {}, "quantile": {}, "blocks": blocks,
            "stats": {"blocks": len(blocks), "methods": 0, "high": 0,
                      "mid": 0, "low": 0}}
    path = tmp_path / "plan.json"
    up.save_plan(plan, path)
    return path


def _blk(bid, fp, status="pending"):
    return {"block_id": bid, "kind": "class", "node_id": None, "name": bid,
            "file_path": fp, "cluster": os.path.dirname(fp) or ".",
            "methods": [{"id": f"M{bid}", "file_path": fp}],
            "level_summary": {"high": 1, "mid": 0}, "priority": 1.0,
            "context_bytes": 0, "status": status}


class TestSelectChanged:
    def test_hits_and_skips_done(self, tmp_path):
        plan_path = _plan_file(tmp_path, [
            _blk("B1", "src/a.h"), _blk("B2", "src/b.h", status="done"),
            _blk("B3", "src/c.h")])
        picked = up.cmd_select(plan_path, mode="changed",
                               client=FakeImpactClient(
                                   [], []), repo_root="/repo",
                               detect_fn=lambda root: {
                                   "added": [], "modified": ["src/a.h"],
                                   "deleted": [], "renamed": []})
        # FakeImpactClient 返回空方法 → 影响面只含变更文件本身，仅命中 B1
        assert picked == ["B1"]
        plan = up.load_plan(plan_path)
        st = {b["block_id"]: b["status"] for b in plan["blocks"]}
        assert st == {"B1": "selected", "B2": "done", "B3": "pending"}
        assert up.cmd_select.last_impact["changed_files"] == 1

    def test_caller_file_blocks_selected(self, tmp_path):
        plan_path = _plan_file(tmp_path, [_blk("B1", "src/a.h"),
                                          _blk("B9", "src/b.h")])
        client = FakeImpactClient(
            file_methods=[{"id": "M1", "file": "src/a.h"}],
            neighbors=[{"method_id": "M1", "direction": "caller", "peer": "f",
                        "peer_file": "src/b.h"}])

        def detect(root):
            return {"added": [], "modified": ["src/a.h"],
                    "deleted": [], "renamed": []}

        picked = up.cmd_select(plan_path, mode="changed", client=client,
                               repo_root="/repo", detect_fn=detect)
        assert picked == ["B1", "B9"]          # caller 文件所在块也被选中
        assert up.cmd_select.last_impact["callers"] == 1

    def test_changed_requires_client(self, tmp_path):
        plan_path = _plan_file(tmp_path, [])
        with pytest.raises(ValueError, match="repo-root"):
            up.cmd_select(plan_path, mode="changed")

    def test_cli_changed_route(self, tmp_path, monkeypatch, capsys):
        plan_path = _plan_file(tmp_path, [_blk("B1", "src/a.h")])
        monkeypatch.setenv("QTAG_GN_REPO", "demo")
        monkeypatch.setattr(up, "RestQueryClient",
                            lambda repo=None: FakeImpactClient([], []))
        monkeypatch.setattr(up, "detect_changes",
                            lambda root: {"added": ["src/a.h"], "modified": [],
                                          "deleted": [], "renamed": []})
        rc = up.main(["select", str(plan_path), "--mode", "changed",
                      "--repo-root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "impact:" in out and "selected 1" in out

    def test_cli_changed_without_repo_env(self, tmp_path, monkeypatch):
        plan_path = _plan_file(tmp_path, [])
        monkeypatch.delenv("QTAG_GN_REPO", raising=False)
        rc = up.main(["select", str(plan_path), "--mode", "changed",
                      "--repo-root", str(tmp_path)])
        assert rc == 2


# ── R6：_git_head / base_commit / verify base 漂移检查 ───────────────

def _git_repo(tmp_path):
    import subprocess as sp
    repo = tmp_path / "repo"
    (repo / "src" / "dfm-base").mkdir(parents=True)
    r = lambda *a, **kw: sp.run(["git", *a], cwd=repo, capture_output=True,
                                text=True, check=True, **kw)
    r("init", "-q")
    r("config", "user.email", "t@t"); r("config", "user.name", "t")
    (repo / "src" / "dfm-base" / "a.h").write_text("int f();\n")
    r("add", "-A"); r("commit", "-qm", "init")
    return repo, r


class TestGitHead:
    def test_head_and_non_git(self, tmp_path):
        repo, _ = _git_repo(tmp_path)
        head = up._git_head(str(repo))
        assert head and len(head) >= 7
        assert up._git_head(str(tmp_path / "nope")) is None


class TestVerifyBase:
    def _env(self, tmp_path, repo):
        plan = {"version": up.PLAN_VERSION, "repo": "demo", "base_commit": None,
                "survey": {}, "quantile": {},
                "blocks": [dict(_blk("B1", "src/dfm-base/a.h"))],
                "stats": {"blocks": 1, "methods": 0, "high": 0, "mid": 0, "low": 0}}
        path = tmp_path / "plan.json"
        up.save_plan(plan, path)
        gen = tmp_path / ".ut-gen" / "B1"
        gen.mkdir(parents=True)
        (gen / "test_a.cpp").write_text("TEST(ATest, F) {}")
        return path, gen / "test_a.cpp"

    def _runner(self):
        import test_ut_plan as tup
        return tup.FakeRunner()

    def test_happy_path_records_base_commit(self, tmp_path):
        repo, _ = _git_repo(tmp_path)
        plan_path, test_file = self._env(tmp_path, repo)
        import test_ut_plan as tup
        ok, detail = up.cmd_verify(plan_path, "B1", str(repo),
                                   test_file=str(test_file),
                                   runner=tup.FakeRunner())
        assert ok
        plan = up.load_plan(plan_path)
        lv = plan["blocks"][0]["last_verify"]
        assert lv["base_commit"] == up._git_head(str(repo))
        assert detail["base_drift"] is None

    def test_base_drift_flagged(self, tmp_path):
        repo, git = _git_repo(tmp_path)
        (repo / "src" / "dfm-base" / "a.h").write_text(
            "int f(int x = 0);\n")  # 未提交改动
        plan_path, test_file = self._env(tmp_path, repo)
        import test_ut_plan as tup
        ok, detail = up.cmd_verify(plan_path, "B1", str(repo),
                                   test_file=str(test_file),
                                   runner=tup.FakeRunner(), base="HEAD")
        assert ok and detail["base_drift"]["file_changed"] is True
        plan = up.load_plan(plan_path)
        lv = plan["blocks"][0]["last_verify"]
        assert lv["base_drift"] == {"base": "HEAD", "file_changed": True}

    def test_no_drift_not_flagged(self, tmp_path):
        repo, _ = _git_repo(tmp_path)
        plan_path, test_file = self._env(tmp_path, repo)
        import test_ut_plan as tup
        ok, detail = up.cmd_verify(plan_path, "B1", str(repo),
                                   test_file=str(test_file),
                                   runner=tup.FakeRunner(), base="HEAD")
        assert ok and detail["base_drift"]["file_changed"] is False

    def test_cli_verify_base_flag(self, tmp_path, monkeypatch, capsys):
        # runner 默认参数定义时绑定，monkeypatch 模块属性不生效——改用 spy 验证接线
        repo, _ = _git_repo(tmp_path)
        plan_path, test_file = self._env(tmp_path, repo)
        called = {}

        def spy(*a, **kw):
            called["base"] = kw.get("base")
            return True, {"block": "B1", "status": "done", "test_file": "x",
                          "run": None, "base_drift": None, "error": None}

        monkeypatch.setattr(up, "cmd_verify", spy)
        rc = up.main(["verify", str(plan_path), "--block", "B1",
                      "--repo-root", str(repo), "--test-file", str(test_file),
                      "--base", "HEAD"])
        assert rc == 0 and called["base"] == "HEAD"
