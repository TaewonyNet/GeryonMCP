import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.embed.embedder import LocalEmbedder  # noqa: E402


def test_model_is_singleton():
    a = LocalEmbedder()
    b = LocalEmbedder()
    assert a.model is b.model # 모듈 캐시 재사용(중복 로드 없음)


def test_chunking_size_and_id():
    text = "\n\n".join(["문단%d %s" % (i, "가" * 60) for i in range(20)])
    chunks = LocalEmbedder().chunk_document("d1", text)
    assert chunks
    assert chunks[0].chunk_id == "d1#0"
    assert all(c.doc_id == "d1" for c in chunks)


def test_embed_query_dim():
    v = LocalEmbedder().embed_query("테스트 질의")
    assert len(v) == 384
    assert all(isinstance(x, float) for x in v)
