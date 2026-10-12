import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.index.classify import classify_doc_type, classify_commit_type  # noqa: E402


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


def test_glossary_rule():
    assert classify_doc_type("용어 정리") == "glossary"
    assert classify_doc_type("Project Glossary") == "glossary"


def test_env_rules_take_priority_over_builtin(monkeypatch):
    # 내장 규칙이면 "가이드"가 걸려 guide 가 되는데, 조직 규칙이 더 구체적인 유형을 먼저 매칭해야 한다.
    monkeypatch.setenv("GERYON_DOC_TYPE_RULES", "runbook_kr:온콜 가이드")
    assert classify_doc_type("온콜 가이드") == "runbook_kr"
    assert classify_doc_type("API 사용 가이드") == "guide"  # 매칭 안 되는 제목은 내장 규칙 그대로


def test_env_rules_malformed_entries_are_skipped(monkeypatch):
    monkeypatch.setenv("GERYON_DOC_TYPE_RULES", "no-colon-here,glossary2:")
    assert classify_doc_type("API 사용 가이드") == "guide"   # 깨진 규칙이 분류 전체를 막지 않음


def test_env_rules_empty_is_noop(monkeypatch):
    monkeypatch.delenv("GERYON_DOC_TYPE_RULES", raising=False)
    assert classify_doc_type("주간 회의록") == "meeting"


def test_commit_type_conventional_prefixes():
    assert classify_commit_type("fix: 널 포인터 수정") == "fix"
    assert classify_commit_type("feat(search): 상세검색 doc_type 필터 추가") == "feat"
    assert classify_commit_type("docs: README 갱신") == "docs"
    assert classify_commit_type("refactor!: 모듈 분리") == "refactor"
    assert classify_commit_type("FIX: 대소문자 무관") == "fix"


def test_commit_type_none_when_no_prefix():
    assert classify_commit_type("PROJ-100 정책 평가 추가") is None
    assert classify_commit_type("버그 수정 PROJ-7") is None
    assert classify_commit_type("") is None
    assert classify_commit_type(None) is None


def test_commit_type_requires_colon_space_not_just_any_colon():
    # "fix:버그"(공백 없음)·"fixup: ..."(비표준 타입)는 매칭하지 않는다 — 과매칭 방지.
    assert classify_commit_type("fix:버그") is None
    assert classify_commit_type("fixup: 수정") is None
