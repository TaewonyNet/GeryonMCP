"""출력 압축. 규칙 기반(비-LLM) 옵트인. 기본 OFF(무손실). 외부 호출 0."""
import re


def estimate_tokens(text: str) -> int:
    """문자/4는 한국어 과소추정 → 한글 가중(≈1.6자/토큰), 그 외 ≈4자/토큰. 보수적."""
    hangul = sum(1 for c in text if "가" <= c <= "힣")
    other = len(text) - hangul
    return int(hangul / 1.6 + other / 4) + 1


def strip_whitespace(t: str) -> str:
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def fold_codeblocks(t: str, keep_lines: int = 3) -> str:
    def _fold(m: re.Match) -> str:
        body = m.group(1).strip("\n").splitlines()
        if len(body) <= keep_lines:
            return m.group(0)
        return "```\n" + "\n".join(body[:keep_lines]) + f"\n… (+{len(body) - keep_lines} lines)\n```"
    return re.sub(r"```(.*?)```", _fold, t, flags=re.S)


def summarize_tables(t: str) -> str:
    def _sum(m: re.Match) -> str:
        rows = [r for r in m.group(0).splitlines() if r.strip().startswith("|")]
        header = rows[0] if rows else ""
        return f"{header}\n| … {max(0, len(rows) - 2)}행 생략 …"
    return re.sub(r"(?:^\|.*\|$\n?)+", _sum, t, flags=re.M)


def heading_outline(t: str) -> str:
    lines = [ln for ln in t.splitlines() if re.match(r"^#{1,6}\s", ln)]
    return "\n".join(lines) if lines else t


def shorten_urls(t: str) -> str:
    """로컬 절단만(외부 호출 0): 쿼리스트링 제거 + 긴 경로 말줄임."""
    def _short(m: re.Match) -> str:
        u = m.group(0).split("?", 1)[0]
        return u if len(u) <= 60 else u[:40] + "…" + u[-15:]
    return re.sub(r"https?://\S+", _short, t)


def truncate_at_section(t: str, max_tokens: int) -> str:
    """섹션(빈 줄/heading) 경계 우선 절단 — 중간 단어를 자르지 않는다."""
    if estimate_tokens(t) <= max_tokens:
        return t
    blocks = re.split(r"(\n\n|\n#{1,6}\s)", t)
    out = ""
    for b in blocks:
        if estimate_tokens(out + b) > max_tokens:
            break
        out += b
    return out.rstrip() + "\n… (truncated)"


def compress(text: str, max_tokens: int | None = None, rules: dict | None = None) -> str:
    """확정 순서: 공백 → 코드접기 → 표요약 → (옵션)아웃라인 → URL 로컬절단 → 상한."""
    rules = rules or {}
    t = strip_whitespace(text)
    if rules.get("fold_code", True):
        t = fold_codeblocks(t)
    if rules.get("summarize_tables", True):
        t = summarize_tables(t)
    if rules.get("outline_only", False):
        t = heading_outline(t)
    t = shorten_urls(t)
    if max_tokens:
        t = truncate_at_section(t, max_tokens)
    return t
