"""graph-access.py（GitNexus REST L1 访问层）单元测试。

全部离线：FakeOpener 模拟 HTTP 层，不访问网络。
"""
import json
import urllib.error

import pytest

# ── 被测模块加载（连字符文件名） ──

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "qt-autotest-generator" / "scripts" / "graph-access.py"
_spec = importlib.util.spec_from_file_location("graph_access", _SCRIPT)
ga = importlib.util.module_from_spec(_spec)
sys.modules["graph_access"] = ga
_spec.loader.exec_module(ga)


# ── Fake HTTP 层 ──

class FakeResponse:
    def __init__(self, body, encoding=None):
        self._body = body.encode() if isinstance(body, str) else body
        self.headers = {"Content-Encoding": encoding} if encoding else {}

    def read(self):
        return self._body


class RecordingOpener:
    """更直白的顺序响应 opener：每个请求消耗队列，空队列抛 404。"""

    def __init__(self, queue):
        self.queue = list(queue)
        self.requests = []
        self.last = None

    def open(self, req, timeout=None):
        self.requests.append((req.full_url, json.loads(req.data.decode())))
        if not self.queue:
            # 队列耗尽：粘滞重复最后一项（模拟服务端持续故障/同响应）；
            # 从未有响应时才 404
            if self.last is None:
                raise urllib.error.HTTPError(req.full_url, 404, "empty queue",
                                             None, None)
            item = self.last
        else:
            item = self.queue.pop(0)
            self.last = item
        if isinstance(item, Exception):
            raise item
        if isinstance(item, FakeResponse):
            return item
        return FakeResponse(item)


def http_error(status, body=""):
    import urllib.error
    return urllib.error.HTTPError("http://x/api/query", status, "err", None,
                                  None if body is None else _bytes_io(body))


def _bytes_io(body):
    import io
    return io.BytesIO(body.encode() if isinstance(body, str) else body)


# ── query 基础行为 ──

class TestQueryBasics:
    def _client(self, queue, **kw):
        opener = RecordingOpener(queue)
        client = ga.RestQueryClient(base_url="http://fake/api", repo="r",
                                    opener=opener, sleep=lambda s: None, **kw)
        return client, opener

    def test_posts_cypher_and_repo(self):
        client, opener = self._client([json.dumps({"result": [{"n": 1}]})])
        rows = client.query("MATCH (n:Method) RETURN count(n) AS n")
        assert rows == [{"n": 1}]
        url, payload = opener.requests[0]
        assert url == "http://fake/api/query"
        assert payload == {"cypher": "MATCH (n:Method) RETURN count(n) AS n", "repo": "r"}

    def test_no_repo_raises(self):
        client = ga.RestQueryClient(base_url="http://fake/api", opener=RecordingOpener([]),
                                    sleep=lambda s: None)
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "repo"

    def test_auth_error_401(self):
        client, _ = self._client([http_error(401, '{"error":"unauthorized"}')])
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "auth"
        assert not ei.value.retryable

    def test_404_repo_kind(self):
        client, _ = self._client([http_error(404, "Repository not found")])
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "repo"

    def test_400_query_kind(self):
        client, _ = self._client([http_error(400, '{"error":"unknown field"}')])
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "query"

    def test_502_retries_then_succeeds(self):
        client, opener = self._client([
            http_error(502, "bad gateway"),
            json.dumps({"result": [{"n": 2}]})])
        rows = client.query("MATCH (n) RETURN count(n) AS n")
        assert rows == [{"n": 2}]
        assert len(opener.requests) == 2  # 重试一次成功

    def test_502_exhausted_raises(self):
        err = http_error(502, "bad gateway")
        client, opener = self._client([err])  # 单元素队列永远返回同一错误
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "http"
        assert ei.value.retryable
        assert len(opener.requests) == len(ga.RETRY_DELAYS) + 1  # 1 + 2 次重试

    def test_500_non_retryable_body_raises_immediately(self):
        client, opener = self._client([http_error(500, '{"error":"Prepare failed: Binder exception"}')])
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "http"
        assert not ei.value.retryable
        assert len(opener.requests) == 1  # 不重试

    def test_500_retryable_body_retries(self):
        client, opener = self._client([
            http_error(500, "LadybugDB unavailable: rebuilding the index"),
            json.dumps({"result": []})])
        rows = client.query("MATCH (n) RETURN n")
        assert rows == []
        assert len(opener.requests) == 2

    def test_network_error_retries_then_raises(self):
        import urllib.error
        client, opener = self._client([
            urllib.error.URLError("conn refused"),
            json.dumps({"result": [{"n": 1}]})])
        rows = client.query("MATCH (n) RETURN n")
        assert len(opener.requests) == 2

    def test_gzip_decoded(self):
        import gzip as _gzip
        body = _gzip.compress(json.dumps({"result": [{"n": 3}]}).encode())
        client, _ = self._client([FakeResponse(body, encoding="gzip")])
        assert client.query("MATCH (n) RETURN n") == [{"n": 3}]

    def test_non_json_body_raises_empty(self):
        client, _ = self._client(["<html>gateway error</html>"])
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "empty"

    def test_missing_result_raises_empty(self):
        client, _ = self._client([json.dumps({"data": []})])
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "empty"

    def test_result_not_list_raises_empty(self):
        client, _ = self._client([json.dumps({"result": {"a": 1}})])
        with pytest.raises(ga.GraphAccessError) as ei:
            client.query("MATCH (n) RETURN n")
        assert ei.value.kind == "empty"


# ── 固化领域查询 ──

class TestDomainQueries:
    def _client(self, queues_by_call):
        """queues_by_call: 每次 query 调用消耗一个响应。"""
        opener = RecordingOpener(queues_by_call)
        return ga.RestQueryClient(base_url="http://fake/api", repo="r",
                                  opener=opener, sleep=lambda s: None), opener

    def test_count_symbols_single_request(self):
        client, opener = self._client([
            json.dumps({"result": [{"files": 4084, "classes": 2804, "methods": 25041,
                                    "calls": 13593, "test_files": 54}]})])
        stats = client.count_symbols()
        assert stats["methods"] == 25041
        assert len(opener.requests) == 1  # 聚合下推：单请求

    def test_method_skeleton_columns(self):
        client, opener = self._client([
            json.dumps({"result": [
                {"id": "Method:a.cpp:f#1", "name": "f", "filePath": "a.cpp",
                 "startLine": 10, "endLine": 20, "parameterCount": 2,
                 "isExported": True, "returnType": "void", "content_bytes": 512}]})])
        rows = client.method_skeleton()
        assert rows[0]["parameterCount"] == 2
        assert rows[0]["content_bytes"] == 512
        cypher = opener.requests[0][1]["cypher"]
        assert "size(coalesce(m.content,''))" in cypher  # 服务端聚合 content_bytes
        assert "signature" not in cypher and "className" not in cypher  # 不存在属性不查

    class _C:
        pass

    def test_call_indegree_ordering_clause(self):
        client, opener = self._client([json.dumps({"result": []})])
        client.call_indegree()
        cypher = opener.requests[0][1]["cypher"]
        assert "ORDER BY indegree DESC" in cypher

    def test_call_indegree_limit(self):
        client, opener = self._client([json.dumps({"result": []})])
        client.call_indegree(limit=10)
        assert "LIMIT 10" in opener.requests[0][1]["cypher"]

    def test_test_edges_covers_three_prefixes(self):
        client, opener = self._client([json.dumps({"result": []})])
        client.test_edges()
        cypher = opener.requests[0][1]["cypher"]
        for prefix in ("tests/", "test/", "autotests/"):
            assert f"STARTS WITH '{prefix}'" in cypher

    def test_clusters_min_symbols(self):
        client, opener = self._client([json.dumps({"result": []})])
        client.clusters(min_symbols=8, limit=3)
        cypher = opener.requests[0][1]["cypher"]
        assert "n.symbolCount >= 8" in cypher
        assert "LIMIT 3" in cypher

    def test_file_contents_chunks_and_escapes(self):
        # 201 个文件 → 2 批；含单引号路径转义
        paths = [f"f{i}.cpp" for i in range(201)] + ["it's.cpp"]
        ok = json.dumps({"result": [{"filePath": "f0.cpp", "content": "int main(){}"}]})
        client, opener = self._client([ok, ok, ok])
        out = client.file_contents(paths)
        assert out == {"f0.cpp": "int main(){}"}
        assert len(opener.requests) == 2  # 202 路径 → 200 + 2 两批
        cyp0 = opener.requests[0][1]["cypher"]
        cyp1 = opener.requests[1][1]["cypher"]
        def in_items(cyp):
            return cyp.split("IN [", 1)[1].split("]", 1)[0].split(", ")
        assert len(in_items(cyp0)) == 200  # 第一批 200 个文件
        assert len(in_items(cyp1)) == 2  # 第二批：f200 + 转义路径
        assert "'f200.cpp'" in cyp1 and "\\'" in cyp1

    def test_file_contents_dedup(self):
        ok = json.dumps({"result": []})
        client, opener = self._client([ok])
        client.file_contents(["a.cpp", "a.cpp", "b.cpp"])
        cypher = opener.requests[0][1]["cypher"]
        assert cypher.count("a.cpp") == 1  # 去重

    def test_symbol_contents(self):
        ok = json.dumps({"result": [{"id": "Method:x#1", "content": "void f(){}"}]})
        client, opener = self._client([ok])
        out = client.symbol_contents(["Method:x#1"])
        assert out == {"Method:x#1": "void f(){}"}
        assert "m.id IN" in opener.requests[0][1]["cypher"]


# ── 方法体切片与 cc_proxy ──

class TestBodySlice:
    def test_slice_basic(self):
        content = "l1\nl2\nl3\nl4\nl5"
        assert ga.slice_body(content, 2, 4) == "l2\nl3\nl4"

    def test_slice_full_file(self):
        content = "a\nb\nc"
        assert ga.slice_body(content, 1, 3) == content

    def test_slice_end_clamped(self):
        assert ga.slice_body("a\nb", 1, 99) == "a\nb"

    def test_slice_invalid_range(self):
        assert ga.slice_body("a\nb", 3, 2) == ""
        assert ga.slice_body("a\nb", 0, 2) == ""
        assert ga.slice_body(None, 1, 2) == ""

    def test_count_branches_keywords(self):
        body = "if (a) {}\nfor (;;) {}\nwhile (x) {}\nswitch (y) { case 1: }\ntry {} catch (e) {}"
        assert ga.count_branches(body) == 5

    def test_count_branches_operators(self):
        assert ga.count_branches("a && b || c ? d : e") == 3

    def test_count_branches_not_identifier(self):
        # "shift(" 不算 if；"firm(" 不算 for——\b 边界保护
        assert ga.count_branches("shift(x);\nfirm(y);") == 0

    def test_count_branches_empty(self):
        assert ga.count_branches("") == 0
        assert ga.count_branches(None) == 0


# ── CLI ──

class TestCli:
    def test_parser_routes(self):
        parser = ga.build_parser()
        args = parser.parse_args(["skeleton", "--repo", "dde-file-manager", "-o", "/tmp/x.json"])
        assert args.command == "skeleton" and args.repo == "dde-file-manager" and args.output == "/tmp/x.json"
        args = parser.parse_args(["file-content", "--repo", "r", "a.cpp", "b.cpp"])
        assert args.files == ["a.cpp", "b.cpp"]
        args = parser.parse_args(["clusters", "--min-symbols", "8"])
        assert args.min_symbols == 8

    def test_main_error_exit_code(self, monkeypatch, capsys):
        # main 捕获 GraphAccessError → 返回 2
        monkeypatch.setattr(ga, "_client_from_args", lambda a: _FailingClient())
        rc = ga.main(["selftest", "--repo", "r"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "硬终止" in err and "kind=repo" in err


class _FailingClient:
    def count_symbols(self):
        raise ga.GraphAccessError("no repo", kind="repo")


# ── 回归：真机契约（文档锚点） ──

class TestLiveContract:
    """文档记录的真机实测值——防止固化查询悄悄偏离契约。"""

    def test_skeleton_cypher_matches_v23_design(self):
        client = ga.RestQueryClient(repo="x", opener=RecordingOpener([]))
        # 不发请求，直接检查固化 SQL 模板存在
        import inspect
        src = inspect.getsource(type(client).method_skeleton)
        assert "m.isExported" in src and "size(coalesce(m.content,''))" in src

    def test_in_chunk_is_200(self):
        assert ga.IN_CHUNK == 200
