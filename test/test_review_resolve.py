"""test-review.py（Mode 6）输入解析单元测试。

覆盖：测试文件识别、类名推断、commit 规格解析、git -z name-status 解析、
变更过滤、inventory 登记集合归一化、uncached 扫描、测试目录探测，以及
真实临时 git 仓库上的端到端（name_status / meta / extract / build_targets）。

纯逻辑全部用合成数据；git 边界用真实仓库（init→commit→modify→commit），
保证解析器与真实 git 输出格式一致。
"""
import json
import os
import subprocess

import pytest

from conftest import SCRIPTS_DIR


# ── 测试文件识别与类名推断 ────────────────────────────────────────────

class TestIsTestFile:
    @pytest.mark.parametrize("path", [
        "test_calculator.cpp",
        "autotests/core/test_calculator.cpp",
        "/abs/path/test_file_view.cpp",
        "foo_test.cpp",
        "sub/dir/foo_test.cpp",
        "ut_bitbutton.cpp",                      # deepin ut_ 前缀约定
        "tests/src/control/ut_memorybutton.cpp",
        "ut_dbusmanager_test.cpp",               # ut_ 前缀 + _test 后缀复合
    ])
    def test_positive(self, test_review, path):
        assert test_review.is_test_file(path)

    @pytest.mark.parametrize("path", [
        "calculator.cpp",          # 无前缀
        "test_calculator.h",       # 头文件
        "test_calculator.txt",     # 非源码
        "tests.cpp",               # 无下划线
        "mytest_foo.cpp",          # mytest_ 不是 test_ 前缀
        "test_",                   # 残缺
        "",                        # 空
    ])
    def test_negative(self, test_review, path):
        assert not test_review.is_test_file(path)

    def test_windows_sep(self, test_review):
        assert test_review.is_test_file("autotests\\core\\test_calc.cpp")


class TestClassHint:
    @pytest.mark.parametrize("path,expected", [
        ("test_calculator.cpp", "Calculator"),
        ("autotests/core/test_file_view.cpp", "FileView"),
        ("foo_test.cpp", "Foo"),
        ("helpers.cpp", "Helpers"),
        ("ut_bitbutton.cpp", "Bitbutton"),
        ("ut_lockworker.cpp", "Lockworker"),
        ("ut_dbusmanager_test.cpp", "Dbusmanager"),
    ])
    def test_hint(self, test_review, path, expected):
        assert test_review.class_hint_from_path(path) == expected


# ── commit 规格解析 ──────────────────────────────────────────────────

class TestParseCommitSpec:
    def test_single(self, test_review):
        assert test_review.parse_commit_spec("abc1234") == (None, "abc1234")

    def test_head(self, test_review):
        assert test_review.parse_commit_spec("HEAD") == (None, "HEAD")

    def test_range(self, test_review):
        assert test_review.parse_commit_spec("aaa..bbb") == ("aaa", "bbb")

    def test_three_dot(self, test_review):
        # a...b（merge-base 差异）同样合法，原样透传 git
        assert test_review.parse_commit_spec("aaa...bbb") == ("aaa", "bbb")

    @pytest.mark.parametrize("spec", ["", None, "a..", "..b", "a..b..c", "a...b...c"])
    def test_invalid(self, test_review, spec):
        with pytest.raises(test_review.ReviewError):
            test_review.parse_commit_spec(spec)


# ── git -z name-status 解析 ──────────────────────────────────────────

class TestParseNameStatusZ:
    def test_basic_statuses(self, test_review):
        raw = "M\0autotests/test_a.cpp\0A\0autotests/test_b.cpp\0D\0old.cpp\0"
        out = test_review.parse_name_status_z(raw)
        assert [(e["status"], e["path"]) for e in out] == [
            ("M", "autotests/test_a.cpp"), ("A", "autotests/test_b.cpp"), ("D", "old.cpp")]
        assert all(e["from"] is None for e in out)

    def test_rename_takes_target(self, test_review):
        raw = "R100\0old_name.cpp\0new_name.cpp\0"
        out = test_review.parse_name_status_z(raw)
        assert out == [{"status": "R", "path": "new_name.cpp", "from": "old_name.cpp"}]

    def test_copy(self, test_review):
        raw = "C75\0src.cpp\0dst.cpp\0"
        out = test_review.parse_name_status_z(raw)
        assert out[0]["path"] == "dst.cpp"

    def test_typechange_and_unmerged(self, test_review):
        out = test_review.parse_name_status_z("T\0x.cpp\0U\0y.cpp\0")
        assert [e["status"] for e in out] == ["T", "U"]

    def test_empty(self, test_review):
        assert test_review.parse_name_status_z("") == []
        assert test_review.parse_name_status_z("\0\0") == []

    def test_malformed_token(self, test_review):
        with pytest.raises(test_review.ReviewError):
            test_review.parse_name_status_z("XYZ\0a.cpp\0")

    def test_missing_path(self, test_review):
        with pytest.raises(test_review.ReviewError):
            test_review.parse_name_status_z("R100\0only_old.cpp\0")


class TestFilterTestChanges:
    def test_mixed(self, test_review):
        changes = [
            {"status": "M", "path": "autotests/test_a.cpp", "from": None},
            {"status": "A", "path": "src/foo.cpp", "from": None},
            {"status": "D", "path": "autotests/test_old.cpp", "from": None},
            {"status": "M", "path": "CMakeLists.txt", "from": None},
            {"status": "R", "path": "autotests/test_new.cpp", "from": "autotests/test_ren.cpp"},
        ]
        review, skipped, non_test = test_review.filter_test_changes(changes)
        assert [c["path"] for c in review] == ["autotests/test_a.cpp", "autotests/test_new.cpp"]
        assert skipped == [{"path": "autotests/test_old.cpp",
                            "reason": skipped[0]["reason"]}]
        assert "deleted" in skipped[0]["reason"]
        assert non_test == 2


# ── inventory 登记集合归一化与 uncached 扫描 ──────────────────────────

def _inv(test_files):
    return {"methods": [{"qualified_name": f"p.C.m{i}", "test_files": tfs}
                        for i, tfs in enumerate(test_files)]}


class TestLoadManagedFiles:
    def test_various_formats(self, test_review):
        inv = _inv([["autotests/core/test_a.cpp"], ["test_b.cpp"], []])
        managed = test_review.load_managed_files(inv)
        assert "test_a.cpp" in managed and "test_b.cpp" in managed
        assert "autotests/core/test_a.cpp" in managed

    def test_empty_inventory(self, test_review):
        assert test_review.load_managed_files({}) == set()
        assert test_review.load_managed_files(None) == set()

    def test_windows_path_normalized(self, test_review):
        inv = _inv([["autotests\\test_w.cpp"]])
        assert "test_w.cpp" in test_review.load_managed_files(inv)


class TestCollectUncached:
    def test_scan_and_diff(self, test_review, tmp_path):
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "test_a.cpp").write_text("x")   # 已登记
        (tmp_path / "core" / "test_c.cpp").write_text("x")   # 未登记
        (tmp_path / "helper.cpp").write_text("x")            # 非测试文件
        uncached = test_review.collect_uncached(tmp_path, {"test_a.cpp"})
        assert uncached == ["core/test_c.cpp"]

    def test_no_inventory_all_uncached(self, test_review, tmp_path):
        (tmp_path / "test_x.cpp").write_text("x")
        (tmp_path / "test_y.cpp").write_text("x")
        assert test_review.collect_uncached(tmp_path, set()) == ["test_x.cpp", "test_y.cpp"]

    def test_empty_dir(self, test_review, tmp_path):
        assert test_review.collect_uncached(tmp_path, set()) == []

    def test_windows_sep_normalized(self, test_review, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "test_s.cpp").write_text("x")
        assert test_review.collect_uncached(tmp_path, set()) == ["sub/test_s.cpp"]


class TestFindTestDir:
    def test_prefers_autotests(self, test_review, tmp_path):
        (tmp_path / "autotests").mkdir()
        (tmp_path / "autotests" / "test_a.cpp").write_text("x")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_b.cpp").write_text("x")
        assert test_review.find_test_dir(tmp_path) == "autotests"

    def test_falls_back_to_tests(self, test_review, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_b.cpp").write_text("x")
        assert test_review.find_test_dir(tmp_path) == "tests"

    def test_dir_without_test_files_ignored(self, test_review, tmp_path):
        (tmp_path / "autotests").mkdir()
        (tmp_path / "autotests" / "main.cpp").write_text("x")
        assert test_review.find_test_dir(tmp_path) is None

    def test_none(self, test_review, tmp_path):
        assert test_review.find_test_dir(tmp_path) is None


class TestDiscoverInventory:
    def test_explicit_test_dir_first(self, test_review, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / ".ut-inventory.json").write_text("{}")
        (tmp_path / "autotests").mkdir()
        (tmp_path / "autotests" / ".ut-inventory.json").write_text("{}")
        got = test_review.discover_inventory(tmp_path, "tests")
        assert got == str(tmp_path / "tests" / ".ut-inventory.json")

    def test_missing(self, test_review, tmp_path):
        assert test_review.discover_inventory(tmp_path, None) is None


# ── 真实 git 仓库端到端 ───────────────────────────────────────────────

GOOD_HEADER = (
    "// SPDX-FileCopyrightText: 2026 UnionTech Software Technology Co., Ltd.\n"
    "// SPDX-License-Identifier: GPL-3.0-or-later\n"
)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def git_repo(tmp_path):
    """真实临时仓库：commit1 建 test_old.cpp + src.cpp；commit2 改 old、增 new、删 gone、改 src。"""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    at = repo / "autotests"
    at.mkdir()
    (at / "test_old.cpp").write_text(GOOD_HEADER + "// v1\n")
    (at / "test_gone.cpp").write_text(GOOD_HEADER + "// will be deleted\n")
    (repo / "src.cpp").write_text("int main() { return 0; }\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "c1: base")
    sha1 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    (at / "test_old.cpp").write_text(GOOD_HEADER + "// v2\n")
    (at / "test_new.cpp").write_text(GOOD_HEADER + "// added\n")
    (at / "test_gone.cpp").unlink()
    (repo / "src.cpp").write_text("int main() { return 1; }\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "c2: changes")
    sha2 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    return {"repo": repo, "sha1": sha1, "sha2": sha2}


class TestGitBoundary:
    def test_name_status_single_commit(self, test_review, git_repo):
        raw = test_review.git_name_status(str(git_repo["repo"]), git_repo["sha2"])
        changes = test_review.parse_name_status_z(raw)
        by_path = {c["path"]: c["status"] for c in changes}
        assert by_path["autotests/test_old.cpp"] == "M"
        assert by_path["autotests/test_new.cpp"] == "A"
        assert by_path["autotests/test_gone.cpp"] == "D"
        assert by_path["src.cpp"] == "M"

    def test_name_status_range(self, test_review, git_repo):
        spec = f"{git_repo['sha1']}..{git_repo['sha2']}"
        raw = test_review.git_name_status(str(git_repo["repo"]), spec)
        changes = test_review.parse_name_status_z(raw)
        assert {c["path"] for c in changes} >= {
            "autotests/test_old.cpp", "autotests/test_new.cpp", "autotests/test_gone.cpp"}

    def test_name_status_invalid_sha(self, test_review, git_repo):
        with pytest.raises(test_review.ReviewError):
            test_review.git_name_status(str(git_repo["repo"]), "deadbeef" * 5)

    def test_commit_meta(self, test_review, git_repo):
        meta = test_review.git_commit_meta(str(git_repo["repo"]), git_repo["sha2"])
        assert meta["sha"] == git_repo["sha2"]
        assert meta["short"] == git_repo["sha2"][:8]
        assert meta["subject"] == "c2: changes"
        assert meta["author"] == "tester"
        assert meta["date"]  # iso 日期非空

    def test_range_meta(self, test_review, git_repo):
        meta = test_review.git_range_meta(str(git_repo["repo"]),
                                          git_repo["sha1"], git_repo["sha2"])
        assert meta["commit_count"] == 1

    def test_extract_commit_files(self, test_review, git_repo, tmp_path):
        repo = str(git_repo["repo"])
        dest = tmp_path / "ws" / "snap"
        copied, errors = test_review.extract_commit_files(
            repo, git_repo["sha1"],
            ["autotests/test_old.cpp", "src.cpp", "no/such/file.cpp"], str(dest))
        assert errors and errors[0]["path"] == "no/such/file.cpp"
        # 旧版本内容（v1，而非工作区的 v2）
        assert "v1" in open(copied["autotests/test_old.cpp"]).read()
        assert "v2" not in open(copied["autotests/test_old.cpp"]).read()
        assert copied["src.cpp"] == str(dest / "src.cpp")

    def test_extract_rejects_traversal(self, test_review, git_repo, tmp_path):
        copied, errors = test_review.extract_commit_files(
            str(git_repo["repo"]), git_repo["sha1"], ["../../etc/passwd"], str(tmp_path / "ws"))
        assert not copied
        assert "路径非法" in errors[0]["error"]

    def test_git_show_missing(self, test_review, git_repo):
        with pytest.raises(test_review.ReviewError):
            test_review.git_show(str(git_repo["repo"]), git_repo["sha1"], "not_here.cpp")


# ── build_targets 编排（commit / uncached / files）───────────────────

class TestBuildTargetsCommit:
    def test_snapshot_targets(self, test_review, git_repo, tmp_path):
        repo = str(git_repo["repo"])
        ws = tmp_path / "review-workspace"
        doc, label = test_review.build_targets_commit(repo, git_repo["sha2"],
                                                      inventory=None, workspace=str(ws))
        assert label == git_repo["sha2"][:8]
        assert doc["scenario"] == "commit" and doc["commit"]["subject"] == "c2: changes"
        paths = {t["source_path"]: t for t in doc["targets"]}
        assert set(paths) == {"autotests/test_old.cpp", "autotests/test_new.cpp"}
        # 快照内容 = 该 commit 版本，非工作区
        assert "v2" in open(paths["autotests/test_old.cpp"]["review_path"]).read()
        assert doc["workspace"] == str(ws / label)
        # 删除的测试文件进 skipped，非测试文件计数
        assert [s["path"] for s in doc["skipped"]] == ["autotests/test_gone.cpp"]
        assert doc["non_test_changes"] == 1
        # 类名推断（与 scorer capitalize 风格对齐）
        assert paths["autotests/test_old.cpp"]["class_hint"] == "Old"

    def test_managed_flag(self, test_review, git_repo, tmp_path):
        inv = _inv([["autotests/test_old.cpp"]])
        doc, _ = test_review.build_targets_commit(str(git_repo["repo"]), git_repo["sha2"],
                                                  inventory=inv, workspace=None)
        managed = {t["source_path"]: t["managed"] for t in doc["targets"]}
        assert managed["autotests/test_old.cpp"] is True
        assert managed["autotests/test_new.cpp"] is False
        # 无工作区时 review_path 落回源路径（review 阶段兜底提取）
        for t in doc["targets"]:
            assert t["review_path"].endswith(t["source_path"].rsplit("/", 1)[-1])

    def test_range(self, test_review, git_repo, tmp_path):
        spec = f"{git_repo['sha1']}..{git_repo['sha2']}"
        doc, label = test_review.build_targets_commit(str(git_repo["repo"]), spec,
                                                      workspace=str(tmp_path))
        assert label == f"{git_repo['sha1'][:8]}..{git_repo['sha2'][:8]}"
        assert doc["range"]["commit_count"] == 1
        assert len(doc["targets"]) == 2

    def test_no_test_files(self, test_review, git_repo):
        # 在 c2 之后再造一个只改非测试文件的 commit
        repo = git_repo["repo"]
        (repo / "src.cpp").write_text("int main() { return 2; }\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "c3: src only")
        with pytest.raises(test_review.ReviewError, match="未发现可审查的测试文件"):
            test_review.build_targets_commit(str(repo), "HEAD", workspace=None)

    def test_bad_repo(self, test_review, tmp_path):
        with pytest.raises(test_review.ReviewError):
            test_review.build_targets_commit(str(tmp_path / "nope"), "HEAD", workspace=None)


class TestBuildTargetsUncached:
    def test_uncached_scan(self, test_review, git_repo, tmp_path):
        repo = git_repo["repo"]
        inv = repo / "autotests" / ".ut-inventory.json"
        inv.write_text(json.dumps(_inv([["autotests/test_old.cpp"]])))
        (repo / "autotests" / "test_fresh.cpp").write_text(GOOD_HEADER + "// fresh\n")
        doc, label = test_review.build_targets_uncached(str(repo), "autotests", str(inv), None)
        assert label == "uncached" and doc["scenario"] == "uncached"
        # test_fresh 与 test_new 均未登记 → 未缓存；test_old 已登记；test_gone 已删不在磁盘
        assert [t["source_path"] for t in doc["targets"]] == [
            "autotests/test_fresh.cpp", "autotests/test_new.cpp"]
        assert doc["targets"][0]["managed"] is False
        assert doc["targets"][0]["review_path"] == str(repo / "autotests" / "test_fresh.cpp")

    def test_no_inventory_all_uncached(self, test_review, git_repo):
        doc, _ = test_review.build_targets_uncached(str(git_repo["repo"]), "autotests",
                                                    None, None)
        names = sorted(t["source_path"] for t in doc["targets"])
        assert names == ["autotests/test_new.cpp", "autotests/test_old.cpp"]

    def test_files_mode(self, test_review, git_repo):
        repo = git_repo["repo"]
        f = str(repo / "autotests" / "test_old.cpp")
        doc, label = test_review.build_targets_uncached(str(repo), None, None, [f])
        assert label == "files" and doc["scenario"] == "files"
        assert doc["targets"][0]["review_path"] == f
        assert doc["targets"][0]["managed"] is False

    def test_files_missing(self, test_review, git_repo):
        with pytest.raises(test_review.ReviewError, match="不存在"):
            test_review.build_targets_uncached(str(git_repo["repo"]), None, None,
                                               ["nope.cpp"])

    def test_test_dir_required(self, test_review, tmp_path):
        with pytest.raises(test_review.ReviewError, match="未找到测试目录"):
            test_review.build_targets_uncached(str(tmp_path), None, None, None)


# ── resolve CLI 端到端 ───────────────────────────────────────────────

class TestResolveCli:
    def test_resolve_commit_writes_json(self, test_review, git_repo, tmp_path):
        out = tmp_path / "targets.json"
        rc = test_review.main([
            "resolve", "--repo", str(git_repo["repo"]), "--commit", git_repo["sha2"],
            "-o", str(out)])
        assert rc == 0
        doc = json.loads(out.read_text())
        assert doc["scenario"] == "commit"
        assert len(doc["targets"]) == 2
        assert doc["_label"] == git_repo["sha2"][:8]

    def test_resolve_stdout(self, test_review, git_repo, capsys):
        rc = test_review.main([
            "resolve", "--repo", str(git_repo["repo"]), "--commit", git_repo["sha2"]])
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["scenario"] == "commit"

    def test_resolve_hard_error_exit2(self, test_review, git_repo, tmp_path):
        rc = test_review.main([
            "resolve", "--repo", str(git_repo["repo"]), "--commit", "deadbeef" * 5,
            "-o", str(tmp_path / "t.json")])
        assert rc == 2

    def test_resolve_requires_input(self, test_review):
        assert test_review.main(["resolve", "--repo", "."]) == 2
