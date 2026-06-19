from abc import ABC, abstractmethod
from collections.abc import Iterator
from geryon.domain.models import SourceType, RawRecord

class Connector(ABC):
    source_type: SourceType
    # 증분(only 필터)을 지원하는가. 파일 기반(confluence)=True,
    # 커밋/그래프 기반(git)=False → 증분 요청 시 자동으로 전체+prune 로 처리.
    supports_incremental: bool = True

    @abstractmethod
    def iter_raw(self, only: set[str] | None = None) -> Iterator[RawRecord]:
        """소스를 순회하며 RawRecord를 지연 산출(yield)한다.

        only(= manifest.last_change 의 added+modified, "category/id" 집합)가 주어지면
        그 항목만 산출한다(증분). None 이면 전체. [24_BRONZE_CONTRACT]
        """
        raise NotImplementedError

    @abstractmethod
    def healthcheck(self) -> bool:
        """소스 접근 가능 여부(로컬 경로 존재/토큰 유효 등)."""
        raise NotImplementedError
