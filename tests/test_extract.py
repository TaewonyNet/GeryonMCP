"""문서 정제 레이어 단위 테스트.

마커:
  (마커 없음)       LLM 불필요 — CI에서 항상 실행
  @pytest.mark.integration  실제 Ollama 필요 — 로컬 검증용

실행 예:
  rye run pytest tests/test_extract.py -v              # 단위만
  rye run pytest tests/test_extract.py -v -m integration  # 통합만
  rye run pytest tests/test_extract.py -v --run-integration  # 전체
"""
from __future__ import annotations

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest

from geryon.analyze.llm_extract import (
    SCHEMA_VERSION,
    DocType,
    DocumentRefiner,
    ExtractionMeta,
    ExtractionResult,
    LLMConfig,
)
from tests.fixtures.synthetic import make_meeting_transcript, make_spec_document


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────
def _refiner_with_mock(raw_response: dict, elapsed: float = 1.0) -> DocumentRefiner:
    """LLM 호출을 mock으로 대체한 DocumentRefiner 반환."""
    refiner = DocumentRefiner(LLMConfig(model="mock-model", base_url="http://localhost:9999"))
    refiner._call = MagicMock(return_value=(raw_response, elapsed))  # type: ignore[method-assign]
    return refiner


# ─────────────────────────────────────────────────────────────
# 1. LLMConfig — LLM 설정·환경변수
# ─────────────────────────────────────────────────────────────
class TestLLMConfig:
    def test_default_model_is_qwen3(self) -> None:
        cfg = LLMConfig()
        assert cfg.model.startswith("qwen3"), \
            f"기본 모델이 qwen3 계열이어야 함: {cfg.model}"

    def test_env_override_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GERYON_LLM_MODEL", "llama3.2:latest")
        cfg = LLMConfig.from_env()
        assert cfg.model == "llama3.2:latest"

    def test_env_override_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GERYON_LLM_URL", "http://gpu-server:11434")
        cfg = LLMConfig.from_env()
        assert cfg.base_url == "http://gpu-server:11434"

    def test_env_override_ctx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GERYON_LLM_CTX", "65536")
        cfg = LLMConfig.from_env()
        assert cfg.num_ctx == 65536

    def test_from_env_fallback_to_defaults(self) -> None:
        for key in ("GERYON_LLM_MODEL", "GERYON_LLM_URL", "GERYON_LLM_CTX", "GERYON_LLM_PREDICT"):
            os.environ.pop(key, None)
        cfg = LLMConfig.from_env()
        assert "qwen3" in cfg.model
        assert "11434" in cfg.base_url


# ─────────────────────────────────────────────────────────────
# 2. 문서 유형 감지
# ─────────────────────────────────────────────────────────────
class TestDocumentTypeDetection:
    def test_meeting_detected_by_timestamp(self) -> None:
        text = make_meeting_transcript()
        assert DocumentRefiner.detect_type(text) == "meeting"

    def test_spec_detected_by_create_table(self) -> None:
        text = make_spec_document()
        assert DocumentRefiner.detect_type(text) == "spec"

    def test_spec_detected_by_code_fence(self) -> None:
        text = "```java\npublic class Foo {}\n```"
        assert DocumentRefiner.detect_type(text) == "spec"

    def test_spec_detected_by_select(self) -> None:
        text = "SELECT id FROM users WHERE active = 1"
        assert DocumentRefiner.detect_type(text) == "spec"

    def test_unknown_for_plain_prose(self) -> None:
        text = "이것은 일반 텍스트 문서입니다. 특별한 패턴이 없습니다."
        assert DocumentRefiner.detect_type(text) == "unknown"

    def test_extract_routes_meeting(self) -> None:
        text = make_meeting_transcript()
        mock_r = {"segments": [{"stream": "topic", "title": "T", "summary": "S",
                                 "evidence": "E", "contains_decision": False,
                                 "linked_topic": None, "action_items": []}]}
        refiner = _refiner_with_mock(mock_r)
        result  = refiner.extract(text)
        assert result.meta.doc_type == "meeting"

    def test_extract_routes_spec(self) -> None:
        text = make_spec_document()
        mock_r = {"title": "T", "purpose": "P", "sections": []}
        refiner = _refiner_with_mock(mock_r)
        result  = refiner.extract(text)
        assert result.meta.doc_type == "spec"


# ─────────────────────────────────────────────────────────────
# 3. ExtractionMeta — 버전 추적
# ─────────────────────────────────────────────────────────────
class TestExtractionMeta:
    def _make_result(self, doc_type: DocType = "meeting") -> ExtractionResult:
        mock_r = {"segments": []} if doc_type == "meeting" else {"title": "T", "purpose": "P", "sections": []}
        refiner = _refiner_with_mock(mock_r, elapsed=2.5)
        refiner.config.model = "qwen3.5:latest"
        with patch.object(refiner.config, "model_digest", return_value="abc123456789"):
            if doc_type == "meeting":
                return refiner.extract_meeting(make_meeting_transcript())
            return refiner.extract_spec(make_spec_document())

    def test_meta_model_name_recorded(self) -> None:
        result = self._make_result()
        assert result.meta.model == "qwen3.5:latest"

    def test_meta_schema_version_is_current(self) -> None:
        result = self._make_result()
        assert result.meta.schema_version == SCHEMA_VERSION

    def test_meta_doc_type_recorded(self) -> None:
        assert self._make_result("meeting").meta.doc_type == "meeting"
        assert self._make_result("spec").meta.doc_type   == "spec"

    def test_meta_processed_at_is_iso8601(self) -> None:
        result = self._make_result()
        from datetime import datetime
        # ISO 파싱 가능하면 통과
        datetime.fromisoformat(result.meta.processed_at)

    def test_meta_elapsed_recorded(self) -> None:
        result = self._make_result()
        assert result.meta.elapsed_sec == 2.5

    def test_meta_char_count_matches(self) -> None:
        text   = make_meeting_transcript()
        mock_r = {"segments": []}
        refiner = _refiner_with_mock(mock_r)
        result  = refiner.extract_meeting(text)
        assert result.meta.char_count == len(text)

    def test_to_db_meta_is_serializable(self) -> None:
        result = self._make_result()
        db_meta = result.to_db_meta()
        json.dumps(db_meta)   # 직렬화 가능해야 함

    def test_to_db_meta_contains_required_keys(self) -> None:
        result = self._make_result()
        db_meta = result.to_db_meta()
        for key in ("model", "model_digest", "schema_version", "doc_type",
                    "processed_at", "elapsed_sec", "char_count"):
            assert key in db_meta, f"db_meta에 {key} 없음"


# ─────────────────────────────────────────────────────────────
# 4. ExtractionResult — 편의 메서드
# ─────────────────────────────────────────────────────────────
class TestExtractionResult:
    def _meeting_result(self) -> ExtractionResult:
        mock_r = {
            "segments": [
                {"stream": "topic", "title": "DB 마이그레이션", "summary": "...",
                 "evidence": "...", "contains_decision": True, "linked_topic": None,
                 "action_items": [
                     {"description": "ClickHouse 테스트", "owner": "이서연", "due": "이번 주"},
                     {"description": "결과 공유",           "owner": "이서연", "due": "완료 후"},
                 ]},
                {"stream": "peripheral", "title": "점심 논의", "summary": "...",
                 "evidence": "...", "contains_decision": False, "linked_topic": "DB 마이그레이션",
                 "action_items": []},
            ],
        }
        return _refiner_with_mock(mock_r).extract_meeting(make_meeting_transcript())

    def test_segment_count(self) -> None:
        assert self._meeting_result().segment_count() == 2

    def test_action_items_flattened(self) -> None:
        assert len(self._meeting_result().action_items()) == 2

    def test_has_decisions(self) -> None:
        assert self._meeting_result().has_decisions() is True


# ─────────────────────────────────────────────────────────────
# 5. 합성 데이터 — 구조 검증
# ─────────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_meeting_contains_timestamps(self) -> None:
        import re
        text = make_meeting_transcript()
        assert re.search(r"\d{2}:\d{2}:\d{2}", text), "타임스탬프 없음"

    def test_meeting_has_no_real_pii(self) -> None:
        """합성 픽스처에 실데이터가 새어들지 않았는지 — **패턴**으로 검사한다.

        특정 사명·계정명을 문자열로 박으면 가드가 지키려던 값을 저장소에 그대로 노출한다
        (공개 저장소에서는 그 자체가 유출). 패턴 검사는 노출 없이 더 넓게 잡는다.
        """
        text = make_meeting_transcript()
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "이메일 주소 발견"
        assert not re.search(r"https?://(?!example\.)", text), "실제 URL 발견"
        assert not re.search(r"\b\d{2,3}-\d{3,4}-\d{4}\b", text), "전화번호 발견"
        # 사내 고유명은 저장소 밖(환경변수)에서 주입해 검사 — 목록 자체를 커밋하지 않는다.
        # 예: GERYON_PII_DENY="사명,계정명" pytest tests/test_extract.py
        deny = [t.strip().lower() for t in os.getenv("GERYON_PII_DENY", "").split(",") if t.strip()]
        low = text.lower()
        for term in deny:
            assert term not in low, f"금칙어 발견: {term}"

    def test_meeting_different_seeds_differ(self) -> None:
        assert make_meeting_transcript(seed=1) != make_meeting_transcript(seed=2)

    def test_spec_contains_sql(self) -> None:
        assert "CREATE TABLE" in make_spec_document()

    def test_spec_contains_java(self) -> None:
        assert "public class" in make_spec_document() or "public static" in make_spec_document()

    def test_spec_no_real_company_info(self) -> None:
        text = make_spec_document()
        assert "tidesquare" not in text.lower()
        assert "tidesquare" not in text.lower()


# ─────────────────────────────────────────────────────────────
# 6. 통합 테스트 — 실제 Ollama 필요
# ─────────────────────────────────────────────────────────────
def _ollama_available() -> bool:
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


_SKIP_INTEGRATION = pytest.mark.skipif(
    not _ollama_available(),
    reason="Ollama 미실행 — 통합 테스트 건너뜀",
)


# ── 통합 테스트용 모듈 수준 fixture (class-scope 경고 회피) ──────
@pytest.fixture(scope="module")
def meeting_result() -> ExtractionResult:
    refiner = DocumentRefiner(LLMConfig.from_env())
    return refiner.extract_meeting(make_meeting_transcript(seed=42))


@pytest.fixture(scope="module")
def spec_result() -> ExtractionResult:
    refiner = DocumentRefiner(LLMConfig.from_env())
    return refiner.extract_spec(make_spec_document(seed=0))


@pytest.mark.integration
@_SKIP_INTEGRATION
class TestMeetingIntegration:
    """실제 LLM으로 합성 회의 전사체를 처리."""

    def test_returns_segments(self, meeting_result: ExtractionResult) -> None:
        assert len(meeting_result.segments) > 0

    def test_has_topic_stream(self, meeting_result: ExtractionResult) -> None:
        streams = {s["stream"] for s in meeting_result.segments}
        assert "topic" in streams, f"topic 스트림 없음. 발견된 스트림: {streams}"

    def test_action_items_not_empty(self, meeting_result: ExtractionResult) -> None:
        assert len(meeting_result.action_items()) > 0, "액션아이템 0개"

    def test_version_info_complete(self, meeting_result: ExtractionResult) -> None:
        m = meeting_result.meta
        assert m.model
        assert m.schema_version == SCHEMA_VERSION
        assert m.processed_at
        assert m.elapsed_sec > 0
        assert m.char_count  > 0

    def test_digest_recorded(self, meeting_result: ExtractionResult) -> None:
        assert meeting_result.meta.model_digest != "", "model_digest 비어있음"

    def test_db_meta_serializable(self, meeting_result: ExtractionResult) -> None:
        json.dumps(meeting_result.to_db_meta())


@pytest.mark.integration
@_SKIP_INTEGRATION
class TestSpecIntegration:
    """실제 LLM으로 합성 스팩 문서를 처리.

    합성 문서는 ## 헤더로 섹션을 명시해 LLM이 db_schema / api / logic을 구분하도록 유도.
    LLM 모델·버전 차이에 따른 변동성을 고려해 섹션 타입보다 내용(SQL 키워드) 기준으로 검증.
    """

    def test_returns_sections(self, spec_result: ExtractionResult) -> None:
        assert len(spec_result.sections) > 0

    def test_has_structured_sections(self, spec_result: ExtractionResult) -> None:
        # db_schema 이거나, 코드 블록이 있는 섹션이 최소 1개 이상
        types = {s["type"] for s in spec_result.sections}
        has_db_section  = "db_schema" in types
        has_code_blocks = any(s.get("code_blocks") for s in spec_result.sections)
        assert has_db_section or has_code_blocks, \
            f"구조화 섹션 없음. 섹션 타입: {types}"

    def test_sql_or_table_content_present(self, spec_result: ExtractionResult) -> None:
        # 코드 블록이나 summary에 SQL 핵심 키워드가 있어야 함
        all_text = " ".join(
            cb["content"]
            for s in spec_result.sections
            for cb in s.get("code_blocks", [])
        ) + " ".join(s.get("summary", "") for s in spec_result.sections)

        sql_keywords = {"CREATE TABLE", "SELECT", "INSERT", "item_code", "price_ceil",
                        "post_process", "history"}
        found = [kw for kw in sql_keywords if kw.lower() in all_text.lower()]
        assert found, f"SQL 관련 키워드를 찾을 수 없음. 섹션 요약:\n" + \
                      "\n".join(f"  [{s['type']}] {s['summary'][:80]}" for s in spec_result.sections)

    def test_java_class_mentioned(self, spec_result: ExtractionResult) -> None:
        # Java 클래스명이 key_items나 코드블록에 등장해야 함
        all_text = " ".join(
            s.get("summary", "") + " " +
            " ".join(s.get("key_items", [])) + " " +
            " ".join(cb.get("content", "") for cb in s.get("code_blocks", []))
            for s in spec_result.sections
        )
        assert "PostProcessParameters" in all_text or "calculatePostProcessAmount" in all_text, \
            "Java 클래스·메서드명이 결과에 없음"

    def test_version_info_complete(self, spec_result: ExtractionResult) -> None:
        m = spec_result.meta
        assert m.doc_type       == "spec"
        assert m.schema_version == SCHEMA_VERSION
        assert m.model_digest   != ""
