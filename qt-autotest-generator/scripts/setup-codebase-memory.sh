#!/bin/bash
# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
# SPDX-License-Identifier: GPL-3.0-or-later
#
# setup-codebase-memory.sh - codebase-memory-mcp 安装与配置脚本
#
# ⚠️ 调用条件：本脚本仅在 `environment_check` 解析到本地提供方
#    （`codebase-memory-mcp`）时调用。若已解析到远端提供方
#    （`remote-codebase-memory-mcp`），则跳过本脚本（远端无需本地安装/索引）。
#    解析逻辑见 reference/mcp-providers.md。
#
# 职责：
#   1. 检测 codebase-memory-mcp 是否已安装
#   2. 未安装则安装（优先官方 install.sh，源码编译作为后备）
#   3. 配置自动索引与文件监听
#   4. 配置 MCP 客户端（opencode / claude code / codex 等）
#   5. 验证服务可用性
#
# 特性：
#   - 幂等：可重复执行，已安装则跳过
#   - 明确退出码：主技能据此决定是否继续
#   - 零交互：默认无需用户输入，可在自动化流程中调用
#
# 退出码：
#   0  成功（已安装或本次安装成功，服务可用）
#   1  安装失败（下载或编译失败）
#   2  配置失败（客户端配置写入失败）
#   3  验证失败（已安装但 cli 不可用）
#
# 用法：
#   bash setup-codebase-memory.sh                    # 默认行为
#   bash setup-codebase-memory.sh --source /path     # 指定源码目录编译安装
#   bash setup-codebase-memory.sh --skip-install     # 跳过安装，仅配置与验证
#   bash setup-codebase-memory.sh --verbose          # 详细输出
#

set -euo pipefail

# ============================================================================
# 全局变量与默认值
# ============================================================================

readonly CBM_BIN_NAME="codebase-memory-mcp"
readonly CBM_MIN_VERSION="0.8.0"          # 最低版本要求
readonly CBM_DEFAULT_INDEX_MODE="moderate" # 首次索引默认模式
readonly CBM_AUTO_INDEX="true"
readonly CBM_AUTO_WATCH="true"
readonly CBM_AUTO_INDEX_LIMIT="50000"
readonly CBM_GH_OWNER_REPO="DeusData/codebase-memory-mcp"
readonly CBM_INSTALL_URL="${QTAG_CBM_INSTALL_URL:-https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh}"

# 可被参数覆盖
CBM_SOURCE_DIR=""        # --source 指定的源码目录
SKIP_INSTALL="false"     # --skip-install
VERBOSE="false"          # --verbose

# 运行期状态（由 step_detect_or_install 填充，下游步骤读取）
CBM_BIN_PATH=""          # 检测/安装后的二进制绝对路径
CBM_INSTALLED_VERSION="" # 检测到的版本号

# 颜色输出（非 TTY 时自动禁用）
if [ -t 1 ]; then
    readonly COLOR_RED='\033[0;31m'
    readonly COLOR_GREEN='\033[0;32m'
    readonly COLOR_YELLOW='\033[0;33m'
    readonly COLOR_BLUE='\033[0;34m'
    readonly COLOR_NC='\033[0m'
else
    readonly COLOR_RED=''
    readonly COLOR_GREEN=''
    readonly COLOR_YELLOW=''
    readonly COLOR_BLUE=''
    readonly COLOR_NC=''
fi

# ============================================================================
# 日志函数
# ============================================================================

log_info()  { echo -e "${COLOR_BLUE}[INFO]${COLOR_NC} $*"; }
log_ok()     { echo -e "${COLOR_GREEN}[✓]${COLOR_NC} $*"; }
log_warn()   { echo -e "${COLOR_YELLOW}[WARN]${COLOR_NC} $*"; }
log_error()  { echo -e "${COLOR_RED}[✗]${COLOR_NC} $*" >&2; }
log_verbose() { [ "$VERBOSE" = "true" ] && echo -e "[DEBUG] $*" || true; }

# ============================================================================
# 参数解析
# ============================================================================

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --source)
                CBM_SOURCE_DIR="${2:-}"
                shift 2
                ;;
            --skip-install)
                SKIP_INSTALL="true"
                shift
                ;;
            --verbose)
                VERBOSE="true"
                shift
                ;;
            -h|--help)
                sed -n '2,28p' "$0"
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                log_error "使用 --help 查看用法"
                exit 1
                ;;
        esac
    done
    log_verbose "参数: source=$CBM_SOURCE_DIR skip_install=$SKIP_INSTALL verbose=$VERBOSE"
}

# ============================================================================
# 前置依赖检查（curl/git/cc）
# ============================================================================

check_prerequisites() {
    log_info "检查前置依赖..."

    local missing=()

    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || missing+=("curl 或 wget")
    command -v git  >/dev/null 2>&1 || missing+=("git")

    # 编译路径才需要 C 编译器（仅当走源码编译时才强制）
    if [ -n "$CBM_SOURCE_DIR" ]; then
        command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || missing+=("gcc/cc")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        log_error "缺少前置依赖: ${missing[*]}"
        log_error "请安装后重试"
        exit 1
    fi

    log_ok "前置依赖检查通过"
}

# ============================================================================
# 版本比较工具
# ============================================================================

# version_ge <v1> <v2>：v1 >= v2 返回 0，否则返回 1
version_ge() {
    local v1="$1" v2="$2"
    if [ "$v1" = "$v2" ]; then return 0; fi
    # 用 sort -V 比较，相同则取第一个
    local first
    first=$(printf '%s\n%s\n' "$v1" "$v2" | sort -V | head -n1)
    [ "$first" = "$v2" ]
}

# ============================================================================
# 步骤 1: 检测与安装
# ============================================================================

# 获取已安装二进制路径，未找到返回空字符串
detect_installed_binary() {
    local path
    path=$(command -v "$CBM_BIN_NAME" 2>/dev/null || true)
    if [ -n "$path" ]; then
        echo "$path"
        return 0
    fi
    # 检查常见安装位置
    for candidate in \
        "$HOME/.local/bin/$CBM_BIN_NAME" \
        "/usr/local/bin/$CBM_BIN_NAME" \
        "$HOME/.cargo/bin/$CBM_BIN_NAME"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

get_installed_version() {
    local bin_path="$1"
    # 输出形如: codebase-memory-mcp 0.8.1
    "$bin_path" --version 2>/dev/null | awk '{print $NF}' || echo "0.0.0"
}

step_detect_or_install() {
    log_info "[1/4] 检测 codebase-memory-mcp 安装状态..."

    local bin_path
    if bin_path=$(detect_installed_binary); then
        local version
        version=$(get_installed_version "$bin_path")
        log_ok "已安装: $bin_path (v$version)"

        if version_ge "$version" "$CBM_MIN_VERSION"; then
            log_ok "版本满足最低要求 (>= $CBM_MIN_VERSION)"
            CBM_BIN_PATH="$bin_path"
            CBM_INSTALLED_VERSION="$version"
            return 0
        else
            log_warn "版本过低 (当前 $version < 最低 $CBM_MIN_VERSION)，将尝试升级"
            # 落到下面的安装逻辑（升级）
        fi
    else
        log_info "未检测到 $CBM_BIN_NAME"
    fi

    if [ "$SKIP_INSTALL" = "true" ]; then
        log_error "--skip-install 已指定，但服务未安装"
        log_error "请去掉 --skip-install 参数以触发自动安装"
        return 1
    fi

    # 安装/升级
    install_cbm
}

# 实际安装逻辑
install_cbm() {
    # 优先：源码编译（仅当 --source 显式指定时）
    if [ -n "$CBM_SOURCE_DIR" ] && [ -d "$CBM_SOURCE_DIR" ] && [ -f "$CBM_SOURCE_DIR/scripts/build.sh" ]; then
        log_info "检测到本地源码仓库: $CBM_SOURCE_DIR"
        install_from_source "$CBM_SOURCE_DIR"
        return $?
    fi

    # 默认：官方 install.sh
    install_from_official_script
}

install_from_official_script() {
    log_info "[安装方式] 官方 install.sh（在线下载）"
    log_info "下载地址: $CBM_INSTALL_URL"

    local tmp_script
    tmp_script=$(mktemp)
    trap 'rm -f "$tmp_script"' EXIT

    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$CBM_INSTALL_URL" -o "$tmp_script" || {
            log_error "下载 install.sh 失败"
            return 1
        }
    else
        wget -qO "$tmp_script" "$CBM_INSTALL_URL" || {
            log_error "下载 install.sh 失败"
            return 1
        }
    fi

    # SHA256 校验（防止供应链篡改）
    # 注意：此校验值需随上游 install.sh 更新而同步更新
    # 若校验失败且确认上游已更新，请更新 CBM_INSTALL_SHA256 值
    local cbm_install_sha256
    cbm_install_sha256="${CBM_INSTALL_SHA256:-}"
    if [ -n "$cbm_install_sha256" ]; then
        log_info "校验 install.sh 完整性 (SHA256)..."
        echo "$cbm_install_sha256  $tmp_script" | sha256sum -c >/dev/null 2>&1 || {
            log_error "SHA256 校验失败！文件可能被篡改"
            log_error "若确认上游已更新，请设置 CBM_INSTALL_SHA256 环境变量为新值"
            log_error "跳过校验: CBM_INSTALL_SHA256= bash $0"
            return 1
        }
        log_ok "SHA256 校验通过"
    else
        log_warn "未设置 CBM_INSTALL_SHA256，跳过完整性校验（建议设置以增强安全性）"
    fi

    log_info "执行安装脚本（该过程会自动检测并配置 MCP 客户端）..."
    if ! bash "$tmp_script"; then
        log_error "官方 install.sh 执行失败"
        return 1
    fi

    rm -f "$tmp_script"
    trap - EXIT

    log_ok "官方安装完成"
}

install_from_source() {
    local src_dir="$1"
    log_info "[安装方式] 源码编译 ($src_dir)"

    # 1. 拉取最新代码
    log_info "拉取最新代码..."
    (
        cd "$src_dir"
        git fetch --quiet origin main 2>/dev/null || git fetch --quiet origin master 2>/dev/null || true
        git checkout --quiet main 2>/dev/null || git checkout --quiet master 2>/dev/null || true
        git pull --quiet origin main 2>/dev/null || git pull --quiet origin master 2>/dev/null || true
    ) || log_warn "git 操作失败，使用当前工作区代码继续"

    # 2. 编译
    log_info "编译中（可能需要 1-2 分钟）..."
    if ! bash "$src_dir/scripts/build.sh"; then
        log_error "源码编译失败"
        log_error "请检查编译依赖：gcc/g++、zlib、git"
        return 1
    fi

    # 3. 安装（build.sh 产物位于 build/c/codebase-memory-mcp）
    local built_bin="$src_dir/build/c/$CBM_BIN_NAME"
    if [ ! -x "$built_bin" ]; then
        log_error "编译产物不存在: $built_bin"
        return 1
    fi

    mkdir -p "$HOME/.local/bin"
    cp -f "$built_bin" "$HOME/.local/bin/$CBM_BIN_NAME"
    chmod +x "$HOME/.local/bin/$CBM_BIN_NAME"

    # 4. 调用内置 install 配置客户端
    log_info "调用 install 配置 MCP 客户端..."
    if ! "$HOME/.local/bin/$CBM_BIN_NAME" install; then
        log_warn "install 子命令配置客户端时返回非零，继续验证"
    fi

    log_ok "源码安装完成"
}

# ============================================================================
# 步骤 2: 配置自动索引
# ============================================================================

step_configure_auto_index() {
    log_info "[2/4] 配置自动索引..."

    local bin_path="${CBM_BIN_PATH:-}"
    if [ -z "$bin_path" ]; then
        bin_path=$(detect_installed_binary) || {
            log_error "无法定位 $CBM_BIN_NAME，跳过自动索引配置"
            return 2
        }
    fi

    # auto_index：新项目首次连接时自动索引
    "$bin_path" config set auto_index "$CBM_AUTO_INDEX" 2>/dev/null || {
        log_warn "设置 auto_index 失败（可能已默认开启）"
    }

    # auto_watch：后台 watcher 检测 git 变更并增量重索引
    "$bin_path" config set auto_watch "$CBM_AUTO_WATCH" 2>/dev/null || {
        log_warn "设置 auto_watch 失败（可能已默认开启）"
    }

    # auto_index_limit：单项目最大文件数
    "$bin_path" config set auto_index_limit "$CBM_AUTO_INDEX_LIMIT" 2>/dev/null || {
        log_warn "设置 auto_index_limit 失败"
    }

    log_ok "自动索引配置完成: auto_index=$CBM_AUTO_INDEX, auto_watch=$CBM_AUTO_WATCH, limit=$CBM_AUTO_INDEX_LIMIT"
}

# ============================================================================
# 步骤 3: 配置 MCP 客户端
# ============================================================================

step_configure_clients() {
    log_info "[3/4] 配置 MCP 客户端..."

    local bin_path="${CBM_BIN_PATH:-}"
    if [ -z "$bin_path" ]; then
        bin_path=$(detect_installed_binary) || return 2
    fi

    # codebase-memory-mcp install 会自动检测并配置：
    # opencode / claude code / codex / cursor / windsurf / zed 等 43 个客户端
    # 该命令是幂等的，重复执行只会补全缺失的配置
    if "$bin_path" install >/dev/null 2>&1; then
        log_ok "MCP 客户端配置完成（已自动检测并写入）"
    else
        log_warn "install 子命令返回非零，手动检查已安装客户端配置"
        # 列出已配置的客户端供用户参考
        local opencode_config="$HOME/.config/opencode/opencode.json"
        if [ -f "$opencode_config" ] && grep -q "$CBM_BIN_NAME" "$opencode_config"; then
            log_ok "检测到 opencode 已配置: $opencode_config"
        else
            log_warn "opencode 配置中未发现 $CBM_BIN_NAME 条目"
            log_warn "请参考: $bin_path install  或  手动添加到 $opencode_config"
        fi
    fi
}

# ============================================================================
# 步骤 4: 验证
# ============================================================================

step_verify() {
    log_info "[4/4] 验证服务可用性..."

    local bin_path="${CBM_BIN_PATH:-}"
    if [ -z "$bin_path" ]; then
        bin_path=$(detect_installed_binary) || {
            log_error "验证失败：无法定位二进制"
            return 3
        }
    fi

    # 验证 1: cli list_projects 可执行
    log_info "执行 cli list_projects..."
    if ! "$bin_path" cli list_projects >/dev/null 2>&1; then
        log_error "cli list_projects 执行失败"
        log_error "服务可能未正确启动，请检查 $bin_path"
        return 3
    fi
    log_ok "cli 服务响应正常"

    # 验证 2: 配置确认
    log_info "当前配置:"
    "$bin_path" config list 2>/dev/null | sed 's/^/    /' || true

    # 验证 3: 已索引项目列表
    log_info "已索引项目:"
    "$bin_path" cli list_projects 2>/dev/null | sed 's/^/    /' || true

    log_ok "验证通过"
}

# ============================================================================
# 主流程
# ============================================================================

main() {
    echo ""
    log_info "======================================"
    log_info " codebase-memory-mcp 安装与配置脚本"
    log_info "======================================"
    echo ""

    parse_args "$@"
    check_prerequisites

    # 步骤 1: 检测或安装（退出码 1 = 安装失败）
    if ! step_detect_or_install; then
        log_error "安装步骤失败"
        exit 1
    fi

    # 步骤 2: 配置自动索引（退出码 2 = 配置失败）
    if ! step_configure_auto_index; then
        log_error "自动索引配置失败"
        exit 2
    fi

    # 步骤 3: 配置 MCP 客户端（退出码 2 = 配置失败）
    if ! step_configure_clients; then
        log_error "MCP 客户端配置失败"
        exit 2
    fi

    # 步骤 4: 验证（退出码 3 = 验证失败）
    if ! step_verify; then
        log_error "服务验证失败"
        exit 3
    fi

    echo ""
    log_ok "======================================"
    log_ok " 全部完成！codebase-memory-mcp 已就绪"
    log_ok "======================================"
    echo ""
    log_info "首次使用时，技能会自动为目标项目建立索引。"
    log_info "后续代码变更由后台 watcher 自动增量同步。"
    echo ""

    exit 0
}

main "$@"
