"""한국어 검색 정규화 (언어 플러그인, 19 §3.6 / 재검증).

`unicode61`이 조사·복합형을 토큰 단위로 놓치는 문제(놓침률 휴가82%·마트57%)를
**색인·쿼리 양쪽에서 어절 끝 조사를 제거**해 토큰을 일치시켜 해소한다.
재검증(full pipeline): 원본 +3%p·조사형 +13%p·전체 +8%p, 속도 오히려 빠름, 부작용 0.

무비용·오프라인·규칙기반(형태소 분석기 의존 0). 쿼리 오버헤드 ~10µs, 색인은 빌드타임.
"""
import re

# 어절 끝 조사(긴 것 우선 매칭). 명사가 남도록 어절 길이 > 조사+1 일 때만 제거.
_JOSA = sorted(
    ["으로서", "으로써", "에서", "에게", "한테", "까지", "부터", "마다", "조차",
     "라도", "이나", "처럼", "보다", "같이", "이라", "으로", "이며",
     "을", "를", "은", "는", "이", "가", "의", "에", "와", "과", "도", "만",
     "로", "나", "라", "및", "께", "요"],
    key=len, reverse=True,
)
_NONWORD = re.compile(r"[^\w\s]")


def strip_josa(word: str) -> str:
    """어절 끝 조사 1개 제거(명사가 남을 때만)."""
    for j in _JOSA:
        if len(word) > len(j) + 1 and word.endswith(j):
            return word[: -len(j)]
    return word


def normalize_korean(text: str) -> str:
    """검색용 정규화: 구두점 제거 + 어절별 조사 제거. 색인·쿼리에 동일 적용."""
    if not text:
        return ""
    return " ".join(strip_josa(w) for w in _NONWORD.sub(" ", text).split())
