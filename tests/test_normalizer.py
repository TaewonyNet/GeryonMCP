from geryon.domain.models import RawRecord, SourceType, Attachment
from geryon.normalize.normalizer import Normalizer

def test_normalizer_confluence_spec_conversions():
    normalizer = Normalizer()
    
    html_body = """
    <html>
    <head><style>body { color: red; }</style><script>alert('hello');</script></head>
    <body>
    <!-- comments should be removed -->
    <h1>최대 할인율 개발</h1>
    <p>할인율 관련 정책 및 공식 정의</p>
    
    <ac:structured-macro ac:name="code">
        <ac:parameter ac:name="language">python</ac:parameter>
        <ac:plain-text-body><![CDATA[def get_max_discount():
    return 0.5]]></ac:plain-text-body>
    </ac:structured-macro>
    
    <ac:structured-macro ac:name="info">
        <ac:rich-text-body><p>중요 안내: 정책 준수 필수</p></ac:rich-text-body>
    </ac:structured-macro>
    
    <ac:structured-macro ac:name="status">
        <ac:parameter ac:name="title">COMPLETED</ac:parameter>
    </ac:structured-macro>
    
    <p>이미지 예시: <ac:image><ri:attachment ri:filename="diagram.png" /></ac:image></p>
    </body>
    </html>
    """
    
    record = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="12345",
        raw_body=html_body,
        raw_format="html",
        title="최대 할인율 개발",
        url="https://confluence.example.com/pages/12345",
        space_or_repo="DEMO",
        metadata={
            "author": "hong.gildong",
            "created_at": "2024-07-01T00:00:00Z",
            "updated_at": "2024-07-03T00:00:00Z",
            "tags": ["pricing", "discount"],
            "hierarchy": ["DEMO", "가격정책"]
        },
        attachments=[
            Attachment(filename="diagram.png", media_type="image/png")
        ]
    )
    
    doc = normalizer.normalize(record)
    
    # 1. Hashing
    assert doc.doc_id == "5c8b57ffcbef7732f5ab443318531086bda8794c" # sha1("confluence:12345")
    assert doc.source == SourceType.CONFLUENCE
    assert doc.source_id == "12345"
    assert doc.space_or_repo == "DEMO"
    assert doc.url == "https://confluence.example.com/pages/12345"
    assert doc.title == "최대 할인율 개발"
    
    # 2. Markdown contents checks
    markdown = doc.body_markdown
    
    # Verify code block is parsed correctly (preserving language)
    assert "```python" in markdown or "```" in markdown
    assert "def get_max_discount" in markdown
    
    # Verify macro wrapper like ac:structured-macro (info) retains inner text
    assert "중요 안내: 정책 준수 필수" in markdown
    
    # Verify script, style, comments are removed
    assert "alert('hello')" not in markdown
    assert "body { color: red; }" not in markdown
    assert "comments should be removed" not in markdown
    
    # Verify date mapping
    assert doc.author == "hong.gildong"
    assert doc.created_at is not None
    assert doc.created_at.year == 2024
    assert doc.updated_at is not None
    assert doc.updated_at.day == 3
    assert doc.tags == ["pricing", "discount"]
    assert doc.hierarchy == ["DEMO", "가격정책"]
    assert len(doc.attachments) == 1
    assert doc.attachments[0].filename == "diagram.png"

def test_normalizer_hash_determinism():
    normalizer = Normalizer()
    
    record = RawRecord(
        source=SourceType.WEB,
        source_id="sha1_web_url",
        raw_body="<p>Test page body</p>",
        raw_format="html",
        title="Web Page Title",
        url="https://example.com/web-page"
    )
    
    doc1 = normalizer.normalize(record)
    doc2 = normalizer.normalize(record)
    
    # Hashing determinism check
    assert doc1.doc_id == doc2.doc_id
    assert doc1.content_hash == doc2.content_hash
    
    # Slight modifications in body changes hash but preserves doc_id
    record_mod = record.model_copy(update={"raw_body": "<p>Modified body</p>"})
    doc_mod = normalizer.normalize(record_mod)
    assert doc_mod.doc_id == doc1.doc_id
    assert doc_mod.content_hash != doc1.content_hash


# ═══════════════════════════════════════ 날짜 파싱 — 콜론 없는 오프셋 (2026-09-20)
#
# 결함: `parse_iso8601` 이 `Z` 접미사만 다루고 콜론 없는 오프셋(`+0900`)을
# 처리하지 않아, Jira REST 가 주는 날짜를 **전건 버렸다**. Python 3.10 의
# `fromisoformat` 이 `+09:00` 을 요구하는데 예외를 삼키고 None 을 돌려줬다.
#
# 실측 피해(2026-09-20, 실물 인덱스): jira 전건 created/updated 100% NULL,
# 전체 문서의 약 1/5. v1.0.0 부터 넉 달간 아무도 몰랐다 — ingest 가 errors 0 으로
# 성공 보고했기 때문이다.

from datetime import timezone, timedelta  # noqa: E402

from geryon.normalize.normalizer import parse_iso8601  # noqa: E402


def test_jira_형식_콜론없는_오프셋을_읽는다():
    """이 시험이 깨지면 jira 날짜가 다시 전멸한다."""
    dt = parse_iso8601("2023-12-11T10:17:37.790+0900")
    assert dt is not None
    assert dt.utcoffset() == timedelta(hours=9)
    assert (dt.year, dt.month, dt.day) == (2023, 12, 11)


def test_음수_오프셋도_읽는다():
    dt = parse_iso8601("2023-12-11T10:17:37-0500")
    assert dt is not None and dt.utcoffset() == timedelta(hours=-5)


def test_날짜만_있는_문자열의_하이픈을_오프셋으로_오인하지_않는다():
    """`2023-12-11` 의 `-11` 을 오프셋으로 잘못 붙이면 파싱이 깨진다.

    정규식이 `(?<=\\d)([+-])(\\d{2})(\\d{2})$` 라 `-1211` 형태만 잡는데,
    경계 조건이므로 고정해 둔다.
    """
    dt = parse_iso8601("2023-12-11")
    assert dt is not None and (dt.year, dt.month, dt.day) == (2023, 12, 11)


def test_기존_형식들이_계속_동작한다():
    """콜론 있는 오프셋·Z·오프셋 없음 — 회귀 방지."""
    assert parse_iso8601("2023-12-11T10:17:37.790+09:00") is not None
    assert parse_iso8601("2023-08-25T08:51:08.736000+00:00") is not None   # confluence 실물
    assert parse_iso8601("2023-12-11T10:17:37Z").tzinfo == timezone.utc
    assert parse_iso8601("2023-12-11T10:17:37z").tzinfo == timezone.utc    # 소문자
    assert parse_iso8601("2023-12-11T10:17:37") is not None                # naive


def test_못_읽는_값은_None_이다():
    for bad in (None, "", "   ", "garbage", 12345, "2023-13-45T99:99:99"):
        assert parse_iso8601(bad) is None
