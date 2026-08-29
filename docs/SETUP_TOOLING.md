# 셋업·튜닝 도구 가이드

> 설치 → 사양 분석 → 설정 권장 → 데이터 수집 → 성능 검증 → 품질 튜닝의 전 과정을 **기계적으로**
> (사람의 감이 아니라 측정·통계로) 돌리기 위한 도구 모음. 모든 판단 기준은 실측값 또는
> 코퍼스에서 유도한 정답셋에서 나온다.

---

## 0. 한눈에 보기

```
[설치 직후]
  autotune analyze          하드웨어·코퍼스 분석 → 권장 설정 즉답(벤치 없음)
  autotune apply            권장 설정을 .env 스니펫으로 기록
        ↓
  autotune measure          이 호스트에서 실측 → 기준선(baseline) 고정
        ↓
[geryon sync — 데이터 수집·색인]
        ↓
  autotune verify           코퍼스 성장 배수 대비 회귀 판정(종료코드로 합격/불합격)

[품질 튜닝 — 별도 트랙]
  golden_bootstrap          골든셋 생성 ① 룰기반(결정적·무LLM, 키워드형 질의)
  toolkit golden-llm        골든셋 생성 ② LLM(로컬 Ollama, 자연어 질의)
        ↓  ※ 두 종류를 다 만들어 결론이 일치하는지 본다
  toolkit golden            골든셋으로 hit-rate 1회 측정
  toolkit bench             설정별 골든셋 hit-rate 비교(스윕)
  ab_significance           A/B 차이가 우연인지 McNemar 검정
        ↓
  calibrate_conflict        임계값을 손이 아니라 정답셋으로 보정
  toolkit dict / term       동의어 사전 · Term Contract 초안 추출
  toolkit quant             벡터 양자화(int8/binary) 손실 측정
```

두 트랙은 목적이 다르다. **autotune = 비용(메모리·지연)**, **toolkit/ab = 품질(정확도)**.
설정을 바꿀 때는 두 축을 같이 봐야 한다(예: `pool=20`은 두 축 모두에서 우세해 채택 근거가 됨).

품질 트랙의 출발점은 **골든셋**이다. 골든셋 없이 bench/McNemar 를 돌려도 잴 대상이 없다.
룰기반·LLM 두 종류를 만들어 **양쪽에서 같은 결론이 나올 때만** 채택한다(기법 3, 방법론 문서).

---

## 1. autotune.py — 셋업 4단계

```bash
python scripts/autotune.py analyze [--db DB]
python scripts/autotune.py apply   [--db DB] [--env-file .env]
python scripts/autotune.py measure [--db DB] [--baseline PATH]
python scripts/autotune.py verify  [--db DB] [--baseline PATH] [--tolerance 20]
```

### analyze — 벤치 없이 즉답
하드웨어(`/proc/meminfo`, `os.cpu_count`)와 코퍼스 규모(문서·벡터 수)를 읽어, 내장
**레퍼런스 실측치**로 외삽해 권장 프로파일을 고른다. 데이터 수집 전에도 쓸 수 있는 게 목적.

| 프로파일 | 선택 조건 | 설정 |
|---|---|---|
| `fast` | RAM ≥ rerank 요구치 | rerank on + int8 + `POOL=20` + threads |
| `lean` | RAM이 rerank엔 부족하나 최소치는 충족 | `RERANK=0` (메모리 1/3, 지연 증가) |
| `insufficient` | 최소치 미달 | 경고만 |

임계 RAM은 상수가 아니라 **레퍼런스 피크 RSS × 안전계수(1.5)** 로 계산된다.

### apply — 설정 파일 생성
권장값을 근거 주석과 함께 `.env` 스니펫으로 출력하거나(`--env-file` 지정 시) 파일에 덧붙인다.

### measure — 이 호스트의 진짜 기준선
`profile_resources.py`를 실행해 설정 5종 × (피크 RSS, p50/p95) + 스레드 스윕을 측정하고
`~/.geryon/autotune_baseline.json`에 고정한다. analyze의 외삽을 실측으로 대체하는 단계.

### verify — 수집 후 회귀 판정
데이터가 늘면 느려지는 게 정상이므로 **절대값이 아니라 성장 배수**로 비교한다.

```
허용 지연 배수 = (현재 벡터수 / 기준 벡터수) × (1 + tolerance/100)
```

전 설정이 허용 범위 안이면 종료코드 0(합격), 하나라도 넘으면 1(불합격) — CI에 걸 수 있다.
피크 RSS × 1.5가 호스트 RAM을 넘어설 조짐도 함께 실패 처리한다.

---

## 2. profile_resources.py — 자원 실측

```bash
python scripts/profile_resources.py <db> [--out profile.json] [--max-threads N]
```

설정 조합마다 **독립 프로세스**로 워커를 띄워 측정한다(`GERYON_*`는 `config.py` import 시점에
한 번만 읽히므로 같은 프로세스 안에서 재설정 불가 — 이걸 놓치면 전부 같은 설정으로 재는 셈).

- `peak_rss_mb`: `resource.getrusage(RUSAGE_SELF).ru_maxrss` — 모델 로드 포함 실제 최대 상주 메모리
- `p50/p95`: 워밍업 1회 제외 후 정상상태 지연
- 스레드 스윕: `GERYON_RERANK_THREADS` 1~20

### 권장 코어 산출 규칙 (주의 — 한 번 틀렸던 부분)
`pick_optimal_threads()`는 **최속 대비 tolerance(5%) 이내이면서 가장 적은 스레드**를 고른다.
초기 구현은 "직전 대비 개선 <5%인 첫 지점"이었는데, 성능이 **악화**되는 구간(8→12스레드 −7.7%)도
조건을 만족해 오답(12)을 냈다. 상대비교 기준으로 교체해 8을 맞게 고른다.

---

## 3. 품질 튜닝 도구

### toolkit.py — 단일 진입점
```bash
python scripts/toolkit.py dict   <db>              # 동의어 사전 초안
python scripts/toolkit.py term   <db>              # Term Contract 초안
python scripts/toolkit.py quant  <db> --mode int8  # 벡터 양자화 손실 비교
python scripts/toolkit.py golden <cases.yml> <db>  # 골든셋 hit-rate 1회
python scripts/toolkit.py golden-llm <db> -n 20    # LLM 자연어 골든셋 생성(로컬 Ollama)
python scripts/toolkit.py bench  <cases.yml> <db> --sweep RERANK_POOL=20,60,120
```
`bench`가 핵심: `GERYON_*` 값을 스윕(카티전곱)해 조합별 hit-rate·소요시간을 정렬 표로 낸다.
조합마다 새 프로세스로 실행하며, `--max-combos`(기본 24)로 폭주를 막는다.

### ab_significance.py — 우연인지 검정
```bash
python scripts/ab_significance.py <cases.yml> <db> --a RERANK=1 --b RERANK=0
```
같은 케이스에 A/B를 돌려 짝지은 결과로 **McNemar 정확검정**(scipy 없이 `math.comb`).
`bench`가 "차이가 있다"까지라면, 이건 "그 차이가 우연이 아니다"를 판정한다.
자세한 절차·해석은 `docs/BENCHMARK_METHODOLOGY.md`.

### calibrate_conflict.py — 임계값 기계 보정
```bash
python scripts/calibrate_conflict.py <db> [--out calib.json]
```
`term_bootstrap`의 정의-충돌 판정 임계값을 **사람 라벨 없이** 보정한다. 정답셋을 DDL 구조에서
유도하는 게 요점:

- **양성(같은 개념)** = 같은 `(테이블, 컬럼)`의 정의들 — 정의상 동일 개념
- **어려운 음성(다른 개념)** = **같은 테이블의 다른 컬럼** — 도메인·어휘는 비슷하나 확실히 다름

이 위에서 임계값 격자를 전수 탐색해 F1 최댓값을 고른다. 실제로 이걸 돌려 `jaccard 0.25 → 0.10`
(F1 0.734→0.791)으로 교정했고, 손으로 넣었던 `containment` 분기가 **판정을 한 번도 바꾸지 않는
무효 파라미터**임을 발견했다.

### golden_llm.py — LLM 기반 골든셋 생성 (자연어 질의)
```bash
python scripts/golden_llm.py <db> --out golden_llm.draft.yml -n 20 --per-doc 2 [--verify]
```
룰기반(`golden_bootstrap.py`)이 만드는 키워드 나열 질의의 **스타일 편향을 보완**한다.
로컬 Ollama 로 문서에서 자연어 질문을 역생성하고, **앵커 검증(할루시네이션 차단)·자기참조 금지·
중복 제거**를 통과한 것만 초안에 남긴다. 비결정적이므로 커밋 전 사람 검수 필수.
설정 비교 결론은 **룰기반·LLM 양쪽에서 일치할 때만** 채택한다 —
자세한 절차·판정 규칙은 `docs/BENCHMARK_METHODOLOGY.md` 기법 3.

### vector_quant_compare.py — 양자화 손실
```bash
python scripts/vector_quant_compare.py <db> --mode int8|binary
```
원본을 읽기전용으로 두고 복사본의 `chunk_embeddings`만 목표 타입으로 재구성해 recall@k를 비교.
(sqlite-vec는 삽입/조회 모두 `vec_int8(?)`/`vec_bit(?)` 캐스트가 필요하다.)

---

## 4. 실측 결과 (레퍼런스: 20코어 / 62GB / 35,768문서 / 143,800벡터)

### 설정별 비용
| 설정 | 피크 RSS | p50 |
|---|---:|---:|
| 기본(rerank on, int8) | 3,095MB | ~900ms |
| **pool 20** | 3,095MB | **~350–410ms** |
| passage off | 3,094MB | ~420ms |
| rerank fp32 | 2,719MB | ~1,650ms |
| rerank off | **909MB** | ~3,200–4,900ms (편차 큼) |

### 스레드 확장성
1→8스레드까지 개선(3,416→882ms), **8이 최적**, 12부터 정체·악화, 20에서 급락(1,659ms).
전체 코어를 다 쓰면 경합으로 역효과.

**재현성(3회 반복 측정)** — 같은 호스트에서 반복해도 결론이 흔들리지 않는지 확인한 결과:

| 항목 | 1회 | 2회 | 3회 | 판단 |
|---|---:|---:|---:|---|
| 기본(pool 60) | 927ms | 883ms | 810ms | 안정 |
| pool 20 | 410ms | 348ms | 357ms | 안정 |
| 8스레드 최적 지점 | 882ms | — | 747ms | **3회 모두 8이 최적** |
| 피크 RSS | 3,095MB | 3,095MB | 3,094MB | 매우 안정 |
| `rerank off` | 4,877ms | 3,198ms | 3,225ms | **편차 큼 — 주의** |

`rerank off` 만 유독 흔들린다(±50%). 회귀 판정에서 이 항목만 실패하면 실제 회귀인지
측정 노이즈인지 재실행으로 확인할 것. 나머지 항목과 스레드 결론은 반복 재현된다.

### 도출된 권장 사양
| 구성 | RAM | 코어 | 지연 |
|---|---|---|---|
| 권장(fast) | **4.5GB** | **8** | ~350ms |
| 저메모리(lean) | **1.4GB** | 4 | ~3.2s |

### 양자화
| 방식 | recall@10 (fp32 대비) | 압축 |
|---|---:|---:|
| int8 | **0.96–0.97** | 4× |
| binary | 0.30 | 32× |

int8은 안전, **binary는 이 임베딩(384차원)에서 부적합** — 벤더 문서의 "~95%"는 1536차원
기준이라 그대로 적용되지 않았다.

---

## 5. 알려진 한계

- **레퍼런스는 이 호스트 기준**이다. 다른 사양에서는 `measure`로 갱신해야 정확하다 —
  `analyze`는 어디까지나 초기 추정.
- `rerank off` 지연은 측정 편차가 크다(3,195 / 4,877 / 3,198ms). `verify`가 이 항목만
  실패하면 실제 회귀인지 노이즈인지 재실행으로 확인할 것.
- **`term_bootstrap`의 규칙 12개 중 기계 보정된 것은 2개뿐**이다. 스톱워드 목록 4종·정규식
  6종·길이 경계 다수, 그리고 채널 신뢰도 서열(`_PRIORITY`)은 아직 손으로 정한 값이며
  검증되지 않았다. 특히 `_PRIORITY`(sql_comment > title_exact > definition_sentence)는
  파이프라인 전체가 의존하는 가정인데 한 번도 측정한 적이 없다.
- 골든셋이 자동생성(사람 검수 전)이라는 점은 여전하다. `golden_llm.py` 로 자연어 질의 축을
  보완했으나, LLM 생성물도 결국 **문서에서 역생성한 합성 질의**다. 완전한 검증은 실사용 질의
  로그 확보 후 재구성이 필요하다.

---

## 6. 관련 문서
- `docs/BENCHMARK_METHODOLOGY.md` — McNemar·term-density 스윕 등 통계 검증 절차
- `docs/PERFORMANCE.md` — 설정별 성능 레버·측정 절차 (이번 실측으로 갱신됨)
- `docs/USER_MANUAL.md` — 사용자용 설정 안내·요구사항
