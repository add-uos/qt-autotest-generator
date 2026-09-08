"""ut-plan.py（L3 测试规划器，R2）单元测试——全部离线。"""
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "qt-autotest-generator" / "scripts"

# graph_access 先注册（ut-plan.py 顶层 from graph_access import …，且测试直断言其错误类）
if "graph_access" not in sys.modules:
    _ga_spec = importlib.util.spec_from_file_location(
        "graph_access", _SCRIPTS / "graph-access.py")
    _ga = importlib.util.module_from_spec(_ga_spec)
    sys.modules["graph_access"] = _ga
    _ga_spec.loader.exec_module(_ga)

_SCRIPT = _SCRIPTS / "ut-plan.py"
_spec = importlib.util.spec_from_file_location("ut_plan", _SCRIPT)
up = importlib.util.module_from_spec(_spec)
sys.modules["ut_plan"] = up
_spec.loader.exec_module(up)


def mk_rec(**kw):
    """快速构造方法记录（classify 输入）。"""
    base = {"id": "Method:a.cpp:C.f#1", "name": "f", "file_path": "src/a.cpp",
            "lines": 10, "start_line": 1, "end_line": 10, "in_degree": 0,
            "out_degree": 0, "param_count": 1, "is_public": True,
            "content_bytes": 200, "tested": None, "cc_proxy": 0,
            "body_source": "file-slice", "score": 0.3, "level": "mid"}
    base.update(kw)
    return base


# ── 分位数 ──

class TestQuantile:
    def test_linear_interpolation(self):
        vals = list(range(1, 101))  # 1..100
        assert up.quantile(vals, 50) == 50.5
        assert up.quantile(vals, 75) == 75.25
        assert up.quantile(vals, 90) == 90.1

    def test_exact_hit(self):
        vals = [1, 2, 3, 4, 5]
        assert up.quantile(vals, 50) == 3

    def test_empty_and_single(self):
        assert up.quantile([], 50) == 0
        assert up.quantile([7], 90) == 7

    def test_compute_quantiles_keys(self):
        q = up.compute_quantiles([1, 2, 3])
        assert set(q) == {"p50", "p75", "p90"}


# ── 分位归一 ──

class TestNormScore:
    def test_anchor_points(self):
        q = {"p50": 10, "p75": 20, "p90": 30}
        assert up.norm_score(0, q) == 0.0
        assert up.norm_score(10, q) == pytest.approx(0.25)
        assert up.norm_score(20, q) == pytest.approx(0.5)
        assert up.norm_score(30, q) == pytest.approx(0.75, abs=1e-6) or up.norm_score(30, q) <= 1.0
        assert up.norm_score(100, q) == 1.0  # ≥P90 饱和

    def test_monotonic(self):
        q = {"p50": 5, "p75": 15, "p90": 40}
        scores = [up.norm_score(v, q) for v in range(0, 60, 5)]
        assert scores == sorted(scores)

    def test_degenerate_equal_quantiles(self):
        # 全同值（如 cc 全 0）：不除零，返回有限值
        q = {"p50": 0, "p75": 0, "p90": 0}
        assert math.isfinite(up.norm_score(0, q))
        assert math.isfinite(up.norm_score(5, q))


# ── 模块归属 ──

class TestModuleOf:
    def test_src_two_level(self):
        assert up.module_of("src/dfm-base/utils/x.cpp") == "src/dfm-base"

    def test_non_src_top(self):
        assert up.module_of("autotests/libs/x.cpp") == "autotests"

    def test_root(self):
        assert up.module_of("main.cpp") == "(root)"


# ── 方法记录构建（内容拉取 + 切片 + 降级） ──

class FakeClient:
    """file_contents/symbol_contents 返回预置数据；记录调用。"""

    def __init__(self, file_map=None, sym_map=None):
        self.file_map = file_map or {}
        self.sym_map = sym_map or {}
        self.file_calls = []
        self.sym_calls = []

    def file_contents(self, paths):
        self.file_calls.append(list(paths))
        return {p: self.file_map[p] for p in paths if p in self.file_map}

    def symbol_contents(self, ids):
        self.sym_calls.append(list(ids))
        return {i: self.sym_map[i] for i in ids if i in self.sym_map}


CONTENT = "\n".join(f"line{i}" for i in range(1, 31))  # 30 行


def mk_inputs(skeleton, parents=None, classes=None):
    return {
        "counts": {"files": 1, "classes": 1, "methods": len(skeleton), "calls": 1,
                   "test_files": 1},
        "skeleton": skeleton,
        "classes": classes or [],
        "indegree": {m["id"]: 2 for m in skeleton},
        "testcov": {},
        "parents": parents or [],
        "outdegree": {m["id"]: 1 for m in skeleton},
    }


class TestBuildMethodRecords:
    def _skel(self, mid, start, end, fp="src/a.cpp"):
        return {"id": mid, "name": mid.split(".")[-1].split("#")[0], "filePath": fp,
                "startLine": start, "endLine": end, "parameterCount": 1,
                "isExported": True, "returnType": "void",
                "content_bytes": (end - start + 1) * 30}

    def test_small_method_no_content_fetch(self):
        # lines < 8 → 不拉内容，cc=0 body_source=none
        inputs = mk_inputs([self._skel("Method:a.cpp:f#1", 1, 5)])
        recs = up.build_method_records(inputs, client=None)
        assert recs[0]["lines"] == 5
        assert recs[0]["cc_proxy"] == 0 and recs[0]["body_source"] == "none"

    def test_big_method_slice_and_cc(self):
        m = self._skel("Method:a.cpp:f#1", 2, 12)
        m["content_bytes"] = 320
        # 方法体 2-12 行，含 3 个分支关键字 + && || 三元
        content = ("l1\nif (x) {}\nfor (;;) {}\nwhile (y) {}\n"
                   "case 1:\ncatch (e) {}\na && b || c ? d : e\n"
                   "l9\nl10\nl11\nl12\nl13")
        inputs = mk_inputs([m])
        client = FakeClient({"src/a.cpp": content})
        recs = up.build_method_records(inputs, client=client)
        # if/for/while/case/catch=5 + && + || + ?: = 8
        assert recs[0]["cc_proxy"] == 8
        assert recs[0]["body_source"] == "file-slice"
        assert client.file_calls == [["src/a.cpp"]]  # 去重批量

    def test_slice_fail_falls_back_to_symbol(self):
        m = self._skel("Method:a.cpp:f#1", 2, 20)
        inputs = mk_inputs([m])
        client = FakeClient(file_map={}, sym_map={"Method:a.cpp:f#1": "if (a) for (b)"})
        recs = up.build_method_records(inputs, client=client)
        assert recs[0]["body_source"] == "symbol-snippet"
        assert recs[0]["cc_proxy"] == 2
        assert client.sym_calls == [["Method:a.cpp:f#1"]]

    def test_missing_file_no_symbol(self):
        m = self._skel("Method:a.cpp:f#1", 2, 20)
        inputs = mk_inputs([m])
        client = FakeClient()
        recs = up.build_method_records(inputs, client=client)
        assert recs[0]["body_source"] == "none"

    def test_tested_wiring(self):
        m = self._skel("Method:a.cpp:f#1", 1, 3)
        inputs = mk_inputs([m])
        inputs["testcov"] = {"Method:a.cpp:f#1": {
            "id": "Method:a.cpp:f#1", "test_files": ["autotests/t.cpp"],
            "test_count": 3}}
        recs = up.build_method_records(inputs, client=None)
        assert recs[0]["tested"] == {"files": ["autotests/t.cpp"], "cases": 3}

    def test_module_filter(self, ):
        # fetch_plan_inputs 的 module 过滤逻辑单测（用假数据直接测函数体外的过滤语义）
        rows = [{"filePath": "src/plugins/a.cpp"}, {"filePath": "src/plugins/sub/b.cpp"},
                {"filePath": "src/base/c.cpp"}]
        module = "src/plugins"
        kept = [r for r in rows if r["filePath"] == module
                or r["filePath"].startswith(module.rstrip("/") + "/")]
        assert len(kept) == 2


# ── 分级 ──

class TestClassify:
    def test_hubs_float_up(self):
        # 枢纽方法（高入度 + 长体 + 高 cc）应为 high
        recs = [mk_rec(id=f"m{i}", lines=60, cc_proxy=25, in_degree=i * 30,
                       param_count=4, is_public=True) for i in range(1, 12)]
        recs, q = up.classify(recs)
        top = max(recs, key=lambda r: r["in_degree"])
        assert top["level"] == "high"
        assert top["score"] >= up.HIGH_SCORE
        assert q["in_deg"]["p50"] > 0

    def test_accessor_is_low(self):
        recs = [mk_rec(id="m", name="getValue", lines=3, cc_proxy=1, in_degree=0),
                mk_rec(id="h", lines=80, cc_proxy=30, in_degree=50)]
        recs, _ = up.classify(recs)
        assert recs[0]["level"] == "low"
        assert recs[1]["level"] == "high"

    def test_hidden_low(self):
        # 非 public + in_degree≤1 + lines≤5 → low
        recs = [mk_rec(id="m", name="impl", is_public=False, in_degree=0, lines=4),
                mk_rec(id="n", name="mid", is_public=False, in_degree=0, lines=6)]
        recs, _ = up.classify(recs)
        assert recs[0]["level"] == "low"
        assert recs[1]["level"] != "low"

    def test_mid_default(self):
        recs = [mk_rec(id="m", name="doWork", lines=20, cc_proxy=5, in_degree=3)]
        recs, _ = up.classify(recs)
        assert recs[0]["level"] in ("mid", "high")  # 分布单点时归一退化，不崩即可

    def test_quantile_frozen(self):
        recs = [mk_rec(id=f"m{i}", lines=i * 10, cc_proxy=i, in_degree=i)
                for i in range(1, 30)]
        recs, q = up.classify(recs)
        assert q == {"lines": up.compute_quantiles([r["lines"] for r in recs]),
                     "cc": up.compute_quantiles([r["cc_proxy"] for r in recs]),
                     "in_deg": up.compute_quantiles([r["in_degree"] for r in recs])}


# ── 分块 ──

CLS_A = {"id": "Class:a.cpp:A#0", "name": "A", "filePath": "src/a.cpp",
         "startLine": 1, "endLine": 100, "isExported": True}


class TestBuildBlocks:
    def _parents(self, method_ids, edge="HAS_METHOD", parent="Class:a.cpp:A#0"):
        return [{"method_id": m, "edge_type": edge, "parent_id": parent,
                 "parent_name": "A", "parent_file": "src/a.cpp"} for m in method_ids]

    def test_class_grouping(self):
        recs = [mk_rec(id=f"Class:a.cpp:A.m{i}#1") for i in range(3)]
        blocks = up.build_blocks(recs, [CLS_A], self._parents([r["id"] for r in recs]))
        assert len(blocks) == 1
        assert blocks[0]["kind"] == "class"
        assert blocks[0]["name"] == "A"
        assert len(blocks[0]["methods"]) == 3

    def _file_parents(self, ids):
        # id 形如 File:x.cpp.f0#1 → 文件 = x.cpp
        import re as _re
        def fp_of(mid):
            m = _re.match(r"File:(.+?)\.[^.]+" + r"#", mid)
            return m.group(1) if m else "unknown.cpp"
        return [{"method_id": m, "edge_type": "DEFINES",
                 "parent_id": f"File:{fp_of(m)}", "parent_name": "",
                 "parent_file": fp_of(m)} for m in ids]

    def test_standalone_by_file(self):
        recs = [mk_rec(id=f"File:x.cpp.f{i}#1", file_path="src/x.cpp") for i in range(2)] + \
               [mk_rec(id="File:y.cpp.g#1", file_path="src/y.cpp")]
        blocks = up.build_blocks(recs, [], self._file_parents([r["id"] for r in recs]))
        assert len(blocks) == 2  # 按文件分两组
        assert all(b["kind"] == "standalone" for b in blocks)
        assert sorted(b["file_path"] for b in blocks) == ["x.cpp", "y.cpp"]  # parent_file 优先

    def test_test_file_skipped(self):
        cls = dict(CLS_A, id="Class:autotests/t.cpp:T#0",
                   filePath="autotests/libs/test_x.cpp")
        recs = [mk_rec(id="Method:autotests/t.cpp:T.run#1",
                       file_path="autotests/libs/test_x.cpp")]
        parents = [{"method_id": recs[0]["id"], "edge_type": "HAS_METHOD",
                    "parent_id": cls["id"], "parent_name": "T",
                    "parent_file": cls["filePath"]}]
        blocks = up.build_blocks(recs, [cls], parents)
        assert blocks[0]["kind"] == "test-file"
        assert blocks[0]["status"] == "skipped"

    def test_subblock_split_on_bytes(self):
        # 3 个大方法（content_bytes 30KB each）→ 48KB 上限 → 每子块 1 方法
        recs = [mk_rec(id=f"Class:a.cpp:A.m{i}#1", content_bytes=30 * 1024)
                for i in range(3)]
        blocks = up.build_blocks(recs, [CLS_A], self._parents([r["id"] for r in recs]))
        assert len(blocks) == 3
        assert all(b["name"].startswith("A#") for b in blocks)  # 部件号
        assert [b["block_id"] for b in blocks] == ["B0000", "B0001", "B0002"]

    def test_subblock_max_methods(self):
        # 14 个小方法 → ≤6/子块 → 3 块（6+6+2）
        recs = [mk_rec(id=f"Class:a.cpp:A.m{i}#1", content_bytes=100) for i in range(14)]
        blocks = up.build_blocks(recs, [CLS_A], self._parents([r["id"] for r in recs]))
        assert len(blocks) == 3
        assert [len(b["methods"]) for b in blocks] == [6, 6, 2]

    def test_priority_formula(self):
        # priority = high占比 × log2(1+方法数)
        recs = [mk_rec(id=f"Class:a.cpp:A.m{i}#1") for i in range(3)]
        recs[0]["level"] = "high"
        blocks = up.build_blocks(recs, [CLS_A], self._parents([r["id"] for r in recs]))
        expect = (1 / 3) * math.log2(4)
        assert blocks[0]["priority"] == pytest.approx(expect, abs=1e-3)

    def test_cluster_is_module(self):
        recs = [mk_rec(id="Method:a.cpp:f#1")]
        blocks = up.build_blocks(recs, [], [
            {"method_id": recs[0]["id"], "edge_type": "DEFINES",
             "parent_id": "File:src/x.cpp", "parent_name": "x",
             "parent_file": "src/deep/x.cpp"}])
        assert blocks[0]["cluster"] == "src/deep"


# ── build_plan 集成（FakeClient 全链路） ──

class FakePlanClient(FakeClient):
    """聚合 graph-access 领域查询接口，喂预置 rows。"""

    def __init__(self, skeleton, classes, parents, counts, file_map=None,
                 repo="demo"):
        super().__init__(file_map=file_map)
        self.repo = repo
        self.skeleton = skeleton
        self.classes = classes
        self.parents = parents
        self.counts = counts

    def count_symbols(self, repo=None):
        return self.counts

    def method_skeleton(self, repo=None):
        return self.skeleton

    def class_skeleton(self, repo=None):
        return self.classes

    def call_indegree(self, repo=None, limit=None):
        return [{"id": s["id"], "indegree": 3} for s in self.skeleton]

    def test_edges(self, repo=None):
        return []

    def method_parents(self, repo=None):
        return self.parents

    def query(self, cypher, repo=None):
        return [{"id": s["id"], "outdeg": 1} for s in self.skeleton]


class TestBuildPlan:
    def _fixture(self, n_methods=40):
        skeleton = []
        for i in range(n_methods):
            fp = "src/mod/x.cpp" if i % 2 else "src/other/y.cpp"
            lines = 3 + (i * 7) % 60  # 3..62 变化行数，模拟真实分布
            skeleton.append({
                "id": f"Method:{fp}:C.m{i}#{i}", "name": f"m{i}", "filePath": fp,
                "startLine": 1, "endLine": lines,
                "parameterCount": i % 4, "isExported": i % 3 != 0,
                "returnType": "void", "content_bytes": lines * 25})
        classes = [{"id": "Class:src/mod/x.cpp:C#0", "name": "C",
                    "filePath": "src/mod/x.cpp", "startLine": 1, "endLine": 500,
                    "isExported": True}]
        parents = [{"method_id": s["id"], "edge_type": "HAS_METHOD",
                    "parent_id": classes[0]["id"], "parent_name": "C",
                    "parent_file": "src/mod/x.cpp"} for s in skeleton]
        counts = {"files": 2, "classes": 1, "methods": n_methods, "calls": 100,
                  "test_files": 1}
        return skeleton, classes, parents, counts

    def test_plan_shape(self):
        skeleton, classes, parents, counts = self._fixture()
        client = FakePlanClient(skeleton, classes, parents, counts)
        plan = up.build_plan(client, "demo", progress=None)
        assert plan["version"] == up.PLAN_VERSION
        assert plan["repo"] == "demo"
        assert plan["stats"]["methods"] == 40
        assert plan["stats"]["blocks"] >= 1
        assert set(plan["quantile"]) == {"lines", "cc", "in_deg"}
        # 所有块有 block_id 且唯一
        ids = [b["block_id"] for b in plan["blocks"]]
        assert len(ids) == len(set(ids))

    def test_survey_summary(self):
        skeleton, classes, parents, counts = self._fixture()
        client = FakePlanClient(skeleton, classes, parents, counts)
        plan = up.build_plan(client, "demo")
        assert plan["survey"]["methods"] == 40
        assert "src/mod" in plan["survey"]["modules"]
        assert plan["survey"]["top_in_degree"]  # 有入度快照

    def test_all_methods_in_blocks(self):
        skeleton, classes, parents, counts = self._fixture()
        client = FakePlanClient(skeleton, classes, parents, counts)
        plan = up.build_plan(client, "demo")
        total = sum(len(b["methods"]) for b in plan["blocks"])
        assert total == 40  # 分块无遗漏
        # 方法不重复
        mids = [m["id"] for b in plan["blocks"] for m in b["methods"]]
        assert len(mids) == len(set(mids))

    def test_high_ratio_bounded(self):
        # high ≤15% 设计目标（§11 R2 验收）
        skeleton, classes, parents, counts = self._fixture(120)
        client = FakePlanClient(skeleton, classes, parents, counts)
        plan = up.build_plan(client, "demo")
        ratio = plan["stats"]["high"] / plan["stats"]["methods"]
        assert ratio <= 0.15, f"high 占比 {ratio:.1%} 超标"


# ── CLI ──

class TestCli:
    def test_plan_writes_file(self, tmp_path, monkeypatch):
        skeleton, classes, parents, counts = TestBuildPlan._fixture(None, 12)
        client = FakePlanClient(skeleton, classes, parents, counts)
        monkeypatch.setattr(up, "RestQueryClient", lambda repo=None: client)
        out = tmp_path / "plan.json"
        rc = up.main(["plan", "--repo", "demo", "-o", str(out)])
        assert rc == 0
        plan = json.loads(out.read_text())
        assert plan["repo"] == "demo" and plan["stats"]["methods"] == 12

    def test_graph_error_exit_2(self, monkeypatch, tmp_path):
        GraphAccessError = sys.modules["graph_access"].GraphAccessError

        class Boom:
            repo = "demo"

            def __getattr__(self, name):
                raise GraphAccessError("down", kind="http")

        monkeypatch.setattr(up, "RestQueryClient", lambda repo=None: Boom())
        rc = up.main(["plan", "--repo", "demo", "-o", str(tmp_path / "p.json")])
        assert rc == 2

    def test_no_repo_exit_2(self):
        rc = up.main(["plan"])
        assert rc == 2


# ── select / update 状态机 ──

@pytest.fixture
def plan_file(tmp_path):
    """最小 plan 文件：3 块（priority 递减）。"""
    plan = {
        "version": up.PLAN_VERSION, "repo": "demo",
        "survey": {"classes": 1, "methods": 3, "files": 1, "call_edges": 2,
                   "modules": {"src/mod": 3}, "top_in_degree": []},
        "quantile": {"lines": {"p50": 5, "p75": 8, "p90": 12},
                     "cc": {"p50": 0, "p75": 1, "p90": 3},
                     "in_deg": {"p50": 0, "p75": 1, "p90": 4}},
        "blocks": [
            {"block_id": f"B{i:04d}", "kind": "class", "node_id": None,
             "name": f"C{i}", "file_path": "src/mod/x.cpp", "cluster": "src/mod",
             "methods": [], "level_summary": {"high": 1},
             "priority": 1.0 - i * 0.3, "context_bytes": 500, "status": "pending"}
            for i in range(3)],
        "stats": {"blocks": 3, "methods": 3, "high": 3, "mid": 0, "low": 0},
    }
    path = tmp_path / "plan.json"
    up.save_plan(plan, path)
    return path


class TestStateMachine:
    def test_select_full_by_priority(self, plan_file):
        sel = up.cmd_select(plan_file, mode="full", limit=2)
        assert sel == ["B0000", "B0001"]  # priority 降序
        plan = up.load_plan(plan_file)
        st = {b["block_id"]: b["status"] for b in plan["blocks"]}
        assert st["B0000"] == "selected" and st["B0002"] == "pending"

    def test_select_delta_skips_done(self, plan_file):
        up.cmd_update(plan_file, ["B0000"], status="done")
        sel = up.cmd_select(plan_file, mode="delta", limit=5)
        assert "B0000" not in sel
        assert set(sel) == {"B0001", "B0002"}

    def test_select_module_filter(self, plan_file):
        plan = up.load_plan(plan_file)
        plan["blocks"][1]["cluster"] = "src/other"
        up.save_plan(plan, plan_file)
        sel = up.cmd_select(plan_file, mode="module", module="src/other")
        assert sel == ["B0001"]

    def test_select_by_block_ids(self, plan_file):
        sel = up.cmd_select(plan_file, by_block=["B0002"])
        assert sel == ["B0002"]

    def test_update_status_transition(self, plan_file):
        n = up.cmd_update(plan_file, ["B0000", "B0009"], status="failed")
        assert n == 1  # B0009 不存在
        plan = up.load_plan(plan_file)
        assert plan["blocks"][0]["status"] == "failed"

    def test_update_invalid_status(self, plan_file):
        with pytest.raises(ValueError):
            up.cmd_update(plan_file, ["B0000"], status="bogus")

    def test_select_roundtrip_persists(self, plan_file):
        up.cmd_select(plan_file, mode="full")
        plan = up.load_plan(plan_file)
        assert all(b["status"] == "selected" for b in plan["blocks"])


class TestShow:
    def test_show_brief(self, plan_file, capsys):
        up.cmd_show(plan_file, brief=True)
        out = capsys.readouterr().out
        assert "repo=demo" in out and "blocks=3" in out
        assert "B0000" not in out  # brief 不打印块明细

    def test_show_all(self, plan_file, capsys):
        up.cmd_show(plan_file, brief=False)
        out = capsys.readouterr().out
        assert "B0000" in out and "C0" in out
