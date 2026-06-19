"""Acquire 플러그인 — registry + GitAcquirer 멱등/증분."""
import subprocess

import pytest

from geryon.acquire import ACQUIRER_REGISTRY, Acquirer
from geryon.acquire.git import GitAcquirer, _repo_name


def test_registry_has_confluence_and_git():
    assert {"confluence", "git"} <= set(ACQUIRER_REGISTRY)
    for cls in ACQUIRER_REGISTRY.values():
        assert issubclass(cls, Acquirer)


def test_repo_name():
    assert _repo_name("https://bitbucket.org/o/myrepo.git") == "myrepo"
    assert _repo_name("git@github.com:o/p") == "p"
    assert _repo_name("https://x/y/repo/") == "repo"


def _mk_origin(tmp, name="r"):
    origin = tmp / f"{name}.git_src"
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.name", "t"], check=True)
    (origin / "README.md").write_text("# 배포\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(origin), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], check=True)
    return origin


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git 필요")
def test_git_acquirer_clone_then_idempotent_then_incremental(tmp_path):
    origin = _mk_origin(tmp_path)
    bronze = tmp_path / "bronze"
    acq = GitAcquirer(repos=[str(origin)], bronze_dir=str(bronze))
    name = _repo_name(str(origin))

    # 1) 최초 clone
    s1 = acq.acquire()
    assert s1["cloned"] == 1 and s1["errors"] == 0
    assert (bronze / name / "README.md").exists()

    # 2) 멱등 — 변경 없으면 fetch 후 no-op(update 카운트만)
    s2 = acq.acquire()
    assert s2["cloned"] == 0 and s2["updated"] == 1 and s2["errors"] == 0

    # 3) 증분 — origin 새 커밋 후 반영
    subprocess.run(["git", "-C", str(origin), "-c", "commit.gpgsign=false",
                    "commit", "--allow-empty", "-q", "-m", "second"], check=True)
    s3 = acq.acquire()
    assert s3["updated"] == 1 and s3["errors"] == 0


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git 필요")
def test_git_acquirer_writes_manifest_last_change(tmp_path):
    from geryon.acquire.manifest import read_manifest
    origin = _mk_origin(tmp_path)
    bronze = tmp_path / "bronze"
    repo_dir = bronze / _repo_name(str(origin))
    acq = GitAcquirer(repos=[str(origin)], bronze_dir=str(bronze))

    # 1) clone → manifest(as_of=HEAD, last_change 빈)
    acq.acquire()
    m1 = read_manifest(repo_dir)
    assert m1["source"] == "git" and m1["as_of"]
    assert m1["last_change"]["added"] == []

    # 2) 새 파일 + 커밋 → manifest.last_change 에 변경 파일·새 커밋 기록
    (origin / "NEW.md").write_text("신규", encoding="utf-8")
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(origin), "-c", "commit.gpgsign=false",
                    "commit", "-q", "-m", "feat"], check=True)
    acq.acquire()
    lc = read_manifest(repo_dir)["last_change"]
    assert any("NEW.md" in x for x in lc["added"])      # 변경 파일(repo:경로)
    assert any("@" in x for x in lc["added"])            # 새 커밋(repo@hash)


def test_git_acquirer_dry_run(tmp_path):
    origin = _mk_origin(tmp_path)
    bronze = tmp_path / "bronze"
    acq = GitAcquirer(repos=[str(origin)], bronze_dir=str(bronze))
    s = acq.acquire(dry_run=True)
    assert s["skipped"] == 1 and s["cloned"] == 0
    assert not (bronze / _repo_name(str(origin))).exists()  # dry-run 은 기록 안 함
