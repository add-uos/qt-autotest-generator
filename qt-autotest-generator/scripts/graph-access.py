#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""graph-access.py — GitNexus 图谱访问层（L1，REST 唯一数据通道）

doc/gitnexus-ut-workflow-v2.md §11 R1 交付物。设计决策（v2.3）：
  - 数据面只走 REST POST /api/query（纯 JSON，免分页免 markdown 截断，
    dde-file-manager 骨架 23307 行实测 4.5s）；通道不可用即抛
    GraphAccessError 硬终止，不降级 MCP 分钟级通道。
  - MCP 仅保留 impact/trace/detect_changes 三语义工具（本文件不含，
    由调用方按需引入 mcp-scan.py 的 MCPClient）。

方言约束（真机实测，doc/gitnexus-ut-workflow-v2.md §3.1 勘误表）：
  - 所有边在 CodeRelation 单表，用 WHERE r.type='...'（独立边表 500）
  - 无 percentile_cont/split/char/round → 分位数本地算
  - Method 属性：id/name/filePath/startLine/endLine/parameterCount/
    isExported/returnType/content；无 signature/className/complexity
  - 类归属边：Class/Struct -[:HAS_METHOD]-> Method；File -[:DEFINES]-> Method
    （自由函数）；File -[:DEFINES]-> Class
  - content 语义：Method/Class 等**符号级** content 在索引写入时截断
    5000 字符（源码 MAX_SNIPPET）；File.content 全量存储 → 方法体一律
    File.content + startLine/endLine 行切片，符号 content 仅作降级。

用法:
  # 连通性自检（repo 可达性 + 计数）
  python3 graph-access.py selftest --repo dde-file-manager

  # 测绘：符号计数 + 聚类 + 流程（秒级）
  python3 graph-access.py survey --repo dde-file-manager

  # 方法骨架全量 → JSON 文件（L3 plan 输入）
  python3 graph-access.py skeleton --repo dde-file-manager -o /tmp/skel.json

  # 调用入度 / 测试覆盖 / 聚类 / 流程 / 类清单
  python3 graph-access.py indegree  --repo dde-file-manager -o /tmp/ind.json
  python3 graph-access.py testcov   --repo dde-file-manager
  python3 graph-access.py clusters  --repo dde-file-manager
  python3 graph-access.py processes --repo dde-file-manager
  python3 graph-access.py classes   --repo dde-file-manager

  # File.content 批量拉取（方法体行切片原料；逗号/多参数分隔）
  python3 graph-access.py file-content --repo dde-file-manager \
      src/a.cpp src/b.cpp -o /tmp/files.json

环境变量:
  QTAG_GN_BASE_URL   REST 基址（默认 https://codegraph.uniontech.com/api）
  QTAG_GN_HEADERS    JSON 字符串，整体替换默认认证头
  QTAG_GN_REPO       默认 repo（CLI --repo 未给时使用）
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# ── 服务端点 ──────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://codegraph.uniontech.com/api"

# 认证头：与 mcp-scan.py 的 MCP 通道同源（Basic）。
DEFAULT_HEADERS = {"Authorization": "Basic Z2l0bnV4dXM6Z2l0bmV4dXMuMTEyMg=="}

# 网关瞬态错误：重试（与 mcp-scan.py MCP_RETRY_CODES 同哲学，间隔更短——REST 快）
RETRY_CODES = {502, 503, 504}
RETRY_DELAYS = (2, 6)

# IN 列表分批大小（file-content 批量拉取；Kuzu 无参数绑定，列表内联）
IN_CHUNK = 200

# LadybugDB 瞬态错误特征（索引重建锁等，可重试）
_RETRYABLE_ERROR_RE = re.compile(
    r"LadybugDB unavailable|rebuilding the index|shadow pages", re.I)


class GraphAccessError(RuntimeError):
    """REST 图谱访问失败。

    kind: http（网关/服务端状态码）/ auth / repo（仓库未解析）/
          query（cypher 语义错）/ empty（非 JSON 或缺 result）/ network
    retryable: True 时上层可退避重试（瞬态 5xx / 索引重建锁）。
    """

    def __init__(self, message, kind="query", retryable=False, status=None):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status


def _classify_http_error(status, body):
    """HTTP 状态码 + 响应体 → (kind, retryable)。"""
    if status == 401 or status == 403:
        return "auth", False
    if status == 404:
        # /api/query 不存在（服务端升级移除）或 repo 未解析——统一按通道缺失硬终止
        return "repo", False
    if status == 400:
        return "query", False
    if status in RETRY_CODES:
        return "http", True
    if status >= 500:
        return "http", bool(_RETRYABLE_ERROR_RE.search(body or ""))
    return "http", False


class RestQueryClient:
    """GitNexus REST /api/query 客户端（L1 唯一数据通道）。

    opener 可注入（测试用）；生产路径为 urllib.request.build_opener。
    """

    def __init__(self, base_url=DEFAULT_BASE_URL, headers=None, timeout=120,
                 repo=None, opener=None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers if headers is not None else DEFAULT_HEADERS)
        self.timeout = timeout
        self.repo = repo
        self._opener = opener if opener is not None else urllib.request.build_opener()
        self._sleep = sleep

    # ── 核心 ──

    def query(self, cypher, repo=None):
        """执行 cypher → list[dict]。任何失败抛 GraphAccessError（硬终止）。"""
        repo = repo or self.repo
        if not repo:
            raise GraphAccessError("repo 未指定（--repo 或 QTAG_GN_REPO）", kind="repo")
        payload = json.dumps({"cypher": cypher, "repo": repo}).encode()
        headers = {**self.headers, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        last_err = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            req = urllib.request.Request(self.base_url + "/query", data=payload,
                                         headers=headers, method="POST")
            try:
                resp = self._opener.open(req, timeout=self.timeout)
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                body = raw.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                ebody = ""
                try:
                    ebody = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                kind, retryable = _classify_http_error(e.code, ebody)
                last_err = GraphAccessError(
                    f"HTTP {e.code}: {(ebody or e.reason)[:300]}",
                    kind=kind, retryable=retryable, status=e.code)
                if retryable and attempt < len(RETRY_DELAYS):
                    self._sleep(RETRY_DELAYS[attempt])
                    continue
                raise last_err
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = GraphAccessError(f"网络错误: {e}", kind="network", retryable=True)
                if attempt < len(RETRY_DELAYS):
                    self._sleep(RETRY_DELAYS[attempt])
                    continue
                raise last_err
            return self._parse_result(body)
        raise last_err  # pragma: no cover（循环必然 return 或 raise）

    def _parse_result(self, body):
        """响应体 → list[dict]；形态不符抛 GraphAccessError(kind=empty)。"""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise GraphAccessError(f"响应非 JSON: {e}: {body[:120]}", kind="empty") from e
        if not isinstance(data, dict) or "result" not in data:
            raise GraphAccessError(
                f"响应缺 result 字段: {json.dumps(data)[:200]}", kind="empty")
        result = data["result"]
        if not isinstance(result, list):
            raise GraphAccessError(
                f"result 非 list: {type(result).__name__}", kind="empty")
        return result

    # ── 聚合下推的领域查询（L1 固化查询，L3 只取 rows） ──

    def count_symbols(self, repo=None):
        """survey 计数：文件/类/方法/调用边/测试目录文件 → 单行 dict。"""
        rows = self.query(
            "MATCH (f:File) WITH count(f) AS files "
            "MATCH (c:Class) WITH files, count(c) AS classes "
            "MATCH (m:Method) WITH files, classes, count(m) AS methods "
            "MATCH (a)-[r:CodeRelation]->(b:Method) WHERE r.type='CALLS' "
            "WITH files, classes, methods, count(r) AS calls "
            "MATCH (t:File) WHERE t.filePath STARTS WITH 'autotests/' "
            "RETURN files, classes, methods, calls, count(t) AS test_files",
            repo=repo)
        return rows[0] if rows else {}

    def method_skeleton(self, repo=None):
        """方法骨架全量：id/name/filePath/行号/参数数/可见性/返回类型/体量。

        content_bytes 用服务端 size() 取（上下文组装依据，免拉内容）。
        """
        return self.query(
            "MATCH (m:Method) "
            "RETURN m.id AS id, m.name AS name, m.filePath AS filePath, "
            "m.startLine AS startLine, m.endLine AS endLine, "
            "m.parameterCount AS parameterCount, m.isExported AS isExported, "
            "m.returnType AS returnType, size(coalesce(m.content,'')) AS content_bytes",
            repo=repo)

    def class_skeleton(self, repo=None):
        """类清单：id/name/filePath/行号/可见性。"""
        return self.query(
            "MATCH (c:Class) "
            "RETURN c.id AS id, c.name AS name, c.filePath AS filePath, "
            "c.startLine AS startLine, c.endLine AS endLine, "
            "c.isExported AS isExported",
            repo=repo)

    def call_indegree(self, repo=None, limit=None):
        """调用入度聚合（谁被调用最多）→ [{id, indegree}]，降序。"""
        sql = ("MATCH (src)-[r:CodeRelation]->(m:Method) WHERE r.type='CALLS' "
               "RETURN m.id AS id, count(r) AS indegree "
               "ORDER BY indegree DESC")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return self.query(sql, repo=repo)

    def test_edges(self, repo=None):
        """测试覆盖边：测试文件 → 被测方法（CALLS，测试目录源）。"""
        return self.query(
            "MATCH (tf)-[r:CodeRelation]->(m:Method) WHERE r.type='CALLS' "
            "AND (tf.filePath STARTS WITH 'tests/' OR tf.filePath STARTS WITH 'test/' "
            "OR tf.filePath STARTS WITH 'autotests/') "
            "RETURN m.id AS id, collect(DISTINCT tf.filePath) AS test_files, "
            "count(DISTINCT tf) AS test_count",
            repo=repo)

    def clusters(self, repo=None, min_symbols=5, limit=None):
        """功能聚类（Community 节点）按内聚度降序。"""
        sql = ("MATCH (n:Community) WHERE n.symbolCount >= " + str(int(min_symbols)) + " "
               "RETURN n.label AS label, n.heuristicLabel AS heuristicLabel, "
               "n.cohesion AS cohesion, n.symbolCount AS symbols "
               "ORDER BY cohesion DESC")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return self.query(sql, repo=repo)

    def processes(self, repo=None, limit=None):
        """执行流程（Process 节点）按步数降序。"""
        sql = ("MATCH (n:Process) "
               "RETURN n.label AS label, n.stepCount AS steps, "
               "n.entryPointId AS entryPointId, n.terminalId AS terminalId "
               "ORDER BY n.stepCount DESC")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return self.query(sql, repo=repo)

    def method_parents(self, repo=None):
        """方法 → 归属类型边：HAS_METHOD（类/结构体）+ DEFINES（文件自由函数）。"""
        return self.query(
            "MATCH (p)-[r:CodeRelation]->(m:Method) "
            "WHERE r.type IN ['HAS_METHOD','DEFINES'] "
            "RETURN m.id AS method_id, r.type AS edge_type, "
            "p.id AS parent_id, coalesce(p.name,'') AS parent_name, "
            "coalesce(p.filePath,'') AS parent_file",
            repo=repo)

    def file_contents(self, file_paths, repo=None):
        """File.content 批量拉取（全量存储，REST 纯 JSON 免截断）。

        IN 列表按 IN_CHUNK 分批；返回 {filePath: content}（无该文件则缺键）。
        """
        out = {}
        paths = list(dict.fromkeys(file_paths))  # 去重保序
        for i in range(0, len(paths), IN_CHUNK):
            chunk = paths[i:i + IN_CHUNK]
            # cypher 字符串字面量转义（反斜杠 + 引号；文件路径不含换行）
            literals = ", ".join(
                "'" + p.replace("\\", "\\\\").replace("'", "\\'") + "'"
                for p in chunk)
            rows = self.query(
                "MATCH (f:File) WHERE f.filePath IN [" + literals + "] "
                "RETURN f.filePath AS filePath, f.content AS content",
                repo=repo)
            for row in rows:
                if row.get("filePath") is not None:
                    out[row["filePath"]] = row.get("content") or ""
        return out

    def symbol_contents(self, method_ids, repo=None):
        """符号级 content 批量拉取（降级通道：切片失败时用；库内截 5000 字符）。"""
        out = {}
        ids = list(dict.fromkeys(method_ids))
        for i in range(0, len(ids), IN_CHUNK):
            chunk = ids[i:i + IN_CHUNK]
            literals = ", ".join(
                "'" + x.replace("\\", "\\\\").replace("'", "\\'") + "'"
                for x in chunk)
            rows = self.query(
                "MATCH (m:Method) WHERE m.id IN [" + literals + "] "
                "RETURN m.id AS id, m.content AS content",
                repo=repo)
            for row in rows:
                if row.get("id") is not None:
                    out[row["id"]] = row.get("content") or ""
        return out

    def methods_in_files(self, file_paths, repo=None):
        """按文件集合取方法（变更驱动 R5 入口）：变更文件 → 方法 id 清单。

        返回 [{id, file}]。与 method_neighbors 串联可得受影响 caller 集合。
        """
        out = []
        files = list(dict.fromkeys(file_paths))
        for i in range(0, len(files), IN_CHUNK):
            chunk = files[i:i + IN_CHUNK]
            literals = ", ".join(
                "'" + x.replace("\\", "\\\\").replace("'", "\\'") + "'"
                for x in chunk)
            out.extend(self.query(
                "MATCH (m:Method) WHERE m.filePath IN [" + literals + "] "
                "RETURN m.id AS id, m.filePath AS file",
                repo=repo))
        return out

    def method_neighbors(self, method_ids, repo=None):
        """一跳邻接（generate 阶段 §6）：谁调我（caller）/ 我调谁（callee）。

        返回 [{method_id, direction('caller'|'callee'), peer, peer_file}]。
        真机确认：CALLS 边直接挂在头文件声明节点上（sqlitehelper.h 实测），
        因此按 plan 里的方法 id（多为头节点）查即可。
        """
        out = []
        ids = list(dict.fromkeys(method_ids))
        for i in range(0, len(ids), IN_CHUNK):
            chunk = ids[i:i + IN_CHUNK]
            literals = ", ".join(
                "'" + x.replace("\\", "\\\\").replace("'", "\\'") + "'"
                for x in chunk)
            # callers：边方向 (src)->(m)
            out.extend(self.query(
                "MATCH (src)-[r:CodeRelation]->(m:Method) "
                "WHERE r.type='CALLS' AND m.id IN [" + literals + "] "
                "RETURN m.id AS method_id, 'caller' AS direction, "
                "src.name AS peer, coalesce(src.filePath,'') AS peer_file",
                repo=repo))
            # callees：边方向 (m)->(t)，t 限 Method 节点
            out.extend(self.query(
                "MATCH (m:Method)-[r:CodeRelation]->(t:Method) "
                "WHERE r.type='CALLS' AND m.id IN [" + literals + "] "
                "RETURN m.id AS method_id, 'callee' AS direction, "
                "t.name AS peer, coalesce(t.filePath,'') AS peer_file",
                repo=repo))
        return out


def slice_body(content, start_line, end_line):
    """按 1-based 行号切片方法体（File.content → 方法体，v2.3 精确路径）。"""
    if content is None or not start_line:
        return ""
    lines = content.split("\n")
    if start_line < 1 or end_line < start_line or start_line > len(lines):
        return ""
    end = min(end_line, len(lines))
    return "\n".join(lines[start_line - 1:end])


def count_branches(body):
    """cc_proxy：方法体分支计数（if/for/while/case/catch/&&/||/?: 各 +1）。

    确定性文本计数（v2.3：File.content 行切片后永远精确）。
    """
    if not body:
        return 0
    n = 0
    for kw in ("if", "for", "while", "catch"):
        n += len(re.findall(r"\b" + kw + r"\s*\(", body))
    # case 标签无括号（`case 1:`），switch 本身不计（分支数由 case 承载）
    n += len(re.findall(r"\bcase\b", body))
    n += body.count("&&") + body.count("||")
    n += body.count(" ? ")  # 三元（保守：带空格分隔）
    return n


# ── CLI ───────────────────────────────────────────────────────────────

def _client_from_args(args):
    headers = DEFAULT_HEADERS
    env_headers = os.environ.get("QTAG_GN_HEADERS")
    if env_headers:
        headers = json.loads(env_headers)
    base_url = os.environ.get("QTAG_GN_BASE_URL", DEFAULT_BASE_URL)
    return RestQueryClient(base_url=base_url, headers=headers,
                           repo=getattr(args, "repo", None) or os.environ.get("QTAG_GN_REPO"))


def _write_output(data, out_path):
    text = json.dumps(data, ensure_ascii=False)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"→ {out_path}（{len(json.dumps(data))} 字节）", file=sys.stderr)
    else:
        print(text)


def _cli_survey(client, args):
    stats = client.count_symbols()
    stats["top_clusters"] = client.clusters(limit=10)
    stats["top_processes"] = client.processes(limit=10)
    _write_output(stats, args.output)
    return 0


def _cli_file_content(client, args):
    files = client.file_contents(args.files)
    _write_output(files, args.output)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="graph-access.py",
        description="GitNexus REST 图谱访问层（L1 唯一数据通道）")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_repo_arg(p):
        p.add_argument("--repo", help="仓库名（默认 QTAG_GN_REPO）")

    def add_out_arg(p):
        p.add_argument("-o", "--output", help="输出 JSON 文件（缺省打印 stdout）")

    p = sub.add_parser("selftest", help="连通性自检：repo 可达 + 计数")
    add_repo_arg(p)
    p.set_defaults(func=lambda c, a: (_write_output(c.count_symbols(), a.output), 0)[1])
    add_out_arg(p)

    p = sub.add_parser("survey", help="测绘：计数 + 聚类 top + 流程 top")
    add_repo_arg(p)
    add_out_arg(p)
    p.set_defaults(func=_cli_survey)

    for name, method, help_ in (
            ("skeleton", "method_skeleton", "方法骨架全量（L3 plan 输入）"),
            ("classes", "class_skeleton", "类清单"),
            ("indegree", "call_indegree", "调用入度聚合（降序）"),
            ("testcov", "test_edges", "测试覆盖边"),
            ("parents", "method_parents", "方法归属边（类/结构体/自由函数）")):
        p = sub.add_parser(name, help=help_)
        add_repo_arg(p)
        add_out_arg(p)
        p.set_defaults(func=lambda c, a, m=method: (_write_output(getattr(c, m)(), a.output), 0)[1])

    p = sub.add_parser("clusters", help="功能聚类（按内聚度降序）")
    add_repo_arg(p)
    add_out_arg(p)
    p.add_argument("--min-symbols", type=int, default=5)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=lambda c, a: (_write_output(
        c.clusters(min_symbols=a.min_symbols, limit=a.limit), a.output), 0)[1])

    p = sub.add_parser("processes", help="执行流程（按步数降序）")
    add_repo_arg(p)
    add_out_arg(p)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=lambda c, a: (_write_output(
        c.processes(limit=a.limit), a.output), 0)[1])

    p = sub.add_parser("file-content", help="File.content 批量拉取（方法体原料）")
    add_repo_arg(p)
    add_out_arg(p)
    p.add_argument("files", nargs="+", help="文件路径（repo 内相对路径）")
    p.set_defaults(func=_cli_file_content)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    client = _client_from_args(args)
    try:
        return args.func(client, args)
    except GraphAccessError as e:
        hint = {
            "auth": "认证失败：检查 QTAG_GN_HEADERS",
            "repo": "仓库未解析或通道不存在：检查 --repo 拼写 / list_repos 确认已索引",
            "query": "cypher 语法/语义错误（L1 固化查询不应出现，请报 bug）",
            "network": "网络不可达：检查 VPN / 代理",
            "empty": "响应形态异常：服务端可能已升级，请人工核查 /api/query",
            "http": "服务端错误：稍后重试",
        }.get(e.kind, "")
        print(f"graph-access: 硬终止（kind={e.kind}"
              + (f" status={e.status}" if e.status else "") + f"）: {e}", file=sys.stderr)
        if hint:
            print(f"提示: {hint}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
