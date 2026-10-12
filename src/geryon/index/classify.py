"""doc_type 규칙 분류기. 무비용·규칙 기반(LLM 0)·결정적.

Confluence(위키 문서)와 Git(README 등 문서 파일) 양쪽에서 쓴다 — 소스 무관.
"""
import os
import re

# 소스 무관 문서 유형 키워드 사전(한/영). 우선순위: 위에서부터 먼저 매칭(구체 → 일반).
#
# ⚠️ 이 목록은 범용(어느 조직에나 있을 법한 문서 유형)으로 한정한다. 특정 조직의 용어·업무명을
#    여기 하드코딩하지 말 것([[oss-flexibility-over-our-corpus]]) — 조직별 유형은
#    `GERYON_DOC_TYPE_RULES` 로 추가한다.
DOC_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("meeting",    ["회의록", "회의", "미팅", "weekly", "주간", "daily", "스크럼", "스탠드업"]),
    ("retro",      ["회고", "retro", "포스트모템", "postmortem", "kpt"]),
    ("data_model", ["스키마", "schema", "데이터모델", "ddl", "테이블정의", "erd", "data schema"]),
    ("spec",       ["명세", "스펙", "spec", "요구사항", "requirement"]),
    ("design",     ["설계", "디자인", "design", "아키텍처", "architecture"]),
    ("issue",      ["이슈", "오류", "장애", "버그", "issue", "bug", "트러블", "incident"]),
    ("analysis",   ["분석", "리포트", "report", "지표", "통계", "metric"]),
    ("ops",        ["운영", "배포", "ops", "deploy", "인프라", "infra", "런북", "runbook"]),
    ("planning",   ["기획", "계획", "roadmap", "로드맵", "전략", "planning"]),
    ("notice",     ["공지", "notice", "안내사항", "announcement"]),
    ("glossary",   ["용어집", "용어 정리", "용어사전", "약어", "acronym", "glossary", "terminology"]),
    ("guide",      ["가이드", "매뉴얼", "안내", "사용법", "guide", "manual", "how-to", "튜토리얼"]),
]


def _env_rules() -> list[tuple[str, list[str]]]:
    """`GERYON_DOC_TYPE_RULES` 로 조직별 유형 추가. 내장 규칙보다 먼저 매칭(우선).

    형식: `type1:kw1|kw2,type2:kw3`. 콜론·콤마가 없으면 조용히 건너뛴다(오타로 분류가
    전부 깨지는 것보단, 그 항목만 무시되는 쪽이 안전).
    """
    raw = os.getenv("GERYON_DOC_TYPE_RULES", "")
    rules: list[tuple[str, list[str]]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        dtype, _, kws = part.partition(":")
        dtype = dtype.strip()
        keywords = [k.strip() for k in kws.split("|") if k.strip()]
        if dtype and keywords:
            rules.append((dtype, keywords))
    return rules


def classify_doc_type(
    title: str,
    hierarchy: list[str] | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """제목·계층·태그에서 doc_type을 규칙으로 분류. 매칭 없으면 None.

    무비용·결정적(동일 입력 → 동일 결과). LLM/외부 호출 없음.
    우선순위: `GERYON_DOC_TYPE_RULES`(조직별, 있으면) → DOC_TYPE_RULES(내장) 순,
    각각 상단(구체 유형)부터 매칭.
    """
    text = " ".join([title or "", " ".join(hierarchy or []), " ".join(tags or [])]).lower()
    if not text.strip():
        return None
    for dtype, keywords in _env_rules() + DOC_TYPE_RULES:
        if any(kw.lower() in text for kw in keywords):
            return dtype
    return None


# Conventional Commits(conventionalcommits.org) 접두사 — 조직 무관 공개 표준이라
# DOC_TYPE_RULES 와 달리 하드코딩해도 OSS 유연성 원칙에 걸리지 않는다.
_COMMIT_TYPE_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]*\))?!?:\s",
    re.IGNORECASE,
)


def classify_commit_type(subject: str) -> str | None:
    """커밋 제목이 Conventional Commits 접두사(`feat:`·`fix:`·`docs:`…)면 그 유형을, 아니면 None.

    매칭되면 `advanced_search(doc_type="fix")` 등으로 전체 히스토리에서 그 종류만 바로
    찾을 수 있다. 접두사를 안 쓰는 저장소(실측: 전체 커밋의 상당수)는 그대로 "commit" 이
    유지되므로 회귀가 없다 — 이 분류는 매칭될 때만 더 구체적인 값을 주는 추가 정보다.
    """
    m = _COMMIT_TYPE_RE.match((subject or "").strip())
    return m.group(1).lower() if m else None
