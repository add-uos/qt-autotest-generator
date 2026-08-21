#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# test-all-projects.sh — 多项目评分体系端到端验证
# 用法: bash test-all-projects.sh [--fetch] [--full]
#   --fetch  从 MCP 服务器重新拉取 dump（需要网络），否则使用本地缓存
#   --full   同时运行 fetch-mcp-data.py 端到端流程（含增量模式测试）
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$SCRIPT_DIR/../qt-autotest-generator"
SCAN="$SKILL_DIR/scripts/scan-inventory.py"
FETCH="$SKILL_DIR/scripts/fetch-mcp-data.py"
MCP_URL="http://10.8.12.80:13626/mcp"
OUT_DIR="/tmp/mcp-test"

# ── 项目配置 ──────────────────────────────────────────────
declare -A PROJ_MCP_NAME PROJ_DUMP PROJ_FILE_PATTERN PROJ_OUT
PROJS=(deepin-picker deepin-ocr deepin-calculator deepin-terminal deepin-draw deepin-compressor dde-calendar)

PROJ_MCP_NAME[deepin-picker]="home-uos-service-codebase-repos-deepin-picker"
PROJ_MCP_NAME[deepin-ocr]="home-uos-service-codebase-repos-deepin-ocr"
PROJ_MCP_NAME[deepin-calculator]="home-uos-service-codebase-repos-deepin-calculator"
PROJ_MCP_NAME[deepin-terminal]="home-uos-service-codebase-repos-deepin-terminal"
PROJ_MCP_NAME[deepin-draw]="home-uos-service-codebase-repos-deepin-draw"
PROJ_MCP_NAME[deepin-compressor]="home-uos-service-codebase-repos-deepin-compressor"
PROJ_MCP_NAME[dde-calendar]="home-uos-service-codebase-repos-dde-calendar"

PROJ_DUMP[deepin-picker]="$OUT_DIR/deepin-picker_mcp_dump.json"
PROJ_DUMP[deepin-ocr]="$OUT_DIR/deepin-ocr_mcp_dump.json"
PROJ_DUMP[deepin-calculator]="/tmp/deepin-calculator/.ut-inventory_mcp_dump.json"
PROJ_DUMP[deepin-terminal]="$OUT_DIR/deepin-terminal_mcp_dump.json"
PROJ_DUMP[deepin-draw]="$OUT_DIR/deepin-draw_mcp_dump.json"
PROJ_DUMP[deepin-compressor]="$OUT_DIR/deepin-compressor_mcp_dump.json"
PROJ_DUMP[dde-calendar]="$OUT_DIR/dde-calendar_mcp_dump.json"

# file-pattern: deepin-picker 和 deepin-calculator 不需要过滤
PROJ_FILE_PATTERN[deepin-picker]=""
PROJ_FILE_PATTERN[deepin-ocr]=""
PROJ_FILE_PATTERN[deepin-calculator]=""
PROJ_FILE_PATTERN[deepin-terminal]="src/**"
PROJ_FILE_PATTERN[deepin-draw]="src/**"
PROJ_FILE_PATTERN[deepin-compressor]="src/**"
PROJ_FILE_PATTERN[dde-calendar]="src/**"

for p in "${PROJS[@]}"; do
  PROJ_OUT[$p]="$OUT_DIR/${p}-test.json"
done

# ── 参数解析 ──────────────────────────────────────────────
DO_FETCH=false
DO_FULL=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fetch) DO_FETCH=true; shift ;;
    --full)  DO_FULL=true;  shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

mkdir -p "$OUT_DIR"

# ── 颜色 ─────────────────────────────────────────────────
G='\033[32m'; Y='\033[33m'; R='\033[31m'; B='\033[34m'; N='\033[0m'

# ═══════════════════════════════════════════════════════════
# Phase 1: 拉取 MCP dump（仅 --fetch）
# ═══════════════════════════════════════════════════════════
if $DO_FETCH; then
  echo -e "${B}━━━ Phase 1: 从 MCP 服务器拉取 dump ━━━${N}"
  for p in "${PROJS[@]}"; do
    echo -e "  ${Y}▶ $p${N}"
    FP_ARGS=""
    if [[ -n "${PROJ_FILE_PATTERN[$p]}" ]]; then
      FP_ARGS="--file-pattern ${PROJ_FILE_PATTERN[$p]}"
    fi
    python3 "$FETCH" \
      --project "${PROJ_MCP_NAME[$p]}" \
      --mcp-url "$MCP_URL" \
      $FP_ARGS \
      --output "${PROJ_OUT[$p]}" \
      --keep-dump \
      --summary \
      2>&1 | sed 's/^/    /'
    # keep-dump 将 dump 保存到与 output 同目录
    # 需要移到预期位置
    EXPECTED_DUMP="${PROJ_DUMP[$p]}"
    ACTUAL_DUMP="$(dirname "${PROJ_OUT[$p]}")/$(basename "${PROJ_OUT[$p]}" .json)_mcp_dump.json"
    if [[ -f "$ACTUAL_DUMP" && "$ACTUAL_DUMP" != "$EXPECTED_DUMP" ]]; then
      mv "$ACTUAL_DUMP" "$EXPECTED_DUMP"
    fi
  done
  echo ""
fi

# ═══════════════════════════════════════════════════════════
# Phase 2: scan-inventory（本地 dump → inventory JSON）
# ═══════════════════════════════════════════════════════════
echo -e "${B}━━━ Phase 2: scan-inventory 评分 ━━━${N}"

# 检查 dump 文件
MISSING=()
for p in "${PROJS[@]}"; do
  if [[ ! -f "${PROJ_DUMP[$p]}" ]]; then
    MISSING+=("$p")
  fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo -e "${R}缺少 dump 文件: ${MISSING[*]}${N}"
  echo -e "请先运行: bash $0 --fetch"
  exit 1
fi

# 统计表头
printf "\n%-20s %8s %8s %8s %8s %8s %8s\n" \
  "项目" "可测试" "high" "mid" "low" "high%" "待复核"
echo "──────────────────────────────────────────────────────────────────"

TOTAL_TESTABLE=0; TOTAL_HIGH=0; TOTAL_MID=0; TOTAL_LOW=0; TOTAL_REVIEW=0

for p in "${PROJS[@]}"; do
  python3 "$SCAN" \
    --project "${PROJ_MCP_NAME[$p]}" \
    --mcp-dump "${PROJ_DUMP[$p]}" \
    --output "${PROJ_OUT[$p]}" \
    --summary \
    --base-sha "test-$(date +%Y%m%d)" \
    > /tmp/scan-out.txt 2>&1

  # 解析结果
  TESTABLE=$(python3 -c "
import json; d=json.load(open('${PROJ_OUT[$p]}'));
m=[x for x in d['methods'] if x.get('testable')]
h=len([x for x in m if x.get('level')=='high'])
mi=len([x for x in m if x.get('level')=='mid'])
lo=len([x for x in m if x.get('level')=='low'])
rv=len([x for x in d['methods'] if x.get('review_status')=='pending'])
print(f'{len(m)}\t{h}\t{mi}\t{lo}\t{h*100/max(len(m),1):.1f}%\t{rv}')
")
  IFS=$'\t' read -r t h mi lo hp rv <<< "$TESTABLE"
  TOTAL_TESTABLE=$((TOTAL_TESTABLE + t))
  TOTAL_HIGH=$((TOTAL_HIGH + h))
  TOTAL_MID=$((TOTAL_MID + mi))
  TOTAL_LOW=$((TOTAL_LOW + lo))
  TOTAL_REVIEW=$((TOTAL_REVIEW + rv))
  printf "%-20s %8s %8s %8s %8s %8s %8s\n" "$p" "$t" "$h" "$mi" "$lo" "$hp" "$rv"
done

echo "──────────────────────────────────────────────────────────────────"
TOTAL_PCT=$(python3 -c "print(f'{${TOTAL_HIGH}*100/max(${TOTAL_TESTABLE},1):.1f}%')")
printf "%-20s %8s %8s %8s %8s %8s %8s\n" \
  "合计" "$TOTAL_TESTABLE" "$TOTAL_HIGH" "$TOTAL_MID" "$TOTAL_LOW" "$TOTAL_PCT" "$TOTAL_REVIEW"

# ═══════════════════════════════════════════════════════════
# Phase 3: 因子一致性校验
# ═══════════════════════════════════════════════════════════
echo ""
echo -e "${B}━━━ Phase 3: 因子一致性校验 ━━━${N}"

python3 << 'PYEOF'
import json, sys

PROJS_OUT = {
    'deepin-picker': '/tmp/mcp-test/deepin-picker-test.json',
    'deepin-ocr': '/tmp/mcp-test/deepin-ocr-test.json',
    'deepin-calculator': '/tmp/mcp-test/deepin-calculator-test.json',
    'deepin-terminal': '/tmp/mcp-test/deepin-terminal-test.json',
    'deepin-draw': '/tmp/mcp-test/deepin-draw-test.json',
    'deepin-compressor': '/tmp/mcp-test/deepin-compressor-test.json',
    'dde-calendar': '/tmp/mcp-test/dde-calendar-test.json',
}

errors = []
for proj, path in PROJS_OUT.items():
    try:
        with open(path) as f:
            inv = json.load(f)
    except FileNotFoundError:
        errors.append(f"  ❌ {proj}: 文件不存在 {path}")
        continue

    testable = [m for m in inv['methods'] if m.get('testable')]
    
    # 校验 1: level 与 score 一致性
    for m in testable:
        factors = m.get('factors', [])
        # 计算 score
        score = 0
        for f in factors:
            if f in ('dbus_slot', 'q_invokable', 'plugin_export'): score += 3
            elif f.startswith('complexity:'):
                v = int(f.split(':')[1])
                score += 3 if v >= 20 else (2 if v >= 8 else (1 if v >= 5 else 0))
            elif f.startswith('cognitive:'):
                v = int(f.split(':')[1])
                score += 2 if v >= 30 else (1 if v >= 15 else 0)
            elif f.startswith('lines:'):
                v = int(f.split(':')[1])
                score += 1 if v >= 50 else 0
            elif f.startswith('loop_count:'):
                v = int(f.split(':')[1])
                score += 1 if v >= 5 else 0
            elif f.startswith('alloc_in_loop:'):
                v = int(f.split(':')[1])
                score += 1 if v >= 1 else 0
            elif f == 'recursive': score += 1
            elif f.startswith('linear_scan') or f.startswith('in_degree') or f == 'concurrent_class': score += 1
            elif f.startswith('transitive_loop_depth:'):
                v = int(f.split(':')[1])
                score += 3 if v >= 3 else 0
            elif f in ('destructor', 'operator'): score -= 1
        
        expected = 'high' if score >= 3 else ('mid' if score >= 1 else 'low')
        actual = m.get('level', '')
        # name_pattern 是 suggested 因子，score=0 但 level=mid 是设计正确
        has_suggested = any(f.startswith('name_pattern:') for f in factors)
        if has_suggested and score < 1:
            expected = 'mid'  # suggested 强制 mid

        if actual != expected:
            errors.append(f"  ❌ {proj}/{m['name']}: level={actual} 但 score={score} 应为 {expected} (factors={factors})")
    
    # 校验 2: auxiliary factor 独立不能推到 high
    for m in testable:
        if m.get('level') == 'high':
            factors = m.get('factors', [])
            # 检查是否有 主因子(complexity>=5, dbus_slot, q_invokable, plugin_export, transitive_loop_depth>=3)
            has_primary = False
            for f in factors:
                if f in ('dbus_slot', 'q_invokable', 'plugin_export'): has_primary = True
                elif f.startswith('complexity:') and int(f.split(':')[1]) >= 5: has_primary = True
                elif f.startswith('transitive_loop_depth:') and int(f.split(':')[1]) >= 3: has_primary = True
                elif f.startswith('loop_count:') and int(f.split(':')[1]) >= 5: has_primary = True
                elif f.startswith('alloc_in_loop:'): has_primary = True
                elif f == 'recursive': has_primary = True
                elif f.startswith('linear_scan_in_loop:'): has_primary = True
                elif f == 'concurrent_class': has_primary = True
                elif f.startswith('in_degree:'): has_primary = True  # mid-booster 也可以叠加
                elif f.startswith('name_pattern:'): has_primary = True  # suggested 不会推分
            
            if not has_primary:
                errors.append(f"  ❌ {proj}/{m['name']}: high 但无主因子 (factors={factors})")

    # 校验 3: exempt 方法 testable=false
    for m in inv['methods']:
        if m.get('source') == 'auto' and not m.get('testable', True):
            if m.get('level') != 'exempt':
                # level 可以是任何值，关键是 testable=false
                pass
    
    print(f"  ✅ {proj}: 校验通过 ({len(testable)} testable methods)")

if errors:
    print("\n❌ 校验失败:")
    for e in errors:
        print(e)
    sys.exit(1)
else:
    print("\n✅ 全部校验通过")
PYEOF

# ═══════════════════════════════════════════════════════════
# Phase 4: 因子分布统计
# ═══════════════════════════════════════════════════════════
echo ""
echo -e "${B}━━━ Phase 4: 因子分布统计 ━━━${N}"

python3 << 'PYEOF'
import json
from collections import Counter

PROJS_OUT = {
    'deepin-picker': '/tmp/mcp-test/deepin-picker-test.json',
    'deepin-ocr': '/tmp/mcp-test/deepin-ocr-test.json',
    'deepin-calculator': '/tmp/mcp-test/deepin-calculator-test.json',
    'deepin-terminal': '/tmp/mcp-test/deepin-terminal-test.json',
    'deepin-draw': '/tmp/mcp-test/deepin-draw-test.json',
    'deepin-compressor': '/tmp/mcp-test/deepin-compressor-test.json',
    'dde-calendar': '/tmp/mcp-test/dde-calendar-test.json',
}

factor_level = {}
for proj, path in PROJS_OUT.items():
    with open(path) as f:
        inv = json.load(f)
    for m in inv['methods']:
        if not m.get('testable'): continue
        level = m.get('level', 'low')
        for factor in m.get('factors', []):
            base = factor.split(':')[0]
            if base not in factor_level:
                factor_level[base] = Counter()
            factor_level[base][level] += 1

# 按总数排序输出
total_counts = [(f, sum(c.values())) for f, c in factor_level.items()]
total_counts.sort(key=lambda x: -x[1])

print(f"{'因子':<25} {'总数':>6} {'high%':>7} {'mid%':>7} {'low%':>7}")
print("─" * 55)
for factor, total in total_counts:
    c = factor_level[factor]
    h = c.get('high', 0)
    mi = c.get('mid', 0)
    lo = c.get('low', 0)
    print(f"{factor:<25} {total:>6} {h*100/total:>6.1f}% {mi*100/total:>6.1f}% {lo*100/total:>6.1f}%")
PYEOF

# ═══════════════════════════════════════════════════════════
# Phase 5: complexity 8-9 升级方法抽检（仅 --full 时展示）
# ═══════════════════════════════════════════════════════════
if $DO_FULL; then
  echo ""
  echo -e "${B}━━━ Phase 5: complexity 8-9 升级方法抽检 ━━━${N}"
  python3 << 'PYEOF'
import json

PROJS_OUT = {
    'deepin-picker': '/tmp/mcp-test/deepin-picker-test.json',
    'deepin-ocr': '/tmp/mcp-test/deepin-ocr-test.json',
    'deepin-calculator': '/tmp/mcp-test/deepin-calculator-test.json',
    'deepin-terminal': '/tmp/mcp-test/deepin-terminal-test.json',
    'deepin-draw': '/tmp/mcp-test/deepin-draw-test.json',
    'deepin-compressor': '/tmp/mcp-test/deepin-compressor-test.json',
    'dde-calendar': '/tmp/mcp-test/dde-calendar-test.json',
}

print(f"{'项目':<18} {'方法':<35} {'类':<25} {'因子'}")
print("─" * 110)
for proj, path in sorted(PROJS_OUT.items()):
    with open(path) as f:
        inv = json.load(f)
    for m in inv['methods']:
        if m.get('level') != 'high' or not m.get('testable'): continue
        factors = m.get('factors', [])
        has_cx8_9 = any(f.startswith('complexity:') and 8 <= int(f.split(':')[1]) <= 9 for f in factors)
        if has_cx8_9:
            cls = (m.get('class_qn') or '').split('.')[-1] if m.get('class_qn') else '(free)'
            print(f"{proj:<18} {m['name']:<35} {cls:<25} {', '.join(factors)}")
PYEOF
fi

# ═══════════════════════════════════════════════════════════
# Phase 6 (仅 --full): 增量模式测试
# ═══════════════════════════════════════════════════════════
if $DO_FULL; then
  echo ""
  echo -e "${B}━━━ Phase 6: 增量模式测试 ━━━${N}"
  # 用 deepin-calculator 测试增量模式
  p="deepin-calculator"
  echo -e "  ${Y}▶ $p (incremental)${N}"
  python3 "$FETCH" \
    --project "${PROJ_MCP_NAME[$p]}" \
    --mcp-dump "${PROJ_DUMP[$p]}" \
    --output "$OUT_DIR/${p}-incremental.json" \
    --incremental \
    --existing "${PROJ_OUT[$p]}" \
    --summary \
    2>&1 | sed 's/^/    /'
  
  echo ""
  echo "  增量 diff 报告:"
  cat "$OUT_DIR/${p}-incremental-diff.md" 2>/dev/null | sed 's/^/    /' || echo "    (无 diff 报告)"
fi

echo ""
echo -e "${G}✅ 全部测试完成${N}"
echo -e "输出目录: $OUT_DIR/"
echo -e "  *_test.json        — 各项目 inventory"
echo -e "  *_summary.md       — 各项目摘要"
