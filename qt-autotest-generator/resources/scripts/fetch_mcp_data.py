#!/usr/bin/env python3
"""
fetch_mcp_data.py — 采集知识图谱数据，保存为 JSON 供 scan_inventory.py 消费

本脚本由 Agent 在对话中通过 MCP 工具手动调用，结果写入 JSON 文件。
之后 scan_inventory.py --mcp-dump 读取该文件生成 .ut-inventory.json。

用法（需要 Agent 手动触发 MCP 调用后写入）:
  python3 fetch_mcp_data.py --project <name> --output <path>
  
  然后由 Agent 将 MCP 查询结果按格式追加到 JSON 文件中。
"""

# 本文件仅作为格式参考，实际 MCP 调用由 Agent 在对话中执行
# Agent 需要执行的 MCP 调用清单:
#
# 1. search_graph(label="Class") 分页 → 全量类 → 写入 mcp_data.classes
# 2. search_graph(label="Method", file_pattern="src/**") 分页 → 源码方法 → mcp_data.methods
#    ⚠️ 用 file_pattern 排除 3rdparty：大项目（如 deepin-reader 含 pdfium）
#    可从 7780 降至 1098 方法，避免收集无用 3rdparty 方法。
#    先探测项目源码目录（src/、reader/ 等），设置 file_pattern 过滤。
# 3. search_graph(label="Function", file_pattern="src/**") 分页 → 源码函数 → mcp_data.functions
# 4. ⚠️ P75 客户端计算（MCP 不支持 percentileCont）:
#    - 收集所有非测试方法的 in_degree
#    - 排除 in_degree=0 后计算 P75
#    - 写入 mcp_data.in_degree_p75_nonzero
# 5. DBus/并发继承检测（search_graph 不返回 base_classes，但 query_graph 可以）:
#    - 方案 A：query_graph 直接筛选（推荐）
#    - 方案 B：候选类名模式筛选 + get_code_snippet 确认（备用）
#    - 写入 mcp_data.dbus_classes, mcp_data.concurrent_classes
# 6. 对已确认的 DBus 类调 get_code_snippet → 解析 Q_SLOTS/Q_SIGNALS →
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
