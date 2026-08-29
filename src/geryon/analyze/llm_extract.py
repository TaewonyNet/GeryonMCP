"""문서 정제 레이어 — LLM 기반 구조화 추출.

지원 문서 유형:
  meeting  — 회의 전사체 (3스트림: topic / peripheral / procedural)
  spec     — 기술 스팩 (SQL · 코드 블록 포함)

LLM 설정:
  환경변수로 재정의 가능 (폐쇄망·air-gap 대응)
    GERYON_LLM_MODEL   기본 qwen3.5:latest  (qwen3 이상 권장)
    GERYON_LLM_URL     기본 http://localhost:11434
    GERYON_LLM_CTX     기본 32768
    GERYON_LLM_PREDICT 기본 16384

버전 추적:
  ExtractionResult.meta 에 모델명·digest·스키마 버전·처리 시각 포함
  → DB 저장 시 raw_meta["_extract"] 에 그대로 직렬화
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal

import httpx

SCHEMA_VERSION = "1.0.0"
_OLLAMA_SHOW  = "/api/show"
_OLLAMA_CHAT  = "/api/chat"

DocType = Literal["meeting", "spec", "unknown"]


# ── 설정 ──────────────────────────────────────────────────────
@dataclass
class LLMConfig:
    """LLM 연결·생성 설정. 환경변수로 재정의 가능."""
    model:       str = "qwen3.5:latest"
    base_url:    str = "http://localhost:11434"
    num_ctx:     int = 32768
    num_predict: int = 16384
    temperature: float = 0.0
    timeout:     int = 600

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            model       = os.getenv("GERYON_LLM_MODEL",   "qwen3.5:latest"),
            base_url    = os.getenv("GERYON_LLM_URL",     "http://localhost:11434"),
            num_ctx     = int(os.getenv("GERYON_LLM_CTX",     "32768")),
            num_predict = int(os.getenv("GERYON_LLM_PREDICT", "16384")),
        )

    def model_digest(self) -> str:
        """Ollama 모델 digest (버전 고정 추적용). 조회 실패 시 'unknown'."""
        try:
            resp = httpx.post(
                f"{self.base_url}{_OLLAMA_SHOW}",
                json={"name": self.model},
                timeout=10,
            )
            return resp.json().get("details", {}).get("digest", "unknown")[:12]
        except Exception:
            return "unknown"


# ── 결과 타입 ──────────────────────────────────────────────────
@dataclass
class ExtractionMeta:
    """처리 시 사용된 모델·스키마 버전을 기록 — LLM 변경 추적 핵심."""
    model:          str
    model_digest:   str
    schema_version: str
    doc_type:       DocType
    processed_at:   str    # ISO 8601 UTC
    elapsed_sec:    float
    char_count:     int


@dataclass
class ExtractionResult:
    meta:     ExtractionMeta
    segments: list[dict] = field(default_factory=list)   # meeting용
    sections: list[dict] = field(default_factory=list)   # spec용
    raw:      dict       = field(default_factory=dict)

    def to_db_meta(self) -> dict:
        """raw_meta["_extract"] 로 DB에 저장할 직렬화 형식."""
        return asdict(self.meta)

    # 빠른 품질 지표
    def segment_count(self) -> int:
        return len(self.segments) + len(self.sections)

    def action_items(self) -> list[dict]:
        return [a for s in self.segments for a in s.get("action_items", [])]

    def has_decisions(self) -> bool:
        return any(s.get("contains_decision") for s in self.segments)


# ── JSON 스키마 ────────────────────────────────────────────────
_MEETING_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stream":            {"type": "string", "enum": ["topic", "peripheral", "procedural"]},
                    "title":             {"type": "string"},
                    "summary":           {"type": "string"},
                    "evidence":          {"type": "string"},
                    "contains_decision": {"type": "boolean"},
                    "linked_topic":      {"type": ["string", "null"]},
                    "action_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "owner":       {"type": "string"},
                                "due":         {"type": "string"},
                            },
                            "required": ["description", "owner", "due"],
                        },
                    },
                },
                "required": ["stream", "title", "summary", "evidence",
                             "contains_decision", "linked_topic", "action_items"],
            },
        },
    },
    "required": ["segments"],
}

_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "title":   {"type": "string"},
        "purpose": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "type": {"type": "string",
                             "enum": ["overview", "db_schema", "api", "logic", "formula", "roadmap"]},
                    "title":       {"type": "string"},
                    "summary":     {"type": "string"},
                    "code_blocks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label":    {"type": "string"},
                                "language": {"type": "string",
                                             "enum": ["sql", "java", "python", "other"]},
                                "content":  {"type": "string"},
                            },
                            "required": ["label", "language", "content"],
                        },
                    },
                    "key_items":      {"type": "array", "items": {"type": "string"}},
                    "dependencies":   {"type": "array", "items": {"type": "string"}},
                    "open_questions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["section_id", "type", "title", "summary",
                             "code_blocks", "key_items", "dependencies", "open_questions"],
            },
        },
    },
    "required": ["title", "purpose", "sections"],
}

_MEETING_SYSTEM = """당신은 한국어 회의/문서 분석 전문가입니다.
스트림:
- topic: 공식 안건, 기술 논의, 명시적 결정
- peripheral: 잡담이지만 결정·정보·액션 포함 가능 (식사·회식 등)
- procedural: 무의미한 진행 발언 (화면공유·잡음·일정조율)
규칙:
1. evidence는 원문 핵심 1~2문장만 (짧게)
2. peripheral 이라도 결정·액션 있으면 contains_decision=true
3. linked_topic: peripheral/procedural이 어떤 안건 중에 나왔는지 명시
4. JSON만 응답"""

_SPEC_SYSTEM = """당신은 기술 문서 분석 전문가입니다.
섹션 유형:
- overview: 목적·배경·일반 설명
- db_schema: SQL 테이블 정의, 쿼리 패턴, 데이터 구조
- api: API 엔드포인트, 클래스/메서드 시그니처
- logic: 비즈니스 로직, 처리 흐름, 조건 분기
- formula: 이익·요금·지표 계산식, 수식, 테이블 기반 수치 정의
- roadmap: Phase 계획, 미래 작업, 일정
규칙:
1. 명시적 섹션 헤더(##)가 없어도 내용의 주제 전환을 기준으로 섹션을 나눈다
2. 마크다운 테이블이 여러 개면 각각 또는 주제별로 묶어 별도 섹션으로 분리한다
3. code_blocks: SQL·코드 블록을 원문 그대로 content에 보존
4. key_items: 핵심 필드명·파라미터·지표명·Dimension 값 목록
5. open_questions: 미정이거나 추가 확인 필요한 항목
6. JSON만 응답"""


# ── 핵심 클래스 ───────────────────────────────────────────────
class DocumentRefiner:
    """단일 진입점. doc_type 자동 감지 또는 명시 지정."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()

    # ── 타입 감지 ────────────────────────────────────────────
    @staticmethod
    def detect_type(text: str) -> DocType:
        """타임스탬프 패턴 있으면 meeting, 코드블록·SQL·마크다운 테이블 있으면 spec."""
        if re.search(r"^\d{1,2}:\d{2}:\d{2}\s*$", text, re.MULTILINE):
            return "meeting"
        if re.search(r"(CREATE TABLE|SELECT .+FROM|public (class|static))", text, re.IGNORECASE):
            return "spec"
        if re.search(r"```(sql|java|python)", text, re.IGNORECASE):
            return "spec"
        # 마크다운 테이블 구분선(| --- |)이 있는 구조화 문서
        if re.search(r"\|\s*[-:]+\s*\|", text) and len(text) > 300:
            return "spec"
        return "unknown"

    # ── 공개 API ─────────────────────────────────────────────
    def extract(self, text: str, doc_type: DocType | None = None) -> ExtractionResult:
        """doc_type 미지정 시 자동 감지."""
        dt = doc_type or self.detect_type(text)
        if dt == "spec":
            return self.extract_spec(text)
        return self.extract_meeting(text)   # unknown → meeting으로 폴백

    def extract_meeting(self, text: str) -> ExtractionResult:
        t0 = time.time()
        raw, elapsed = self._call(_MEETING_SYSTEM, f"아래 회의를 분석하세요:\n\n{text}", _MEETING_SCHEMA)
        meta = self._make_meta("meeting", text, elapsed)
        return ExtractionResult(
            meta     = meta,
            segments = raw.get("segments", []),
            raw      = raw,
        )

    def extract_spec(self, text: str) -> ExtractionResult:
        raw, elapsed = self._call(_SPEC_SYSTEM, f"아래 스팩 문서를 분석하세요:\n\n{text}", _SPEC_SCHEMA)
        meta = self._make_meta("spec", text, elapsed)
        return ExtractionResult(
            meta     = meta,
            sections = raw.get("sections", []),
            raw      = raw,
        )

    # ── 내부 ─────────────────────────────────────────────────
    def _make_meta(self, doc_type: DocType, text: str, elapsed: float) -> ExtractionMeta:
        return ExtractionMeta(
            model          = self.config.model,
            model_digest   = self.config.model_digest(),
            schema_version = SCHEMA_VERSION,
            doc_type       = doc_type,
            processed_at   = datetime.now(timezone.utc).isoformat(),
            elapsed_sec    = round(elapsed, 2),
            char_count     = len(text),
        )

    def _call(self, system: str, user: str, schema: dict, retries: int = 3) -> tuple[dict, float]:
        cfg = self.config
        for attempt in range(retries):
            try:
                payload = {
                    "model": cfg.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                    "format": schema,
                    "stream": False,
                    "options": {
                        "temperature": cfg.temperature,
                        "num_ctx":     cfg.num_ctx,
                        "num_predict": cfg.num_predict,
                    },
                }
                t0   = time.time()
                resp = httpx.post(
                    f"{cfg.base_url}{_OLLAMA_CHAT}",
                    json=payload,
                    timeout=cfg.timeout,
                )
                elapsed = time.time() - t0
                content = resp.json()["message"]["content"]
                if not content.strip():
                    continue
                import json
                return json.loads(content), elapsed
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(2)
        return {}, 0.0
