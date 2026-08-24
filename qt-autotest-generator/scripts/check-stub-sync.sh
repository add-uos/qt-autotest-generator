#!/usr/bin/env bash
# 校验 examples/sample-qt-project/autotests/3rdparty/stub/ 与 templates/stub-ext/ 是否字节同步。
#
# 示例项目的 3rdparty/stub/ 是 templates/stub-ext/ 的冻结副本——保留副本（而非运行时
# 从 templates 复制）是为了让 sample-qt-project/ 可被独立复制后直接构建运行。
# 因此更新 stub-ext 时两处必须同步，否则示例将静默漂移。
#
# 退出码：0=一致，1=存在漂移，2=目录缺失。
# 发布前运行：bash scripts/check-stub-sync.sh
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$SKILL_DIR/templates/stub-ext"
DST="$SKILL_DIR/examples/sample-qt-project/autotests/3rdparty/stub"

if [ ! -d "$SRC" ] || [ ! -d "$DST" ]; then
  echo "[FATAL] stub-ext 源或副本目录不存在：" >&2
  echo "  src: $SRC" >&2
  echo "  dst: $DST" >&2
  exit 2
fi

if ! diff -rq "$SRC" "$DST" >/dev/null 2>&1; then
  echo "[DRIFT] stub-ext 副本与规范源不一致：" >&2
  diff -rq "$SRC" "$DST" >&2 || true
  echo "" >&2
  echo "修复：cp -r \"$SRC/\". \"$DST/\"" >&2
  exit 1
fi

echo "[OK] stub-ext 同步（templates/stub-ext/ == examples/.../3rdparty/stub/）"
