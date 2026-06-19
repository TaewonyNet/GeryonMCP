# 데이터 파이프라인 — 수집(acquire) → 적재(ingest)

GeryonMCP는 원본을 **Bronze 파일로 보존**하고, Bronze에서 **검색 DB(Silver)** 를 만듭니다.

```
외부 소스 ─acquire→ Bronze 원본파일 ─ingest→ 검색 DB(Silver) ─serve→ MCP 검색
 (네트워크)         (로컬 파일)        (로컬, 빠름)
```

**왜 분리하나**: 검색·정규화 로직을 바꿔도 `ingest`만 다시 돌리면 됩니다 — 외부 API 재호출 0, 완전 오프라인. 원본이 보존돼 감사·디버깅도 됩니다.

---

## 데이터 경로 지정 — "무엇을 쓸지"

| 대상 | 지정 방법 | 기본값 |
|---|---|---|
| **Bronze 원본** | `acquire --bronze-dir <dir>` / `ingest --path <dir>` | `bronze/confluence/` |
| **검색 DB(Silver)** | 환경변수 `GERYON_DB=<path>` | `~/.geryon/geryon.db` |

프로젝트마다 DB를 분리하려면 `GERYON_DB`를 다르게 줍니다:
```bash
GERYON_DB=./proj.db geryon ingest --source confluence --path ./bronze/confluence
GERYON_DB=./proj.db geryon serve          # 같은 DB 로 검색
```

---

## 1) acquire — 외부 → Bronze (멱등 · 증분)

```bash
geryon acquire --source confluence                       # 기본: DB 워터마크 이후(증분). DB가 비었으면 최근 30일
geryon acquire --source confluence --days 7              # 최근 7일
geryon acquire --source confluence --since 2026-01-01    # 2026-01-01 이후 수정분(상한 없음)
geryon acquire --source confluence --since 2026-01-01 --until 2026-03-31  # 날짜 구간 수집
geryon acquire --source confluence --all                 # 전체(날짜 제한 없음)
geryon acquire --source confluence --bronze-dir ./bronze/confluence   # 출력 경로 지정
```

| 옵션 | 의미 |
|---|---|
| `--days N` | 현 시점부터 최근 N일 수정분. **미지정 시 기본 = DB 워터마크 이후**(증분), DB가 비었으면 30일 |
| `--since YYYY-MM-DD` | 그 날짜 이후 수정분만(지정 시 `--days` 무시) |
| `--until YYYY-MM-DD` | 구간 상한(기본: 오늘). `--since` 와 함께 **날짜 구간** 수집 |
| `--all` | 전체 수집(`--days`/`--since` 무시) |
| `--bronze-dir <dir>` | Bronze 출력 경로 |
| `--force` | 기존 Bronze 무시하고 강제 재수집 |
| `--max-pages N` | 최대 N건(테스트용) |
| `--attachments` | 첨부도 다운로드(기본은 미수집 — 첨부 본문은 색인되지 않음) |
| `--dry-run` | 기록 없이 대상만 출력 |

> 수집 범위 우선순위: `--all`(전체) → `--since`/`--until`(날짜 구간) → `--days N`(명시한 최근 N일) → **(아무것도 없으면) DB 워터마크 이후 = 기본 증분** → 워터마크도 없으면(첫 실행) 30일. `lastmodified`(최종 수정일) 기준이며 날짜는 `YYYY-MM-DD` 형식이어야 합니다(잘못된 형식·역전 구간은 실행 전 거부).

**첨부는 기본 미수집**: 첨부 본문은 색인되지 않으므로(검색은 본문만 사용) 기본은 받지 않습니다 — 수집 시간·404 노이즈 절감. **`--attachments`** 를 줄 때만 다운로드합니다.

**첨부 스킵 필터**(`--attachments` 로 받을 때만 의미): 무겁거나 무의미한 첨부는 자동으로 건너뜁니다.
- `GERYON_ATTACH_MAX_MB`(기본 50): 이 크기 초과 첨부 skip — 메타 `fileSize` + 응답 `Content-Length` **양쪽** 검사(메타 누락 대비).
- `GERYON_ATTACH_SKIP_EXT`(콤마): 차단 확장자. 기본 표준 목록 —
  · 압축·아카이브 `zip 7z rar tar gz tgz bz2 xz` · 동영상 `mp4 mov avi mkv wmv flv webm` · 오디오 `mp3 wav flac m4a aac` · 디스크 이미지·실행 `iso dmg exe msi dll bin apk`

- **멱등**: 같은 `version`이면 skip(다시 받지 않음). **증분**: `--days`/`--since`로 변경분만.
- **자격증명**: **환경변수 전용** — `CONFLUENCE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_API_TOKEN`. `.env`(또는 셸 export)로 제어하며, `.env` 는 실행 시 자동 로드됩니다.

---

## 2) ingest — Bronze → 검색 DB (**기본 증분**)

```bash
geryon ingest --source confluence --path ./bronze/confluence        # 기본: 증분(수정된 것만)
GERYON_DB=./proj.db geryon ingest --source git --path ./my-repo --no-vector
```

**기본이 증분입니다** — acquire 가 Bronze `manifest.json` 의 `last_change`(added/modified/deleted)에 기록한 **변경분만** 처리합니다.
소스(confluence·git·jira)에 무관하게 manifest 하나로 증분합니다(28922개 중 71개만 바뀌면 71개만 처리).

| 옵션 | 의미 |
|---|---|
| (기본) | **증분** — Bronze `manifest.last_change` 의 변경분만(소스 공통) |
| `--full` | 전체 재처리 + prune + 품질신호(static_score) 재계산 + Safety Gate 우회 |
| `--no-prune` | (full에서) Bronze에 없는 DB 문서 삭제 안 함 |
| `--no-vector` | 임베딩 생략(키워드+rerank만, 빠른 색인) |
| `--path <dir>` | Bronze 데이터 경로 |

### 최초 실행 / 결과 가이드
- **최초 실행**은 기준 시각이 없어 자동으로 **전체 색인**하고, 그 시각을 저장해 다음부터 증분이 됩니다(별도 설정 불필요).
- 실행하면 결과 가이드가 출력됩니다 — 예:
  ```
  ⓘ 최초 실행 — 기준 시각이 없어 전체를 색인했습니다(다음부터 자동 증분).
  ✓ 증분 갱신: 신규 12 · 변경(덮어쓰기) 59
    소요: 05:31:46 → 05:31:53
    다음: `geryon serve`
  ```
  변경이 없으면 `✓ 이미 최신 상태입니다`가 출력됩니다.

### 언제 `--full` 인가
- **증분(기본)**: 평소 갱신 — 빠름. 단 품질신호(backlink·static_score, 검색 랭킹)는 갱신 안 함.
- **`--full`**: 품질신호 재계산·삭제 정리(prune)가 필요할 때 가끔. confluence·git·jira **모두 manifest 기반 증분**(git 도 GitAcquirer 가 `git diff/log` 로 변경분 기록).

**Bronze가 진실(source of truth)** — `--full`의 prune은 Bronze에 없는 문서를 DB에서 제거해 일치시킵니다.
**Safety Gate**: 본 문서가 0이거나 이전의 절반 이하로 급감하면 **중단**(실수 대량 삭제 방지). 의도적 대량 변경은 `--full`로 우회.

### Bronze를 일부 지우면?
| 한 일 | 결과 |
|---|---|
| Bronze 일부 삭제 + `ingest 안 함` | DB 그대로(검색 OK). 단 그 문서 재색인 불가 |
| Bronze 일부 삭제 + `ingest`(기본 prune) | DB에서도 삭제 → 검색에서 빠짐 |
| Bronze 일부 삭제 + `ingest --no-prune` | DB 유지(삭제 안 함) |
| `acquire --all` 다시 | Bronze 복구 → 원상복구 |
| `acquire --days`(증분) 다시 | 최근 변경 아니면 **복구 안 됨** |

→ 디스크 절약 등으로 Bronze를 줄였다면, **`acquire --all`로 언제든 복구**됩니다. DB를 건드리고 싶지 않으면 `--no-prune`.

### DB만 공유받아 Bronze가 없을 때 갱신 — `--since-db`
멱등성의 진짜 기준은 Bronze가 아니라 **DB 안의 `doc_id` + `content_hash`**(`upsert` 가 동일 해시면 skip). 그래서 DB만 받아도 갱신이 안전합니다.
```bash
geryon sync --source confluence --since-db    # DB의 MAX(updated_at) 이후 수정분만 수집·색인
```
- `--since-db` 는 DB에 기록된 **최신 수정시각 이후**만 소스에서 가져옵니다(Bronze 없이 증분). 풀재수집 불필요.
- 경계 날짜의 문서는 다시 받아도 **content_hash 가 같으면 skip** → 멱등.
- 검색만 할 소비자는 갱신 자체가 불필요(서버만 띄우면 됨). 갱신은 보통 **업데이터 1명**이 담당.

---

## 3) 새 데이터 받기 = `sync` (또는 acquire + ingest)

평소 갱신은 **`sync` 한 번**(acquire 후 ingest, acquire 실패 시 ingest 안 함):
```bash
GERYON_DB=./proj.db geryon sync --source confluence --days 7
GERYON_DB=./proj.db geryon sync --source git --repo <url> --no-vector
```
따로 돌리려면:
```bash
geryon acquire --source confluence --days 7
GERYON_DB=./proj.db geryon ingest --source confluence --path ./bronze/confluence
```
검색 로직만 바꿔 **재색인할 때는 `ingest`만**(API 재호출 0, 오프라인):
```bash
GERYON_DB=./proj.db geryon ingest --source confluence --path ./bronze/confluence
```

---

## Git 소스 — acquire 지원

원격 저장소를 **자동 수집(clone/pull)** 합니다(멱등/증분):
```bash
geryon acquire --source git --repo <url> [--repo <url2> ...] [--depth N] [--branch main]
geryon ingest  --source git --path bronze/repos            # Bronze(repos/) → DB
# 또는 한 번에:
geryon sync    --source git --repo <url> --no-vector
```

| 옵션 | 의미 |
|---|---|
| `--repo <url>` | 원격 저장소(여러 번 지정 가능) |
| `--bronze-dir` | clone 위치(기본 `bronze/repos/`) |
| `--depth N` | shallow clone(미지정=full; 커밋 히스토리 색인엔 full 권장) |
| `--branch` | 브랜치 |

- **멱등/증분**: 없으면 `git clone`, 있으면 `git fetch + reset --hard`(변경분만, 재실행 안전).
- **Bronze**: `bronze/repos/<repo>/`(작업트리). `GERYON_GIT_CODE/COMMITS/MAX_COMMITS/MAX_BYTES` 로 색인 범위 조절([EXTENDING_SOURCES.md](EXTENDING_SOURCES.md)).
- 코드·커밋·문서가 출처(repo·파일·커밋 URL)와 함께 검색됩니다.
