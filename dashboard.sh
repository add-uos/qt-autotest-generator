#!/usr/bin/env bash
# dashboard.sh — UT Cockpit (dashboard-server.py) 启动/停止/状态管理
# 用法:
#   ./dashboard.sh start     启动服务（已在运行则提示）
#   ./dashboard.sh stop      停止服务（PID 文件 → 端口 → 进程名 三重兜底）
#   ./dashboard.sh restart   重启
#   ./dashboard.sh status    查看运行状态
#   ./dashboard.sh log [n|-f]  查看日志尾部（默认 40 行，-f 跟随）
# 从任意目录执行均可；进程匹配按脚本名+python，不受启动时相对/绝对路径影响。

set -u

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 自动探测服务目录：本目录 或 assets/ut-inventory-editor/
if   [ -f "$BASE_DIR/scripts/dashboard-server.py" ]; then
  APP_DIR="$BASE_DIR"
elif [ -f "$BASE_DIR/assets/ut-inventory-editor/scripts/dashboard-server.py" ]; then
  APP_DIR="$BASE_DIR/assets/ut-inventory-editor"
else
  printf '\033[31m✗ 找不到 dashboard-server.py（在 %s 下）\033[0m\n' "$BASE_DIR" >&2
  exit 1
fi
SERVER="$APP_DIR/scripts/dashboard-server.py"
LOG_FILE="/tmp/dashboard-server.log"
PORT_CFG="$APP_DIR/config.json"

# 端口: 环境变量 UTIE_DASH_PORT > config.json > 默认 8765
get_port() {
  if [ -n "${UTIE_DASH_PORT:-}" ]; then echo "$UTIE_DASH_PORT"; return; fi
  python3 -c "
import json
try:
    c = json.load(open('$PORT_CFG'))
    print(c.get('server', {}).get('port', 8765))
except Exception:
    print(8765)" 2>/dev/null || echo 8765
}

PORT="$(get_port)"
PID_FILE="/tmp/utie-dashboard-$PORT.pid"

# 找出所有服务进程 PID（comm 必须是 python*，避免误杀命令行里恰好含脚本名的 shell）
service_pids() {
  ps -eo pid=,comm=,args= 2>/dev/null \
    | awk '$2 ~ /^python/ && index($0, "dashboard-server.py") > 0 {print $1}'
}

port_pid() {
  ss -tlnp 2>/dev/null | grep ":$PORT " \
    | grep -oP 'pid=\K[0-9]+' | head -1
}

is_alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

say()  { printf '%s\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
err()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }

do_start() {
  local pids; pids="$(service_pids)"
  if [ -n "$pids" ]; then
    ok "已在运行 (PID $(echo $pids | tr '\n' ' '))，如需重启请用 restart"
    exit 0
  fi
  if [ -n "$(port_pid)" ]; then
    warn "端口 $PORT 被非服务进程占用 (PID $(port_pid))，先处理该进程再启动"
    exit 1
  fi
  # 日志超过 5MB 轮转，避免无限增长
  if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    mv -f "$LOG_FILE" "$LOG_FILE.old"
  fi
  cd "$APP_DIR"
  nohup python3 "$SERVER" >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  local newpid; newpid="$(cat "$PID_FILE" 2>/dev/null)"
  # 健康检查：最多 15s
  for _ in $(seq 1 30); do
    if curl -sf --max-time 2 "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
      ok "✓ 服务已启动  PID=$newpid  http://127.0.0.1:$PORT"
      ok "  日志: $LOG_FILE"
      return 0
    fi
    is_alive "$newpid" || break
    sleep 0.5
  done
  err "✗ 启动失败（健康检查未通过），日志尾部："
  tail -n 15 "$LOG_FILE" 2>/dev/null | sed 's/^/    /'
  is_alive "$newpid" && kill "$newpid" 2>/dev/null
  rm -f "$PID_FILE"
  exit 1
}

do_stop() {
  local pids killed=0
  # ① PID 文件
  if [ -f "$PID_FILE" ]; then
    local fpid; fpid="$(cat "$PID_FILE" 2>/dev/null)"
    if is_alive "$fpid"; then kill "$fpid" 2>/dev/null; killed=1; fi
    rm -f "$PID_FILE"
  fi
  # ② 端口占用者
  local pp; pp="$(port_pid)"
  if [ -n "$pp" ] && is_alive "$pp"; then kill "$pp" 2>/dev/null; killed=1; fi
  # ③ 进程名兜底（相对路径启动、PID 文件丢失的情况）
  pids="$(service_pids)"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null; killed=1
  fi
  if [ "$killed" -eq 0 ]; then
    say "服务未在运行"
    return 0
  fi
  # 等待退出，最多 6s，仍存活则 SIGKILL
  local waited=0
  while [ $waited -lt 60 ]; do
    pids="$(service_pids)"
    [ -z "$pids" ] && break
    sleep 0.1; waited=$((waited+1))
  done
  pids="$(service_pids)"
  if [ -n "$pids" ]; then
    kill -9 $pids 2>/dev/null
    warn "强制终止: PID $(echo $pids | tr '\n' ' ')"
  fi
  ok "✓ 服务已停止"
}

do_status() {
  local pids; pids="$(service_pids)"
  if [ -z "$pids" ]; then
    warn "○ 未运行"
    exit 1
  fi
  for pid in $pids; do
    local start etime
    start="$(ps -o lstart= -p "$pid" 2>/dev/null)"
    etime="$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')"
    ok "● 运行中  PID=$pid  启动于: $start  (已运行 $etime)"
  done
  if curl -sf --max-time 2 "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
    ok "  健康检查通过  http://127.0.0.1:$PORT"
  else
    warn "  ⚠ 进程存在但 HTTP 健康检查失败"
  fi
}

do_log() {
  [ -f "$LOG_FILE" ] || { warn "无日志文件 $LOG_FILE"; exit 0; }
  if [ "${1:-}" = "-f" ]; then tail -f "$LOG_FILE"; else tail -n "${1:-40}" "$LOG_FILE"; fi
}

case "${1:-}" in
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop; sleep 0.5; do_start ;;
  status)  do_status ;;
  log)     shift; do_log "$@" ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
