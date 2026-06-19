import pytest
import tempfile
import json
from pathlib import Path
from geryon.domain.models import SourceType
from geryon.connectors.confluence import ConfluenceConnector

@pytest.fixture
def mock_confluence_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # 1. Create a page with meta.json (Standard)
        page1_dir = tmp_path / "DEMO" / "4923260939"
        page1_dir.mkdir(parents=True, exist_ok=True)
        
        meta = {
            "source": "confluence",
            "source_id": "4923260939",
            "space_or_repo": "DEMO",
            "title": "최대 할인율 개발",
            "url": "https://confluence.example.com/pages/4923260939",
            "author": "hong.gildong",
            "created_at": "2024-07-01T00:00:00Z",
            "updated_at": "2024-07-03T00:00:00Z",
            "tags": ["pricing", "discount"],
            "hierarchy": ["DEMO", "가격정책", "할인율"],
            "attachments": [
                {"filename": "image-20240702.png", "media_type": "image/png",
                 "local_path": "attachments/image-20240702.png", "url": None}
            ],
            "content_file": "content.html",
            "content_format": "storage",
            "raw_meta": { "version": 12 },
            "acquired_at": "2026-05-28T09:26:00Z"
        }
        
        with open(page1_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)
            
        with open(page1_dir / "content.html", "w", encoding="utf-8") as f:
            f.write("<p>Hello from Confluence storage format!</p>")
            
        # 2. Create a legacy page without meta.json (Fallback test)
        page2_dir = tmp_path / "TE" / "987654321"
        page2_dir.mkdir(parents=True, exist_ok=True)
        
        with open(page2_dir / "content.html", "w", encoding="utf-8") as f:
            f.write("<html><head><title>Legacy Title</title></head><body><p>Legacy body</p></body></html>")
            
        yield tmp_path

def test_confluence_connector_healthcheck(mock_confluence_db):
    connector = ConfluenceConnector(db_path=mock_confluence_db)
    assert connector.healthcheck() is True
    
    # Non-existent path check
    bad_connector = ConfluenceConnector(db_path=mock_confluence_db / "nonexistent")
    assert bad_connector.healthcheck() is False

def test_confluence_connector_iter_raw(mock_confluence_db):
    connector = ConfluenceConnector(db_path=mock_confluence_db)
    records = list(connector.iter_raw())
    
    assert len(records) == 2
    
    # Find record 1 (DEMO / 4923260939)
    rec1 = next(r for r in records if r.source_id == "4923260939")
    assert rec1.source == SourceType.CONFLUENCE
    assert rec1.space_or_repo == "DEMO"
    assert rec1.raw_body == "<p>Hello from Confluence storage format!</p>"
    assert rec1.raw_format == "storage"
    assert rec1.title == "최대 할인율 개발"
    assert rec1.url == "https://confluence.example.com/pages/4923260939"
    assert rec1.metadata["author"] == "hong.gildong"
    assert rec1.metadata["tags"] == ["pricing", "discount"]
    assert len(rec1.attachments) == 1
    assert rec1.attachments[0].filename == "image-20240702.png"
    
    # Find record 2 (TE / 987654321)
    rec2 = next(r for r in records if r.source_id == "987654321")
    assert rec2.source == SourceType.CONFLUENCE
    assert rec2.space_or_repo == "TE"
    assert "Legacy body" in rec2.raw_body
    assert rec2.raw_format == "html"
    assert rec2.title == "Legacy Title" # Extracted from HMTL <title>
