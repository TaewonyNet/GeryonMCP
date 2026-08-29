"""문서 맥락 분석기 PoC 시연 — `python -m geryon.analyze._demo`.

신제품 출시 관련 회의/인터뷰 3건. 진짜 주제(일정·가격·물류)는 여러 문서에 반복,
탈선(날씨·점심·주차 등)은 한 번씩만 등장 → outlier 로 분리되는지 확인.
"""
from geryon.analyze.core import analyze, OFF_TOPIC

DOCS: list[tuple[str, str]] = [
    ("interview_A", "\n".join([
        "신제품 출시는 3분기 말로 잡는 게 현실적입니다.",
        "QA 일정이 빠듯해서 출시일을 한 주 미루는 안도 검토했습니다.",
        "가격은 경쟁사 대비 10% 낮게 책정하자는 의견이 많았어요.",
        "참, 어제 비가 너무 많이 와서 출근이 힘들었네요.",
        "물류 창고 계약이 다음 달에 끝나서 재계약을 서둘러야 합니다.",
    ])),
    ("meeting_B", "\n".join([
        "출시 일정은 3분기 안에 마무리하는 걸 목표로 합니다.",
        "가격 정책은 초기 할인으로 시장 점유율을 먼저 확보하는 방향입니다.",
        "점심으로 뭐 먹을지 다들 정했어요?",
        "물류는 외주 업체를 한 곳 더 늘려 배송 지연을 막기로 했습니다.",
        "QA 인력이 부족해 출시 직전 테스트 기간이 짧아질까 걱정입니다.",
    ])),
    ("interview_C", "\n".join([
        "가격을 낮추면 마진이 줄어드니 물량으로 보전해야 합니다.",
        "출시 시점은 경쟁사 신제품 발표 직후가 좋다고 봅니다.",
        "주차장 공사가 언제 끝나는지 아시는 분 있나요?",
        "배송과 물류 안정성이 초기 평판을 좌우할 겁니다.",
    ])),
]


def main() -> None:
    res = analyze(DOCS, k=3, alpha=1.0, min_size=2, min_doc_support=1)

    topics = [c for c in res.clusters if not c.off_topic]
    off = [c for c in res.clusters if c.off_topic]
    print(f"\n엣지임계(μ+ασ)={res.threshold:.4f}   조각 {len(res.passages)}개   "
          f"본주제 {len(topics)}개   탈선군집 {len(off)}개\n")

    for c in topics:
        print(f"── 본주제 #{c.cluster_id}  (조각 {c.size}, 문서지지 {c.doc_support}, "
              f"주제밀착 {c.subject_affinity:.3f})  대표어: {', '.join(c.keywords)}")
        for i in c.passage_idx:
            print(f"     [{res.passages[i].doc_id}] {res.passages[i].text}")
        print()

    for c in off:
        print(f"── ✂ 탈선 #{c.cluster_id}  (조각 {c.size}, 문서지지 {c.doc_support}, "
              f"주제밀착 {c.subject_affinity:.3f} ↓)  대표어: {', '.join(c.keywords)}")
        for i in c.passage_idx:
            print(f"     [{res.passages[i].doc_id}] {res.passages[i].text}")


if __name__ == "__main__":
    main()
