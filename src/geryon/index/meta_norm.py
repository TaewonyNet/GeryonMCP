"""메타데이터 노이즈 정규화 — 컬럼별 규칙(진단 14 §메타 기반).

검색·facet·표시에서 같은 값이 노이즈로 분리되는 것을 막는다.
- author     : 상태 접미사 괄호 제거(`박지훈 (Unlicensed)`→`박지훈`), 익명 통일
- category   : 따옴표·해시 접두 제거(`"Other"`→`Other`, `#2024년 10월`→`2024년 10월`)
- title/tag  : 앞 장식기호·공백 trim(표시·매칭 안정)
원본은 보존이 필요하면 `raw_meta`에 남긴다. 정규화는 멱등(두 번 적용해도 동일)."""

from __future__ import annotations
import re

# author 괄호 안이 이들 중 하나면 상태 표식 → 제거(이름 별칭 괄호는 보존)
_AUTHOR_STATUS = {
    "deactivated", "unlicensed", "deleted", "inactive", "disabled",
    "suspended", "퇴사", "비활성", "탈퇴", "삭제",
}
_ANON = "(익명)"  # 'Former user (Deleted)' 등 식별 불가 작성자 통일값


def normalize_author(value: str | None) -> str:
    """작성자 정규화: 상태 접미사 제거·익명 통일·공백 정리."""
    if not value:
        return ""
    s = value.strip()
    if s.lower().startswith("former user") or s.lower() in ("anonymous", "unknown"):
        return _ANON
    # 괄호 그룹 중 '상태 표식'만 제거(별칭은 보존)
    s = re.sub(
        r"\s*\(([^)]*)\)",
        lambda m: "" if m.group(1).strip().lower() in _AUTHOR_STATUS else m.group(0),
        s,
    )
    return re.sub(r"\s+", " ", s).strip()


def normalize_category(value: str | None) -> str:
    """분류 정규화: 양끝 따옴표·앞 해시·공백 제거."""
    if not value:
        return ""
    s = value.strip().strip('"').strip("'").lstrip("#").strip()
    return re.sub(r"\s+", " ", s)


_LEAD_DECOR = "●·•‣▪◦∙◆▶▷○✱*※ \t"  # 순수 장식 불릿(대괄호·해시는 의미일 수 있어 보존)


def normalize_label(value: str | None) -> str:
    """제목·태그 등 일반 라벨: 앞 장식 불릿·공백 trim(검색/표시 안정)."""
    if not value:
        return ""
    s = value.strip().lstrip(_LEAD_DECOR).strip()
    return re.sub(r"\s+", " ", s) or value.strip()
