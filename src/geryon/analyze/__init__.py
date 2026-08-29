"""문서 맥락 분석기 (v2, PoC) — 무앵커 주제 발견 + 탈선(off-topic) 분리.

기존 DB·ingest 와 완전 분리된 독립 유틸리티. ingest 에서 import 하지 않는다.
설계 메모: memory/doc-context-analyzer-design.md
"""
from geryon.analyze.core import analyze, Passage, Cluster, AnalyzeResult

__all__ = ["analyze", "Passage", "Cluster", "AnalyzeResult"]
