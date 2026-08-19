#!/usr/bin/env bash
#
# build-ut-squad.sh — 单元测试小队建队脚本（数组传参版，避免 shell 转义）
#
# 用途：在 multica 创建独立「单元测试小队-v2」4 角色 + 2 skill + 小队编组。
#       幂等：已存在则跳过，可重复执行。
#
# 用法：
#   ./build-ut-squad.sh              # 执行建队
#   ./build-ut-squad.sh --dry-run    # 只打印，不执行
#   ./build-ut-squad.sh --teardown   # 拆队（归档 agent + 删 squad，skill 保留）
#

set -euo pipefail

# ─── 配置 ───
WS="b982c611-c032-4874-ac62-0f66ae001f2f"
RUNTIME_ID="137f1a6d-3ef5-4b95-b890-ceacf37f7c30"
MODEL="local/glm-5.2"
SQUAD_NAME="单元测试小队-v2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROLES_DIR="$SCRIPT_DIR/roles"
SKILLS_DIR="$SCRIPT_DIR/skills"

DRY_RUN=false; TEARDOWN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true
[[ "${1:-}" == "--teardown" ]] && TEARDOWN=true

# ─── 颜色 ───
G='\033[0;32m'; Y='\033[1;33m'; C='\033[0;36m'; R='\033[0;31m'; N='\033[0m'
log()  { echo -e "$(date '+%H:%M:%S') ${C}[INFO]${N}  $*"; }
ok()   { echo -e "$(date '+%H:%M:%S') ${G}[OK]${N}    $*"; }
warn() { echo -e "$(date '+%H:%M:%S') ${Y}[WARN]${N}  $*"; }
fail() { echo -e "$(date '+%H:%M:%S') ${R}[FAIL]${N}  $*"; }

# ─── 执行函数：直接调用，不用 eval ───
# 用法：mc_run multica agent create --name xxx --instructions "$instr"
mc_run() {
  if $DRY_RUN; then
    printf "  ${Y}[DRY]${N} "; printf '%q ' "$@"; echo
  else
    "$@"
  fi
}

# ─── 查询函数 ───
agent_id_by_name() {
  multica agent list --workspace-id "$WS" --output json 2>/dev/null \
    | jq -r --arg n "$1" '.[] | select(.name==$n) | .id' | head -1
}
skill_id_by_name() {
  multica skill list --workspace-id "$WS" --output json 2>/dev/null \
    | jq -r --arg n "$1" '.[] | select(.name==$n) | .id' | head -1
}
squad_id_by_name() {
  multica squad list --workspace-id "$WS" --output json 2>/dev/null \
    | jq -r --arg n "$1" '.[] | select(.name==$n) | .id' | head -1
}

# ─── 角色定义 ───
declare -A ROLE_IDS
declare -A SKILL_IDS
CREATED_AGENTS=()

# ─── 建队 ───
build() {
  echo -e "${C}╔════════ 单元测试小队建队 ════════╗${N}"
  log "WS=$WS | Runtime=$RUNTIME_ID | Model=$MODEL | Squad=$SQUAD_NAME"
  echo ""

  # ── 1. 创建 4 个角色 agent ──
  log "【1/3】创建角色智能体"
  create_role() {
    local name="$1" instr_file="$2" desc="$3" thinking="$4" cc="$5"
    local existing; existing=$(agent_id_by_name "$name")
    if [[ -n "$existing" ]]; then
      ok "已存在，跳过: $name ($existing)"; ROLE_IDS[$name]=$existing; return
    fi
    log "创建: $name (thinking=$thinking, cc=$cc)"
    local instr; instr=$(cat "$instr_file")
    local aid; aid=$(mc_run multica agent create \
      --workspace-id "$WS" \
      --name "$name" \
      --description "$desc" \
      --model "$MODEL" \
      --thinking-level "$thinking" \
      --max-concurrent-tasks "$cc" \
      --runtime-id "$RUNTIME_ID" \
      --instructions "$instr" | jq -r '.id' 2>/dev/null)
    if [[ -n "$aid" && "$aid" != "null" ]]; then
      ok "已创建: $name ($aid)"; ROLE_IDS[$name]=$aid; CREATED_AGENTS+=("$aid")
    else
      fail "创建失败: $name"
    fi
  }
  create_role "UT-队长规划" "$ROLES_DIR/ut-leader.md"  "单元测试小队入口与路由：解析输入形态、锁定流程基线、设定覆盖率双目标、分级确认豁免" medium 2
  create_role "UT-广度补全" "$ROLES_DIR/ut-breadth.md" "单元测试广度阶段：每方法≥1用例，有效函数覆盖率=100%（含统计豁免候选）" medium 2
  create_role "UT-深度补全" "$ROLES_DIR/ut-depth.md"   "单元测试深度阶段：lcov缺口驱动+源码理解，行覆盖率≥90%，强制非trivial断言" high 1
  create_role "UT-验证审查" "$ROLES_DIR/ut-verifier.md" "单元测试质量门禁：跑项目统计脚本核验双门禁+ASAN+断言lint，产出patch.gz" high 2
  echo ""

  # ── 2. 创建 2 个新 skill ──
  log "【2/3】创建 skill"
  create_skill() {
    local name="$1" content_file="$2" desc="$3"
    local existing; existing=$(skill_id_by_name "$name")
    if [[ -n "$existing" ]]; then
      ok "已存在，跳过: $name ($existing)"; SKILL_IDS[$name]=$existing; return
    fi
    log "创建: $name"
    local sid; sid=$(mc_run multica skill create \
      --workspace-id "$WS" \
      --name "$name" \
      --description "$desc" \
      --content-file "$content_file" | jq -r '.id' 2>/dev/null)
    if [[ -n "$sid" && "$sid" != "null" ]]; then
      ok "已创建: $name ($sid)"; SKILL_IDS[$name]=$sid
    else
      fail "创建失败: $name"
    fi
  }
  create_skill "ut-depth-enhancer"  "$SKILLS_DIR/ut-depth-enhancer/SKILL.md"  "单元测试深度补全：lcov缺口驱动+源码理解+6种用例设计技术，行覆盖率≥90%"
  create_skill "ut-coverage-verifier" "$SKILLS_DIR/ut-coverage-verifier/SKILL.md" "覆盖率统计与门禁验证：封装项目脚本，双门禁+ASAN+断言lint+patch.gz"
  echo ""

  # ── 3. 创建小队 + 注入路由 + 加成员 ──
  log "【3/3】创建小队"
  local leader_id="${ROLE_IDS[UT-队长规划]:-}"
  if [[ -z "$leader_id" ]]; then fail "队长未创建，无法组队"; exit 1; fi

  local sid; sid=$(squad_id_by_name "$SQUAD_NAME")
  if [[ -n "$sid" ]]; then
    ok "小队已存在，复用: $SQUAD_NAME ($sid)"
  else
    log "创建小队: $SQUAD_NAME (leader=UT-队长规划/$leader_id)"
    sid=$(mc_run multica squad create \
      --workspace-id "$WS" \
      --name "$SQUAD_NAME" \
      --leader "$leader_id" \
      --description "独立单元测试小队：广度(函数覆盖率100%含豁免)+深度(行覆盖率≥90%)双达标，patch.gz交付" \
      | jq -r '.id' 2>/dev/null)
    if [[ -z "$sid" || "$sid" == "null" ]]; then fail "小队创建失败"; exit 1; fi
    ok "已创建小队: $SQUAD_NAME ($sid)"
  fi

  # 注入路由指令（squad update 支持 --instructions）
  log "注入小队路由指令"
  local squad_instr; squad_instr=$(cat "$SCRIPT_DIR/squad-instructions.md")
  mc_run multica squad update "$sid" --workspace-id "$WS" --instructions "$squad_instr" \
    && ok "路由指令已注入" || warn "注入失败，需手动贴 squad-instructions.md"

  # 加入成员（leader 已在 create 时指定，补齐其余 3 个角色）
  log "加入小队成员"
  for mname in "UT-广度补全" "UT-深度补全" "UT-验证审查"; do
    local mid="${ROLE_IDS[$mname]:-}"
    if [[ -n "$mid" ]]; then
      mc_run multica squad member add "$sid" --workspace-id "$WS" --member-id "$mid" --role member 2>/dev/null \
        && ok "成员已加入: $mname" || warn "$mname 加入需手动（Web UI）"
    fi
  done
  echo ""

  # ── 汇总 ──
  echo -e "${C}╔════════ 建队完成 ════════╗${N}"
  log "角色:"; for n in UT-队长规划 UT-广度补全 UT-深度补全 UT-验证审查; do echo "    $n = ${ROLE_IDS[$n]}"; done
  log "skill:"; for n in ut-depth-enhancer ut-coverage-verifier; do echo "    $n = ${SKILL_IDS[$n]}"; done
  log "小队: $SQUAD_NAME = $sid"
  echo ""
  warn "后续手动步骤（CLI 不支持 skill 绑定，需 Web UI）："
  echo "  给角色绑定 skill："
  echo "    - UT-广度补全 ← qt-autotest-generator"
  echo "    - UT-深度补全 ← ut-depth-enhancer"
  echo "    - UT-验证审查 ← ut-coverage-verifier"
  echo "    - UT-队长规划 ← ut-coverage-verifier(baseline) + send-wecom-webhook"
  echo "  确认 send-wecom-webhook 可用；若 member add 未生效，Web UI 检查成员列表"
}

# ─── 拆队 ───
teardown() {
  echo -e "${R}╔════════ 拆队 ════════╗${N}"
  for n in UT-队长规划 UT-广度补全 UT-深度补全 UT-验证审查; do
    local aid; aid=$(agent_id_by_name "$n")
    if [[ -n "$aid" ]]; then
      log "归档: $n ($aid)"
      mc_run multica agent archive "$aid" --workspace-id "$WS"
    fi
  done
  local sid; sid=$(squad_id_by_name "$SQUAD_NAME")
  if [[ -n "$sid" ]]; then
    log "删除小队: $SQUAD_NAME ($sid)"
    mc_run multica squad delete "$sid" --workspace-id "$WS"
  fi
  ok "拆队完成（skill 保留）"
}

if $TEARDOWN; then teardown; else build; fi
