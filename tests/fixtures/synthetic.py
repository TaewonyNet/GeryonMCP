"""임의 테스트 문서 생성기.

실제 개인정보·회사정보 없이 구조적으로 동일한 문서를 생성한다.
  make_meeting_transcript() — 타임스탬프 기반 한국어 회의 전사체
  make_spec_document()      — SQL DDL + Java 메서드 포함 기술 스팩
"""
from __future__ import annotations

import random
from typing import NamedTuple


# ── 임의 이름 풀 (실존 인물 아님) ─────────────────────────────
_FIRST  = ["민준", "서연", "지호", "수아", "예준", "하윤", "도윤", "채원"]
_LAST   = ["김", "이", "박", "최", "정", "강", "조", "윤"]
_TEAM   = "알파팀"
_COMPANY = "테크노바"   # 가상 회사명


def _name(seed: int) -> str:
    r = random.Random(seed)
    return r.choice(_LAST) + r.choice(_FIRST)


class Speaker(NamedTuple):
    name:  str
    role:  str


def _speakers(n: int = 4, seed: int = 0) -> list[Speaker]:
    roles = ["팀장", "개발자", "개발자", "기획자", "디자이너"]
    r     = random.Random(seed)
    return [Speaker(_name(seed + i), roles[i % len(roles)]) for i in range(n)]


# ── 회의 전사체 ────────────────────────────────────────────────
_TOPICS = [
    ("DB 마이그레이션",
     [
         "{s0}: 포스트그레 적재 시간이 너무 오래 걸려요. 새 데이터 넣는 데 70분씩 걸립니다.",
         "{s1}: ClickHouse 검토해봤어요?",
         "{s0}: 테스트해봐야 할 것 같아요. 결과 공유할게요.",
         "{s2}: 그럼 이번 주 안에 테스트 완료해줘요.",
     ]),
    ("API 엔드포인트 정리",
     [
         "{s2}: 미사용 API가 절반 넘어요. 응답 속도 개선 가능합니다.",
         "{s0}: 목록 정리해서 공유해줘요.",
         "{s3}: 제가 오픈서치 로그 분석 자동화할게요.",
         "{s2}: 이번 주 중으로 목록 완성하겠습니다.",
     ]),
    ("할인 계산 로직 수정",
     [
         "{s1}: 손실 표시 오류 나오고 있어요. 할인 코드 적용 순서 문제인 것 같아요.",
         "{s0}: 계산식 모듈도 같이 봐야 해요.",
         "{s1}: 수정하고 바로 반영 요청할게요.",
         "{s2}: 완료 후 스테이지 반영 요청하세요.",
     ]),
]

_PERIPHERAL = [
    [
        "{s3}: 오늘 점심 뭐 먹을지 정했어요?",
        "{s0}: 일단 회의 끝내고 얘기해요.",
    ],
    [
        "{s2}: 이번 달 회식 예산이 얼마예요?",
        "{s0}: 총무한테 확인하고 다시 알려줄게요.",
        "{s2}: 알겠습니다. 채팅방에 올릴게요.",
    ],
]

_PROCEDURAL = [
    ["{s0}: 화면 공유 잘 보이시나요?", "{s3}: 네, 잘 보입니다."],
    ["{s0}: 마이크 잡음 있어요. 음소거 해주세요."],
]


def make_meeting_transcript(seed: int = 42) -> str:
    """타임스탬프·발화자 라벨 포함 한국어 회의 전사체."""
    spk   = _speakers(4, seed)
    names = {f"s{i}": spk[i].name for i in range(len(spk))}
    lines: list[str] = []
    ts    = 0  # seconds

    def add_block(utterances: list[str]) -> None:
        nonlocal ts
        h, m, s = ts // 3600, (ts % 3600) // 60, ts % 60
        lines.append(f"{h:02d}:{m:02d}:{s:02d}")
        for u in utterances:
            lines.append(u.format(**names))
        lines.append("")
        ts += random.Random(seed + ts).randint(60, 180)

    # 절차적 시작
    add_block(_PROCEDURAL[0])

    # 안건별 논의
    for topic_name, topic_lines in _TOPICS:
        lines.append(f"\n# 안건: {topic_name}\n")
        add_block(topic_lines)

    # 잡담 (peripheral)
    add_block(_PERIPHERAL[0])
    add_block(_TOPICS[1][1])   # 안건 재개

    # 잡담2
    add_block(_PERIPHERAL[1])

    # 마무리
    add_block([
        "{s0}: 오늘 정리하면 — DB 마이그레이션 테스트, API 정리, 할인 로직 수정 이번 주 내 완료해주세요.",
        "{s1}: 네.",
        "{s2}: 알겠습니다.",
        "{s3}: 네, 수고하셨습니다.",
    ])

    return "\n".join(lines)


# ── 기술 스팩 문서 ─────────────────────────────────────────────
def make_spec_document(seed: int = 0) -> str:
    """SQL DDL + Java 메서드 포함 기술 스팩.

    명시적 섹션 헤더(## 형식)를 사용하여 LLM이 db_schema / api / logic 섹션을
    명확히 구분할 수 있도록 구조화. 실제 개인정보·회사명 없음.
    """
    r          = random.Random(seed)
    tbl_prefix = r.choice(["order", "payment", "discount", "booking"])
    hash_val   = f"_{r.randint(1000, 9999)}"

    return f"""## 개요 (Overview)

목적: {tbl_prefix} 후처리 관련 데이터 스팩 정의

단계별 계획:
- Phase 1: 항목별 최대 처리율 조정, 가격 구간별 처리율 조정
- Phase 2: 등급 클래스 차별화, 예약 클래스 차별화
- Phase n: 전략 고도화, 외부 시스템 통합

## DB 스키마 (db_schema)

### {tbl_prefix}_history — 히스토리 테이블

```sql
CREATE TABLE "{tbl_prefix}_history" (
  "schema_name" varchar(50) NOT NULL COMMENT '스키마 이름',
  "table_name"  varchar(50) NOT NULL COMMENT '테이블 이름',
  "inserted_at" datetime    NOT NULL COMMENT '입력 완료 시점',
  PRIMARY KEY ("schema_name", "inserted_at", "table_name"),
  KEY "idx_schema_inserted" ("schema_name", "inserted_at" DESC)
);
```

최신 이력 조회 쿼리:

```sql
SELECT schema_name, table_name, inserted_at
FROM {tbl_prefix}_history
WHERE schema_name = '{tbl_prefix}_post_process'
ORDER BY inserted_at DESC
LIMIT 1;
```

### {tbl_prefix}_post_process{hash_val} — 후처리 마트

```sql
CREATE TABLE "{tbl_prefix}_post_process{hash_val}" (
  "item_code"  varchar(10) NOT NULL COMMENT '항목 코드',
  "price_ceil" bigint      NOT NULL DEFAULT 0 COMMENT '가격 상한선',
  "extra_rate" float       NOT NULL COMMENT '추가 이익률',
  "max_rate"   float       NOT NULL COMMENT '최대 처리율'
);
```

조회 예시:

```sql
SELECT item_code, price_ceil, extra_rate, max_rate
FROM {tbl_prefix}_post_process{hash_val}
WHERE item_code = ? AND price_ceil >= ?
LIMIT 1;
```

## API / 클래스 명세 (api)

### PostProcessParameters 클래스

가격 상한선 및 항목 코드로 필터링 후 결과 한 건 전달.

```java
public class PostProcessParameters {{
    private double extraProfitRate;  // 추가 이익률 (extra_rate)
    private double maxProcessRate;   // 최대 처리율 (max_rate)
}}
```

### calculatePostProcessAmount 메서드

```java
public static ProcessResult calculatePostProcessAmount(
        String itemCode,
        double basePrice,
        double tax,
        PostProcessParameters params
) {{
    // 가격 상한선 이하 항목에 처리율 적용 후 결과 반환
}}
```

## 비즈니스 로직 (logic)

처리 흐름:
1. {tbl_prefix}_history 에서 최신 후처리 로직 버전 확인
2. {tbl_prefix}_post_process{{hash}} 에서 item_code + price_ceil 필터링
3. calculatePostProcessAmount() 호출하여 최종 금액 산출

## 로드맵 (roadmap)

- 2026년 추가 아이템: 외부 시스템 통합
- 기존 로드맵 연계 필요 (일정 미정)
"""
