"""pytest 공통 설정.

단위 테스트는 소규모 합성 데이터라 임베딩 cosine score가 실데이터와 다르다.
운영 기본값(distance 0.12)을 그대로 쓰면 벡터 히트가 전부 게이트에 걸리므로,
테스트 환경에서는 게이트를 느슨하게(0.9) 둔다. 실데이터/운영에는 영향 없음.
config import 이전에 env를 설정해야 하므로 conftest 최상단에서 처리한다.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("GERYON_RELEVANCE_DISTANCE", "0.9")
# 단위 테스트는 rerank 모델(1.1GB)을 받지 않도록 기본 OFF → 하이브리드 경로 검증.
# rerank 경로는 test_rerank.py에서 가짜 reranker로 단독 검증.
os.environ.setdefault("GERYON_RERANK", "0")
# 벡터 후보 합류도 테스트에선 OFF(임베더 로드·지연 방지). 효과는 별도 측정 스크립트로.
os.environ.setdefault("GERYON_RERANK_VEC_POOL", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
