from fastembed import TextEmbedding
from fastembed.common.model_description import PoolingType, ModelSource
from geryon.domain.models import Chunk

_MODEL_CACHE: dict[str, TextEmbedding] = {}


def _get_model(model_name: str) -> TextEmbedding:
    """모듈 레벨 캐시 — fastembed 모델을 프로세스당 1회만 로드."""
    if model_name not in _MODEL_CACHE:
        try:
            TextEmbedding.add_custom_model(
                model=model_name,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=model_name),
                dim=384,
            )
        except Exception:
            pass
        _MODEL_CACHE[model_name] = TextEmbedding(model_name=model_name)
    return _MODEL_CACHE[model_name]


class LocalEmbedder:
    """Generates local embeddings using fastembed."""

    MODEL_NAME = "intfloat/multilingual-e5-small"
    model: TextEmbedding

    def __init__(self) -> None:
        self.model = _get_model(self.MODEL_NAME)  # 캐시 재사용(중복 로드 없음)
        
    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Prepend 'passage: ' to each passage text before embedding as mandated by E5 conventions,
        and embed texts in batch (batch_size=64).
        """
        prefixed_texts: list[str] = [f"passage: {text}" for text in texts]
        # TextEmbedding.embed returns a generator of numpy arrays. We convert them to lists of floats.
        embeddings = list(self.model.embed(prefixed_texts, batch_size=64))
        return [list(map(float, emb)) for emb in embeddings]
        
    def embed_query(self, query: str) -> list[float]:
        """Prepend 'query: ' to the query text before embedding and return the single float vector."""
        prefixed_query: str = f"query: {query}"
        # embed a single query text (returns a generator)
        embeddings = list(self.model.embed([prefixed_query]))
        return list(map(float, embeddings[0]))
        
    def chunk_document(self, doc_id: str, text: str) -> list[Chunk]:
        """Clean and split text (body_markdown) into chunks of ~512 characters
        with ~64 characters overlap, using paragraph splitting.
        """
        if not text:
            return []
            
        raw_paragraphs: list[str] = text.split("\n\n")
        paragraphs: list[str] = []
        for p in raw_paragraphs:
            p_clean: str = p.strip()
            if p_clean:
                paragraphs.append(p_clean)
                
        if not paragraphs:
            return []
            
        chunks: list[Chunk] = []
        current_paragraphs: list[str] = []
        current_length: int = 0
        ordinal: int = 0
        
        for p in paragraphs:
            if current_paragraphs and current_length + 2 + len(p) > 512:
                # Finalize current chunk
                chunk_text: str = "\n\n".join(current_paragraphs)
                chunks.append(Chunk(
                    chunk_id=f"{doc_id}#{ordinal}",
                    doc_id=doc_id,
                    ordinal=ordinal,
                    text=chunk_text
                ))
                ordinal += 1
                
                # Keep overlap of ~64 characters
                overlap_paragraphs: list[str] = []
                overlap_len: int = 0
                for prev_p in reversed(current_paragraphs):
                    if overlap_paragraphs and overlap_len + 2 + len(prev_p) > 64:
                        break
                    overlap_paragraphs.insert(0, prev_p)
                    overlap_len += len(prev_p) + (2 if len(overlap_paragraphs) > 1 else 0)
                    
                current_paragraphs = overlap_paragraphs
                current_length = overlap_len
                
            current_paragraphs.append(p)
            current_length += len(p) + (2 if len(current_paragraphs) > 1 else 0)
            
        if current_paragraphs:
            chunk_text = "\n\n".join(current_paragraphs)
            chunks.append(Chunk(
                chunk_id=f"{doc_id}#{ordinal}",
                doc_id=doc_id,
                ordinal=ordinal,
                text=chunk_text
            ))
            
        return chunks
