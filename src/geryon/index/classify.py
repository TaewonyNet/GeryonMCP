"""doc_type 규칙 분류기. 무비용·규칙 기반(LLM 0)·결정적."""

# 소스 무관 문서 유형 키워드 사전(한/영). 우선순위: 위에서부터 먼저 매칭(구체 → 일반).
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
    ("guide",      ["가이드", "매뉴얼", "안내", "사용법", "guide", "manual", "how-to", "튜토리얼"]),
]


def classify_doc_type(
    title: str,
    hierarchy: list[str] | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """제목·계층·태그에서 doc_type을 규칙으로 분류. 매칭 없으면 None.

    무비용·결정적(동일 입력 → 동일 결과). LLM/외부 호출 없음.
    우선순위: DOC_TYPE_RULES 상단(구체 유형)부터 매칭.
    """
    text = " ".join([title or "", " ".join(hierarchy or []), " ".join(tags or [])]).lower()
    if not text.strip():
        return None
    for dtype, keywords in DOC_TYPE_RULES:
        if any(kw.lower() in text for kw in keywords):
            return dtype
    return None
