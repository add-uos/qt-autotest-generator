#!/usr/bin/env python3
"""
批量从 MCP 收集项目 inventory + 测试映射
项目清单唯一来源: projects.json 注册表 (由 sync-registry-from-mcp.py 维护)
用法: python3 batch-collect.py [--skip-fetch-mcp] [--skip-test-mapping] [--parallel N]
"""
import json, os, sys, subprocess, argparse, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = Path(__file__).resolve().parent
FETCH_MCP = SCRIPT_DIR / "fetch-mcp-data.py"
FETCH_TM  = SCRIPT_DIR / "fetch-test-mapping.py"
BASE_DIR  = SCRIPT_DIR.parent / "mcp-projects"
REGISTRY  = SCRIPT_DIR.parent / "projects.json"


def require_registry_projects():
    """从 projects.json 注册表取启用项目 (mcp_name, gh_name, size)。缺失/为空时直接退出。"""
    try:
        reg = json.loads(REGISTRY.read_text("utf-8"))
    except OSError:
        sys.exit(f"❌ 未找到注册表 {REGISTRY}\n   生成: python3 scripts/sync-registry-from-mcp.py")
    except ValueError as e:
        sys.exit(f"❌ 注册表 JSON 解析失败: {e}\n   重新生成: python3 scripts/sync-registry-from-mcp.py")
    projects = [(p.get("mcp_name") or p["name"], p["name"], p.get("size", "?"))
                for p in reg.get("projects", []) if p.get("enabled", True)]
    if not projects:
        sys.exit("❌ 注册表无启用项目 — 在编辑器「设置」页勾选启用, 或运行 sync-registry-from-mcp.py")
    return projects

def run_step(cmd, label, project_dir):
    """运行子进程命令，返回 (success, elapsed_sec, output_tail)"""
    t0 = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600,
        env={**os.environ, "PYTHONUNBUFFERED": "1"}
    )
    elapsed = time.time() - t0
    tail = (result.stdout + result.stderr)[-500:]
    ok = result.returncode == 0
    return ok, elapsed, tail


def collect_project(mcp_name, gh_name, skip_fetch_mcp=False, skip_tm=False):
    """收集单个项目: fetch-mcp-data + fetch-test-mapping"""
    proj_dir = BASE_DIR / gh_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    inventory_path = proj_dir / ".ut-inventory.json"
    mapping_path = proj_dir / "test-mapping.json"
    report_path = proj_dir / "test-mapping-report.md"
    log_path = proj_dir / "collect.log"

    log_f = open(log_path, "w")
    results = {"project": gh_name, "mcp_name": mcp_name, "steps": {}}

    def log(msg):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        log_f.write(line + "\n")
        log_f.flush()

    log(f"=== 开始收集 {gh_name} ===")

    # Step 1: fetch-mcp-data.py
    if not skip_fetch_mcp and not inventory_path.exists():
        cmd = [sys.executable, str(FETCH_MCP), "--project", mcp_name,
               "--output", str(inventory_path)]
        log(f"Step 1: fetch-mcp-data.py ...")
        ok, elapsed, tail = run_step(cmd, "fetch-mcp", proj_dir)
        results["steps"]["fetch_mcp"] = {"ok": ok, "elapsed": round(elapsed, 1)}
        if ok:
            log(f"  ✅ fetch-mcp 完成 ({elapsed:.1f}s)")
        else:
            log(f"  ❌ fetch-mcp 失败 ({elapsed:.1f}s): {tail[-200:]}")
            log_f.close()
            return results
    elif inventory_path.exists():
        log(f"Step 1: inventory 已存在，跳过")
        results["steps"]["fetch_mcp"] = {"ok": True, "elapsed": 0, "skipped": True}
    else:
        log(f"Step 1: 跳过 (--skip-fetch-mcp)")
        results["steps"]["fetch_mcp"] = {"ok": True, "elapsed": 0, "skipped": True}

    # Step 2: fetch-test-mapping.py
    if not skip_tm:
        cmd = [sys.executable, str(FETCH_TM), "--project", mcp_name,
               "--inventory", str(inventory_path),
               "--mapping-out", str(mapping_path),
               "--report", str(report_path)]
        log(f"Step 2: fetch-test-mapping.py ...")
        ok, elapsed, tail = run_step(cmd, "fetch-tm", proj_dir)
        results["steps"]["test_mapping"] = {"ok": ok, "elapsed": round(elapsed, 1)}
        if ok:
            log(f"  ✅ test-mapping 完成 ({elapsed:.1f}s)")
        else:
            log(f"  ❌ test-mapping 失败 ({elapsed:.1f}s): {tail[-200:]}")
    else:
        log(f"Step 2: 跳过 (--skip-test-mapping)")
        results["steps"]["test_mapping"] = {"ok": True, "elapsed": 0, "skipped": True}

    # Step 3: 统计
    if inventory_path.exists():
        try:
            with open(inventory_path) as f:
                inv = json.load(f)
            methods = inv.get("methods", [])
            testable = [m for m in methods if m.get("testable", True)]
            high = [m for m in testable if m.get("level") == "high"]
            mid  = [m for m in testable if m.get("level") == "mid"]
            with_tc = [m for m in testable if m.get("test_cover_count", 0) > 0]
            no_tc_high = [m for m in high if m.get("test_cover_count", 0) == 0]
            no_tc_mid  = [m for m in mid if m.get("test_cover_count", 0) == 0]
            results["stats"] = {
                "total_methods": len(methods),
                "testable": len(testable),
                "high": len(high), "mid": len(mid),
                "with_test_cover": len(with_tc),
                "no_cover_high": len(no_tc_high),
                "no_cover_mid": len(no_tc_mid),
            }
            log(f"  📊 总方法 {len(methods)}, 可测 {len(testable)}, "
                f"high {len(high)}, mid {len(mid)}, "
                f"有测试覆盖 {len(with_tc)}, "
                f"高优无覆盖 {len(no_tc_high)}, 中优无覆盖 {len(no_tc_mid)}")
        except Exception as e:
            log(f"  ⚠️ 统计失败: {e}")

    log(f"=== {gh_name} 完成 ===\n")
    log_f.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="批量从 MCP 收集项目 inventory + 测试映射")
    parser.add_argument("--skip-fetch-mcp", action="store_true", help="跳过 fetch-mcp-data.py")
    parser.add_argument("--skip-test-mapping", action="store_true", help="跳过 fetch-test-mapping.py")
    parser.add_argument("--parallel", type=int, default=1, help="并行数 (建议 1~3，MCP 单线程)")
    parser.add_argument("--filter", type=str, default=None, help="过滤项目名 (子串匹配)")
    parser.add_argument("--size", type=str, default=None, help="只跑指定规模: S/M/L/XL")
    args = parser.parse_args()

    BASE_DIR.mkdir(parents=True, exist_ok=True)

    projects = require_registry_projects()
    if args.filter:
        projects = [p for p in projects if args.filter.lower() in p[1].lower()]
    if args.size:
        projects = [p for p in projects if p[2] == args.size.upper()]

    if not projects:
        print("没有匹配的项目")
        return

    print(f"📋 计划收集 {len(projects)} 个项目")
    print(f"   输出目录: {BASE_DIR}")
    print(f"   并行数: {args.parallel}")
    print()

    all_results = []
    t0 = time.time()

    if args.parallel <= 1:
        for mcp_name, gh_name, size in projects:
            r = collect_project(mcp_name, gh_name,
                                skip_fetch_mcp=args.skip_fetch_mcp,
                                skip_tm=args.skip_test_mapping)
            all_results.append(r)
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {
                pool.submit(collect_project, mcp_name, gh_name,
                            skip_fetch_mcp=args.skip_fetch_mcp,
                            skip_tm=args.skip_test_mapping): gh_name
                for mcp_name, gh_name, size in projects
            }
            for f in as_completed(futures):
                try:
                    r = f.result()
                    all_results.append(r)
                except Exception as e:
                    print(f"❌ {futures[f]} 异常: {e}")
                    all_results.append({"project": futures[f], "error": str(e)})

    elapsed = time.time() - t0

    # 汇总报告
    summary_path = BASE_DIR / "_summary.json"
    summary = {
        "total_projects": len(projects),
        "elapsed_sec": round(elapsed, 1),
        "results": all_results,
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # 打印汇总表
    print("\n" + "=" * 80)
    print("📊 收集汇总")
    print("=" * 80)
    print(f"{'项目':<28} {'总方法':>6} {'可测':>6} {'H':>4} {'M':>4} {'TC覆盖':>6} {'H无覆盖':>7} {'M无覆盖':>7}")
    print("-" * 80)

    total_methods = 0; total_testable = 0; total_high = 0; total_mid = 0
    total_with_tc = 0; total_no_h = 0; total_no_m = 0
    ok_count = 0; fail_count = 0

    for r in sorted(all_results, key=lambda x: x.get("project", "")):
        name = r.get("project", "?")
        st = r.get("stats", {})
        steps = r.get("steps", {})
        fm_ok = steps.get("fetch_mcp", {}).get("ok", False)
        tm_ok = steps.get("test_mapping", {}).get("ok", False)

        if not st:
            status = "✅" if fm_ok and tm_ok else "❌"
            print(f"{name:<28} {'—':>6} {'—':>6} {'—':>4} {'—':>4} {'—':>6} {'—':>7} {'—':>7}  {status}")
            if not (fm_ok and tm_ok):
                fail_count += 1
            else:
                ok_count += 1
            continue

        tm = st.get("total_methods", 0)
        tb = st.get("testable", 0)
        h  = st.get("high", 0)
        m  = st.get("mid", 0)
        tc = st.get("with_test_cover", 0)
        nh = st.get("no_cover_high", 0)
        nm = st.get("no_cover_mid", 0)
        total_methods += tm; total_testable += tb
        total_high += h; total_mid += m
        total_with_tc += tc; total_no_h += nh; total_no_m += nm
        ok_count += 1
        print(f"{name:<28} {tm:>6} {tb:>6} {h:>4} {m:>4} {tc:>6} {nh:>7} {nm:>7}")

    print("-" * 80)
    print(f"{'合计':<28} {total_methods:>6} {total_testable:>6} {total_high:>4} {total_mid:>4} {total_with_tc:>6} {total_no_h:>7} {total_no_m:>7}")
    print()
    print(f"✅ 成功: {ok_count}  ❌ 失败: {fail_count}  ⏱ 总耗时: {elapsed:.1f}s")
    print(f"📁 汇总: {summary_path}")


if __name__ == "__main__":
    main()
