# GeryonMCP 사용 매뉴얼

내 컴퓨터에서 무료·오프라인으로 도는 문서 검색 서버의 설치부터 운영까지 전체 안내입니다.
빠른 시작은 [README](../README.md)를, 이 문서는 단계별 상세 설명을 봅니다.

---

## 1. 요구사항
- **Python 3.10 이상**
- **메모리 16GB 이내** 권장 (CPU 전용, GPU 불필요)
- 디스크 여유 ~2GB (모델·인덱스)
- 인터넷은 **최초 설치(모델 다운로드)와 데이터 수집 때만** 필요. 검색은 완전 오프라인.

## 2. 설치

### 2.1 Python·uv 준비
```bash
python3 --version            # 3.10 이상인지 확인
# uv(빠른 설치 도구) 설치 — 택1
curl -LsSf https://astral.sh/uv/install.sh | sh      # macOS/Linux
# Windows PowerShell: irm https://astral.sh/uv/install.ps1 | iex
# 또는: pip install uv
```

### 2.2 GeryonMCP 설치
```bash
uv tool install .            # 또는 pip 사용 시: pip install -e .
```

### 2.3 부트스트랩 (최초 1회)
```bash
geryon bootstrap
```
- 검색·재정렬 **모델을 자동 다운로드**(수백 MB, 1회만)
- 설정 파일 `.env`이 없으면 **샘플(`.env.sample`)에서 생성**(기존 파일은 보존). 자격증명은 **env 전용**
- 완료 후엔 모델이 캐시되어 오프라인으로 동작

### 2.4 별도 설정 없이 바로 사용 — 기본값으로 충분
**설치(2.2) → 부트스트랩(2.3) → (실데이터면) 자격증명(4.2)** 이 전부입니다. 검색 동작을 위해 더 입력할 것은 없습니다.

- **사용자 사전도 선택입니다.** `~/.geryon/gold/dictionary.yaml`(동의어 사전)이 **없어도 검색은 동일하게 동작**합니다(없으면 동의어 확장만 생략). 음차·약어가 자주 안 잡힐 때 **나중에** 추가하면 됩니다 — 처음엔 만들 필요 없습니다.
- **자동 유의어 사전은 기본 OFF.** 데이터에 따라 정확도를 떨어뜨릴 수 있어, 내 코퍼스가 적합한지 확인 후 `GERYON_DICT_AUTO=1` 로 켜는 opt-in 입니다([동의어 가이드](GOLDEN_AND_DICTIONARY.md)).
- **튜닝 불필요.** 재정렬·후보 수·양자화·첨부 필터 등 모든 설정은 **합리적 기본값**을 가집니다(필수 환경변수 없음). 바꾸고 싶을 때만 §6.3 참고.

> 검증됨: 사용자 사전이 **31개일 때와 0개(아예 없음)일 때 검색 결과가 동일**했고, `geryon demo` 는 자격증명·DB·사전 없이도 정상 동작합니다. 즉 **기본값만으로 바로 사용 가능**합니다.

## 3. 자격증명 없이 체험 (데모)
```bash
geryon demo
```
가상 문서 6건으로 검색을 시연합니다. 예:
```
질의: '무중단 배포 절차'  →  서버 배포 가이드, 데이터 백업 절차
질의: '휴가 신청'         →  휴가 정책
```
여기까지 동작하면 설치는 정상입니다.

## 4. 실제 위키 연동

### 4.1 Atlassian API 토큰 발급
1. **https://id.atlassian.com/manage-profile/security/api-tokens** 접속
2. **Create API token** → 이름 입력 → 생성된 토큰 **복사**(다시 표시되지 않음)

### 4.2 자격증명·수집 소스 설정 — `geryon init` (권장)
**Confluence → Jira → Git** 순으로 안내합니다. 대화형에서는 **토큰을 확인한 뒤 스페이스/프로젝트/저장소 목록을 보여주고 번호로 선택**합니다(Confluence 스페이스·Jira 프로젝트·Git 호스트 GitHub/GitLab/Bitbucket 저장소). 결과는 `.env` 에 기록됩니다.
```bash
geryon init                                   # 대화형(질문에 답): URL/토큰/스페이스/프로젝트/repo
# 또는 플래그로 한 번에:
geryon init --confluence-url https://<도메인>.atlassian.net/wiki \
            --confluence-user <이메일> --confluence-token <토큰> \
            --space ENG --space HR --jira-project TDT --git-repo https://github.com/org/repo
```
> 자격증명은 **env 전용**입니다. `geryon init` 없이 `.env` 를 직접 채워도 됩니다:
> ```
> CONFLUENCE_URL=https://<회사도메인>.atlassian.net/wiki
> CONFLUENCE_USERNAME=<로그인 이메일>
> CONFLUENCE_API_TOKEN=<복사한 토큰>
> GERYON_CONFLUENCE_SPACES=ENG,HR        # (선택) 비우면 전체
> GERYON_JIRA_PROJECTS=TDT               # (선택) Jira
> GERYON_GIT_REPOS=https://github.com/org/repo   # (선택) Git
> ```

| 소스 | 가져오는 법 | 인증 |
|---|---|---|
| Confluence | REST API + CQL(스페이스 필터) | `CONFLUENCE_*`(env) |
| Jira | REST API + JQL | `JIRA_*`(없으면 `CONFLUENCE_*` 재사용) |
| GitHub/GitLab/Bitbucket | `git clone`/`fetch` | **사용자 git 인증**(SSH/credential). 없으면 `GERYON_GIT_TOKEN`(+`GERYON_GIT_USERNAME`) |

#### Git 소스 연결 — **SSH 권장(가장 안정적)**
수집(clone)은 REST API가 아니라 **git 인증**으로 됩니다. 그래서 토큰 만료·조직 정책의 영향을 안 받는 **SSH가 정답**입니다(예: Bitbucket 앱 비밀번호는 2026-07 폐지·Atlassian API 토큰은 조직이 Bitbucket API 인증을 막는 경우가 많음).
1. **SSH 키 1회 등록**:
   ```bash
   ls ~/.ssh/id_ed25519.pub 2>/dev/null || ssh-keygen -t ed25519 -C "you@example.com"
   cat ~/.ssh/id_ed25519.pub      # → 호스트(GitHub/GitLab/Bitbucket) 설정 → SSH keys 에 등록
   ssh -T git@bitbucket.org       # "logged in/authenticated" 나오면 성공
   ```
2. **SSH URL 로 수집**(토큰 불필요):
   ```bash
   geryon sync --source git --repo git@bitbucket.org:<workspace>/<repo>.git
   # 또는 .env: GERYON_GIT_REPOS=git@github.com:org/repo.git,git@bitbucket.org:ws/repo.git
   ```
- **저장소 자동 목록(선택)**: `geryon init` 에서 호스트 토큰을 넣으면 목록을 보여주고 고를 수 있습니다(GitHub/GitLab=토큰, Bitbucket=스코프 API토큰/액세스토큰). **단 조직이 API 토큰을 막으면(특히 Bitbucket) 목록조회는 안 되며, 그땐 SSH + 직접 URL** 로 진행하면 됩니다(clone 은 그대로 동작).
- `Permission denied (publickey)` → SSH 공개키가 호스트에 미등록이거나 `ssh-add` 안 된 상태. 위 1번 재확인.

### 4.3 수집·색인
```bash
geryon sync --source confluence    # Confluence(설정한 스페이스/전체) 수집 → 색인
geryon sync --source git           # GERYON_GIT_REPOS(또는 --repo) 저장소
geryon sync --source jira          # GERYON_JIRA_PROJECTS(또는 --project) 프로젝트
geryon sync --source confluence --all   # 날짜 제한 없이 전체
```
문서 양에 따라 시간이 걸립니다. 한 번 색인하면 이후 검색은 오프라인.

## 5. MCP 클라이언트에서 사용

### 5.1 등록 (예: Claude Desktop)
설정 파일(`Settings → Developer → Edit Config`)에 추가:
```json
{
  "mcpServers": {
    "geryon": { "command": "geryon", "args": ["serve"] }
  }
}
```
저장 후 클라이언트를 재시작하면 검색 도구가 나타납니다.

### 5.2 검색 도구
| 도구 | 용도 | 예시(자연어로 요청하면 클라이언트가 호출) |
|---|---|---|
| `search` | 통합 검색 | "배포 가이드 찾아줘" |
| `advanced_search` | 메타 필드별 상세검색 | "홍길동이 2024년에 쓴 배포 문서" → author·date·본문 분리 |
| `get_document` | 문서 전문 | "그 문서 전체 보여줘" |
| `get_related` | 연관 문서 | "이거랑 관련된 문서" |
| `browse` | 공간/계층 목록 | "ENG 공간 목록" |
| `list_sources` | 색인된 소스/공간 목록 | "어떤 공간이 색인돼 있어?" |
| `reindex` | 재색인 트리거 | "최신 변경 반영해줘" |

`advanced_search`는 제목·작성자·공간·태그·날짜를 **독립 필드로 결합**하며, 본문 없이 메타만으로도(작성자=X 전체) 검색합니다. 같은 검색을 **CLI로** 쓰려면 `geryon search "<질의>"`(§6.1) — MCP 도구와 동일 결과입니다.

## 6. 운영

### 6.1 상태·점검
```bash
geryon status                # 문서/청크/인덱스 수 JSON
geryon health                # 시스템 정상 여부
geryon search "배포 절차" -k 5  # 터미널에서 직접 검색(MCP 없이 색인 품질 확인). --json 도 지원
```
> `geryon search` 는 MCP `search` 도구와 **동일한 검색 코어**를 씁니다 — CLI 결과와 MCP 결과가 일치합니다(스크립트·디버깅·벤치용).

### 6.2 재색인·갱신
```bash
geryon sync --all          # 최신 변경 반영(증분)
geryon reindex --full        # 전체 재색인
```

### 6.3 주요 설정(.env)
| 변수 | 기본 | 설명 |
|---|---|---|
| `GERYON_RERANK_PASSAGE` | 1 | 본문중심 검색 강화(0=제목만·더 빠름) |
| `GERYON_RERANK_POOL` | 60 | 재정렬 후보 수(낮추면 빠르고 메모리↓) |
| `GERYON_RERANK_QUANTIZE` | 1 | int8 가속(0=fp32 정확도 우선) |
| `GERYON_RERANK_MODEL` | bge-reranker-base | 재정렬 모델 |
| `GERYON_ATTACH_MAX_MB` | 50 | 첨부 크기 상한(MB). 초과 시 다운로드 skip(메타 fileSize + 응답 Content-Length 양쪽 검사) |
| `GERYON_ATTACH_SKIP_EXT` | 압축·미디어·실행 | 차단 확장자(콤마). 기본: `zip,7z,rar,tar,gz,…`·`mp4,mov,…`·`mp3,wav,…`·`iso,exe,dmg,…` |
| `GERYON_DICT_AUTO` | 0(OFF) | 자동 유의어 사전(`dictionary.auto.yaml`) 로드. 기본 OFF — 내 데이터가 동의어 연결에 적합한지 골든으로 확인 후 `1`로 켠다(수동 `dictionary.yaml`은 항상 적용) |
| `GERYON_LOG_LEVEL` | INFO | 로그 상세도 |

## 7. 트러블슈팅
| 증상 | 원인 | 해결 |
|---|---|---|
| `command not found: geryon` | 설치 경로 미등록 | `uv tool install .` 재실행, 또는 `python -m geryon.cli ...` |
| `command not found: uv` | uv 미설치 | §2.1 참고, 또는 `pip install -e .` |
| bootstrap 오래 걸림 | 최초 모델 다운로드 | 정상(1회). 이후 캐시로 빠름 |
| 검색 결과 비어 있음 | 색인 전 | `geryon demo` 확인 → 실데이터는 `geryon sync --all` |
| `Confluence 자격증명을 찾지 못했습니다` | `.env` 미설정 | §4 참고해 `.env` 채우기 |
| health "원본 데이터 없음" 경고 | 수집 전 | 정상. 색인하면 사라짐 |
| 메모리 부족 | 16GB 미만 | `GERYON_RERANK_QUANTIZE=1` 유지, `GERYON_RERANK_POOL` 낮추기 |

## 8. 자주 묻는 질문
- **유료 AI나 인터넷이 필요한가요?** 아니요. 모델 다운로드와 수집 때만 인터넷을 쓰고, 검색은 완전 오프라인·무료입니다.
- **내 데이터가 외부로 나가나요?** 아니요. 검색·랭킹은 로컬 데이터만 사용합니다.
- **Confluence 외 다른 소스는?** 현재 Confluence 중심이며, 다른 커넥터는 로드맵입니다.
- **설정 파일을 덮어쓰나요?** 아니요. `geryon bootstrap`은 `.env` 가 **없을 때만** 현재 디렉터리에 내장 템플릿으로 생성하고 기존 값은 보존합니다.

## 9. 제거
```bash
uv tool uninstall geryonmcp  # 또는 pip uninstall geryonmcp
rm -rf ~/.geryon             # 인덱스·모델 캐시 삭제
```

---
더 알아보기: [설치~MCP 연결 단계별](INSTALL_WALKTHROUGH.md) · [데이터 파이프라인](DATA_PIPELINE.md) · [성능 튜닝](PERFORMANCE.md) · [초급 가이드](BEGINNER_GUIDE.md)
