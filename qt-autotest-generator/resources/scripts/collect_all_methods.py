#!/usr/bin/env python3
"""
collect_all_methods.py — 直接通过 HTTP MCP 协议全量收集项目方法

绕过 pi MCP 网关，直接调用远程 codebase-memory-mcp HTTP 接口，
自动分页收集所有 Method 节点，保存为 JSON 文件。

用法:
  python3 collect_all_methods.py --project <name> [--file-pattern <glob>] \
    [--output <path>] [--limit 2000]

示例:
  # 收集 dde-file-manager 自有代码（排除 3rdparty）
  python3 collect_all_methods.py \
    --project home-uos-service-codebase-repos-dde-file-manager \
    --file-pattern "src/**" \
    --output /tmp/inventory-test/dde-file-manager/all_methods.json

  # 收集 deepin-reader 自有代码
  python3 collect_all_methods.py \
    --project home-uos-service-codebase-repos-deepin-reader \
    --file-pattern "reader/**" \
    --output /tmp/inventory-test/deepin-reader/all_methods.json
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

MCP_URL = "http://10.8.12.80:13626/mcp"


class MCPClient:
    """Minimal MCP HTTP client."""

    def __init__(self, url=MCP_URL, timeout=120):
        self.url = url
        self.timeout = timeout
        self.session_id = None
        self._id = 0

    def _next_id(self):
        self._id += 1
        return self._id

    def _post(self, payload, expect_response=True):
        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.url, data=data, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"HTTP {e.code}: {body[:500]}") from e
        if not expect_response:
            return None
        body = resp.read().decode()
        result = json.loads(body)
        if "error" in result:
            raise RuntimeError(f"RPC error: {json.dumps(result['error'], ensure_ascii=False)[:500]}")
        return result

    def initialize(self):
        """Initialize MCP session."""
        resp = self._post({
            "jsonrpc": "2.0", "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "collect-all-methods", "version": "1.0"}
            },
            "id": self._next_id()
        })
        self.session_id = resp.get("_headers", {}).get("Mcp-Session-Id") if hasattr(resp, "_headers") else None
        # urllib doesn't expose headers on parsed dict; re-fetch via raw
        # Actually we need the session ID from the response headers
        return resp

    def initialize_with_session(self):
        """Initialize and capture session ID from response headers."""
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0", "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "collect-all-methods", "version": "1.0"}
            },
            "id": self._next_id()
        }
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers)
        resp = urllib.request.urlopen(req, timeout=self.timeout)
        self.session_id = resp.headers.get("Mcp-Session-Id")
        body = resp.read().decode()
        result = json.loads(body)
        if "error" in result:
            raise RuntimeError(f"Initialize error: {result['error']}")
        # Send initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        req2 = urllib.request.Request(self.url, data=json.dumps(notif).encode(),
            headers={**headers, "Mcp-Session-Id": self.session_id})
        urllib.request.urlopen(req2, timeout=self.timeout)
        return result

    def call_tool(self, name, arguments):
        """Call an MCP tool and return parsed result."""
        payload = {
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": self._next_id()
        }
        result = self._post(payload)
        # MCP returns content as text blocks
        content = result.get("result", {}).get("content", [])
        for block in content:
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return block["text"]
        return content


def collect_methods(project, file_pattern=None, limit=2000, output_path=None):
    """Collect all Method nodes via paginated search_graph."""
    client = MCPClient()
    print(f"🔗 Connecting to {MCP_URL}...")
    client.initialize_with_session()
    print(f"✅ Session established: {client.session_id[:12]}...")

    all_methods = []
    offset = 0
    page = 0

    # First call to get total
    args = {"project": project, "label": "Method", "limit": limit, "offset": 0}
    if file_pattern:
        args["file_pattern"] = file_pattern

    print(f"\n📊 Collecting Method nodes for: {project}")
    if file_pattern:
        print(f"   file_pattern: {file_pattern}")
    print(f"   page size: {limit}")

    while True:
        page += 1
        args["offset"] = offset
        retry = 0
        while retry < 3:
            try:
                data = client.call_tool("search_graph", args)
                break
            except Exception as e:
                retry += 1
                print(f"   ⚠️  Page {page} error (retry {retry}/3): {e}")
                if retry >= 3:
                    raise
                time.sleep(2 * retry)
                # Re-initialize if session expired
                try:
                    client.initialize_with_session()
                except Exception:
                    pass

        results = data.get("results", [])
        total = data.get("total", 0)
        has_more = data.get("has_more", False)

        all_methods.extend(results)
        elapsed = 0
        print(f"   Page {page}: +{len(results)} methods (offset={offset}, total={total}, "
              f"collected={len(all_methods)})")

        if not has_more or len(results) == 0:
            break

        offset += limit

    print(f"\n✅ Collection complete: {len(all_methods)} methods (from total={total})")

    # Save
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(all_methods, f, ensure_ascii=False)
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"💾 Saved to {output_path} ({size_mb:.1f} MiB)")

    return all_methods


def main():
    parser = argparse.ArgumentParser(description="Collect all Method nodes via MCP HTTP")
    parser.add_argument("--project", required=True, help="MCP project name")
    parser.add_argument("--file-pattern", default=None,
                        help="Glob to filter source dirs (e.g. 'src/**', 'reader/**')")
    parser.add_argument("--output", "-o", default=None,
                        help="Output JSON path (default: /tmp/inventory/<project>/all_methods.json)")
    parser.add_argument("--limit", type=int, default=2000,
                        help="Page size (default: 2000, max recommended)")
    args = parser.parse_args()

    output = args.output or f"/tmp/inventory/{args.project.split('.')[-1]}/all_methods.json"
    collect_methods(args.project, args.file_pattern, args.limit, output)


if __name__ == "__main__":
    main()
