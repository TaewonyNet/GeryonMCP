# Changelog

이 프로젝트의 주요 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르며,
버전은 [유의적 버전(SemVer)](https://semver.org/lang/ko/) `MAJOR.MINOR.PATCH` 를 사용합니다.
- **MAJOR**: 비호환(breaking) 변경 · **MINOR**: 하위호환 기능 추가 · **PATCH**: 하위호환 버그 수정.

## [Unreleased] — 1.3.0 목표

### Fixed — static_score 가 랭킹에 반영되지 않던 문제 (동작 변경)
`quality_signals.py` 가 문서마다 계산해 저장하던 `static_score`
(recency·richness(길이)·backlink_centrality)가 **rerank 경로에 연결돼 있지 않아**,
rerank 가 켜진 기본 설정에서는 랭킹에 **전혀 쓰이지 않았다**(35,829건 전부 사장).
- 확인 방법: 골든 300건에서 `GERYON_STATIC_ALPHA` 를 0→5(50배)로 바꿔도 결과가 완전히
  동일했고 McNemar 분할표의 불일치 쌍이 0 이었다 — 파라미터가 무효라는 결정적 증거.
- 수정: `_rerank_search` 에 static_score 부스트 연결(federation 대응 `_fetch_static_scores`).
  함께 로짓→[0,1] 정규화를 정렬 **전**으로 이동(로짓은 음수가 가능해 곱셈 부스트 시
  부호가 뒤집혀 순위가 깨짐)하고 하류의 이중 정규화를 제거.
- 하드코딩 `STATIC_ALPHA=0.1` → `GERYON_STATIC_ALPHA` 환경변수로 노출.
- 기본값은 **0.1 유지**: alpha 0/0.1/0.5/1/3 = hit 168/169/170/168/166 이지만
  0 vs 0.5 McNemar p=0.625 로 **유의하지 않아** 변경 근거가 없다.

### Added — 검색 행동 로그 (스키마 v8)
오프라인 평가의 근본 한계(합성 골든셋 ≠ 실사용 질의 분포)를 푸는 신호원.
- `search_log`(질의·k·결과수·지연) / `selection_log`(선택 문서·**선택 랭크**) 테이블 추가.
  MCP 의 `search` → `get_document` 호출이 자연스럽게 "질의 → 선택"이라 암묵적 클릭을 얻는다.
- **로컬 전용**(외부 전송 없음) · **비침습**(로깅 실패가 검색을 막지 않음) ·
  `GERYON_SEARCH_LOG=0` 으로 비활성 · 공유 DB 배포 전 `purge_logs()` 로 삭제 가능.
- **`scripts/analyze_logs.py`**: MRR·선택 랭크 분포·무선택(abandonment) 분석과
  **실사용 (질의→선택) 골든셋 export**(`--export-golden`). 선택=정답 가정의 위치 편향은 명시.


### Added — 셋업·튜닝 도구 (측정 기반 설정)

설정값을 감이 아니라 **측정·통계**로 정하기 위한 도구 모음. 전체 설명은 `docs/SETUP_TOOLING.md`,
통계 판정 절차는 `docs/BENCHMARK_METHODOLOGY.md`.

- **`scripts/autotune.py`** — 셋업 4단계: `analyze`(하드웨어·코퍼스 → 권장 설정 즉답) ·
  `apply`(.env 스니펫) · `measure`(실측 기준선 고정) · `verify`(수집 후 회귀 판정, 종료코드로 합격/불합격).
  `verify` 는 절대값이 아니라 **코퍼스 성장 배수** 대비로 비교한다.
- **`scripts/profile_resources.py`** — 설정별 피크 RSS·p50/p95·스레드 확장성 실측.
  설정 조합마다 독립 프로세스로 실행(`GERYON_*` 는 config import 시점에만 읽히므로).
- **`scripts/toolkit.py`** — 흩어진 스크립트의 단일 진입점(`dict`/`term`/`quant`/`golden`/`bench`).
  `bench` 는 `GERYON_*` 스윕으로 설정별 골든 hit-rate·소요시간 비교표를 낸다.
- **`scripts/ab_significance.py`** — 설정 A/B 차이가 우연인지 **McNemar 정확검정**(scipy 불필요).
- **`scripts/calibrate_conflict.py`** — 정의-충돌 임계값을 사람 라벨 없이 보정.
  정답셋을 DDL 구조에서 유도(같은 테이블·같은 컬럼=동일 개념 / 같은 테이블·다른 컬럼=다른 개념).
- **`scripts/term_bootstrap.py`** — Term Contract(용어 정의) 초안 추출. 충돌은 자동 확정하지 않고 표시.
- **`scripts/vector_quant_compare.py`** — fp32 vs int8/binary 벡터 양자화 손실(recall@k) 비교.
- **`scripts/golden_llm.py`** — LLM(로컬 Ollama) 기반 **자연어 골든셋 생성**.
  룰기반(`golden_bootstrap.py`)이 만드는 키워드 나열 질의의 스타일 편향을 보완한다.
  생성물은 **앵커 검증(할루시네이션 차단)·자기참조 금지·중복 제거**를 통과한 것만 수록하며,
  `_self_check`(현 검색기 적중 여부)는 표기만 하고 탈락 기준으로 쓰지 않는다
  (버리면 "이미 맞히는 문제"만 남아 골든셋이 개선 측정 능력을 잃음).
  설정 비교 결론은 **룰기반·LLM 양쪽에서 일치할 때만** 채택 — 판정 절차는 방법론 문서 기법 3.
- `scripts/golden_eval.py` — `--json-out` 추가(케이스별 결과 JSON, McNemar 입력용).
- `scripts/toolkit.py` — `golden-llm` 서브커맨드로 통합.

### Fixed — 패키징(OSS 공개 전 정리)
- **신규 설치가 깨지던 문제**: `mcp>=1.0.0` 에 상한이 없어 새로 설치하면 mcp 2.x 를 받고,
  2.x 에서 `FastMCP`→`MCPServer` 로 개명되어 `mcp.server.fastmcp` 임포트가 실패했다.
  → `mcp>=1.0.0,<2` 로 고정. (기존 개발 환경은 1.x 가 깔려 있어 드러나지 않던 잠재 버그)
- **미사용 의존성 제거**: `llmlingua`·`sumy`·`scikit-learn` 은 코드 어디에서도 import 하지
  않으면서 `torch`(1.1GB)·`transformers`·`accelerate`·`nltk` 를 끌어왔다.
  → 제거. **깨끗한 설치 기준 5.3GB → 392MB**. "무비용·오프라인·CPU 전용·경량" 지향과 정합.
- **누락 의존성 명시**: `httpx`·`numpy`·`onnxruntime`·`typing-extensions` 는 코드가 직접
  쓰면서 전이 의존에만 기대고 있었다 → 명시 선언. 불필요한 `fastmcp` 는 제거
  (`mcp.server.fastmcp` 는 `mcp` 패키지 소속이라 별도 패키지가 필요 없음).
- `[project.optional-dependencies] analyze` 자리 신설 — 향후 로컬 요약·압축 모델을 넣더라도
  core 는 경량으로 유지하기 위한 지점.

### Changed
- `docs/PERFORMANCE.md` — 35,768문서/143,800벡터 환경 재측정으로 1절·3절 갱신.
  옛 수치와 어긋난 항목(int8 정확도 −6.5%p, RERANK=0 정확도 −13pp, pool 축소 시 recall 하락)을
  실측 근거와 함께 정정하고, 코퍼스마다 뒤집힐 수 있음을 명시.
- `docs/USER_MANUAL.md` — 요구사항을 실측값으로 교체(RAM 권장 4.5GB/최소 1.4GB, CPU 8코어 권장).

### 실측 요약 (레퍼런스: 20코어/62GB, 35,768문서/143,800벡터)
- 권장 설정 `RERANK_POOL=20` + `RERANK_THREADS=8`: p50 **~350–410ms**, 피크 RSS 3.1GB
- 스레드는 **8이 최적** — 12부터 정체, 20에서 급락(1,659ms)
- 벡터 양자화: **int8 recall@10 0.96–0.97**(4× 압축, 안전) / **binary 0.30**(384차원엔 부적합)

### 목표: 이중 청크 검색 인덱스 (원본 + 핵심요약본)

회의록·명세서 등 긴 문서의 검색 품질을 높이기 위해 **원본 청크와 LLM 정제 청크를 함께 색인**한다.

**검색 DB에 두 종류의 청크를 저장:**
- `raw` — 현재 방식의 슬라이딩 윈도우 청크 (원문 그대로)
- `refined` — LLM이 추출한 의미 단위 청크 (불필요한 내용 제거, 핵심만)

**문서 유형별 정제 방식:**
- `meeting` — topic 세그먼트만 추출 (잡담·진행 발언 제거), `제목 + 요약 + 근거`를 청크로
- `spec / wiki` — 섹션별 `요약 + 핵심항목`을 청크로, 수식·테이블은 보존

**검색 흐름:**
- `raw` + `refined` 풀을 동시에 검색 → cross-encoder rerank로 최종 순위 결정
- refined 청크가 있는 문서는 의미 검색 정밀도 향상, raw 청크는 정확한 문구 검색 커버

**필요 기술:**
- `chunks` 테이블 `chunk_type` 컬럼 추가 (`raw` / `refined`)
- `documents` 테이블 `refined_at` 추가 (정제 처리 여부·시각 추적)
- `DocumentRefiner` → `IngestionPipeline` 통합 (조건: 500자 이상 + meeting/spec 감지)
- Ollama 미실행 시 raw만 색인하는 graceful 폴백
- `detect_type` 확장 — 마크다운 테이블(`|---|`) 포함 문서 `spec`으로 분류 (완료 v1.2.0)
- `_SPEC_SYSTEM` 프롬프트 — 헤더 없는 문서도 주제 전환 기준으로 섹션 분리, `formula` 타입 추가 (완료 v1.2.0)

### Added
- **`scripts/team_install_template.py`**: 팀 배포용 원클릭 설치 스크립트 템플릿(PEP 723, 표준 라이브러리만 사용). wheel·공유 DB를 scp 우선/HTTP 폴백으로 받아 `uv tool install` + `geryon setup`(MCP 등록·샘플 검색)까지 자동화. 상수 5개(`GERYON_INSTALL_*`)만 바꾸면 팀마다 재사용. `docs/TEAM_DEPLOY.md` §2에 사용법 추가.

## [1.2.0] - 2026-07-26

문서 정제 레이어(LLM 추출) + 자동 싱크 데몬 + 멀티소스 sync + Confluence 접근 감지 + MCP 클라이언트 상세 가이드.

### Added
- **`geryon watch` 자동 싱크 데몬**: `sync` 를 주기적으로 반복 실행(기본 10분 간격). SIGINT/SIGTERM 우아한 종료, 1초 단위 sleep으로 인터럽트 반응성 유지. `--interval`, `--no-vector`, `--full`, `--no-prune` 지원. systemd / launchd / Windows 작업 스케줄러 등록 방법은 `docs/MCP_INSTALL.md` §7 참고.
- **멀티소스 `sync`·`watch`**: `--source` 를 생략하면 confluence+git+jira 를 한 인덱스로 통합 수집. 한 소스가 실패해도 나머지는 계속 진행하고, 실패가 있으면 끝에서 요약과 함께 비정상 종료코드를 낸다.
- **Confluence 접근 상실 감지** (`check_space_access`): 계정 ID·API 토큰 변경으로 스페이스 접근 권한을 잃으면 sync 가 조용히 0건이 되던 사고를 방지. 설정된 스페이스 중 접근 불가를 감지해 `⚠ 접근 불가 스페이스 N개` 경고를 `sync`/`acquire` 배너로 노출(재시도 없는 프로브·상한으로 점검 비용 최소화). 트러블슈팅은 `docs/USER_MANUAL.md` §7.
- **문서 정제 레이어** (`src/geryon/analyze/llm_extract.py`): 회의 전사체·기술 스팩 문서를 LLM(Ollama)으로 처리해 의미 단위로 분해. `LLMConfig`(환경변수 오버라이드), `ExtractionMeta`(모델·digest·스키마 버전 추적), `DocumentRefiner`(meeting/spec 자동 감지·추출) 포함.
- **LLM 변경 추적**: `ExtractionMeta.model_digest` — Ollama `/api/show` 로 모델 파일 hash(앞 12자리)를 기록. 환경변수 `GERYON_LLM_MODEL`, `GERYON_LLM_URL`, `GERYON_LLM_CTX`, `GERYON_LLM_PREDICT`로 qwen2.5·Gemma 등 다른 LLM으로 교체 가능.
- **단위·통합 테스트** (`tests/test_extract.py`, `tests/test_watch_and_access.py`, `tests/fixtures/synthetic.py`): 실제 개인정보·회사정보 없는 합성 데이터, `@pytest.mark.integration` 마커(Ollama 미실행 시 자동 skip). watch 프리픽스 폴백·스페이스 접근 점검 커버.
- **`docs/MCP_INSTALL.md`**: Claude Code / Claude Desktop / Cursor / VS Code / Zed 클라이언트별 상세 설치·등록 방법 + `geryon watch` 데몬 연동 방법(systemd·launchd·Windows 스케줄러 예제 포함).

### Changed
- `docs/INSTALL_WALKTHROUGH.md` §6: `MCP_INSTALL.md` 링크로 요약. 공통 JSON 예시는 유지.
- 설치·배포 문서(USER_MANUAL·TEAM_DEPLOY·MCP_INSTALL·INSTALL_WALKTHROUGH·AIRGAP): MCP `command: "geryon"` 의 PATH 주의(격리 venv·`uv tool` 시 절대경로), `geryon watch` 자동 싱크 안내, 자격증명 회전 트러블슈팅, `sync`/`--all` 의미 정정, 소스 지원 현황(Confluence·Git·Jira) 반영.

### Fixed
- **`geryon watch` 데몬 무동작**: `geryon` 이 PATH 에 없는 격리 venv 에서 자식 `sync` 가 `python sync` 로 실행돼 매 주기 실패하던 문제 수정(`_geryon_cmd_prefix` — 현재 스크립트를 인터프리터와 함께 실행).
- **멀티소스 sync 연쇄 중단**: 한 소스 실패가 나머지 소스 수집을 막던 문제 수정(실패는 집계 후 계속 진행).
- **watch 우아한 종료**: Ctrl+C 시 진행 중 싱크가 함께 중단되던 문제 — 자식을 새 세션으로 분리(`start_new_session`)해 "현재 싱크 완료 후 종료" 약속대로 동작. `--interval` 하한 클램프.

## [1.1.0] - 2026-06-20

폐쇄망(air-gap) 지원 강화.

### Added
- **폐쇄망 설치 가이드**(`docs/AIRGAP_INSTALL.md`): 오프라인 휠 번들(`pip wheel .`) + 모델 캐시 반입 + 외부빌드 DB 반입(전략 B) 전 과정. 깨끗한 환경·`HF_HUB_OFFLINE=1`에서 설치→모델→검색→MCP 전 과정 실측 검증.
- **`GERYON_MODEL_CACHE`**: 임베드+rerank **통합 모델 캐시 경로**(기본 `~/.geryon/models`). 폐쇄망 반입 단위가 디렉터리 하나로 단순화. `GERYON_RERANK_CACHE` 는 하위호환 별칭.

### Fixed
- 임베드 모델이 `/tmp/fastembed_cache`(재부팅 시 휘발)에 별도 캐시되던 문제 — 통합 캐시(`~/.geryon/models`)로 영구화. rerank 모델과 동일 위치.

## [1.0.0] - 2026-06-18

첫 안정 공개 릴리스 — **공개 API 안정화 선언**(SemVer 시작). 로컬·무료·오프라인·CPU 전용(≤16GB) 멀티소스 검색 MCP 서버.

### Added
- **멀티소스 수집(acquire)**: Confluence · Git · Jira. 통합 Bronze 계약(원본 + `manifest.json`).
- **Federation 검색**: 소스별 DB를 독립 유지하고 통합 cross-encoder rerank 로 병합(BM25 스케일 차이를 절대 점수가 흡수).
- **Best-passage rerank**: 제목과 제목+본문을 함께 재정렬해 max 결합(본문 중심 질의 recall 강화). 본문중심 골든에서 +34pp 검증(passage ON 58% vs OFF 24%).
- **증분 적재(ingest)**: Bronze `manifest.json` 의 `last_change` 기반(소스 공통). 변경분만 처리, 멱등 보장.
- **날짜 구간 수집**: `--since` / `--until`(YYYY-MM-DD). 기본은 `--days 30`(현 시점부터 최근 한 달).
- **Confluence 첨부 다운로드**: `api.atlassian.com/ex/confluence/{cloudId}` 게이트웨이 경유(사이트 도메인 `/download` 는 API 토큰을 거부·OAuth 401 → cloudId 자동 해석, 실패 시 폴백).
- **첨부 기본 미수집**(opt-in): 첨부 본문은 색인되지 않으므로 기본은 안 받음(수집 시간·404 노이즈 제거). `--attachments` 로만 다운로드. 받을 때 스킵 필터 `GERYON_ATTACH_MAX_MB`(기본 50, 메타+Content-Length 검사) + `GERYON_ATTACH_SKIP_EXT`.
- **자격증명 env 전용**: Confluence/Jira 토큰은 환경변수(`CONFLUENCE_*`/`JIRA_*`)로만 제어. `.env` 는 실행 시 자동 로드(의존성 없는 내장 로더, OS env 우선). mcp.json 자격증명 폴백은 제거(혼란 방지). MCP 클라이언트 등록용 `.cursor/mcp.json`·`.mcp.json` 은 별개로 유지.
- **`geryon init`**(대화형+플래그): Confluence → Jira → Git 순. 대화형에서는 **토큰 검증 후 스페이스/프로젝트/저장소 목록을 나열해 선택**(Confluence `/space`·Jira `/project/search`·Git 호스트 API). Git 은 GitHub/GitLab/Bitbucket 토큰 입력 또는 직접 URL. 결과는 `.env`(`GERYON_CONFLUENCE_SPACES`·`GERYON_JIRA_PROJECTS`·`GERYON_GIT_REPOS`)에 기록 — `sync`(인자 미지정 시) 가 소비.
- **Confluence 스페이스 필터**: `--space`/`GERYON_CONFLUENCE_SPACES` 로 특정 스페이스만 수집(CQL `space in (...)`). Jira 다중 프로젝트 수집 지원.
- **증분이 기본**: acquire 창을 명시(`--all`/`--since`/`--days`)하지 않으면 **DB 워터마크(`MAX(documents.updated_at)`) 이후**만 수집(첫 실행=빈 DB는 30일 부트스트랩). 고정 30일 창이 갖던 "뜸하게 갱신 시 공백"을 제거. Bronze 없이도 동작(멱등 안전: doc_id+content_hash). `--since-db` 는 이 동작의 명시 플래그.
- **Git 인증**: 기본은 사용자 git 인증(SSH/credential helper)으로 clone, 없으면 `GERYON_GIT_TOKEN`(+`GERYON_GIT_USERNAME`: GitLab=oauth2·Bitbucket=x-token-auth)로 HTTPS 주입.
- **개인화 사전**: 수동(`dictionary.yaml`, 항상 적용) / 자동 부트스트랩(`dictionary.auto.yaml`, **기본 OFF·opt-in** `GERYON_DICT_AUTO=1`) / 제외(`dictionary.deny.yaml`).
- **골든 부트스트랩**: `scripts/golden_bootstrap.py` — DB에서 본문중심 골든 질의를 역생성하는 반자동 도구(오프라인, 사람 검수 전제).
- **MCP 서버**: `geryon serve`(stdio) — 도구 `search`·`advanced_search`·`get_related`·`get_document`·`browse`·`list_sources`·`reindex`. setup / publish CLI 로 팀 배포.
- **CLI 직접 검색**: `geryon search "<질의>"`(표 / `--json`, 필터·`-k`·`--user` 지원) — MCP `search` 와 **동일 검색 코어**(`geryon.search.query`). MCP 클라이언트 없이 터미널·스크립트·디버깅에서 검색.
- **공유 질의 서비스**(`geryon.search.query`): 질의→필터→검색→직렬화를 단일 경로로. MCP 도구(search·advanced_search·get_related)와 CLI 가 모두 이 헬퍼를 통해 동일 결과·형식 보장(어댑터별 복제·drift 제거).
- **문서**: 설치~MCP 연결 검증 가이드(`INSTALL_WALKTHROUGH.md`), 무설정 시작(USER_MANUAL §2.4), 성능·벤치(PERFORMANCE §2.6, Confluence 공식검색 비교).

### Notes
- 질의 시 외부 API 불필요(완전 오프라인). 재정렬 모델은 MIT. 한글·영문(다국어) 검색 지원.
- 실데이터 end-to-end 검증: fresh 설치 → 최근 한 달 수집·색인 → MCP 연결까지 통과.
- 구성: `src/geryon` 11개 모듈(acquire·connectors·domain·embed·gold·index·mcp·normalize·pipeline·search·store).
- 공개 전 정리: 문서·코드·테스트의 실제 인명 제거(placeholder), `embed` 패키지 `__init__.py` 보강, `pytest testpaths` 고정.

[1.2.0]: https://github.com/TaewonyNet/GeryonMCP/releases/tag/v1.2.0
[1.1.0]: https://github.com/TaewonyNet/GeryonMCP/releases/tag/v1.1.0
[1.0.0]: https://github.com/TaewonyNet/GeryonMCP/releases/tag/v1.0.0
