"""Acquirer ABC — 외부 소스 → Bronze 원본파일(멱등/증분). connector(ingest)와 대칭."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Acquirer(ABC):
    """수집기 인터페이스. 네트워크 호출은 이 계층에만 허용된다."""

    @abstractmethod
    def acquire(self, *, force: bool = False, dry_run: bool = False) -> dict[str, int]:
        """외부 → Bronze 원본파일 생성(멱등). 통계 dict 반환."""

    @abstractmethod
    def healthcheck(self) -> bool:
        """자격증명/대상 접근 가능 여부."""
