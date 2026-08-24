#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
mutation-score.py — Mode 4: 变异测试 (Mutation Testing)
归属: qt-autotest-generator/scripts/

用法 (直接模式 — 测试/单方法验证):
    python3 mutation-score.py \\
        --source src/utils.cpp \\
        --function Utils::stringIsDigit \\
        --build-dir build-test \\
        --test-target deepin-calculator-test \\
        --gtest-filter '*stringIsDigit*'

用法 (inventory 模式 — 生产 Mode 4):
    python3 mutation-score.py \\
        --inventory .ut-inventory.json \\
        --all-high \\
        --build-dir build-test \\
        --test-target deepin-calculator-test

变异算子 (框架无关，直接复用原版):
    AOR: 算术运算符替换 (+ → -, *, /)
    ROR: 关系运算符替换 (< → <=, >, >=, ==, !=)
    LOR: 逻辑运算符替换 (&& → ||)
    CRC: 常量替换 (0 → 1, 1 → 0)
    RVF: 返回值修改 (return true → return false, return N → return N±1)
"""

import argparse
import json
import os
import re
import sys
import shutil
import atexit
import signal
import subprocess
from collections import defaultdict
from datetime import datetime


# ═══════════════════════════════════════════════════════════════
# 源码安全四铁律: 备份 + 恢复机制 (atexit + signal)
# ═══════════════════════════════════════════════════════════════

_PENDING_RESTORES = []  # [(backup_path, source_file), ...]
_PROJECT_DIR = None     # 用于退出时 git diff 校验
_MUTATED_FILES = set()  # 被变异过的源文件集合 (退出时只校验这些文件的 git diff)


def _relative_path(abs_path, base_dir):
    """将绝对路径转为相对 base_dir 的相对路径; 转换失败则原样返回."""
    try:
        return os.path.relpath(abs_path, base_dir)
    except ValueError:
        return abs_path


def _restore_on_exit():
    """铁律 #2: 进程退出/被kill时恢复所有未恢复的源码"""
    for backup_path, source_file in _PENDING_RESTORES:
        if os.path.exists(backup_path):
            try:
                shutil.move(backup_path, source_file)
                print("  [RESTORE] 恢复 {} <- {}".format(source_file, backup_path))
            except Exception as e:
                print("  [RESTORE ERROR] {} : {}".format(source_file, e))


atexit.register(_restore_on_exit)


def _signal_handler(signum, frame):
    _restore_on_exit()
    sys.exit(128 + signum)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def _git_diff_check():
    """铁律 #4: 退出时校验被变异的源文件 git diff 为空
    只校验 _MUTATED_FILES 中的文件, 不检查全仓库 (避免预存改动误报)"""
    if not _PROJECT_DIR or not os.path.isdir(os.path.join(_PROJECT_DIR, '.git')):
        return True  # 非 git 项目, 跳过
    if not _MUTATED_FILES:
        return True
    # 只检查被变异过的文件
    cmd = ['git', 'diff', '--exit-code'] + sorted(_MUTATED_FILES)
    rc, stdout, _ = run_command(cmd, cwd=_PROJECT_DIR, timeout=30)
    if rc != 0:
        print("\n[FATAL] 源码未恢复干净! 以下被变异文件的 git diff 非空:")
        print(stdout[:2000])
        print("\n请手动检查 .mutation_backup 残留文件")
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# 变异算子 (框架无关，移植自 unit-test-generate，未改动)
# ═══════════════════════════════════════════════════════════════

AOR_MAP = {
    '+': ['-', '*', '/'],
    '-': ['+', '*', '/'],
    '*': ['/', '+', '-'],
    '/': ['*', '+', '-'],
    '%': ['*', '/', '+'],
}

ROR_MAP = {
    '<':  ['<=', '>', '>=', '==', '!='],
    '>':  ['>=', '<', '<=', '==', '!='],
    '<=': ['<', '>', '>=', '==', '!='],
    '>=': ['>', '<', '<=', '==', '!='],
    '==': ['!=', '<', '>', '<=', '>='],
    '!=': ['==', '<', '>', '<=', '>='],
}

LOR_MAP = {
    '&&': ['||'],
    '||': ['&&'],
}

RVF_BOOL_MAP = {
    'return true':  ['return false'],
    'return false': ['return true'],
}


def in_string(line, token):
    """检查 token 在该行中首次出现是否在字符串/字符字面量或注释中"""
    idx = line.find(token)
    if idx < 0:
        return False
    before = line[:idx]
    in_str = False
    in_char = False
    escape = False
    i = 0
    while i < len(before):
        c = before[i]
        if escape:
            escape = False
        elif c == '\\':
            escape = True
        elif in_str and c == '"':
            in_str = False
        elif in_char and c == "'":
            in_char = False
        elif not in_str and not in_char and c == '"':
            in_str = True
        elif not in_str and not in_char and c == "'":
            in_char = True
        elif not in_str and not in_char and c == '/' and i + 1 < len(before):
            if before[i + 1] == '/' or before[i + 1] == '*':
                return True
        i += 1
    return in_str or in_char


def _is_unary_op(line, idx, op):
    """检查 idx 处的运算符是否是一元运算符 (如 -1, +a) 而非二元"""
    if op not in ('+', '-'):
        return False
    before = line[:idx].rstrip()
    if not before:
        return True
    last = before[-1]
    if last in '([{,;=>&|!?:*+-%~^':
        return True
    return False


# 指针声明启发式：C++ 类型关键字 + Qt 常见大写开头的类型
_CPP_TYPE_KEYWORDS = frozenset({
    'int', 'char', 'void', 'bool', 'float', 'double', 'long', 'short',
    'unsigned', 'signed', 'const', 'static', 'volatile', 'auto',
    'wchar_t', 'size_t', 'ssize_t', 'int8_t', 'int16_t', 'int32_t', 'int64_t',
    'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t',
})


def _is_pointer_decl(line, op_idx):
    """检查 op_idx 处的 * 是否为指针声明或解引用 (非算术乘法)."""
    before = line[:op_idx].rstrip()
    after_char = line[op_idx + 1:op_idx + 2] if op_idx + 1 < len(line) else ''

    # * 后面紧跟标识符/下划线/多级* → 指针声明
    if after_char and (after_char.isalpha() or after_char == '_' or after_char == '*'):
        last_word = re.search(r'\b(\w+)\s*$', before)
        if last_word:
            word = last_word.group(1)
            # 类型关键字 或 大写开头（如 DBusNotify, QWidget）
            if word in _CPP_TYPE_KEYWORDS or (word[0].isupper() and not word.isupper()):
                return True
            # 单字母类型: T, U 等模板参数
            if len(word) == 1 and word.isupper():
                return True

    # 解引用: *expr — 前面是 ) 或标识符且不是类型
    # 但难以与乘法区分 (a * b), 只跳过明确模式
    # 模式: (Type *) 强转 / new Type *
    if before.endswith(')') or 'new ' in before:
        return True

    return False


def generate_aor_mutants(lines, func_start, func_end):
    mutants = []
    for i in range(func_start, func_end):
        line = lines[i]
        for op, replacements in AOR_MAP.items():
            if op not in line:
                continue
            stripped = line.strip()
            if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
                continue
            if in_string(line, op):
                continue
            op_idx = line.find(op)
            if op_idx >= 0:
                if _is_unary_op(line, op_idx, op):
                    continue
                after = line[op_idx + 1:op_idx + 2] if op_idx + 1 < len(line) else ''
                before_char = line[op_idx - 1] if op_idx > 0 else ''
                if after == '=' or before_char in ('+', '-', '*', '/'):
                    continue
                # 跳过注释分隔符 (// /* */) — AOR 的 / 不能变异注释
                if op == '/' and (after in ('/', '*') or before_char in ('/', '*')):
                    continue
                # 跳过指针成员访问 -> (变异 - 会产生 +> 无效代码)
                if op == '-' and after == '>':
                    continue
                # 跳过指针声明/解引用 * (Bug #5)
                if op == '*' and _is_pointer_decl(line, op_idx):
                    continue
            for repl in replacements:
                new_line = line.replace(op, repl, 1)
                if new_line != line:
                    mutants.append({
                        'id': 'AOR_{}_{}_{}'.format(i, op, repl),
                        'line': i + 1,
                        'operator': 'AOR',
                        'original': op,
                        'replacement': repl,
                        'description': 'L{}: {} -> {}'.format(i + 1, op, repl),
                    })
    return mutants


def generate_ror_mutants(lines, func_start, func_end):
    mutants = []
    for i in range(func_start, func_end):
        line = lines[i]
        for op, replacements in ROR_MAP.items():
            if op not in line:
                continue
            if in_string(line, op):
                continue
            op_idx = line.find(op)
            if op_idx < 0:
                continue
            after = line[op_idx + len(op):op_idx + len(op) + 1] if op_idx + len(op) < len(line) else ''
            before_char = line[op_idx - 1] if op_idx > 0 else ''
            # 跳过流操作符 << >> (变异会产生 >< 等无效代码)
            if op == '<' and (after == '<' or before_char == '<'):
                continue
            if op == '>' and (after == '>' or before_char == '>'):
                continue
            # 跳过 <= >= 的单字符匹配 (由 <= >= 键单独处理)
            if op == '<' and after == '=':
                continue
            if op == '>' and after == '=':
                continue
            # 跳过指针成员访问 -> (变异 > 会产生 -+ 等无效代码)
            if op == '>' and before_char == '-':
                continue
            for repl in replacements:
                new_line = line.replace(op, repl, 1)
                if new_line != line:
                        mutants.append({
                            'id': 'ROR_{}_{}_{}'.format(i, op, repl),
                            'line': i + 1,
                            'operator': 'ROR',
                            'original': op,
                            'replacement': repl,
                            'description': 'L{}: {} -> {}'.format(i + 1, op, repl),
                        })
    return mutants


def generate_lor_mutants(lines, func_start, func_end):
    mutants = []
    for i in range(func_start, func_end):
        line = lines[i]
        for op, replacements in LOR_MAP.items():
            if op in line and not in_string(line, op):
                for repl in replacements:
                    new_line = line.replace(op, repl, 1)
                    if new_line != line:
                        mutants.append({
                            'id': 'LOR_{}_{}_{}'.format(i, op, repl),
                            'line': i + 1,
                            'operator': 'LOR',
                            'original': op,
                            'replacement': repl,
                            'description': 'L{}: {} -> {}'.format(i + 1, op, repl),
                        })
    return mutants


def generate_crc_mutants(lines, func_start, func_end):
    mutants = []
    # Bug #11: 识别一元负号，-N 作为整体常量
    int_pattern = re.compile(r'(?<!\w)-?(\d+)(?!\w)')
    for i in range(func_start, func_end):
        line = lines[i]
        for m in int_pattern.finditer(line):
            full_match = m.group(0)   # 可能含前导 -
            val = int(full_match)
            pos = m.start()
            prefix = line[:pos]
            if prefix.count('"') % 2 == 1:
                continue
            # 跳过负号是二元减法 (如 a - 5) 而非一元负号 (如 -5)
            if full_match.startswith('-') and prefix.rstrip():
                last_char = prefix.rstrip()[-1]
                if last_char not in '([{,;=<>!&|?:*/%+-~^':
                    continue  # 二元减法，跳过
            if val == 0:
                replacements = [1, -1]
            elif val == 1:
                replacements = [0, 2]
            elif val == -1:
                replacements = [0, 1]
            else:
                replacements = [val + 1, val - 1]
            for repl in replacements:
                mutants.append({
                    'id': 'CRC_{}_{}_{}'.format(i, val, repl),
                    'line': i + 1,
                    'operator': 'CRC',
                    'original': str(val),
                    'replacement': str(repl),
                    'description': 'L{}: {} -> {}'.format(i + 1, val, repl),
                })
    return mutants


def generate_rvf_mutants(lines, func_start, func_end):
    mutants = []
    for i in range(func_start, func_end):
        line = lines[i]
        stripped = line.strip()
        for original, replacements in RVF_BOOL_MAP.items():
            if original in stripped:
                for repl in replacements:
                    mutants.append({
                        'id': 'RVF_{}_{}_{}'.format(i, original, repl),
                        'line': i + 1,
                        'operator': 'RVF',
                        'original': original,
                        'replacement': repl,
                        'description': 'L{}: {} -> {}'.format(i + 1, original, repl),
                    })
        return_int = re.search(r'return\s+(-?\d+)\s*;', stripped)
        if return_int:
            val = int(return_int.group(1))
            for repl in [val + 1, val - 1]:
                if repl != val:
                    mutants.append({
                        'id': 'RVF_int_{}_{}_{}'.format(i, val, repl),
                        'line': i + 1,
                        'operator': 'RVF',
                        'original': 'return {}'.format(val),
                        'replacement': 'return {}'.format(repl),
                        'description': 'L{}: return {} -> return {}'.format(i + 1, val, repl),
                    })
    return mutants


# ═══════════════════════════════════════════════════════════════
# 函数定位 (正则; 生产环境可换 MCP get_code_snippet)
# ═══════════════════════════════════════════════════════════════

def find_function_range(lines, function_name, source_file=""):
    """在源码中找到函数的行范围 (0-indexed, 半开区间 [start, end))"""
    short_name = function_name.split('::')[-1] if '::' in function_name else function_name
    class_name = function_name.split('::')[0] if '::' in function_name else None

    in_func = False
    brace_depth = 0
    func_start = -1
    func_end = -1

    for i, line in enumerate(lines):
        if not in_func and re.search(r'\b' + re.escape(short_name) + r'\s*\(', line):
            # 注意: \bstringIsDigit\s*\( 不会匹配 stringIsDigitPro( 因为 Pro 在中间
            if class_name and class_name not in line and i > 0:
                # Bug #6: 放宽 class_name 检查 — 搜索多行上下文 (含 .h 内联方法)
                context_lines = 10
                found = False
                for j in range(max(0, i - context_lines), i):
                    if class_name in lines[j]:
                        found = True
                        break
                if not found:
                    # .h 文件中的内联方法: 跳过 class_name 检查
                    if not source_file.endswith('.h'):
                        continue
            in_func = True
            func_start = i
            brace_depth = 0

        if in_func:
            brace_depth += line.count('{') - line.count('}')
            if brace_depth <= 0 and '{' in ''.join(lines[func_start:i + 1]):
                func_end = i + 1
                break

    return func_start, func_end


def apply_mutation(lines, mutant):
    """应用变异到源码行, 返回新行列表"""
    new_lines = lines[:]
    line_idx = mutant['line'] - 1
    original = mutant['original']
    replacement = mutant['replacement']
    new_lines[line_idx] = new_lines[line_idx].replace(original, replacement, 1)
    return new_lines


# ═══════════════════════════════════════════════════════════════
# 编译和测试 (核心改造: 增量编译 + GTest)
# ═══════════════════════════════════════════════════════════════

def run_command(cmd, cwd=None, env=None, timeout=120):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd, env=full_env, timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, '', 'Timeout({}s)'.format(timeout)
    except Exception as e:
        return -2, '', str(e)


def compile_and_test(source_file, mutant_lines, build_dir, test_target,
                     gtest_filter=None, timeout=180):
    """
    应用变异 -> 增量编译 -> 跑 GTest -> 恢复源码

    返回:
      ('killed', output)       - 测试失败 (变异被杀死)
      ('survived', output)     - 测试通过 (变异存活)
      ('compile_failed', err)  - 编译失败
    """
    backup_path = source_file + '.mutation_backup'
    shutil.copy2(source_file, backup_path)
    restore_entry = (backup_path, source_file)
    _PENDING_RESTORES.append(restore_entry)
    _MUTATED_FILES.add(source_file)  # 记录被变异的文件, 退出时校验

    try:
        # 2. 写入变异后的源码
        with open(source_file, 'w') as f:
            f.writelines(mutant_lines)

        # 3. 增量编译 (cmake --build . --target, cwd=build_dir)
        #    不 make clean; 禁 ccache 避免变异被缓存跳过
        build_cmd = ['cmake', '--build', '.', '--target', test_target,
                     '--', '-j{}'.format(os.cpu_count() or 4)]
        rc, stdout, stderr = run_command(
            build_cmd, cwd=build_dir, timeout=timeout,
            env={'CCACHE_DISABLE': '1'}
        )

        if rc != 0:
            return 'compile_failed', stderr[-500:] if stderr else stdout[-500:]

        # 4. 定位测试二进制 (多路径搜索 + 递归兜底)
        test_exe = ''
        search_paths = [
            os.path.join(build_dir, 'tests', test_target),
            os.path.join(build_dir, 'autotests', 'src', test_target),
            os.path.join(build_dir, test_target),
        ]
        for candidate in search_paths:
            if os.path.exists(candidate):
                test_exe = os.path.abspath(candidate)
                break
        if not test_exe:
            # 递归搜索
            for root, dirs, files in os.walk(build_dir):
                if test_target in files:
                    candidate = os.path.join(root, test_target)
                    if os.access(candidate, os.X_OK):
                        test_exe = os.path.abspath(candidate)
                        break
        if not test_exe:
            return 'test_not_found', 'Test executable not found: {} in {}'.format(test_target, build_dir)
        # 转绝对路径, 避免 subprocess cwd=build_dir 导致相对路径叠加
        test_exe = os.path.abspath(test_exe)

        # 5. 跑 GTest (可选 --gtest_filter 只跑相关用例, 加速)
        test_cmd = [test_exe]
        if gtest_filter:
            test_cmd.extend(['--gtest_filter={}'.format(gtest_filter)])

        rc, stdout, stderr = run_command(
            test_cmd, cwd=build_dir, timeout=60,
            env={'QT_QPA_PLATFORM': 'offscreen', 'CCACHE_DISABLE': '1'}
        )

        # 6. GTest 判定: 退出码非0 = 有用例失败 = killed
        #    (GTest 返回 0 全过, 非0 有失败; 也检查输出含 [  FAILED  ])
        if rc != 0 or '[  FAILED  ]' in stdout:
            return 'killed', stdout[-500:] if stdout else stderr[-500:]
        else:
            return 'survived', stdout[-200:] if stdout else ''

    finally:
        # 恢复原始文件 (铁律: 必恢复; 不重新编译——下次变异写入会触发增量重编)
        if os.path.exists(backup_path):
            shutil.move(backup_path, source_file)
        if restore_entry in _PENDING_RESTORES:
            _PENDING_RESTORES.remove(restore_entry)


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

THRESHOLD = 85.0  # 变异得分阈值 (可配)


def run_mutation_testing(source_file, function_name, func_start, func_end,
                         build_dir, test_target, gtest_filter=None, max_mutants=20):
    """对单个函数运行变异测试"""
    print("\n" + "=" * 60)
    print("  变异测试: {} (L{}-L{})".format(function_name, func_start + 1, func_end))
    print("=" * 60)

    with open(source_file) as f:
        lines = f.readlines()

    # 生成所有变异体
    all_mutants = []
    all_mutants.extend(generate_aor_mutants(lines, func_start, func_end))
    all_mutants.extend(generate_ror_mutants(lines, func_start, func_end))
    all_mutants.extend(generate_lor_mutants(lines, func_start, func_end))
    all_mutants.extend(generate_crc_mutants(lines, func_start, func_end))
    all_mutants.extend(generate_rvf_mutants(lines, func_start, func_end))

    # 去重
    seen_ids = set()
    unique_mutants = []
    for m in all_mutants:
        if m['id'] not in seen_ids:
            seen_ids.add(m['id'])
            unique_mutants.append(m)

    if len(unique_mutants) > max_mutants:
        # Bug #8: 分层截断 (stratified) — 按算子类型均匀分配配额
        by_op = defaultdict(list)
        for m in unique_mutants:
            by_op[m['operator']].append(m)
        operator_order = ['AOR', 'ROR', 'LOR', 'CRC', 'RVF']
        per_op_quota = max(1, max_mutants // len(operator_order))
        sampled = []
        remaining_quota = max_mutants
        for op in operator_order:
            if op in by_op and remaining_quota > 0:
                quota = min(per_op_quota, remaining_quota, len(by_op[op]))
                sampled.extend(by_op[op][:quota])
                remaining_quota -= quota
        # 余量分配给剩余算子
        if remaining_quota > 0:
            existing_ids = {m['id'] for m in sampled}
            for op in operator_order:
                if op in by_op:
                    for m in by_op[op]:
                        if m['id'] not in existing_ids and remaining_quota > 0:
                            sampled.append(m)
                            existing_ids.add(m['id'])
                            remaining_quota -= 1
        unique_mutants = sampled
        print("  变异体 {} 个, 截断为 {} 个 (分层采样)".format(
            len(seen_ids), max_mutants))
    else:
        print("  变异体: {} 个".format(len(unique_mutants)))

    by_operator = defaultdict(int)
    for m in unique_mutants:
        by_operator[m['operator']] += 1
    for op, count in sorted(by_operator.items()):
        print("    {}: {} 个".format(op, count))

    results = []
    killed = 0
    survived = 0
    compile_failed = 0
    test_not_found = 0

    for i, mutant in enumerate(unique_mutants):
        mutated_lines = apply_mutation(lines, mutant)

        status, output = compile_and_test(
            source_file, mutated_lines, build_dir, test_target, gtest_filter
        )

        results.append({
            'id': mutant['id'],
            'operator': mutant['operator'],
            'line': mutant['line'],
            'description': mutant['description'],
            'status': status,
            'output_snippet': output[:200] if status != 'survived' else '',
        })

        if status == 'killed':
            killed += 1
            marker = 'KILLED  '
        elif status == 'survived':
            survived += 1
            marker = 'SURVIVED'
        elif status == 'test_not_found':
            test_not_found += 1
            marker = 'NOTFOUND'
        else:
            compile_failed += 1
            marker = 'CFAIL  '

        print("  [{:>2}/{}] {} — {}".format(
            i + 1, len(unique_mutants), marker, mutant['description']))

    total_valid = killed + survived
    score = round(killed / total_valid * 100, 1) if total_valid > 0 else 0

    print("\n  结果: killed={}, survived={}, compile_failed={}, test_not_found={}".format(
        killed, survived, compile_failed, test_not_found))
    print("  变异得分: {}/{} = {:.1f}% (阈值 {:.0f}%)".format(
        killed, total_valid, score, THRESHOLD))
    if total_valid > 0:
        verdict = 'PASS' if score >= THRESHOLD else 'BELOW_THRESHOLD'
        print("  判定: {} ({})".format(
            verdict,
            '有效性达标' if score >= THRESHOLD else '存活变异体过多,建议补强测试'))

    return {
        'function': function_name,
        'file': source_file,
        'line_range': [func_start + 1, func_end],
        'total_mutants': len(unique_mutants),
        'killed': killed,
        'survived': survived,
        'compile_failed': compile_failed,
        'test_not_found': test_not_found,
        'mutation_score': score,
        'threshold': THRESHOLD,
        'details': results,
    }


def generate_report(results, output_path, project=None, base_sha=None, config=None):
    """生成变异测试报告 (Markdown + .ut-mutation.json)"""
    lines = []
    lines.append("# 变异测试报告 (Mode 4)")
    lines.append("")
    lines.append("## 概述")
    lines.append("")

    total_killed = sum(r['killed'] for r in results)
    total_survived = sum(r['survived'] for r in results)
    total_valid = total_killed + total_survived
    overall_score = round(total_killed / total_valid * 100, 1) if total_valid > 0 else 0

    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append("| 测试函数 | {} |".format(len(results)))
    lines.append("| 变异体总数 | {} |".format(sum(r['total_mutants'] for r in results)))
    lines.append("| 编译成功 | {} |".format(total_killed + total_survived))
    lines.append("| 编译失败 | {} |".format(sum(r['compile_failed'] for r in results)))
    lines.append("| 测试目标未找到 | {} |".format(sum(r.get('test_not_found', 0) for r in results)))
    lines.append("| 杀死 (Killed) | {} |".format(total_killed))
    lines.append("| 存活 (Survived) | {} |".format(total_survived))
    lines.append("| **变异得分** | **{:.1f}%** (阈值 {:.0f}%) |".format(overall_score, THRESHOLD))
    lines.append("")

    lines.append("## 按函数详情")
    lines.append("")
    lines.append("| 函数 | 变异体 | 杀死 | 存活 | 编译失败 | 得分 | 判定 |")
    lines.append("|------|--------|------|------|---------|------|------|")
    for r in results:
        verdict = 'PASS' if r['mutation_score'] >= THRESHOLD else 'BELOW'
        lines.append("| {} | {} | {} | {} | {} | {:.1f}% | {} |".format(
            r['function'], r['total_mutants'],
            r['killed'], r['survived'], r['compile_failed'],
            r['mutation_score'], verdict))
    lines.append("")

    # 存活变异体详情 (补强建议清单)
    survived_details = []
    for r in results:
        for d in r['details']:
            if d['status'] == 'survived':
                survived_details.append({'function': r['function'], **d})

    if survived_details:
        lines.append("## 存活变异体 (测试缺口 — 补强建议)")
        lines.append("")
        lines.append("> Mode 4 只出建议不自动补强。以下存活变异体表示测试未覆盖该变异,")
        lines.append("> 建议回 Mode 2 用 incremental-updater 补强对应用例。")
        lines.append("")
        lines.append("| 函数 | 算子 | 行 | 变异描述 |")
        lines.append("|------|------|----|---------|")
        for d in survived_details:
            lines.append("| {} | {} | L{} | {} |".format(
                d['function'], d['operator'], d['line'], d['description']))
        lines.append("")

    # 编译失败变异体
    cfail_details = []
    for r in results:
        for d in r['details']:
            if d['status'] == 'compile_failed':
                cfail_details.append({'function': r['function'], **d})

    if cfail_details:
        lines.append("## 编译失败变异体 (不计入分母)")
        lines.append("")
        lines.append("| 函数 | 算子 | 行 | 变异描述 |")
        lines.append("|------|------|----|---------|")
        for d in cfail_details:
            lines.append("| {} | {} | L{} | {} |".format(
                d['function'], d['operator'], d['line'], d['description']))
        lines.append("")

    # 按算子统计
    lines.append("## 按算子统计")
    lines.append("")
    by_op = defaultdict(lambda: {'killed': 0, 'survived': 0, 'compile_failed': 0, 'test_not_found': 0})
    for r in results:
        for d in r['details']:
            by_op[d['operator']][d['status']] += 1

    lines.append("| 算子 | 杀死 | 存活 | 编译失败 | 杀死率 |")
    lines.append("|------|------|------|---------|--------|")
    for op in sorted(by_op.keys()):
        stats = by_op[op]
        total = stats['killed'] + stats['survived']
        rate = '{:.1f}%'.format(stats['killed'] / total * 100) if total > 0 else 'N/A'
        lines.append("| {} | {} | {} | {} | {} |".format(
            op, stats['killed'], stats['survived'],
            stats['compile_failed'], rate))
    lines.append("")

    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))

    # JSON 报告 (.ut-mutation.json — 与 .ut-inventory.json 命名对齐)
    json_path = os.path.join(os.path.dirname(output_path), '.ut-mutation.json')

    total_killed = sum(r['killed'] for r in results)
    total_survived = sum(r['survived'] for r in results)
    total_valid = total_killed + total_survived
    overall_score = round(total_killed / total_valid * 100, 1) if total_valid > 0 else 0

    mutation_json = {
        "version": 1,
        "project": project or os.path.basename(_PROJECT_DIR or '.'),
        "base_sha": base_sha or "",
        "timestamp": datetime.now().isoformat(timespec='seconds'),
        "config": config or {},
        "summary": {
            "total_mutants": sum(r['total_mutants'] for r in results),
            "killed": total_killed,
            "survived": total_survived,
            "compile_failed": sum(r['compile_failed'] for r in results),
            "test_not_found": sum(r.get('test_not_found', 0) for r in results),
            "mutation_score": overall_score,
            "verdict": "PASS" if overall_score >= THRESHOLD else "BELOW_THRESHOLD"
        },
        "functions": []
    }

    for r in results:
        func_data = {
            "function": r['function'],
            "file": _relative_path(r['file'], _PROJECT_DIR) if _PROJECT_DIR else r['file'],
            "line_range": r['line_range'],
            "total_mutants": r['total_mutants'],
            "killed": r['killed'],
            "survived": r['survived'],
            "compile_failed": r['compile_failed'],
            "mutation_score": r['mutation_score'],
            "verdict": "PASS" if r['mutation_score'] >= THRESHOLD else "BELOW_THRESHOLD",
            "details": r['details']
        }
        mutation_json["functions"].append(func_data)

    with open(json_path, 'w') as f:
        json.dump(mutation_json, f, indent=2, ensure_ascii=False)

    return output_path, json_path


def main():
    parser = argparse.ArgumentParser(
        description='Mode 4: 变异测试 (qt-autotest-generator)',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', default=None,
                        help='直接模式: 源文件路径 (相对项目根或绝对)')
    parser.add_argument('--function', default=None,
                        help='直接模式: 函数全限定名 (Class::method)，多个用逗号分隔')
    parser.add_argument('--inventory', default=None,
                        help='inventory 模式: .ut-inventory.json 路径')
    parser.add_argument('--all-high', action='store_true',
                        help='inventory 模式: 对所有 high 级 testable 方法跑变异')
    parser.add_argument('--build-dir', required=True,
                        help='构建目录 (需已 cmake 配置)')
    parser.add_argument('--test-target', default=None,
                        help='GTest 测试 target 名 (如 deepin-calculator-test); inventory 模式可省略，自动推断 test_<小写类名>')
    parser.add_argument('--gtest-filter', default=None,
                        help='GTest 过滤器 (如 *stringIsDigit*), 加速只跑相关用例')
    parser.add_argument('--max-mutants', type=int, default=20,
                        help='每函数最大变异体数 (默认 20)')
    parser.add_argument('--project-dir', default='.',
                        help='项目根目录 (用于 git diff 校验)')
    parser.add_argument('--threshold', type=float, default=85.0,
                        help='变异得分阈值 (默认 85)')
    args = parser.parse_args()

    global _PROJECT_DIR, THRESHOLD
    _PROJECT_DIR = os.path.abspath(args.project_dir)
    THRESHOLD = args.threshold

    # 校验 build 目录
    if not os.path.isdir(args.build_dir):
        print("[Mode 4] 错误: 构建目录不存在: {}".format(args.build_dir))
        sys.exit(1)

    # 确定目标函数列表
    targets = []  # [(source_file, function_name), ...]

    if args.source and args.function:
        # 直接模式 (支持逗号分隔多函数)
        source_abs = args.source if os.path.isabs(args.source) \
            else os.path.join(_PROJECT_DIR, args.source)
        if not os.path.exists(source_abs):
            print("[Mode 4] 错误: 源文件不存在: {}".format(source_abs))
            sys.exit(1)
        for fn in (f.strip() for f in args.function.split(',')):
            targets.append((source_abs, fn))
        print("[Mode 4] 直接模式: {}::{}".format(args.source, args.function))
    elif args.inventory and args.all_high:
        # inventory 模式
        with open(args.inventory) as f:
            inv = json.load(f)
        for m in inv.get('methods', []):
            if m.get('level') == 'high' and m.get('testable'):
                # Bug #1: inventory 字段名为 file_path，非 file
                src_rel = m.get('file_path', m.get('file', ''))
                source_abs = os.path.join(_PROJECT_DIR, src_rel) if src_rel and not os.path.isabs(src_rel) else (src_rel or '')
                # Bug #2: qualified_name 点分隔 → Class::Method
                qn = m.get('qualified_name', '')
                parts = qn.split('.')
                if len(parts) >= 2:
                    class_method = '{}::{}'.format(parts[-2], parts[-1])
                else:
                    class_method = qn
                targets.append((source_abs, class_method))
        print("[Mode 4] inventory 模式: {} 个 high 级方法".format(len(targets)))
    else:
        parser.error("需要 --source + --function (直接模式) 或 --inventory + --all-high (inventory 模式)")

    if not targets:
        print("[Mode 4] 无目标函数")
        sys.exit(0)

    print("[Mode 4] 变异测试")
    print("  目标函数: {} 个".format(len(targets)))
    print("  构建目录: {}".format(args.build_dir))
    print("  测试目标: {}".format(args.test_target))
    print("  GTest过滤: {}".format(args.gtest_filter or '(none)'))
    print("  最大变异体/函数: {}".format(args.max_mutants))
    print("  阈值: {:.0f}%".format(THRESHOLD))
    print("  禁 ccache: CCACHE_DISABLE=1")

    all_results = []

    for source_file, func_name in targets:
        if not os.path.exists(source_file):
            print("  跳过: 源文件不存在: {}".format(source_file))
            continue

        # Bug #7: inventory 模式自动推断 test_target
        current_test_target = args.test_target
        if not current_test_target:
            # 从 Class::Method 推断 test_<小写类名>
            if '::' in func_name:
                class_part = func_name.split('::')[0]
                current_test_target = 'test_' + class_part[0].lower() + class_part[1:]
            else:
                current_test_target = args.test_target  # None → 后续会报错
            print("  推断测试目标: {} → {}".format(func_name, current_test_target))

        with open(source_file) as f:
            lines = f.readlines()

        func_start, func_end = find_function_range(lines, func_name, source_file)

        if func_start < 0 or func_end < 0:
            print("  跳过: 未找到函数 {} 在 {}".format(func_name, source_file))
            continue

        result = run_mutation_testing(
            source_file, func_name, func_start, func_end,
            args.build_dir, current_test_target,
            args.gtest_filter, args.max_mutants
        )
        all_results.append(result)

    if not all_results:
        print("\n[Mode 4] 无有效结果")
        sys.exit(0)

    # 生成报告
    report_path = os.path.join(args.build_dir, 'mutation_report.md')

    # 元数据 (用于 .ut-mutation.json 顶层字段)
    base_sha = ''
    try:
        base_sha = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=_PROJECT_DIR, stderr=subprocess.DEVNULL
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    config = {
        'threshold': THRESHOLD,
        'max_mutants_per_function': args.max_mutants,
        'test_target': args.test_target,
    }
    if args.gtest_filter:
        config['gtest_filter'] = args.gtest_filter

    md_path, json_path = generate_report(
        all_results, report_path,
        project=os.path.basename(_PROJECT_DIR) if _PROJECT_DIR else None,
        base_sha=base_sha,
        config=config
    )

    # 总结
    total_killed = sum(r['killed'] for r in all_results)
    total_survived = sum(r['survived'] for r in all_results)
    total_valid = total_killed + total_survived
    overall_score = round(total_killed / total_valid * 100, 1) if total_valid > 0 else 0

    print("\n" + "=" * 60)
    print("[Mode 4] 变异测试完成")
    print("  变异体: {} 个".format(sum(r['total_mutants'] for r in all_results)))
    print("  杀死: {}, 存活: {}, 编译失败: {}".format(
        total_killed, total_survived, sum(r['compile_failed'] for r in all_results)))
    print("  变异得分: {:.1f}% (阈值 {:.0f}%)".format(overall_score, THRESHOLD))
    print("  报告: {}".format(md_path))
    print("  JSON: {}".format(json_path))

    # 铁律 #4: 退出时 git diff 校验
    print("\n[源码安全校验] git diff --exit-code ...")
    clean = _git_diff_check()
    if clean:
        print("  ✅ 源码无改动, Mode 4 安全退出")
    else:
        print("  ❌ 源码未恢复干净!")
        sys.exit(2)


if __name__ == '__main__':
    main()
