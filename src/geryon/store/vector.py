import sqlite3
import array
from pathlib import Path
from typing import cast

from geryon.config import DB_PATH
from geryon.store.db import init_db
from geryon.domain.models import SearchFilter

class VectorStore:
    """Manages document chunks and their high-dimensional vector embeddings using sqlite-vec."""
    
    db_path: Path
    _conn: sqlite3.Connection | None

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(DB_PATH)
        self._conn = None
        
    def get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = init_db(self.db_path)
        return self._conn
        
    def save_chunks(self, doc_id: str, chunks_data: list[tuple[str, int, str, list[float]]]) -> None:
        """Saves chunks and their embeddings, replacing any existing chunks for the given doc_id."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Delete old chunk embeddings first
            _ = cursor.execute(
                "DELETE FROM chunk_embeddings WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_id = ?);",
                (doc_id,)
            )
            # Delete old chunks
            _ = cursor.execute("DELETE FROM chunks WHERE doc_id = ?;", (doc_id,))
            
            # Insert new chunks and chunk embeddings
            for chunk_id, ordinal, text, vector in chunks_data:
                _ = cursor.execute(
                    "INSERT INTO chunks (chunk_id, doc_id, ordinal, text) VALUES (?, ?, ?, ?);",
                    (chunk_id, doc_id, ordinal, text)
                )
                vector_bytes = array.array('f', vector).tobytes()
                _ = cursor.execute(
                    "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?);",
                    (chunk_id, vector_bytes)
                )
                
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
            
    def search_vector(
        self,
        query_vector: list[float],
        k: int = 10,
        offset: int = 0,
        filters: SearchFilter | None = None
    ) -> list[dict[str, object]]:
        """Queries chunk_embeddings using cosine distance and returns standard dictionaries."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query_bytes = array.array('f', query_vector).tobytes()
        
        query_parts = [
            """
            SELECT 
                c.chunk_id, 
                c.doc_id, 
                c.text,
                vec_distance_cosine(e.embedding, ?) AS distance
            FROM chunk_embeddings e
            JOIN chunks c ON e.chunk_id = c.chunk_id
            LEFT JOIN documents d ON c.doc_id = d.doc_id
            WHERE 1=1
            """
        ]
        params: list[object] = [query_bytes]
        
        if filters:
            if filters.sources:
                placeholders = ", ".join("?" for _ in filters.sources)
                query_parts.append(f"AND d.source IN ({placeholders})")
                params.extend(s.value if hasattr(s, "value") else str(s) for s in filters.sources)

            if filters.spaces_or_repos:
                placeholders = ", ".join("?" for _ in filters.spaces_or_repos)
                query_parts.append(f"AND d.space_or_repo IN ({placeholders})")
                params.extend(filters.spaces_or_repos)

            if filters.tags:
                placeholders = ", ".join("?" for _ in filters.tags)
                query_parts.append(
                    f"AND EXISTS (SELECT 1 FROM document_tags WHERE document_tags.doc_id = d.doc_id AND document_tags.tag IN ({placeholders}))"
                )
                params.extend(filters.tags)

            if filters.categories:
                placeholders = ", ".join("?" for _ in filters.categories)
                query_parts.append(
                    f"AND EXISTS (SELECT 1 FROM document_categories WHERE document_categories.doc_id = d.doc_id AND document_categories.category IN ({placeholders}))"
                )
                params.extend(filters.categories)

            if filters.authors:
                placeholders = ", ".join("?" for _ in filters.authors)
                query_parts.append(f"AND d.author IN ({placeholders})")
                params.extend(filters.authors)

            if filters.date_from:
                query_parts.append("AND COALESCE(d.updated_at, d.created_at) >= ?")
                params.append(filters.date_from.isoformat())

            if filters.date_to:
                query_parts.append("AND COALESCE(d.updated_at, d.created_at) <= ?")
                params.append(filters.date_to.isoformat())
                
        query_parts.append("ORDER BY distance ASC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([k, offset])
        
        sql = "\n".join(query_parts)
        _ = cursor.execute(sql, params)
        
        rows = cursor.fetchall()
        results: list[dict[str, object]] = []
        for r in rows:
            chunk_id = cast(str, r[0])
            doc_id = cast(str, r[1])
            text = cast(str, r[2])
            distance = cast(float | None, r[3])
            
            # cosine similarity = 1.0 - cosine distance
            score = 1.0 - distance if distance is not None else 0.0
            results.append({
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "text": text,
                "score": score
            })
            
        return results
