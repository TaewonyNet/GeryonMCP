# Changelog

이 프로젝트의 주요 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르며,
버전은 [유의적 버전(SemVer)](https://semver.org/lang/ko/) `MAJOR.MINOR.PATCH` 를 사용합니다.
- **MAJOR**: 비호환(breaking) 변경 · **MINOR**: 하위호환 기능 추가 · **PATCH**: 하위호환 버그 수정.

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

[1.0.0]: https://github.com/TaewonyNet/GeryonMCP/releases/tag/v1.0.0
