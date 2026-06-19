from datetime import datetime

def test_document_roundtrip():
    # This will fail because src/geryon/domain/models.py does not exist yet or cannot be imported
    from geryon.domain.models import Document, SourceType, Attachment
    
    doc = Document(
        doc_id="test-doc-id",
        source=SourceType.CONFLUENCE,
        source_id="12345",
        space_or_repo="DEMO",
        url="https://confluence.example.com/pages/12345",
        title="Test Title",
        body_markdown="# Test Body\nHello world",
        summary="Test Summary",
        tags=["pricing", "discount"],
        hierarchy=["DEMO", "pricing"],
        author="test.user",
        created_at=datetime(2024, 7, 1, 0, 0, 0),
        updated_at=datetime(2024, 7, 3, 0, 0, 0),
        attachments=[
            Attachment(filename="image.png", media_type="image/png", local_path="attachments/image.png")
        ],
        raw_meta={"version": 1},
        content_hash="test-content-hash",
        ingested_at=datetime(2026, 5, 29, 9, 26, 0)
    )
    
    serialized = doc.model_dump()
    deserialized = Document.model_validate(serialized)
    
    assert deserialized.doc_id == doc.doc_id
    assert deserialized.source == doc.source
    assert deserialized.source_id == doc.source_id
    assert deserialized.space_or_repo == doc.space_or_repo
    assert deserialized.url == doc.url
    assert deserialized.title == doc.title
    assert deserialized.body_markdown == doc.body_markdown
    assert deserialized.summary == doc.summary
    assert deserialized.tags == doc.tags
    assert deserialized.hierarchy == doc.hierarchy
    assert deserialized.author == doc.author
    assert deserialized.created_at == doc.created_at
    assert deserialized.updated_at == doc.updated_at
    assert len(deserialized.attachments) == 1
    assert deserialized.attachments[0].filename == "image.png"
    assert deserialized.raw_meta == doc.raw_meta
    assert deserialized.content_hash == doc.content_hash
    assert deserialized.ingested_at == doc.ingested_at
