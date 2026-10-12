# 실제 데이터에 영향을 주는 상수 — 위치와 수정 방법

검색 결과·분류·랭킹을 바꾸는 상수들이 코드 곳곳에 흩어져 있다. 이 문서는 **어디 있고, 어떻게
고치는지**를 모은다. 상수 자체를 설명하는 문서가 아니라 **지도**다 — 각 항목의 "왜 이 값인가"는
해당 코드의 주석에 있다(측정 근거가 있으면 거기 적혀 있고, 없으면 이 문서가 "미검증"이라 표시한다).

## 고치기 전에 — CLAUDE.md §3

> 설정값·판정 기준은 측정 또는 코퍼스 유도 정답셋에서 나와야 한다.

아래 표에서 "env" 열이 있는 항목은 `.env` 한 줄로 끄고 켜 보면 된다. **"env 없음, 소스 수정"** 인
항목은 값을 감으로 바꾸지 말고, 바꾼 뒤 반드시 아래로 측정할 것:

| 바꾼 게 | 확인 도구 |
|---|---|
| 분류·필터 결과 | `scripts/toolkit.py bench --sweep KEY=v1,v2` (골든셋 hit-rate 비교) |
| 그 차이가 우연인지 | `scripts/ab_significance.py` (McNemar) |
| 랭킹 가중치 | 같은 질의셋으로 `geryon search` 전/후 비교, 가능하면 룰기반·LLM 골든셋 둘 다(교차검증) |
| 임계값(예: 유사도 cutoff) | `scripts/calibrate_conflict.py` 같은 패턴 — 사람 라벨 없이 정답셋에서 역산 |

절차 전체는 `docs/BENCHMARK_METHODOLOGY.md`, 도구 목록은 `docs/SETUP_TOOLING.md`.

---

## 1. 이미 `.env` 로 켜고 끌 수 있는 것

전체 목록은 `.env.sample`. 이 문서에서 다루는 "데이터 결과를 바꾸는" 것만 다시 짚으면:

| 변수 | 기본값 | 영향 |
|---|---|---|
| `GERYON_STATIC_ALPHA` | 0.1 | static_score 가 최종 순위에 반영되는 세기 |
| `GERYON_RELEVANCE_DISTANCE` | 0.12 | 벡터 전용 히트를 걸러내는 cosine distance 컷오프 |
| `GERYON_RERANK` / `_POOL` / `_VEC_POOL` / `_PASSAGE` | — | 재정렬 on/off·후보 수·벡터 합류·본문중심 결합 |
| `GERYON_DICT_AUTO` | 0 | 자동 동의어 사전 적용 여부 |
| `GERYON_DOC_TYPE_RULES` | (없음) | 조직별 `doc_type` 규칙 **추가**(아래 §2 참고 — 교체·비활성은 안 됨) |

## 2. `doc_type` 분류 규칙 — `src/geryon/index/classify.py`

| 상수 | 내용 | 수정 방법 |
|---|---|---|
| `DOC_TYPE_RULES`(12줄) | meeting·retro·data_model·spec·design·issue·analysis·ops·planning·notice·glossary·guide — 범용 키워드만 | **소스 직접 수정**(classify.py:13). `GERYON_DOC_TYPE_RULES` 로는 **추가**만 되고(내장보다 먼저 매칭), 내장 규칙을 끄거나 순서를 바꿀 수는 없다. 조직 고유 유형은 반드시 env 로 — 범용 규칙에 조직 용어를 넣지 말 것([[oss-flexibility-over-our-corpus]]) |
| `_COMMIT_TYPE_RE` | Conventional Commits 접두사(`feat`·`fix`·`docs`·… 11종) | 소스 직접 수정(classify.py:71). 공개 표준이라 env 확장은 안 두었다 — 필요하면 정규식에 직접 추가 |

## 3. 한국어·메타 정규화 — `src/geryon/index/korean.py`, `meta_norm.py`

검색 토큰화·표시값을 바꾸는 상수. **env 없음, 전부 소스 수정.**

| 파일:줄 | 상수 | 내용 |
|---|---|---|
| `korean.py:12` | `_JOSA` | 조사 접미사 ~38개(은/는/이/가/을/를/…) — 어느 걸 떼어내고 검색하는지 |
| `korean.py:25` | 길이 가드 | 조사를 떼고도 `len(word) > len(조사)+1` 이어야 함(과도한 절단 방지) |
| `meta_norm.py:13` | `_AUTHOR_STATUS` | "(Deactivated)" 류 접미사로 인식할 단어(퇴사·탈퇴·unlicensed…) |
| `meta_norm.py:17` | `_ANON` | 식별 불가 작성자의 표준값 `"(익명)"` |
| `meta_norm.py:44` | `_LEAD_DECOR` | 제목/태그 선행 장식 문자(●·•‣▪…) 제거 대상 |

## 4. 랭킹 가중치 — ⚠️ 알려진 중복·불일치 2건

### static_score 공식 — `src/geryon/pipeline/quality_signals.py`

```
static_score = recency × 0.3 + richness × 0.3 + backlink_centrality × 0.4   (quality_signals.py:181)
richness     = length_score × 0.7 + heading_score × 0.3                     (:148)
```
세 가중치(0.3/0.3/0.4)·richness 하위 가중치(0.7/0.3)·포화점(`_RICHNESS_MAX_CHARS=50_000`,
`_RICHNESS_MAX_HEADINGS=50`, :113-114) **전부 env 없음, 소스 수정.** 이 값 자체를 바꾸는 건 아니고
반영 세기만 조절하는 `GERYON_STATIC_ALPHA` 는 있다(§1) — 공식 자체를 바꾸려면 소스를 고치고
`--recalc-static`(`backfill_dates.py` 참고)으로 재계산해야 기존 DB 에도 반영된다.

### ⚠️ recency 반감기가 두 군데 다른 값으로 중복돼 있다
- `config.py:116` `RECENCY_HALF_LIFE_DAYS = 30.0` — **쿼리 시점** 신선도 부스트(`keyword.py` 가 씀). env 로 노출돼 있지 않다(이름은 `GERYON_*` 패턴인데 실제 `os.getenv` 호출이 없음 — 다른 설정들과 다르게 예외).
- `quality_signals.py:131` `half_life = 180.0` — **색인 시점** static_score 의 recency 성분. 완전히 독립된 하드코딩 값.

**둘 다 "recency 반감기"라는 같은 개념인데 30일/180일로 6배 차이난다.** 의도한 차이(쿼리 시점 vs
사전계산)인지 드리프트인지 이 문서 작성 시점에는 미확인 — recency 를 튜닝하려는 사람은 **반드시
둘 다** 확인할 것. 하나만 바꾸면 절반만 바뀐다.

### ⚠️ BM25 title/body 가중치가 두 파일에 따로 박혀 있다
- `keyword.py:193` `TITLE_WEIGHT = 5.0` (이름 있는 상수, `bm25(documents_fts_norm, 0, {TITLE_WEIGHT}, 1.0)` 로 사용)
- `advanced.py:91` 같은 값이 **리터럴 `5.0, 1.0`** 으로 중복 — 상수 공유 안 함

하나만 고치면 `search` 와 `advanced_search` 의 제목 가중치가 갈라진다. 두 파일 다 고칠 것.

### 하이브리드 RRF — `src/geryon/search/hybrid.py`
| 줄 | 상수 | 내용 |
|---|---|---|
| 21 | `CANDIDATE_POOL = 200` | 키워드·벡터 후보 풀의 하한(`(k+offset)×2` 와 `×4` 의 바닥값) |
| 149 | `constant_k = 60` | RRF 공식 `1/(k+rank)` 의 평활 상수(관례값) |
| 192 | `max_rrf_possible = 2.0/61.0` | **`constant_k=60` 에 종속된 값** — 저걸 바꾸면 이것도 같이 바꿔야 한다(안 그러면 정규화가 깨짐) |
| 389, 414 | get_related 가중치 0.9(형제)·0.7(부모)·0.5(링크 1-hop) | 연관문서 랭킹 |

`RERANK_POOL`(env, §1)과 달리 이 4개는 env 없음, 소스 수정. `RERANK_POOL` 은 federation 분배
시 `// n_dbs` 로 나뉘는데(hybrid.py:272) 그 나누는 로직 자체는 env 가 아니다.

## 5. 골든셋 보정이 안 된 것 — `scripts/term_bootstrap.py`, `dict_bootstrap.py`

CLAUDE.md §6 TODO 에 이미 적힌 대로, `term_bootstrap.py` 규칙 12개 중 **보정된 건 2개뿐**
(`_CONFLICT_JACCARD_TH=0.10` 는 DDL 유도 정답셋으로 보정, `_CONFLICT_CONTAINMENT_TH=0.7` 은
보정했더니 **무효 파라미터**로 드러남). 나머지 10개 — 스톱워드(`_STOP_KO`), SQL 예약어, 길이
경계(제목 40자/5단어, 정의 12자, 본문 스캔 상한 4000자), 채널 신뢰도 서열(`_PRIORITY`) — 는
전부 감으로 넣은 값이다. `dict_bootstrap.py` 의 약어·동의어 추출 정규식·스톱워드(`_STOP_KO`,
`term_bootstrap.py` 것과 바이트 단위로 동일한데 별도 사본으로 유지됨)도 마찬가지로 미검증.

바꾸기 전에 `scripts/calibrate_conflict.py` 가 `_CONFLICT_*` 를 보정한 방식(DDL 구조에서 정답셋
유도)을 참고할 것 — 나머지 규칙도 같은 패턴(사람 라벨 없이 코퍼스에서 역산)으로 보정 가능하다.

## 6. 기타 (참고, 영향은 작음)

- `keyword.py`: LIKE 폴백 기본점수 `0.5`(:173), "제목이 질의를 전부 포함" 부스트 `×1.5`(:289), 토큰 길이 하한 `len(s)>=2`(FTS 대상 판정) — 전부 소스 수정.
- `src/geryon/analyze/core.py`(선택 의존성, 회의록/스펙 정제): `k=3`·`alpha=1.0`·`min_size=2` 등 클러스터링 파라미터, `trim=0.2`(중심점 계산 시 이상치 제거 비율) — 함수 인자 기본값, env/CLI 연결 없음.
- `src/geryon/analyze/llm_extract.py`: 문서를 회의록/스펙으로 1차 분류하는 정규식(타임스탬프→meeting, `CREATE TABLE`/코드펜스/긴 표→spec) — 소스 수정.

---

## 요약 — "이것부터 보라"

1. **분류를 바꾸고 싶다** → `GERYON_DOC_TYPE_RULES` 로 추가가 먼저(§1·§2). 범용 유형 자체를
   바꿔야 한다면 `classify.py`.
2. **랭킹이 이상하다** → recency 반감기 두 곳(§4), BM25 가중치 두 곳(§4)을 **먼저 일치시켰는지**
   확인. 드리프트가 원인인 경우가 흔하다.
3. **용어 사전·분류 규칙이 안 맞다** → `term_bootstrap.py`/`dict_bootstrap.py` 는 대부분
   미검증(§5) — 바꾸기 전에 `calibrate_conflict.py` 패턴으로 먼저 보정.
