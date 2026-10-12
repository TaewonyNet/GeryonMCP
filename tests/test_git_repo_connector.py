import sys, os, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from geryon.connectors.git_repo import GitRepoConnector, _first_heading
from geryon.normalize.normalizer import Normalizer
from geryon.domain.models import SourceType

def _mk_repo(base, name, remote, files, msg="init"):
    repo = base / name; repo.mkdir(parents=True)
    subprocess.run(["git","init","-q",str(repo)],check=True)
    subprocess.run(["git","-C",str(repo),"config","user.email","t@t"],check=True)
    subprocess.run(["git","-C",str(repo),"config","user.name","테스터"],check=True)
    subprocess.run(["git","-C",str(repo),"remote","add","origin",remote],check=True)
    for fn,c in files.items():
        p=repo/fn; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(c,encoding="utf-8")
    subprocess.run(["git","-C",str(repo),"add","-A"],check=True)
    subprocess.run(["git","-C",str(repo),"commit","-q","-m",msg],check=True)
    return repo

def test_docs_only(tmp_path):  # 문서만 (코드·커밋 끔)
    _mk_repo(tmp_path,"p","git@github.com:o/p.git",{"README.md":"# 배포 가이드\n무중단","main.py":"print(1)"})
    recs=list(GitRepoConnector(tmp_path,include_code=False,include_commits=False).iter_raw())
    assert {r.title for r in recs}=={"배포 가이드"}
    assert all(r.source==SourceType.GITHUB for r in recs)

def test_code_indexed(tmp_path):  # 소스 코드 색인
    _mk_repo(tmp_path,"p","git@github.com:o/p.git",
             {"README.md":"# 문서","app.py":"def deploy():\n  pass","q.sql":"SELECT 1"})
    recs={r.source_id.split(':')[-1]: r for r in
          GitRepoConnector(tmp_path,include_code=True,include_commits=False).iter_raw()}
    assert "app.py" in recs and "q.sql" in recs
    assert recs["app.py"].metadata["doc_type"]=="code"
    assert "deploy" in recs["app.py"].raw_body

def test_commits_indexed(tmp_path):  # 커밋 메세지/히스토리
    _mk_repo(tmp_path,"p","git@bitbucket.org:o/p.git",{"a.md":"# A"},msg="PROJ-100 정책 평가 추가")
    recs=[r for r in GitRepoConnector(tmp_path,include_code=False,include_commits=True).iter_raw()
          if r.metadata["doc_type"]=="commit"]
    assert len(recs)>=1
    c=recs[0]
    assert "PROJ-100" in c.title and c.source==SourceType.BITBUCKET
    assert c.source_id.startswith("p@")

def test_commit_to_document(tmp_path):
    _mk_repo(tmp_path,"p","git@github.com:o/p.git",{"a.md":"# A"},msg="버그 수정 PROJ-7")
    rec=next(r for r in GitRepoConnector(tmp_path,include_commits=True).iter_raw()
             if r.metadata["doc_type"]=="commit")
    doc=Normalizer().normalize(rec)
    assert "PROJ-7" in doc.title and doc.doc_type=="commit" and doc.author=="테스터"

def test_doc_files_get_classified_like_confluence(tmp_path):
    """git 의 문서 파일도 Confluence 와 같은 규칙으로 세분화된다(코드 분류 가능)."""
    _mk_repo(tmp_path, "p", "git@github.com:o/p.git",
             {"README.md": "# 설치 가이드", "docs/arch.md": "# 설계 문서", "notes.md": "# 아무 제목",
              "main.py": "print(1)"})
    recs = {r.source_id.split(":")[-1]: r for r in
            GitRepoConnector(tmp_path, include_code=True, include_commits=False).iter_raw()}
    assert recs["README.md"].metadata["doc_type"] == "guide"
    assert recs["docs/arch.md"].metadata["doc_type"] == "design"
    assert recs["notes.md"].metadata["doc_type"] == "doc"     # 매칭 없으면 예전 그대로
    assert recs["main.py"].metadata["doc_type"] == "code"     # 코드는 분류 대상 아님


def test_commits_classified_by_conventional_prefix(tmp_path):
    """feat:/fix: 등을 쓰는 저장소는 커밋이 세분화되고, 안 쓰는 저장소는 그대로 "commit"."""
    repo = _mk_repo(tmp_path, "p", "git@github.com:o/p.git", {"a.md": "# A"}, msg="fix: 널 포인터 수정")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "일반 작업 메모"], check=True)
    recs = [r for r in GitRepoConnector(tmp_path, include_code=False, include_commits=True).iter_raw()
            if r.metadata["doc_type"] in ("fix", "commit")]
    by_subject = {r.title: r.metadata["doc_type"] for r in recs}
    assert by_subject["fix: 널 포인터 수정"] == "fix"
    assert by_subject["일반 작업 메모"] == "commit"   # 접두사 없는 저장소는 회귀 없이 예전 그대로


def test_host_detection(tmp_path):
    _mk_repo(tmp_path,"gl","https://gitlab.com/x/gl.git",{"a.md":"# A"})
    recs=list(GitRepoConnector(tmp_path,include_commits=False).iter_raw())
    assert all(r.source==SourceType.GITLAB for r in recs)

def test_first_heading():
    assert _first_heading("x\n## 두번째\n# 첫") == "두번째"

def test_registry_has_git():
    from geryon.pipeline.ingest import CONNECTOR_REGISTRY
    assert "git" in CONNECTOR_REGISTRY

def test_origin_url_github(tmp_path):  # 출처 원본 링크 (파일·커밋)
    _mk_repo(tmp_path,"p","git@github.com:org/p.git",{"docs/a.md":"# A"},msg="fix")
    recs={r.metadata["doc_type"]: r for r in GitRepoConnector(tmp_path,include_commits=True).iter_raw()}
    assert recs["doc"].url.startswith("https://github.com/org/p/blob/")
    assert "docs/a.md" in recs["doc"].url
    assert "/commit/" in recs["commit"].url

def test_origin_url_hosts(tmp_path):
    _mk_repo(tmp_path,"gl","https://gitlab.com/o/gl.git",{"a.md":"# A"})
    _mk_repo(tmp_path,"bb","git@bitbucket.org:o/bb.git",{"b.md":"# B"})
    urls={r.space_or_repo: r.url for r in GitRepoConnector(tmp_path,include_commits=False).iter_raw()}
    assert urls["gl"].startswith("https://gitlab.com/o/gl/-/blob/")
    assert urls["bb"].startswith("https://bitbucket.org/o/bb/src/")
