#!/usr/bin/env python3
"""
fetch_mcp_data.py — 采集知识图谱数据，保存为 JSON 供 scan_inventory.py 消费

**推荐使用 `collect_all_methods.py`** 直接通过 HTTP MCP 全自动收集：

  python3 collect_all_methods.py \
    --project home-uos-service-codebase-repos-dde-file-manager \
    --file-pattern "src/**" \
    --output ${test_dir}/all_methods.json

  # 然后构建 mcp_dump.json（P75 + 继承数据）
  python3 -c "... 见 importance_inventory.md 示例 ..."

  # 最后运行评分
  python3 scan_inventory.py --mcp-dump mcp_dump.json --output .ut-inventory.json

本文件作为格式参考和手动回退方案，记录 MCP 数据采集的完整步骤。
"""

# MCP 服务器配置:
# - HTTP 直连: http://10.8.12.80:13626/mcp (JSON-RPC 2.0) ← collect_all_methods.py 使用
# - CLI 模式: codebase-memory-mcp cli <tool> '{json}'
# - pi 网关: mcp({tool: "...", args: {...}})
#
# Agent 需要执行的 MCP 调用清单:
#
# 1. search_graph(label="Method", file_pattern="src/**", limit=2000) 分页 → 全量方法
#    ⚠️ 用 file_pattern 排除 3rdparty，limit=2000 减少分页次数
#    或直接用 collect_all_methods.py 自动完成（推荐）
# 2. ⚠️ P75 客户端计算（MCP 不支持 percentileCont）:
#    - 收集所有非测试方法的 in_degree
#    - 排除 in_degree=0 后计算 P75
#    - 写入 mcp_data.in_degree_p75_nonzero
# 3. DBus/并发继承检测（search_graph 不返回 base_classes，但 query_graph 可以）:
#    - 方案 A：query_graph 直接筛选（推荐）
#    - 方案 B：候选类名模式筛选 + get_code_snippet 确认（备用）
#    - 写入 mcp_data.dbus_classes, mcp_data.concurrent_classes
# 4. 对已确认的 DBus 类调 get_code_snippet → 解析 Q_SLOTS/Q_SIGNALS →
#    写入 mcp_data.dbus_slots, mcp_data.q_invokables, mcp_data.q_plugins
#
# 最终 mcp_data JSON 结构:
# {
#   "classes": [...],
#   "methods": [...],
#   "functions": [...],
#   "in_degree_p75_nonzero": 2,    # ⚠️ 基于非零 in_degree 计算
#   "in_degree_p75": 1,            # (弃用) 基于全值计算，仅供参考
#   "dbus_classes": [...],
#   "concurrent_classes": [...],
#   "dbus_slots": {"ClassName": ["method1", "method2"]},
#   "dbus_signals": {"ClassName": ["signal1"]},
#   "q_invokables": {"ClassName": ["method1"]},
#   "q_plugins": {"ClassName": true}
# }
