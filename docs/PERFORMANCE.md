# 성능 · 최적화 가이드

검색 속도와 품질은 **trade-off**다. 항상 **골든으로 전후 측정**하고 바꾼다(무작정 키우지 않는다).

## 1. 검색 속도 노브 (환경변수)

> **설정을 손으로 고르기 전에** `python scripts/autotune.py analyze` 를 먼저 돌린다 —
> 하드웨어·코퍼스 규모에서 권장 설정을 자동 산출한다. 도구 전체는 `docs/SETUP_TOOLING.md`.

아래는 **35,768문서 / 143,800벡터 / CPU 20코어 / 62GB** 환경에서
`scripts/profile_resources.py` 로 잰 값이다(질의 8개, 워밍업 제외 p50, 설정마다 독립 프로세스).

| 설정 | 피크 RSS | p50 | 비고 |
|---|---:|---:|---|
| 기본 (rerank on, int8, pool 60) | 3,095MB | ~900ms | 기준선 |
| **`RERANK_POOL=20`** | 3,095MB | **~350–410ms** | **속도 2.5배 + 골든 정확도도 상승** — 아래 참고 |
| `RERANK_PASSAGE=0` | 3,094MB | ~420ms | 2배 빠르나 본문중심 recall 크게 하락(골든 58%→33%) |
| `RERANK_QUANTIZE=0` (fp32) | 2,719MB | ~1,650ms | int8 대비 **느리고 메모리도 더 씀**. int8 유지 권장 |
| `RERANK=0` (rerank 끔) | **909MB** | ~3,200–4,900ms | 메모리 1/3, 대신 지연 3~5배. 측정 편차 큼 |
| `RERANK_VEC_POOL=10/20` | — | +3배 이상 | 이 코퍼스에선 정확도 이득 없음(골든 동일/하락) |

**스레드 확장성** (`GERYON_RERANK_THREADS`)

| 1 | 2 | 4 | **8** | 12 | 16 | 20 |
|---|---|---|---|---|---|---|
| 3,416ms | 1,826ms | 1,093ms | **882ms** | 889ms | 954ms | 1,659ms |

**8스레드가 최적**이고 그 이상은 이득이 없거나 해롭다(20스레드에서 급락 — 코어 경합).
`0`(자동)으로 두지 말고 `min(8, cpu_count)` 로 명시 지정할 것.

> rerank 비용은 대략 **후보 수 × (passage면 2배)** 에 비례한다. 느리면 `RERANK_POOL` 부터 줄인다.

### 권장 조합

**기본 권장** — 속도·정확도 모두 우세(실측)
```
GERYON_RERANK=1
GERYON_RERANK_QUANTIZE=1
GERYON_RERANK_POOL=20
GERYON_RERANK_THREADS=8      # min(8, cpu_count)
```
→ 실측 **~350–410ms** · 피크 RSS 3.1GB → **RAM 4.5GB 이상 권장**

**저메모리** (RAM 4.5GB 미만)
```
GERYON_RERANK=0
GERYON_RERANK_THREADS=4
```
→ 피크 RSS 909MB(**RAM 1.4GB**면 동작) · 대신 지연 ~3.2초

### static_score 랭킹 반영 (`GERYON_STATIC_ALPHA`, 기본 0.1)

`quality_signals.py` 가 문서마다 사전계산하는 품질점수:
`static_score = recency×0.3 + richness(길이·heading)×0.3 + backlink_centrality×0.4`

최종 점수는 `base × (1 + STATIC_ALPHA × static_score)` 로 반영된다.

> **알려진 결함이었던 부분**: rerank 경로(`_rerank_search`)에 이 반영이 **누락돼 있었다**.
> rerank 는 기본 ON 이므로, 계산·저장된 static_score 가 평상시 랭킹에 **전혀 쓰이지 않았다**.
> 골든 300건에서 alpha 를 0→5(50배)로 바꿔도 결과가 완전히 동일(McNemar 불일치 쌍 0)한 것으로
> 확인해 수정했다. 함께 로짓→[0,1] 정규화를 정렬 **전**으로 옮겼다(로짓은 음수가 가능해
> 곱셈 부스트 시 부호가 뒤집혀 순위가 깨진다).

측정(골든 300건, 이 저장소 코퍼스):

| alpha | 0 | 0.1(기본) | 0.5 | 1 | 3 |
|---|---|---|---|---|---|
| hit | 168 | 169 | **170** | 168 | 166 |

`alpha 0 vs 0.5` McNemar **p=0.625 (불일치 1:3) — 유의하지 않음.**
겉보기 최적값(0.5)도 우연과 구분되지 않아 **기본값 0.1 을 유지**한다.
다만 3 이상으로 키우면 나빠지는 경향은 보이므로 무작정 올리지 말 것.

### ⚠ 옛 문서와 달라진 점 (재측정으로 정정)

기존 문서는 29,000문서 기준이었고 아래 항목이 이번 실측과 어긋나 정정한다.

- **`int8` 정확도 −6.5%p** → 골든셋(60케이스)에서 fp32와 **hit-rate 완전 동일**(35/60 = 35/60).
  게다가 fp32가 더 느리고 메모리도 더 썼다. int8을 끌 이유가 확인되지 않았다.
- **`RERANK=0` 이 정확도 −13pp** → 이번 골든셋에서는 오히려 **rerank off 가 더 정확**했고
  (58% → 81%), McNemar 검정에서도 유의(p=0.0043)했다. 다만 **지연은 3~5배 늘어난다.**
  이 상충은 미해결 상태이므로 `docs/BENCHMARK_METHODOLOGY.md` 의 판정 절차를 참고해
  각자 코퍼스에서 재확인할 것.
- **`RERANK_POOL` 은 낮추면 recall 하락** → 이번엔 pool=20 이 속도·정확도 **양쪽 모두** 우세했다.

수치가 코퍼스마다 뒤집힐 수 있다는 게 요점이다. 문서 값을 그대로 믿지 말고
`scripts/toolkit.py bench` 로 **자기 데이터에서** 재현할 것.

## 2. Federation 성능 (소스별 DB) [25_SILVER_FEDERATION]

여러 DB 를 묶어 검색(federation)하면 **후보가 DB 수만큼 늘어** rerank 입력이 폭증한다:
```
각 DB pool=60, N개 DB → 후보 60N → best-passage rerank 입력 120N (느림)
```

### pool 자동 분배 (구현됨)
각 DB 에서 `RERANK_POOL / N` 만 가져와 **후보 총량을 단일 DB 수준으로 유지**한다:
```
per_db_pool = max(RERANK_POOL // N, k + offset)
N=1(단일) → RERANK_POOL 그대로 / N=3 → 20씩 → 후보 60 (단일 수준)
```

### 실측 (3-DB federation: confluence 28,963 + 코드 3,527 + jira 146)
| | 케이스당 속도 | 품질(conf/bit/jira) |
|---|---|---|
| 분배 전(각 60, 후보 180) | ~4.5s | 77 / 83 / 100 |
| **분배 후(각 20, 후보 60)** | **~2.5s** | **77 / 83 / 100** |

→ **속도 ~2배, 품질 손실 0**. rerank 가 절대점수라 후보를 줄여도 상위 정답은 유지된다.

### trade-off
- 소스가 많아 `RERANK_POOL // N` 이 너무 작아지면(예: N=10) 경계 케이스 recall 이 떨어질 수 있다.
- 최소 `k+offset` 은 보장하지만, **소스 수가 많으면 `RERANK_POOL` 을 키워** per-DB pool 을 확보하고 골든으로 확인한다.

## 2.5 검색 속도의 큰 비용 = best-passage (실측)

"느리다"의 범인을 실측으로 분해한 결과 — **사전·federation 은 거의 무관, best-passage 가 절반**:

| 구성 | 속도(케이스당) | 메모 |
|---|---|---|
| 단일 DB | 1656ms | — |
| **federation 3DB** | **1424ms** | pool 분배로 단일보다 오히려 빠름 |
| 사전 OFF / ON(31) | 1510 / 1506ms | **사전 영향 ≈ 0** (질의 확장은 가벼움) |
| **best-passage ON**(기본) | ~1500ms | 제목+본문구절 2N rerank |
| **best-passage OFF** | **735ms** | rerank 입력 N → **2배 빠름** |

- **federation·사전은 속도를 늘리지 않는다.** federation 은 pool 분배 덕에 단일보다 빠르고, 사전은 검색 전 질의 확장이라 무시할 수준.
- **best-passage(`RERANK_PASSAGE=1`)가 속도의 절반**이다. 제목만으론 "핵심이 본문에만 있는 문서"를 놓쳐(본문중심 recall 33%) 제목+본문구절을 둘 다 rerank(입력 2N)하기 때문. 본문중심 recall 33→100% 의 대가다.
- 속도가 급하면 `RERANK_PASSAGE=0`(735ms, 2배)로 — 단 "제목과 본문이 동떨어진 문서" 검색이 약해진다.

## 2.6 vs Confluence 공식 검색 (siteSearch / text~) — 실측

약 **29,000 문서** 규모의 Confluence 코퍼스(본문 색인) + **31개 골든 질의**(자연어·구어·동의어 포함)로 동일 질의를 세 엔진에 던져 비교했다. 정확도는 `top_k` 안에 정답 문서가 있으면 hit, 속도는 질의당 지연(로컬 vs 클라우드 API 왕복).

| 엔진 | 정확도(hit@top_k) | 지연(중앙값) | 성격 |
|---|---|---|---|
| **GeryonMCP**(로컬) | **87%** (27/31) | ~1.7s · passage OFF 시 **632ms** | 오프라인·무료·CPU |
| Confluence **siteSearch** | 80% (25/31) | ~382ms | 클라우드(검색창과 동일 랭킹) |
| Confluence `text ~` | 9% (3/31) | ~346ms | 클라우드(단순 full-text) |

- **공정 기준은 siteSearch다.** `text ~`(단순 전문검색)는 9%로 약하지만, 검색창이 실제로 쓰는 siteSearch는 80%로 강하다 — 비교는 반드시 siteSearch로 한다.
- **GeryonMCP 우위는 +7pp(87 vs 80)로 modest**하지만, hit한 정답을 **rank 1로 올리는 top-1 정밀도**가 높고(siteSearch는 2~6위 분산), siteSearch가 놓친 동의어·구어 질의를 잡는다. 게다가 **오프라인·무료**다. 반대로 siteSearch가 이긴 케이스도 있다(정확 제목·완전일치 질의).
- **속도**: 현재 ~1.7s는 best-passage가 지배. `RERANK_PASSAGE=0`이면 **정확도 87% 그대로 632ms**로 siteSearch(382ms)에 근접한다(2.5절과 동일 결론, 이 코퍼스에서 재확인).
- ⚠️ **주의(측정 게이트)**: 이 골든은 **제목으로 찾히는 질의** 위주라 passage rerank의 정확도 기여가 0으로 나왔다. → **본문중심 질의셋으로 재검증 완료**(아래).

#### 본문중심 재검증 — passage ON이 필수 (기본값 ON 유지)
`golden_bootstrap`(제목에 없는 본문 희소어로 질의 역생성) 50건으로 passage ON/OFF 를 재측정:

| 구성 | 정확도 | 지연(중앙값) |
|---|---|---|
| **passage ON(기본)** | **29/50 (58%)** | ~2.3s |
| passage OFF | 12/50 (24%) | ~510ms |

- 본문중심에선 passage ON이 **+34pp(24→58%, ≈2.4배)**. 제목지향(cases_llm)에선 기여 0이었던 것과 정반대.
- **결론**: "passage OFF = 공짜 속도"는 **제목으로 찾히는 워크로드에 한정**. 본문에만 답이 있는 질의를 포함하면 OFF는 정답을 절반 가까이 잃는다. → **기본값 ON이 옳다.** OFF는 질의가 제목지향임이 확실할 때만 켜는 속도 옵션.

> rerank 자체의 기여: 같은 골든에서 rerank OFF(순수 하이브리드 RRF)는 **74%**로, rerank가 **+13pp**를 만든다.

## 2.7 reranker 모델 교체 실험 — 한국어 변별력 실측

경량 reranker(`Xenova/ms-marco-MiniLM-L-6-v2`, 0.08GB)가 한국어에서 동작하는지 직접 테스트.
질의: `"프로모션 알고리즘"`, `"무중단 배포"` / 문서 4건(관련 2건 + 무관 2건).

| 모델 | 크기 | 1위 정답률 | 점수 분포 | 한국어 |
|---|---|---|---|---|
| `Xenova/ms-marco-MiniLM-L-6-v2` | **0.08GB** | ✓ | **±0.4 (변별력 없음)** | ✗ |
| `BAAI/bge-reranker-base` fp32 | 1.04GB | ✓ | ±11.9 | ✓ |
| `BAAI/bge-reranker-base` **int8** | **0.28GB** | ✓ | ±13.5 **(fp32보다 넓음)** | ✓ |

- 경량 모델은 점수 범위가 0.4 내외로 사실상 **랜덤 순위** — 한국어 reranker로 사용 불가.
- int8이 fp32보다 점수 분포가 더 넓고 관련 문서 2위 회복 — 현재 기본값(int8) 유지가 맞다.
- fastembed 공식 reranker 목록 기준, 한국어 지원이 확인된 경량 모델은 현재 없음.

> **결론**: ternary/BitNet 방향의 초경량 reranker는 Python/fastembed 생태계에서 한국어 지원 미확인.
> 현 `bge-reranker-base` int8(0.28GB)이 크기·정확도·한국어 지원 기준 최선이다.

## 3. 측정 방법 (필수)

노브를 바꾸기 전후로 **품질과 비용을 둘 다** 재고, 차이가 우연이 아닌지까지 확인한다.
도구 전체 설명은 `docs/SETUP_TOOLING.md`, 통계 판정 절차는 `docs/BENCHMARK_METHODOLOGY.md`.

```bash
# (1) 품질 — 설정별 골든 hit-rate 비교(조합 스윕)
python scripts/toolkit.py bench <cases.yml> <db> --sweep RERANK_POOL=20,60,120

# (2) 그 차이가 우연인지 — McNemar 검정
python scripts/ab_significance.py <cases.yml> <db> --a RERANK_POOL=60 --b RERANK_POOL=20

# (3) 비용 — 피크 RAM·지연·스레드 확장성 실측
python scripts/profile_resources.py <db>

# (4) 수집 후 회귀 판정 — 기준선 대비(코퍼스 성장 배수 감안), 종료코드로 합격/불합격
python scripts/autotune.py measure --db <db>     # 최초 1회: 기준선 고정
python scripts/autotune.py verify  --db <db>     # 이후 반복
```

원칙:
- **한 번에 하나의 노브만** 바꿔 인과를 분리한다.
- hit-rate 차이는 그 자체로 결론이 아니다 — n=60 규모에서는 우연일 수 있으므로 (2)로 검정한다.
- 골든셋이 자동생성(`golden_bootstrap.py`)이면 질의 스타일 편향이 결론을 뒤집을 수 있다.
  `--terms` 를 바꿔 여러 밀도의 골든셋에서 같은 결론이 나오는지 확인할 것(방법론 문서 참고).
- 룰기반 골든은 **키워드 나열**이라 문장 관련성을 학습한 rerank 에 구조적으로 불리하다.
  `python scripts/golden_llm.py <db>` 로 **자연어 질의 골든셋**을 따로 만들어,
  같은 비교를 양쪽에서 돌리고 **결론이 일치할 때만** 채택한다(방법론 문서 기법 3).

## 4. 색인(ingest) 속도
- `--no-vector`: 임베딩 생략(키워드+rerank만) — 색인 대폭 단축
- 증분(기본): manifest.last_change 의 변경분만 처리 [24_BRONZE_CONTRACT]
- 큰 git repo: `GERYON_GIT_COMMITS=0`·`GERYON_GIT_MAX_COMMITS`·`GERYON_GIT_MAX_BYTES`

---
**원칙**: 속도↔품질은 trade-off. `RERANK_POOL`·`QUANTIZE`·federation pool 분배가 핵심 레버이고, 변경은 항상 골든으로 측정해 확인한다.
