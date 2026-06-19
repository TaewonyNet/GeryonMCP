"""증분 ingest(기본) + 갱신 메타 + git source_type 정합(prune 회귀 방지)."""
import json
import subprocess

from geryon.store.repository import SqliteRepository
from geryon.pipeline.ingest import IngestionPipeline
from geryon.connectors.confluence import ConfluenceConnector
from geryon.connectors.git_repo import GitRepoConnector
from geryon.domain.models import SourceType


def _mk_page(bronze, space, pid, title, body):
    d = bronze / space / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "content.html").write_text(f"<h1>{body}</h1>", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "source": "confluence", "source_id": pid, "title": title,
        "content_file": "content.html", "content_format": "html",
    }), encoding="utf-8")


def _pipeline(db):
    return IngestionPipeline(repository=SqliteRepository(str(db)))


def test_incremental_only_processes_modified(tmp_path):
    from geryon.acquire.manifest import write_manifest
    bronze = tmp_path / "cb"
    _mk_page(bronze, "SP", "p1", "문서1", "배포 프로세스")
    _mk_page(bronze, "SP", "p2", "문서2", "정책 평가")
    conn = ConfluenceConnector(str(bronze))
    pipe = _pipeline(tmp_path / "db")

    # 1) manifest 없음 → 최초 실행(first_run) 전체 색인
    s1 = pipe.run(conn, incremental=True)
    assert s1["inserted"] == 2
    assert s1["first_run"] is True
    assert "started_at" in s1 and "finished_at" in s1 and s1["mode"] == "incremental"

    # 2) manifest.last_change 가 p1 만 변경으로 표시 → 증분: p1 만 처리(p2 순회 안 함)
    (bronze / "SP" / "p1" / "content.html").write_text("<h1>배포 수정본</h1>", encoding="utf-8")
    write_manifest(bronze, source="confluence", as_of="2026-06-10T00:00:00Z",
                   last_change={"since": None, "added": [], "modified": ["SP/p1"], "deleted": []})
    s2 = pipe.run(conn, incremental=True)
    assert s2["updated"] == 1
    assert s2["inserted"] == 0
    assert s2["first_run"] is False

    # 3) last_change 가 빈 manifest → 변경 없음
    write_manifest(bronze, source="confluence", as_of="2026-06-10T01:00:00Z",
                   last_change={"since": None, "added": [], "modified": [], "deleted": []})
    s3 = pipe.run(conn, incremental=True)
    assert s3["inserted"] == 0 and s3["updated"] == 0


def test_full_mode_prunes_removed_bronze(tmp_path):
    bronze = tmp_path / "cb"
    _mk_page(bronze, "SP", "p1", "문서1", "본문1")
    _mk_page(bronze, "SP", "p2", "문서2", "본문2")
    conn = ConfluenceConnector(str(bronze))
    repo = SqliteRepository(str(tmp_path / "db"))
    pipe = IngestionPipeline(repository=repo)

    pipe.run(conn, incremental=False)  # full, 2건
    # Bronze 에서 p2 제거 후 full → prune 으로 DB 에서도 삭제
    import shutil
    shutil.rmtree(bronze / "SP" / "p2")
    s = pipe.run(conn, full_reindex=True, prune=True)
    assert s["deleted"] == 1
    assert s["mode"] == "full"


def _mk_git_remote(repo, remote_url):
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", remote_url], check=True)
    (repo / "README.md").write_text("# 배포 가이드\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], check=True)


def test_git_source_type_runtime_detection(tmp_path):
    """git connector.source_type 이 remote 로 판별돼 doc.source 와 일치해야 prune 이 정합한다.

    (회귀: source_type 이 GITHUB 고정이면 bitbucket 문서를 prune 이 못 봐 중복 누적.)
    """
    repo = tmp_path / "myrepo"
    _mk_git_remote(repo, "https://bitbucket.org/org/myrepo.git")
    conn = GitRepoConnector(str(repo))
    assert conn.source_type == SourceType.BITBUCKET
    # manifest.last_change 기반 증분 지원(GitAcquirer 가 git diff/log 로 기록)
    assert conn.supports_incremental is True
