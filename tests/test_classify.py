import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.index.classify import classify_doc_type  # noqa: E402


def test_doc_type_basic_rules():
    assert classify_doc_type("주간 회의록") == "meeting"
    assert classify_doc_type("API 사용 가이드") == "guide"
    assert classify_doc_type("[공통] Data Schema (silo)") == "data_model"
    assert classify_doc_type("결제 오류 트러블슈팅") == "issue"
    assert classify_doc_type("2026 운영 배포 런북") == "ops"


def test_doc_type_none_when_no_match():
    assert classify_doc_type("알 수 없는 제목 xyz") is None
    assert classify_doc_type("") is None


def test_priority_specific_over_general():
    # '설계'(design)가 '가이드'(guide)보다 규칙 우선순위 위 → design
    assert classify_doc_type("설계 가이드 문서") == "design"


def test_uses_hierarchy_and_tags():
    # 제목엔 단서 없지만 계층/태그에서 매칭
    assert classify_doc_type("문서", hierarchy=["프로젝트", "회의록"]) == "meeting"
    assert classify_doc_type("문서", tags=["guide"]) == "guide"


def test_deterministic():
    assert classify_doc_type("배포 운영 가이드") == classify_doc_type("배포 운영 가이드")
