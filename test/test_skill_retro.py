# SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mode 7：skill-retro.py 技能复盘脚本单测。

覆盖：add 自增 id/参数校验、resolve/reopen、list 过滤、
build_report 纯函数、report 写盘、eval-suggest（open/--all）。
"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "qt-autotest-generator" / "scripts" / "skill-retro.py"

_spec = None
if "skill_retro" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("skill_retro", _SCRIPT)
    sr = importlib.util.module_from_spec(_spec)
    sys.modules["skill_retro"] = sr
    _spec.loader.exec_module(sr)
else:
    sr = sys.modules["skill_retro"]


@pytest.fixture
def backlog(tmp_path):
    return str(tmp_path / "backlog.json")


class TestAdd:
    def test_add_auto_id_and_fields(self, backlog):
        id1 = sr.cmd_add(backlog, "S", "假绿事故", severity="P0",
                         evidence="verify 0 例 exit 0", fix="等号形式")
        id2 = sr.cmd_add(backlog, "S", "第二条")
        assert (id1, id2) == ("S-001", "S-002")
        data = json.load(open(backlog))
        it = data["items"][0]
        assert it["category"] == "S" and it["severity"] == "P0"
        assert it["status"] == "open" and it["evidence"] and it["fix"]
        assert it["created"] and it["updated"]

    def test_add_id_per_category(self, backlog):
        sr.cmd_add(backlog, "G", "文档缺口")
        assert sr.cmd_add(backlog, "T", "触发") == "T-001"

    def test_add_invalid_category(self, backlog):
        with pytest.raises(ValueError, match="category"):
            sr.cmd_add(backlog, "X", "坏类别")

    def test_add_invalid_severity(self, backlog):
        with pytest.raises(ValueError, match="severity"):
            sr.cmd_add(backlog, "S", "x", severity="P3")

    def test_add_no_id_reuse_after_manual_delete(self, backlog):
        """回归：删除中间项后 add 不得复用 id（防 _find 改错记录）。"""
        import json as _json
        i1 = sr.cmd_add(backlog, "S", "一")
        sr.cmd_add(backlog, "S", "二")
        d = _json.load(open(backlog))
        d["items"] = [i for i in d["items"] if i["id"] != i1]
        _json.dump(d, open(backlog, "w"))
        i3 = sr.cmd_add(backlog, "S", "三")
        assert i3 == "S-003" and i3 != i1


class TestLifecycle:
    def _seed(self, backlog, n=2):
        return [sr.cmd_add(backlog, "S", f"问题{i}") for i in range(n)]

    def test_resolve_and_reopen(self, backlog):
        i1, _ = self._seed(backlog)
        it = sr.cmd_resolve(backlog, i1, note="commit abc")
        assert it["status"] == "resolved" and it["note"] == "commit abc"
        it = sr.cmd_reopen(backlog, i1, note="复发")
        assert it["status"] == "open" and it["note"] == "复发"

    def test_unknown_id_raises(self, backlog):
        self._seed(backlog)
        with pytest.raises(ValueError, match="未找到"):
            sr.cmd_resolve(backlog, "Z-999")


class TestList:
    def test_filters(self, backlog):
        sr.cmd_add(backlog, "S", "s1", severity="P0")
        sr.cmd_add(backlog, "G", "g1", severity="P1")
        sr.cmd_add(backlog, "S", "s2", severity="P1", status="resolved")
        assert len(sr.cmd_list(backlog)) == 3
        assert [i["title"] for i in sr.cmd_list(backlog, status="open")] == ["s1", "g1"]
        assert [i["id"] for i in sr.cmd_list(backlog, severity="P1")] == ["G-001", "S-002"]
        assert [i["title"] for i in sr.cmd_list(backlog, category="S")] == ["s1", "s2"]


class TestReport:
    def test_build_report_counts_and_sections(self, backlog):
        sr.cmd_add(backlog, "S", "假绿", severity="P0", evidence="e1", fix="f1")
        sr.cmd_add(backlog, "G", "缺口", severity="P1")
        sr.cmd_add(backlog, "T", "路由", severity="P2", status="resolved", note="done")
        items = sr.cmd_list(backlog)
        md = sr.build_report(items, backlog)
        assert "共 3 项：open 2 / resolved 1" in md
        assert "## P0" in md and "### S-001 假绿" in md
        assert "证据：e1" in md and "修复建议：f1" in md
        assert "## 已解决" in md and "**T-001** 路由" in md

    def test_report_writes_file(self, backlog, tmp_path):
        sr.cmd_add(backlog, "S", "x")
        out = sr.cmd_report(backlog, out_dir=str(tmp_path / "r"))
        assert os.path.exists(out) and "S-001" in open(out).read()


class TestEvalSuggest:
    def test_open_default_and_all(self, backlog):
        sr.cmd_add(backlog, "S", "开", severity="P0", evidence="ev", fix="fx")
        sr.cmd_add(backlog, "G", "闭", status="resolved")
        open_only = sr.cmd_eval_suggest(backlog)
        assert len(open_only) == 1 and open_only[0]["source"] == "S-001"
        assert open_only[0]["id"] == "retro-s-001"
        assert open_only[0]["success_criteria"] == ["遵循修复建议：fx"]
        everything = sr.cmd_eval_suggest(backlog, all_status=True)
        assert len(everything) == 2


class TestCli:
    def test_cli_add_list_resolve_roundtrip(self, backlog, capsys):
        assert sr.main(["--backlog", backlog, "add", "--category", "S",
                        "--title", "t1"]) == 0
        assert sr.main(["--backlog", backlog, "list"]) == 0
        assert "S-001" in capsys.readouterr().out
        assert sr.main(["--backlog", backlog, "resolve", "S-001",
                        "--note", "n"]) == 0
        # 已解决后再 list --status open 应为空
        assert sr.main(["--backlog", backlog, "list", "--status", "open"]) == 0
        assert "(空)" in capsys.readouterr().out

    def test_cli_error_exit_2(self, backlog):
        # argparse choices 校验失败抛 SystemExit(2)；未知 id 走 ValueError → 2
        with pytest.raises(SystemExit) as ei:
            sr.main(["--backlog", backlog, "add", "--category", "X",
                     "--title", "t"])
        assert ei.value.code == 2
        assert sr.main(["--backlog", backlog, "resolve", "NOPE"]) == 2

    def test_cli_eval_suggest_json(self, backlog, capsys):
        sr.cmd_add(backlog, "S", "x", severity="P1")
        assert sr.main(["--backlog", backlog, "eval-suggest"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data[0]["severity"] == "P1"
