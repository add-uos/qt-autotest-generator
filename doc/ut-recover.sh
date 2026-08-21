#!/usr/bin/env bash
#
# ut-recover.sh — 自动化单元测试 Issue 持续监控与恢复脚本
#
# 用途：持续监控 UT issue 的执行状态，任务因外部原因中断后自动恢复，
#       循环直到覆盖率达标。如果已有任务在运行，则只监控不重复开。
#
# 用法：
#   ./ut-recover.sh <issue-id> [选项]
#
# 选项：
#   --agent <agent-id>   指定执行智能体（默认自动选 idle 的 UT 智能体）
#   --max-retries <N>    最大 rerun 次数（默认 5）
#   --wait <seconds>     每次重试前等待秒数（默认 60，给 API 恢复时间）
#   --poll <seconds>     监控轮询间隔（默认 60）
#   --workspace <id>     Workspace ID（默认 v25 功能线）
#   --dry-run            只检测不执行，展示当前状态
#
# 示例：
#   ./ut-recover.sh b90fa8da-2cc4-470a-a58d-bd7b8e3bb8a5
#   ./ut-recover.sh b90fa8da-2cc4-470a-a58d-bd7b8e3bb8a5 --agent c1d54f6d-fbe0-45d6-b6dd-61851ddb6917
#   ./ut-recover.sh b90fa8da-2cc4-470a-a58d-bd7b8e3bb8a5 --max-retries 3 --poll 30
#

set -euo pipefail

# ─── 默认配置 ───
DEFAULT_WS="b982c611-c032-4874-ac62-0f66ae001f2f"
DEFAULT_MAX_RETRIES=5
DEFAULT_WAIT=60
DEFAULT_POLL=60

# UT 智能体列表（按优先级排序）
UT_AGENTS=(
  "e750ad9d-b6f1-41a4-9153-694a4601f66b"  # 自动化单元测试
  "8d7c399a-715a-4361-9269-abeb74a4d326"  # 自动化单元测试-P2
  "c1d54f6d-fbe0-45d6-b6dd-61851ddb6917"  # 自动化单元测试P3
)

# ─── 颜色 ───
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "$(date '+%H:%M:%S') ${BLUE}[INFO]${NC}  $*"; }
warn() { echo -e "$(date '+%H:%M:%S') ${YELLOW}[WARN]${NC}  $*"; }
ok()   { echo -e "$(date '+%H:%M:%S') ${GREEN}[OK]${NC}    $*"; }
fail() { echo -e "$(date '+%H:%M:%S') ${RED}[FAIL]${NC}  $*"; }

# ─── 参数解析 ───
ISSUE_ID=""
AGENT_ID=""
MAX_RETRIES=$DEFAULT_MAX_RETRIES
WAIT_SECONDS=$DEFAULT_WAIT
POLL_SECONDS=$DEFAULT_POLL
WS_ID=$DEFAULT_WS
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)       AGENT_ID="$2"; shift 2 ;;
    --max-retries) MAX_RETRIES="$2"; shift 2 ;;
    --wait)        WAIT_SECONDS="$2"; shift 2 ;;
    --poll)        POLL_SECONDS="$2"; shift 2 ;;
    --workspace)   WS_ID="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=true; shift ;;
    -*)            echo "未知选项: $1"; exit 1 ;;
    *)
      if [[ -z "$ISSUE_ID" ]]; then
        ISSUE_ID="$1"
      else
        echo "未知参数: $1"; exit 1
      fi
      shift ;;
  esac
done

if [[ -z "$ISSUE_ID" ]]; then
  echo "用法: $0 <issue-id> [--agent <id>] [--max-retries <N>] [--wait <s>] [--poll <s>] [--workspace <id>] [--dry-run]"
  exit 1
fi

# ─── 工具函数 ───

# 获取 issue 信息
issue_get() {
  multica issue get "$ISSUE_ID" --workspace-id "$WS_ID" --output json 2>/dev/null
}

# 获取最新运行的任务
latest_task() {
  multica issue runs "$ISSUE_ID" --workspace-id "$WS_ID" --output json 2>/dev/null | \
    jq '.[0] | {id, status, started_at, error: (.error[:150] // null)}'
}

# 从 issue 评论中提取覆盖率数字（兼容多种格式）
# 格式1: "当前 **74.4%（1779/2392 函数）**"
# 格式2: "当前函数覆盖率：74.4%"
# 格式3: "函数覆盖率 74.4%"
extract_coverage() {
  multica issue comment list "$ISSUE_ID" --workspace-id "$WS_ID" --output json 2>/dev/null | \
    jq -r '[.[] | .content | capture("当前.*?\\*\\*?(?<cov>[0-9.]+)%"; "g")] | last?.cov // "N/A"'
}

# 从 issue 标题提取覆盖率目标
extract_target() {
  multica issue get "$ISSUE_ID" --workspace-id "$WS_ID" --output json 2>/dev/null | \
    jq -r '.title | capture("UT-.*-(?<target>[0-9]+)%$")?.target // "80"'
}

# 找一个空闲的 UT 智能体
find_idle_agent() {
  for aid in "${UT_AGENTS[@]}"; do
    local status
    status=$(multica agent get "$aid" --workspace-id "$WS_ID" --output json 2>/dev/null | jq -r '.status')
    if [[ "$status" == "idle" ]]; then
      echo "$aid"
      return
    fi
  done
  echo ""
}

# 获取智能体名称
agent_name() {
  multica agent get "$1" --workspace-id "$WS_ID" --output json 2>/dev/null | jq -r '.name'
}

# 检查覆盖率是否达标
check_coverage_met() {
  local cov="$1"
  local target="$2"
  if [[ "$cov" != "N/A" ]] && [[ "$cov" != "null" ]] && [[ -n "$cov" ]]; then
    awk "BEGIN {exit !($cov >= $target)}" && return 0
  fi
  return 1
}

# ─── 主流程 ───

echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  自动化单元测试 Issue 监控与恢复脚本       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# 1. 检查 issue 是否存在
log "检查 Issue $ISSUE_ID ..."
ISSUE_INFO=$(issue_get)
if [[ -z "$ISSUE_INFO" ]]; then
  fail "Issue $ISSUE_ID 不存在或无法访问"
  exit 1
fi

ISSUE_TITLE=$(echo "$ISSUE_INFO" | jq -r '.title')
ISSUE_STATUS=$(echo "$ISSUE_INFO" | jq -r '.status')
log "Issue 标题: $ISSUE_TITLE"
log "Issue 状态: $ISSUE_STATUS"

# 2. 提取覆盖率目标
TARGET=$(extract_target)
log "目标覆盖率: ${TARGET}%"

# 3. 检查当前覆盖率
CURRENT_COV=$(extract_coverage)
log "当前覆盖率: ${CURRENT_COV}%"

# 4. 检查是否已达标
if check_coverage_met "$CURRENT_COV" "$TARGET"; then
  ok "覆盖率 ${CURRENT_COV}% ≥ 目标 ${TARGET}%，已达标！"
  exit 0
fi

# 5. dry-run 模式到此结束
if $DRY_RUN; then
  echo ""
  log "=== DRY RUN 模式，不执行恢复 ==="
  log "当前覆盖率: ${CURRENT_COV}%"
  log "目标覆盖率: ${TARGET}%"

  TASK_INFO=$(latest_task)
  TASK_STATUS=$(echo "$TASK_INFO" | jq -r '.status')
  if [[ "$TASK_STATUS" == "running" || "$TASK_STATUS" == "queued" ]]; then
    log "有任务在运行，监控模式会等待其完成"
  else
    log "无任务在运行，需要 rerun"
  fi
  exit 0
fi

# ─── 持续监控循环 ───

log "开始持续监控 (轮询间隔 ${POLL_SECONDS}s, 最大 rerun ${MAX_RETRIES} 次, 重试等待 ${WAIT_SECONDS}s)"
echo ""

RERUN_COUNT=0

while true; do
  # ── 检查覆盖率 ──
  CURRENT_COV=$(extract_coverage)
  if check_coverage_met "$CURRENT_COV" "$TARGET"; then
    ok "覆盖率 ${CURRENT_COV}% ≥ 目标 ${TARGET}%，已达标！🎉"
    exit 0
  fi

  # ── 检查当前任务状态 ──
  TASK_INFO=$(latest_task)
  TASK_ID=$(echo "$TASK_INFO" | jq -r '.id')
  TASK_STATUS=$(echo "$TASK_INFO" | jq -r '.status')
  TASK_ERROR=$(echo "$TASK_INFO" | jq -r '.error // "none"')

  if [[ "$TASK_STATUS" == "running" || "$TASK_STATUS" == "queued" ]]; then
    # 有任务在跑 → 只监控，不重复开
    log "任务 ${TASK_ID:0:8} 运行中，等待... (当前覆盖率: ${CURRENT_COV}%)"
    sleep "$POLL_SECONDS"
    continue
  fi

  # ── 任务已结束（completed / failed / cancelled） ──

  # 再次检查覆盖率（任务刚结束可能更新了评论）
  CURRENT_COV=$(extract_coverage)
  if check_coverage_met "$CURRENT_COV" "$TARGET"; then
    ok "覆盖率 ${CURRENT_COV}% ≥ 目标 ${TARGET}%，已达标！🎉"
    exit 0
  fi

  # 判断是否需要 rerun
  case "$TASK_STATUS" in
    completed)
      # 正常完成但未达标 → skill 可能还有更多类要处理，继续 rerun
      warn "任务完成但覆盖率 ${CURRENT_COV}% 未达目标 ${TARGET}%"
      ;;
    failed)
      # 判断是否可重试
      if echo "$TASK_ERROR" | grep -qi "500\|timeout\|i/o\|cancelled\|stream\|dial tcp"; then
        warn "任务失败（外部错误）: ${TASK_ERROR:0:100}"
      else
        fail "任务失败（非可重试错误）: ${TASK_ERROR:0:100}"
        exit 1
      fi
      ;;
    cancelled)
      warn "任务被取消"
      ;;
    *)
      warn "任务状态未知: $TASK_STATUS"
      ;;
  esac

  # ── rerun ──
  RERUN_COUNT=$((RERUN_COUNT + 1))
  if (( RERUN_COUNT > MAX_RETRIES )); then
    fail "已达最大 rerun 次数 ${MAX_RETRIES}，覆盖率 ${CURRENT_COV}% 仍未达标 ${TARGET}%"
    fail "建议手动检查: multica issue get $ISSUE_ID --workspace-id $WS_ID"
    exit 1
  fi

  echo ""
  echo -e "${CYAN}── 第 ${RERUN_COUNT}/${MAX_RETRIES} 次 rerun ──${NC}"

  # 选择智能体
  if [[ -z "$AGENT_ID" ]]; then
    SELECTED_AGENT=$(find_idle_agent)
    if [[ -z "$SELECTED_AGENT" ]]; then
      warn "所有 UT 智能体都在工作中，等待 ${WAIT_SECONDS}s ..."
      sleep "$WAIT_SECONDS"
      RERUN_COUNT=$((RERUN_COUNT - 1))  # 不算次数
      continue
    fi
  else
    SELECTED_AGENT="$AGENT_ID"
  fi

  ANAME=$(agent_name "$SELECTED_AGENT")
  log "使用智能体: $ANAME ($SELECTED_AGENT)"

  # 等待一下再 rerun（给 API 恢复时间）
  if [[ "$TASK_STATUS" == "failed" || "$TASK_STATUS" == "cancelled" ]]; then
    log "等待 ${WAIT_SECONDS}s 后重试..."
    sleep "$WAIT_SECONDS"
  fi

  # 执行 rerun
  log "执行 rerun ..."
  RERUN_RESULT=$(multica issue rerun "$ISSUE_ID" --workspace-id "$WS_ID" --output json 2>&1) || {
    warn "rerun 失败: $RERUN_RESULT"
    sleep "$WAIT_SECONDS"
    RERUN_COUNT=$((RERUN_COUNT - 1))  # 不算次数
    continue
  }

  NEW_TASK=$(echo "$RERUN_RESULT" | jq -r '.id // "unknown"')
  log "新任务已创建: ${NEW_TASK:0:8}"

  # 回到循环顶部，开始监控这个新任务
  sleep "$POLL_SECONDS"
done
