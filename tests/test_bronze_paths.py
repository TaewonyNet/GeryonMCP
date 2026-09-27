"""Bronze 경로 해석 — 실행 폴더(CWD)와 무관해야 하고, 폴더를 옮겨도 싱크가 이어져야 한다.

예전 기본값은 CWD 의 `bronze/` 였다. 실행 위치마다 Bronze 가 따로 생겨 DB 하나에
여러 Bronze 가 섞였고, 옛 폴더로 재색인해 최신 문서가 옛 버전으로 되돌아간 적이 있다.
"""
import json
import shutil

import pytest

from geryon import config
from geryon.acquire.git import GitAcquirer
from geryon.acquire.manifest import write_manifest
from geryon.connectors.confluence import ConfluenceConnector
from geryon.connectors.git_repo import GitRepoConnector
from geryon.connectors.jira import JiraConnector
from geryon.pipeline.ingest import IngestionPipeline
from geryon.store.repository import SqliteRepository

_KEYS = ("GERYON_BRONZE_DIR", "GERYON_BRONZE_CONFLUENCE", "GERYON_BRONZE_JIRA",
         "GERYON_BRONZE_GIT", "GERYON_CONFLUENCE_DB_PATH")


@pytest.fixture
def clean(tmp_path, monkeypatch):
    """Bronze 관련 env·`.env` 출처 기록을 비우고 홈(~/.geryon)을 임시 폴더로."""
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "_ENV_FILE_DIR", {})
    monkeypatch.setattr(config, "_legacy_warned", set())
    home = tmp_path / "home" / ".geryon"
    monkeypatch.setattr(config, "GERYON_DIR", home)
    return home


def test_default_is_under_geryon_dir_regardless_of_cwd(clean, tmp_path, monkeypatch):
    seen = set()
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        monkeypatch.chdir(tmp_path / d)
        seen.add(config.default_bronze("confluence"))
    assert seen == {str(clean / "bronze" / "confluence")}
    assert config.default_bronze("jira") == str(clean / "bronze" / "jira")
    assert config.default_bronze("git") == str(clean / "bronze" / "repos")


def test_base_env_and_per_source_override(clean, tmp_path, monkeypatch):
    monkeypatch.setenv("GERYON_BRONZE_DIR", str(tmp_path / "base"))
    assert config.default_bronze("git") == str(tmp_path / "base" / "repos")

    monkeypatch.setenv("GERYON_BRONZE_CONFLUENCE", str(tmp_path / "live"))
    assert config.default_bronze("confluence") == str(tmp_path / "live")
    assert config.default_bronze("jira") == str(tmp_path / "base" / "jira")  # 다른 소스는 베이스


def test_legacy_confluence_db_path_env_still_works(clean, tmp_path, monkeypatch):
    monkeypatch.setenv("GERYON_CONFLUENCE_DB_PATH", str(tmp_path / "old"))
    assert config.default_bronze("confluence") == str(tmp_path / "old")
    monkeypatch.setenv("GERYON_BRONZE_CONFLUENCE", str(tmp_path / "new"))
    assert config.default_bronze("confluence") == str(tmp_path / "new")  # 새 이름이 우선


def test_relative_value_from_env_file_resolves_against_that_file(clean, tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("GERYON_BRONZE_DIR=./data/bronze\n", encoding="utf-8")
    monkeypatch.setenv("GERYON_BRONZE_DIR", "x")      # teardown 에서 원래(없음)로 복원되도록 등록
    monkeypatch.delenv("GERYON_BRONZE_DIR")
    monkeypatch.setenv("GERYON_ENV_FILE", str(proj / ".env"))
    config._load_dotenv()

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert config.default_bronze("jira") == str(proj / "data" / "bronze" / "jira")


def test_legacy_cwd_bronze_used_with_warning(clean, tmp_path, monkeypatch, capsys):
    work = tmp_path / "work"
    (work / "bronze" / "confluence").mkdir(parents=True)
    monkeypatch.chdir(work)
    assert config.default_bronze("confluence") == str(work / "bronze" / "confluence")
    assert "예전 기본 위치" in capsys.readouterr().err

    (clean / "bronze" / "confluence").mkdir(parents=True)  # 새 기본이 생기면 그쪽
    assert config.default_bronze("confluence") == str(clean / "bronze" / "confluence")


def test_components_follow_the_setting(clean, tmp_path, monkeypatch):
    monkeypatch.setenv("GERYON_BRONZE_DIR", str(tmp_path / "b"))
    assert ConfluenceConnector().db_path == tmp_path / "b" / "confluence"
    assert JiraConnector().db_path == tmp_path / "b" / "jira"
    assert GitRepoConnector().db_path == tmp_path / "b" / "repos"
    assert GitAcquirer(repos=[]).bronze_dir == tmp_path / "b" / "repos"


def _page(root, space, pid, body, att=None):
    d = root / space / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "content.html").write_text(f"<p>{body}</p>", encoding="utf-8")
    meta = {"source": "confluence", "source_id": pid, "title": pid,
            "content_file": "content.html", "content_format": "html"}
    if att:
        (d / "attachments").mkdir()
        (d / "attachments" / att).write_bytes(b"x")
        meta["attachments"] = [{"filename": att, "media_type": "text/plain",
                                "local_path": f"attachments/{att}", "url": None}]
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_moving_bronze_keeps_incremental_sync_working(clean, tmp_path, monkeypatch):
    """폴더를 옮기고 경로 설정만 바꾸면 이어서 증분 싱크가 된다(재수집·재색인 불필요)."""
    old = tmp_path / "disk1" / "confluence"
    _page(old, "SP", "p1", "배포 절차", att="a.txt")
    _page(old, "SP", "p2", "정책 평가")
    monkeypatch.setenv("GERYON_BRONZE_CONFLUENCE", str(old))
    repo = SqliteRepository(str(tmp_path / "db"))
    assert IngestionPipeline(repository=repo).run(ConfluenceConnector(),
                                                  incremental=True)["inserted"] == 2

    new = tmp_path / "disk2" / "confluence"
    shutil.move(str(old), str(new))
    monkeypatch.setenv("GERYON_BRONZE_CONFLUENCE", str(new))

    _page(new, "SP", "p3", "새 문서")                     # 이동 뒤 수집된 변경분
    write_manifest(new, source="confluence", as_of="2026-01-01T00:00:00+00:00",
                   last_change={"since": None, "added": ["SP/p3"], "modified": [], "deleted": []})
    s = IngestionPipeline(repository=repo).run(ConfluenceConnector(), incremental=True)
    assert (s["inserted"], s["deleted"]) == (1, 0)
    assert len(repo.get_doc_ids_by_source(ConfluenceConnector.source_type)) == 3

    att = repo.get_connection().execute("SELECT local_path FROM attachments").fetchall()
    assert att == [("attachments/a.txt",)]   # DB 에 절대경로가 없어 이동에 영향 없음
