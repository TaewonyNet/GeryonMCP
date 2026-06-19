# 새 데이터 소스 추가하기 (멀티소스 확장)

GeryonMCP는 **Connector 플러그인**으로 소스를 확장합니다. Confluence·Jira 외에
GitHub·GitLab·웹·PDF 등을 같은 패턴으로 붙일 수 있습니다. 핵심은 **커넥터가 소스별 데이터를
표준 `RawRecord` 로 매핑**하면, 그 뒤(정제·색인·검색·MCP 도구)는 **소스 무관**으로 동작한다는 점입니다.

```
소스별  ─[Connector]→  표준 RawRecord  ─[Normalizer]→  표준 Document  →  색인·검색
(Jira/GitHub/…)         (이것만 만들면 됨)              (소스 무관, 공통)
```

## 4단계

### 1) SourceType 확인/추가
`src/geryon/domain/models.py` 의 `SourceType` 에 값이 있는지 확인합니다.
이미 `confluence·web·jira·gitlab·github` 가 정의돼 있습니다. 없으면 추가합니다.

### 2) 커넥터 구현 — `src/geryon/connectors/<source>.py`
`Connector` 를 상속해 둘만 구현합니다:
```python
class MyConnector(Connector):
    source_type = SourceType.GITHUB
    def healthcheck(self) -> bool: ...           # 소스 접근 가능?
    def iter_raw(self) -> Iterator[RawRecord]:   # 레코드를 yield
        yield RawRecord(
            source=SourceType.GITHUB,
            source_id="<고유 id>",
            raw_body="<본문>",
            raw_format="markdown",   # html/storage 면 자동 md 변환, markdown/text 면 그대로
            title="<제목>",
            url="<링크>",
            space_or_repo="<저장소/공간>",
            metadata={               # ★ 표준 키만 채우면 Normalizer 가 알아서 처리
                "author": "...", "created_at": "ISO", "updated_at": "ISO",
                "tags": [...], "doc_type": "issue|spec|guide|...",
            },
        )
```

### 3) 레지스트리 등록 — `src/geryon/pipeline/ingest.py`
```python
CONNECTOR_REGISTRY = {
    "confluence": ConfluenceConnector,
    "jira": JiraConnector,
    "github": MyConnector,   # 추가
}
```

### 4) 끝 — 색인·검색
```bash
geryon ingest --source github
```
Normalizer·FTS·재정렬·`search`/`advanced_search` 가 그대로 적용됩니다. **검색 코드 변경 0.**

## 출처(provenance) — 결과가 어디서 왔는지
검색 결과마다 **`source`(confluence/jira/github/gitlab/bitbucket) · `space_or_repo`(스페이스/저장소) · `url`(원본 링크)** 가 함께 반환된다. Git은 파일→`/blob/<branch>/<path>`, 커밋→`/commit/<hash>`, Jira는 `/browse/<KEY>` 로 원본 위치를 가리킨다. `sources`·`spaces` 필터로 특정 출처만 검색할 수도 있다.

## 표준 metadata 키 (커넥터가 채우는 값)
| 키 | 용도 |
|---|---|
| `author` | 작성자(노이즈 정규화 자동 적용) |
| `created_at` / `updated_at` | ISO8601 날짜(신선도·필터) |
| `tags` | 라벨/태그 목록 |
| `category` / `hierarchy` | 분류·계층(없으면 hierarchy→category 자동) |
| `doc_type` | `issue`·`spec`·`guide`… (없으면 제목/계층으로 자동 분류) |

## 예시: Jira (`connectors/jira.py`)
- **Bronze 레이아웃**: `~/.geryon/jira_db/{project}/{ISSUE-KEY}.json` (Jira REST 이슈 응답 그대로)
- **매핑**: `summary→title`, `description→body`(ADF/평문 모두 처리), `reporter→author`, `labels→tags`, `project→space_or_repo`, `doc_type=issue`
- **사용**: `geryon ingest --source jira`

## 예시: Git 저장소 (`connectors/git_repo.py`)
- **Bitbucket·GitLab·GitHub 통합** — 모두 Git이라 **하나의 커넥터**로. `.git/config` remote URL로 호스트 자동 판별.
- **일반 문서 검색과 다름** — 문서뿐 아니라 **소스 코드·커밋 히스토리(메세지)** 까지 색인(`doc_type=doc|code|commit`):
  | 대상 | 내용 | 옵션(기본) |
  |---|---|---|
  | 문서 | `*.md·*.rst·*.txt` → 본문 | 항상 |
  | **소스 코드** | `*.py·*.sql·*.yaml·*.js·…` → 파일 내용 | `include_code`(True) |
  | **커밋 메세지/히스토리** | `git log` → subject+body(이슈키 등) | `include_commits`(True) |
- **Bronze**: `~/.geryon/repos/<repo>/` (`git clone` 된 작업 트리). 호스트→source, 첫 heading/경로→title, `git log`→author·수정일, repo명→space.
- **사용**:
  ```bash
  git clone <repo-url> ~/.geryon/repos/<name>
  geryon ingest --source git     # 코드+커밋 포함. 끄려면 GERYON_GIT_CODE=0 / GERYON_GIT_COMMITS=0
  ```
  자격증명 불필요(클론된 트리만 읽음). 환경변수: `GERYON_GIT_MAX_COMMITS`(기본 2000)·`GERYON_GIT_MAX_BYTES`(파일 본문 상한).
- **실증**: 실제 코드 repo(1818건 = code 1452·commit 300·doc 66) 색인 시 — 이슈키→커밋, 함수·테이블명→소스 파일, 키워드→문서·커밋이 모두 검색되고 source는 remote URL로 자동 판별(bitbucket/gitlab/github).

## 자동 수집(acquire)은 향후
Confluence는 `acquire/confluence_atlassian.py` 가 REST로 Bronze(`bronze/confluence/`)를 채웁니다.
새 소스도 **동일 패턴으로 `acquire/<source>_*.py` 를 추가**하면 수집까지 자동화됩니다. 그 전엔
소스의 export(JSON 등)를 Bronze 레이아웃에 두고 `ingest` 하면 됩니다.
