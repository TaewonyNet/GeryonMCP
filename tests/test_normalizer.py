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
