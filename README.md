# GeryonMCP

> 내 컴퓨터에서 **무료·오프라인·CPU**로 도는 문서 검색 MCP 서버.
> 사내 위키(Confluence 등)를 로컬 DB에 적재해, 유료 AI·인터넷 없이 검색합니다.

이름의 유래: 머리·몸이 여럿인 그리스 신화의 거인 **Geryon** — 여러 소스를 한 몸으로 묶는 검색엔진.

## 특징
- **무비용 · 오프라인 · CPU 전용 · 메모리 16GB 이내** — 유료/온라인 추론 호출 0. 모델은 1회 받고 완전 오프라인.
- **키워드(BM25) + cross-encoder 재정렬(ONNX)** — 벡터에 의존하지 않고도 정밀한 상위 결과. **한글·영문 다국어**(음차·기술용어 교차언어 매칭).
- **메타 필드별 상세검색**(`advanced_search`) — 제목·작성자·공간·태그·날짜를 독립 필드로 결합.
- **멀티소스 federation** — Confluence·Jira·Git(GitHub/GitLab/Bitbucket, **소스 코드·커밋 포함**)을 **소스별 DB로 두고 검색 시 통합 rerank**(소스 무관 랭킹). 새 소스는 플러그인(검색 코드 변경 0).
- **증분 색인** — `manifest.last_change`로 **변경분만 재색인**(소스 공통, API 재호출 0).
- **메달리온 + 사전** — 원본 보존(Bronze) → 정제 DB(Silver) → 개인화 사전(Gold). 수동 사전은 항상 적용(선택), 데이터에서 동의어를 뽑는 **자동 부트스트랩은 opt-in**(기본 OFF).
- **MCP 서버** — MCP 클라이언트(Claude Desktop, VS Code 등)에 바로 연결.

## 동작 원리

```
원본 위키 ─[수집]→ 로컬 파일(Bronze) ─[적재·정제]→ 로컬 DB(Silver)
                                                      │
질문 ─[검색]→ 키워드 후보(BM25, 한국어 정규화 FTS)
            → cross-encoder 재정렬(ONNX·int8)   ← 정확도의 핵심
            → 결과            (벡터 검색은 보조/폴백)
```
키워드 검색이 후보를 빠르게 추리고, **재정렬 모델이 정답을 위로 올립니다.** 모두 로컬 CPU에서 외부 호출 없이.

## 핵심 설계 (측정 기반)
모든 채택·제외는 추측이 아니라 **실측**으로 결정했습니다.
- **한국어 친화 토큰화** — FTS5 토크나이저·조사 정규화로 한국어 BM25 정확도 확보.
- **cross-encoder 재정렬** — fastembed ONNX(torch 불필요)·int8 양자화로 CPU에서 sub-second. 무거운 벡터 brute-force 대신 키워드+재정렬로 재균형.
- **best-passage 결합** — 제목과 본문 매칭 구절을 함께 평가(점수 max 결합)해, 제목과 동떨어진 본문 중심 문서도 회복.
- **메타 노이즈 정규화** — 작성자 상태 접미사(`(Deactivated)` 등)·분류 노이즈를 정규화해 필터·표시 일관성 확보.
- **라이선스 안전** — 재정렬 모델은 MIT(상업 이용 가능).

## 사전 준비
1. **Python 3.10 이상** — 확인: `python3 --version` (없으면 [python.org](https://www.python.org/downloads/) 또는 OS 패키지 매니저로 설치)
2. **uv**(빠른 설치 도구) — 택1:
   - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows(PowerShell): `irm https://astral.sh/uv/install.ps1 | iex`
   - 또는 공통: `pip install uv`
   - uv 없이도 `pip` 만으로 설치 가능(Quickstart 1단계 주석 참고)

> 처음이라면 **2~3단계(설치·bootstrap·demo)만으로 자격증명 없이 검색을 체험**할 수 있습니다. 실제 위키 연동은 4단계(토큰)부터입니다.

## Quickstart

```bash
# 1) 설치
uv tool install .            # 또는: pip install -e .

# 2) 부트스트랩 — 모델 자동 다운로드 + 설정 샘플(.env) 생성
geryon bootstrap

# 3) 자격증명 없이 검색 체험 (가상 데이터)
geryon demo

# 4) 실제 데이터 — 수집(acquire) → 색인(ingest)
#    2)에서 만든 .env 를 열어 CONFLUENCE_URL / USERNAME / API_TOKEN 채우기 (또는 `geryon init`)
geryon acquire               # 외부 → Bronze 원본 파일(기본: 최근 한 달, 멱등/증분)
#   --since 2026-01-01 --until 2026-03-31  날짜 구간만 / --all 전체
geryon ingest --source confluence --path ./bronze/confluence   # Bronze → 검색 DB
geryon serve                 # MCP 서버 기동
```
> 수집(`acquire`)과 색인(`ingest`)을 나눈 이유·전체 옵션(증분/전체, prune, 데이터 경로 지정)은 [docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md) 참고. 검색 로직만 바꿔 재색인할 땐 `ingest`만 다시 돌리면 됩니다(API 재호출 0).

> **별도 설정 없이 시작**: 위 흐름 외에 입력할 것은 없습니다. 동의어 사전(`~/.geryon/gold/dictionary.yaml`)은 **선택** — 없어도 검색은 동일하게 동작하며 나중에 추가하면 됩니다. 자동 동의어 사전은 **기본 OFF**(데이터 적합성 확인 후 `GERYON_DICT_AUTO=1` 로 opt-in). 모든 환경변수는 합리적 기본값을 가져 튜닝 없이 바로 쓸 수 있습니다.

`geryon demo` 출력 예시:
```
질의: '무중단 배포 절차'   →  서버 배포 가이드, 데이터 백업 절차
질의: '휴가 신청'          →  휴가 정책
```

## Atlassian API 토큰 발급 (실제 위키 연동 시)
4단계 `acquire`(수집)에 필요합니다. 비밀번호 대신 토큰을 씁니다.
1. 브라우저에서 **https://id.atlassian.com/manage-profile/security/api-tokens** 접속
2. **Create API token** 클릭 → 이름 입력(예: geryon) → 생성된 토큰 **복사**(다시 못 봄)
3. `.env` 파일을 열어 채우기:
   ```
   CONFLUENCE_URL=https://<회사도메인>.atlassian.net/wiki
   CONFLUENCE_USERNAME=<로그인 이메일>
   CONFLUENCE_API_TOKEN=<복사한 토큰>
   ```
   (`.env` 가 없으면 `geryon bootstrap` 이 `.env.sample` 에서 만들어 줍니다)

## MCP 클라이언트 등록
검색을 AI 클라이언트(Claude Desktop, VS Code 등)에서 쓰려면 설정 파일에 등록합니다.

**Claude Desktop** — 설정 파일을 열고(`Settings → Developer → Edit Config`) 아래를 추가:
```json
{
  "mcpServers": {
    "geryon": { "command": "geryon", "args": ["serve"] }
  }
}
```
저장 후 클라이언트를 재시작하면 검색 도구(`search` 등)가 나타납니다.

## 도구
| 도구 | 설명 |
|---|---|
| `search` | 통합 검색(키워드 + 재정렬, 동의어·개인화) |
| `advanced_search` | 메타 필드별 상세검색(제목·작성자·공간·태그·날짜) |
| `get_document` | 문서 전문 조회 |
| `get_related` | 연관 문서(링크 기반) |
| `browse` | 공간/계층 목록 |
| `list_sources` | 색인된 소스/공간 목록 |
| `reindex` | 재색인 트리거 |

> 같은 검색을 터미널에서 직접: `geryon search "<질의>"`(표/`--json`) — MCP 도구와 동일 코어.

## 설정
모든 옵션은 `.env`(또는 환경변수)로 조정합니다. 옵션 전체 목록은 저장소의 `.env.sample` 참고. 파일이 없으면 `geryon bootstrap`이 **현재 디렉터리에 내장 템플릿으로 `.env`를 생성**하며(설치본엔 `.env.sample` 파일이 없어도 동작), **기존 값은 절대 덮어쓰지 않습니다.**

주요 옵션: `GERYON_RERANK_PASSAGE`(본문중심 강화 on/off), `GERYON_RERANK_POOL`(후보 수), `GERYON_RERANK_QUANTIZE`(int8 가속), `GERYON_RERANK_MODEL`, `GERYON_LOG_LEVEL`.

## CLI
| 명령 | 설명 |
|---|---|
| `geryon bootstrap` | 모델 다운로드 + 설정 샘플 생성 |
| `geryon init` | 자격증명·수집 소스(Confluence 스페이스·Jira 프로젝트·Git repo)를 `.env` 에 설정(대화형/플래그) |
| `geryon demo` | 가상 데이터로 검색 체험(자격증명 불필요) |
| `geryon sync --all` | 수집·색인 |
| `geryon search "<질의>"` | 터미널에서 직접 검색(표 / `--json`). MCP `search` 와 동일 코어 |
| `geryon serve` | MCP 서버 기동 |
| `geryon status` / `health` | 상태·헬스 점검 |

## 문서
- **[docs/USER_MANUAL.md](docs/USER_MANUAL.md) — 사용 매뉴얼(설치~운영 단계별)**
- [docs/INSTALL_WALKTHROUGH.md](docs/INSTALL_WALKTHROUGH.md) — 설치부터 MCP 연결까지 검증된 단계별 가이드(최근 한 달 수집·다국어 포함)
- [docs/AIRGAP_INSTALL.md](docs/AIRGAP_INSTALL.md) — 폐쇄망(인터넷 없는 망) 설치·운영 가이드(오프라인 휠 번들·모델 반입, 검증됨)
- [docs/GOLDEN_AND_DICTIONARY.md](docs/GOLDEN_AND_DICTIONARY.md) — 골든 테스트셋·동의어 사전 작성 노하우(조사형·번역왕복·영한 음차)
- [docs/TEAM_DEPLOY.md](docs/TEAM_DEPLOY.md) — 팀 배포(프로젝트에 검색 MCP 붙이기·프로젝트별 인덱스)
- [docs/EXTENDING_SOURCES.md](docs/EXTENDING_SOURCES.md) — 새 데이터 소스 추가법(Jira·GitHub 등 멀티소스 확장)
- [docs/BEGINNER_GUIDE.md](docs/BEGINNER_GUIDE.md) — 초급자용 개념·코드 지도
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — 검색 파이프라인·성능 튜닝(rerank·pool·int8)
- [CHANGELOG.md](CHANGELOG.md) — 변경 이력 · [CONTRIBUTING.md](CONTRIBUTING.md) — 기여 가이드

## 트러블슈팅
| 증상 | 원인 | 해결 |
|---|---|---|
| `command not found: geryon` | 설치 경로 미등록 | `uv tool install .` 재실행, 또는 `python -m geryon.cli ...` 로 실행 |
| `command not found: uv` | uv 미설치 | 위 "사전 준비" 참고, 또는 `pip install -e .` 사용 |
| bootstrap 이 오래 걸림 | 최초 1회 모델 다운로드(수백 MB) | 정상. 이후엔 캐시로 빠름(오프라인 동작) |
| 검색 결과가 비어 있음 | 아직 색인 전 | `geryon demo` 로 동작 확인 → 실데이터는 `geryon sync --all` |
| `Confluence 자격증명을 찾지 못했습니다` | `.env` 미설정 | "Atlassian API 토큰 발급" 참고해 `.env` 채우기 |
| health 가 "원본 데이터 없음" 경고 | 수집(ingest) 전 | 정상. 색인하면 사라짐 |
| 메모리 부족 | 16GB 미만 | `GERYON_RERANK_QUANTIZE=1`(기본) 유지, `GERYON_RERANK_POOL` 낮추기 |

## 제약·철학
무비용·오프라인·CPU/16GB는 타협 불가 원칙입니다. 약점은 무거운 보완책으로 덮지 않고 직접 고치며, 모든 결정을 측정으로 내립니다.

## License
[MIT](LICENSE)
