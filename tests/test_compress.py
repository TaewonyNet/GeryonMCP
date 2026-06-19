import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.search.compress import (  # noqa: E402
    compress, estimate_tokens, shorten_urls, strip_whitespace,
    fold_codeblocks, heading_outline, truncate_at_section,
)


def test_korean_token_estimate_is_conservative():
    assert estimate_tokens("한국어 텍스트") >= estimate_tokens("abcd efgh")


def test_strip_whitespace():
    assert strip_whitespace("a   b\n\n\n\nc") == "a b\n\nc"


def test_shorten_urls_local_only():
    t = "see https://x.atlassian.net/wiki/spaces/TE/pages/123/Long?version=4&x=y end"
    out = shorten_urls(t)
    assert "?" not in out and "atlassian" in out  # 쿼리 제거·외부 호출 없음


def test_fold_codeblocks():
    out = fold_codeblocks("```\nL1\nL2\nL3\nL4\nL5\n```")
    assert "L3" in out and "L5" not in out and "+2 lines" in out


def test_heading_outline():
    assert heading_outline("# A\n본문\n## B") == "# A\n## B"


def test_truncate_at_section_boundary():
    txt = "문단1.\n\n문단2.\n\n문단3."
    out = truncate_at_section(txt, estimate_tokens("문단1."))
    assert "문단1" in out and "문단2" not in out and out.endswith("(truncated)")


def test_compress_respects_max_tokens():
    txt = "문단1입니다.\n\n문단2입니다.\n\n문단3입니다."
    out = compress(txt, max_tokens=estimate_tokens("문단1입니다."))
    assert estimate_tokens(out) <= estimate_tokens("문단1입니다.") + 5  # 상한 근사(접미 포함)
