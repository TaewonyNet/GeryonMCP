# 성능 · 최적화 가이드

검색 속도와 품질은 **trade-off**다. 항상 **골든으로 전후 측정**하고 바꾼다(무작정 키우지 않는다).

## 1. 검색 속도 노브 (환경변수)

| 변수 | 기본 | 효과 |
|---|---|---|
| `GERYON_RERANK_POOL` | 60 | BM25 후보 수. ↑ recall↑·느림 / ↓ 빠름·recall↓ |
| `GERYON_RERANK_QUANTIZE` | 1(int8) | int8 양자화 — 약 28% 빠르고 모델 4배↓, 정확도 −6.5%p. fp32 는 `0` |
| `GERYON_RERANK_THREADS` | CPU 코어 | rerank 스레드 수 |
| `GERYON_RERANK_VEC_POOL` | 0 | 벡터 후보 보강(조사형 recall↑, 느림). 0=끔 |
| `GERYON_RERANK_PASSAGE` | 1 | best-passage(제목+본문구절) 결합 — 본문중심 문서 recall↑(입력 2N) |

> rerank 비용은 대략 **후보 수 × (passage면 2배)** 에 비례한다. 느리면 먼저 `RERANK_POOL` 을 본다.

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

## 3. 측정 방법 (필수)
```bash
# 케이스당 속도 + hit율 — 노브 바꾸기 전후 비교
python scripts/golden_eval.py tests/golden/cases_*.yml "<db1>,<db2>,..."
```
- **속도**: 케이스당 초(전체/케이스 수)
- **품질**: top_k 안에 정답(hit율)
- 한 번에 하나의 노브만 바꿔 인과를 분리한다.

## 4. 색인(ingest) 속도
- `--no-vector`: 임베딩 생략(키워드+rerank만) — 색인 대폭 단축
- 증분(기본): manifest.last_change 의 변경분만 처리 [24_BRONZE_CONTRACT]
- 큰 git repo: `GERYON_GIT_COMMITS=0`·`GERYON_GIT_MAX_COMMITS`·`GERYON_GIT_MAX_BYTES`

---
**원칙**: 속도↔품질은 trade-off. `RERANK_POOL`·`QUANTIZE`·federation pool 분배가 핵심 레버이고, 변경은 항상 골든으로 측정해 확인한다.
