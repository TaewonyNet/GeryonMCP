import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from geryon.connectors.jira import JiraConnector, _description_text
from geryon.normalize.normalizer import Normalizer
from geryon.domain.models import SourceType

def _write_issue(d, key, project, summary, desc, reporter, labels):
    pdir = d / project
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{key}.json").write_text(json.dumps({
        "key": key, "self": f"https://x.atlassian.net/rest/api/3/issue/{key}",
        "fields": {
            "summary": summary, "description": desc,
            "reporter": {"displayName": reporter},
            "created": "2024-03-01T00:00:00.000+0000",
            "updated": "2024-03-02T00:00:00.000+0000",
            "labels": labels, "project": {"key": project},
            "issuetype": {"name": "Bug"}, "status": {"name": "Open"},
        }}), encoding="utf-8")

def test_jira_iter_raw_maps_fields(tmp_path):
    _write_issue(tmp_path, "PROJ-1", "PROJ", "배포 실패 버그", "롤백이 안 됨", "홍길동", ["deploy"])
    recs = list(JiraConnector(tmp_path).iter_raw())
    assert len(recs) == 1
    r = recs[0]
    assert r.source == SourceType.JIRA and r.source_id == "PROJ-1"
    assert r.title == "배포 실패 버그" and r.space_or_repo == "PROJ"
    assert r.metadata["author"] == "홍길동" and r.metadata["doc_type"] == "issue"
    assert r.metadata["tags"] == ["deploy"]

def test_jira_to_standard_document(tmp_path):
    _write_issue(tmp_path, "PROJ-2", "PROJ", "휴가 기능", "연차 계산", "김철수", [])
    rec = next(JiraConnector(tmp_path).iter_raw())
    doc = Normalizer().normalize(rec)
    assert doc.title == "휴가 기능" and doc.author == "김철수"
    assert doc.source == SourceType.JIRA and doc.doc_type == "issue"
    assert "연차" in doc.body_markdown

def test_adf_description_extracted():
    adf = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "ADF 본문입니다"}]}]}
    assert _description_text(adf) == "ADF 본문입니다"
    assert _description_text("평문") == "평문"

def test_registry_has_jira():
    from geryon.pipeline.ingest import CONNECTOR_REGISTRY
    assert "jira" in CONNECTOR_REGISTRY

def test_healthcheck_missing_dir(tmp_path):
    assert JiraConnector(tmp_path / "none").healthcheck() is False
