from datetime import datetime
from geryon.domain.models import SearchHit, SearchFilter

def calculate_recency_decay(updated_at: datetime | None, half_life_days: float = 30.0) -> float:
    """Calculate exponential recency decay.
    
    Returns 1.0 when delta = 0, 0.5 when delta = half_life_days,
    0.0 if updated_at is None, and clamps negative delta days to 0.0 (resulting in 1.0).
    """
    if updated_at is None:
        return 0.0
        
    if updated_at.tzinfo is not None:
        now = datetime.now(updated_at.tzinfo)
    else:
        now = datetime.now()
        
    delta = now - updated_at
    delta_days = delta.total_seconds() / (24.0 * 3600.0)
    
    if delta_days < 0.0:
        delta_days = 0.0
        
    return 0.5 ** (delta_days / half_life_days)

class Retriever:
    def search(
        self,
        query: str,
        k: int = 10,
        offset: int = 0,
        filters: SearchFilter | None = None
    ) -> list[SearchHit]:
        raise NotImplementedError
