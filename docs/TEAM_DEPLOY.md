# 팀 배포 — 프로젝트에 검색 MCP 붙이기

특정 프로젝트(예: 사내 repo)에 GeryonMCP를 MCP로 등록해, **팀원이 클론하면 바로 검색**하게 하는 방법입니다.
프로젝트별로 **독립 인덱스 DB**를 쓰므로 다른 프로젝트와 섞이지 않습니다.

## 핵심 아이디어
- **프로젝트별 DB**: `GERYON_DB` 환경변수로 인덱스를 프로젝트마다 분리.
- **그 repo를 색인**: `geryon ingest --source git --path <repo>` (코드·커밋·문서).
- **MCP 설정을 repo에 커밋**: 팀원의 MCP 클라이언트가 자동 인식.

## 1) 프로젝트에 MCP 설정 추가
프로젝트 루트에 클라이언트별 설정 파일을 둡니다(팀 공유 위해 커밋).

**Cursor** — `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "myproj-search": {
      "command": "geryon",
      "args": ["serve"],
      "env": { "GERYON_DB": "${workspaceFolder}/.geryon/myproj.db" }
    }
  }
}
```
**Claude Code** — `.mcp.json`(루트, 팀 공유) / **VS Code** — `.vscode/mcp.json` 도 같은 형식.

> `GERYON_DB` 로 이 프로젝트 전용 인덱스를 가리킵니다. `${workspaceFolder}` 가 안 되는 클라이언트는 절대경로/홈경로(`~/.geryon/myproj.db`)로.

## 2) 온보딩 — uv + `geryon setup` (맥/우분투/윈도우 공통)
팀원 OS 가 섞여 있으면(맥·우분투·윈도우) **bash 셸 스크립트(`.sh`)는 윈도우 native 에서 안 돕니다**.
대신 **uv(크로스 플랫폼 단일 설치) + `geryon setup`(Python CLI)** 조합을 씁니다 — `curl`/`Invoke-WebRequest`/WSL 분기 없이 3 OS 동일.

```bash
# 1) uv 설치 (한 번만) — 3 OS 동일 효과
#    맥/우분투:  curl -LsSf https://astral.sh/uv/install.sh | sh
#    윈도우(PowerShell):  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2) GeryonMCP 설치 — 어느 OS든 동일
uv tool install "git+https://github.com/<org>/GeryonMCP.git"

# 3) 온보딩 — 셸 불필요, geryon CLI 가 OS 무관 처리
geryon setup                       # 모델 캐시(최초 1회)
#   ↳ 방식 B(공유 DB 받기): geryon setup --db-url "<사내 DB URL>" --db-path .geryon/myproj.db
```
> `geryon setup` 은 모델 준비 + (옵션) 공유 DB 다운로드를 **표준 라이브러리만으로** 수행합니다(원자적 교체로 부분 다운로드 방지). 셸·curl 없이 윈도우에서도 그대로 동작.

`.gitignore` 에 `.geryon/` 추가(인덱스는 각자 로컬 생성).

## 3) 팀원 사용 흐름
```bash
git clone <repo> && cd <repo>
uv tool install "git+<oss-repo>"   # 최초 1회 (이미 설치돼 있으면 생략)
geryon setup --db-url "<DB URL>" --db-path .geryon/myproj.db   # 방식 B: 색인 생략
#   또는 방식 A: GERYON_DB=.geryon/myproj.db geryon ingest --source git --path .
# 에디터(Cursor/Claude/VS Code) 재시작 → MCP 도구 search·advanced_search 사용
```
질의 예: "정책 평가 코드 어디 있어", "PROJ-xxxx 커밋", "작성자가 X인 변경" — 코드·커밋·문서가 출처(repo·파일·커밋 URL)와 함께 검색됩니다.

## 갱신 (코드/커밋이 바뀌면)
```bash
git pull
GERYON_DB=.geryon/myproj.db geryon ingest --source git --path "$(pwd)"   # 증분
```
cron/CI로 주기 재색인하면 항상 최신. (자동 수집 acquire는 향후)

## 배포 방식 두 가지
같은 `mcp.json`(repo 커밋)을 쓰되, 인덱스를 만드는 방법만 다릅니다.

> **팀 공유 모노레포라면(팀원이 이미 그 repo를 clone) DB·원본 전달이 필요 없습니다** — clone이 곧 원본(Bronze)이고, 각자 `ingest`로 색인하면 됩니다(방식 A). 전달할 건 `mcp.json`·`setup-search.sh`(텍스트)뿐. 방식 B(DB 전달)는 원본이 팀원에게 없거나 색인 시간을 아끼려는 경우의 선택지입니다.

### 방식 A — 각자 색인 (항상 최신)
팀원이 `setup-search.sh` 로 직접 `geryon ingest`. 변경 즉시 반영, 단 각자 색인 시간.

### 방식 B — uv 설치 + 공유 DB 전달 (PoC·빠른 시작)
한 명(또는 CI)이 색인한 **DB 파일만 전달**하면 팀원은 색인을 건너뜁니다(검증: DB 복사→`ingest` 없이 검색 동작).
- **코드**: `uv tool install "git+<oss-repo>"` (각자, 3 OS 동일)
- **설정**: `mcp.json` 은 repo에 커밋(텍스트, git 공유)
- **DB**: 바이너리·갱신 잦음 → **사내 스토리지/아티팩트로 전달**(git repo·LFS보다 권장). `geryon setup` 이 다운로드.
```bash
# 방식 B — 셸 스크립트 없이 geryon CLI 한 줄 (맥/우분투/윈도우 공통)
uv tool install "git+<oss-repo>"
geryon setup --db-url "<사내 DB 다운로드 URL>" --db-path .geryon/myproj.db
#   ↳ 모델 캐시 + DB 다운로드(원자적 교체) 를 OS 무관하게 한 번에. curl/Invoke-WebRequest 불필요.
#   ↳ 인증 필요한 서버(예: Jupyter `/files/...` + 토큰)면 실행 중 토큰/비밀번호를 입력받음.
#      비대화형(CI)은  --token <값>  또는 환경변수  GERYON_DB_TOKEN.
#      ※ Jupyter 다운로드 URL 은 트리(/tree/...)가 아니라 파일(/files/...) 엔드포인트.
```
`mcp.json` 의 `GERYON_DB` 를 같은 경로(`.geryon/myproj.db` 또는 `~/.geryon/myproj.db`)로 맞춥니다.
- **갱신/배포(`geryon publish`)**: 색인 담당이 한 줄로 올립니다(setup 의 대칭).
  ```bash
  # 원격(scp) 또는 공유 마운트 경로로 업로드(기존 덮어쓰기) + 본인 MCP 등록까지
  geryon publish --to user@host:/srv/index/myproj.db --mcp myproj-search --test
  geryon publish --to /mnt/team-share/myproj.db                  # 파일/마운트 경로면 복사
  ```
  → 팀원은 `geryon setup --db-url ...`(또는 같은 공유 경로)로 최신 DB 재다운로드.
  `--to` 가 `user@host:path` 면 scp, 로컬/마운트 경로면 복사(덮어쓰기). `--mcp` 로 올린 본인도 즉시 검색.
- **나중에 방식 A로 전환**: 같은 `mcp.json` 그대로, 팀원이 `geryon ingest` 로 직접 갱신하면 됨.

## 운영 팁
- **프로젝트별 분리**: 프로젝트마다 다른 `GERYON_DB` → 인덱스·검색 격리.
- **여러 소스 한 DB**: 같은 `GERYON_DB` 로 Confluence·Jira·여러 repo를 색인하면 통합 검색(출처 `source`·`space_or_repo` 로 구분, `sources` 필터로 좁히기).
- **무게**: 큰 repo는 `GERYON_GIT_COMMITS=0`(커밋 제외)·`GERYON_GIT_MAX_COMMITS`·`GERYON_GIT_MAX_BYTES` 로 조절.
- **공유 인덱스**(선택): 한 명이 만든 `*.db` 를 배포해 팀이 읽기만 할 수도 있으나, 각자 로컬 색인이 단순·안전합니다.

## 공유 단위 — `geryon.db` 한 파일

색인·검색에 필요한 모든 것이 **단일 `geryon.db`** 안에 있습니다 — 본문(`documents`)·키워드 색인(FTS5)·**벡터(sqlite-vec)**·청크·첨부 메타·카테고리·태그까지 한 파일입니다(별도 벡터/트리 파일 없음). 따라서 팀에 전달할 것은 **이 파일 하나**뿐입니다.

- **멀티소스도 한 파일**: 같은 `GERYON_DB` 로 Confluence·Git·Jira 를 모두 ingest 하면 한 `geryon.db` 에 통합됩니다(`documents.source` 로 구분, status 의 `by_source` 가 이 구분). 검색은 통합되고 `sources` 필터로 좁히기 — **전송은 여전히 1개**.
  ```bash
  GERYON_DB=/data/mcp/geryon.db geryon sync --all                          # confluence
  GERYON_DB=/data/mcp/geryon.db geryon ingest --source git  --path <repo>  # git 추가
  GERYON_DB=/data/mcp/geryon.db geryon sync   --source jira --project <키>  # jira 추가
  ```
- **federation(여러 파일)**: 소스별로 따로 배포·갱신해야 할 때만 `GERYON_DB="a.db,b.db"` 콤마로 묶어 검색합니다(품질은 통합 rerank 로 동일). 이때만 전송 파일이 소스 수만큼 늘어납니다.
- **전송 시**: `geryon` 종료 후(WAL 체크포인트로 본 파일에 통합) 전달하거나 `geryon publish --to <경로>`(이 처리 포함, 원자적 교체). 모델은 DB 가 아니므로 수신자가 `geryon bootstrap` 으로 받습니다.

## 코드 ↔ 데이터 분리 (보안)

`.gitignore` 와 데이터 공유는 **서로 다른 일**입니다 — `.gitignore` 는 공유 수단이 아니라 **유출 가드**입니다.

| 목적 | 수단 | 대상 |
|---|---|---|
| 코드 공개 + 데이터 유출 방지 | `.gitignore` (git) | `*.db`·`bronze/`·토큰을 repo 에서 제외 |
| 팀에 데이터 공유 | 공유 마운트 / `publish` (git 아님) | `geryon.db` 파일 전달 |

- **데이터는 git 에 올리지 않습니다** — 1GB 급 DB·대용량 Bronze 는 git 에 부적합(LFS 도 비권장). `.gitignore` 가 코드 repo 에 데이터·시크릿이 섞여 외부로 나가지 않게 막습니다.
- **데이터 공유는 git 밖 채널**(사내 공유 마운트·스토리지)로 합니다. 팀이 동일 권한(예: 같은 Confluence 접근)이면 공유 마운트의 `geryon.db` 를 팀이 함께 `GERYON_DB` 로 가리키는 게 가장 단순합니다(SQLite 다중 읽기 동시성, 갱신은 색인 담당이 `publish` 로 원자적 교체).
- **경계**: 팀 내는 자유(동일 권한이라 추가 노출 0), **팀 밖(공개 repo·외부 스토리지)으로 `geryon.db` 가 나가지 않게** — 코드는 공개 OK, 데이터는 사내 전용.
