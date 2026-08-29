"""Cross-encoder rerank.

키워드(unicode61) 후보를 cross-encoder가 (query, 제목) 쌍으로 재정렬한다.
런타임은 fastembed(onnxruntime + Rust tokenizers, torch 불필요) — 임베더와 동일 스택.
PoC 실측: jina-reranker-v2-base-multilingual로 74.2%/565ms(중앙), 벡터 brute-force보다
빠르고 정확. 모델 로드 실패(미설치/오프라인 미수신) 시 None을 돌려 호출측이 하이브리드로
graceful fallback 하게 한다.

int8 동적 양자화는 기본 ON(`GERYON_RERANK_QUANTIZE`): ~28% 빠르고 모델 4배↓이나 정확도 -6.5%p.
fp32 복귀는 `GERYON_RERANK_QUANTIZE=0`. sympy 미설치 등 양자화 실패 시 fp32 graceful fallback.
"""
from __future__ import annotations

import glob
import logging
from pathlib import Path

from geryon.config import RERANK_MODEL, RERANK_THREADS, RERANK_QUANTIZE, RERANK_CACHE_DIR

logger = logging.getLogger(__name__)

# 모듈 레벨 캐시 — 프로세스당 1회 로드(임베더와 동일 패턴). 로드 실패는 False로 표시.
_RERANKER_CACHE: dict[str, object] = {}


def _encoder(model_name: str):
    """fastembed TextCrossEncoder 인스턴스(스레드·캐시 디렉토리 적용)."""
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    kwargs: dict = {"cache_dir": RERANK_CACHE_DIR}
    if RERANK_THREADS > 0:
        kwargs["threads"] = RERANK_THREADS
    return TextCrossEncoder(model_name=model_name, **kwargs)


def _quantized_encoder():
    """fp32 ONNX를 int8로 동적 양자화해 등록·로드(opt-in). 실패 시 예외 → fp32 폴백."""
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    from fastembed.common.model_description import ModelSource
    from onnxruntime.quantization import quantize_dynamic, QuantType

    int8_name = f"{RERANK_MODEL}-int8"
    # 이미 등록돼 있으면 재사용
    registered = {
        (getattr(m, "model", None) or (m.get("model") if isinstance(m, dict) else None))
        for m in TextCrossEncoder._list_supported_models()
    }
    if int8_name not in registered:
        # 1) fp32 모델 다운로드 보장(캐시에 onnx/model.onnx 확보)
        _encoder(RERANK_MODEL)
        # 2) 캐시에서 fp32 onnx 찾기
        pattern = str(Path(RERANK_CACHE_DIR) / "**" / "onnx" / "model.onnx")
        matches = [p for p in glob.glob(pattern, recursive=True)
                   if RERANK_MODEL.split("/")[-1] in p]
        if not matches:
            raise FileNotFoundError(f"fp32 onnx 미발견: {pattern}")
        src = Path(matches[0])
        dst = src.with_name("model_int8.onnx")
        # 3) 1회 양자화(이미 있으면 스킵)
        if not dst.exists():
            logger.info("int8 양자화 시작: %s", src)
            quantize_dynamic(str(src), str(dst), weight_type=QuantType.QInt8)
            logger.info("int8 양자화 완료: %s", dst)
        # 4) 커스텀 모델 등록
        TextCrossEncoder.add_custom_model(
            model=int8_name,
            sources=ModelSource(hf=RERANK_MODEL),
            model_file="onnx/model_int8.onnx",
            description="int8 dynamic quantized (opt-in)",
            license="mit",   # bge-reranker-base 원본 라이선스(MIT) 상속 — 상업 이용 가능
            size_in_gb=0.28,
        )
    return _encoder(int8_name)


def get_reranker(model_name: str = RERANK_MODEL):
    """fastembed TextCrossEncoder 싱글턴. 사용 불가하면 None(→ 하이브리드 폴백)."""
    key = f"{model_name}{'#int8' if RERANK_QUANTIZE else ''}"
    if key in _RERANKER_CACHE:
        return _RERANKER_CACHE[key] or None
    try:
        if RERANK_QUANTIZE:
            try:
                encoder = _quantized_encoder()
                logger.info("reranker 로드(int8): %s", model_name)
            except Exception as e:  # noqa: BLE001 — 양자화 실패 시 fp32로
                logger.warning("int8 양자화 실패, fp32 폴백: %s", e)
                encoder = _encoder(model_name)
        else:
            encoder = _encoder(model_name)
            logger.info("reranker 로드: %s (threads=%s)", model_name, RERANK_THREADS or "auto")
        _RERANKER_CACHE[key] = encoder
        return encoder
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 폴백이 안전
        _RERANKER_CACHE[key] = False
        logger.warning("reranker 로드 실패(하이브리드 폴백): %s", e)
        return None


def rerank_scores(query: str, documents: list[str], model_name: str = RERANK_MODEL) -> list[float] | None:
    """(query, documents[i]) 적합도 점수 리스트. 큰 값일수록 관련. 불가 시 None."""
    if not documents:
        return []
    encoder = get_reranker(model_name)
    if encoder is None:
        return None
    try:
        return [float(s) for s in encoder.rerank(query, documents)]
    except Exception as e:  # noqa: BLE001
        logger.warning("rerank 추론 실패(하이브리드 폴백): %s", e)
        return None
