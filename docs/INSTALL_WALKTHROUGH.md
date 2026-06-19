# 설치부터 MCP 연결까지 — 검증된 단계별 가이드

빈 환경에서 **설치 → 모델 준비 → 자격증명 → 최근 한 달 수집·색인 → MCP 클라이언트 연결**까지의 전 과정을 순서대로 안내합니다. 각 단계는 격리된 임시 환경에서 **end-to-end 실제 검증**된 흐름입니다(맨 아래 "검증 로그" 참고).

> 빠른 요약: 설치 → `bootstrap` → `.env` 자격증명 → `sync` → MCP 등록. **그 외 입력은 없습니다**(동의어 사전·튜닝 전부 선택). → [USER_MANUAL §2.4](USER_MANUAL.md)

---

## 0. 요구사항
- Python 3.10+ , 메모리 16GB 이내(CPU 전용), 디스크 ~2GB(모델·인덱스)
- 인터넷은 **모델 1회 다운로드 + 데이터 수집** 때만. 검색은 완전 오프라인.

## 1. 설치
**일반(권장)** — 시스템에 CLI로 설치:
```bash
uv tool install .          # 또는: pip install -e .
geryon --help              # 명령 확인
```
**격리 설치(테스트/병행)** — 별도 venv:
```bash
uv venv .venv && uv pip install --python .venv/bin/python -e .
.venv/bin/geryon --help
```

## 2. 부트스트랩 (최초 1회)
```bash
geryon bootstrap
```
- 검색·재정렬 **모델 자동 다운로드**(수백 MB, 1회). 이후 캐시로 오프라인 동작.
- `.env` 가 **없을 때만** 현재 디렉터리에 내장 템플릿으로 생성(기존 값 보존). 자격증명은 **env 전용**(`.env` 또는 셸 export).
- `~/.geryon/`(상태·인덱스·모델 캐시)와 빈 DB가 만들어집니다.

## 3. 동작 확인 (자격증명 없이)
```bash
geryon demo                # 가상 문서 6건으로 검색 시연 — 여기까지 되면 설치 정상
```

## 4. 실제 위키 자격증명
1. **https://id.atlassian.com/manage-profile/security/api-tokens** 에서 API 토큰 생성.
2. `.env`(없으면 bootstrap이 생성)에 채웁니다:
```
CONFLUENCE_URL=https://<회사도메인>.atlassian.net/wiki
CONFLUENCE_USERNAME=<로그인 이메일>
CONFLUENCE_API_TOKEN=<발급 토큰>
```
> 환경변수로 직접 줘도 됩니다(`CONFLUENCE_URL/USERNAME/API_TOKEN`). `.env`·토큰은 **절대 외부로 공유/커밋 금지**.

## 5. 최근 한 달 수집 → 색인
```bash
geryon sync --source confluence --days 30          # 최근 30일(=한 달) acquire + ingest
#   --since 2026-01-01 --until 2026-01-31           날짜 구간만
#   --all                                           전체
#   (첨부는 기본 미수집=본문만·빠름. 첨부도 받으려면 --attachments)
```
- `sync` = `acquire`(외부→Bronze) + `ingest`(Bronze→검색 DB)를 한 번에.
- **최초 실행은 전체 색인**, 이후 자동 증분. 색인이 끝나면 검색은 오프라인.
- 점검:
```bash
geryon status              # {"documents":..., "chunk_embeddings":..., "sqlite_vec":true, ...}
geryon health              # "System healthy."
```

## 6. MCP 클라이언트 연결
`geryon serve` 는 표준 **stdio MCP 서버**입니다. 클라이언트 설정에 등록합니다.

**Claude Desktop / 호환 클라이언트** (`Settings → Developer → Edit Config`):
```json
{
  "mcpServers": {
    "geryon": { "command": "geryon", "args": ["serve"] }
  }
}
```
- **기본이 아닌 DB**(예: 팀 공유 DB)를 쓰면 `env` 로 경로를 줍니다:
```json
{
  "mcpServers": {
    "geryon": {
      "command": "geryon",
      "args": ["serve"],
      "env": { "GERYON_DB": "/path/to/shared/geryon.db" }
    }
  }
}
```
- 격리 venv 설치면 `"command"` 를 `/abs/path/.venv/bin/geryon` 로.

저장 후 클라이언트를 재시작하면 도구가 나타납니다. 제공 도구:
`search` · `advanced_search` · `get_related` · `get_document` · `browse` · `list_sources` · `reindex`

### 연결이 됐는지 확인
클라이언트에서 "배포 가이드 찾아줘" 처럼 물으면 `search` 가 호출돼 결과가 옵니다. MCP 클라이언트 없이 **CLI로 같은 검색을 바로** 확인할 수도 있습니다(serve 와 동일 코어):
```bash
geryon search "배포 가이드" -k 5        # 표 출력
geryon search "배포 가이드" --json      # MCP search 와 동일한 JSON
```

## 7. 다국어(한글·영문) 지원
검색·재정렬 모델이 다국어(`multilingual-e5-small` + `bge-reranker-base`)라 **한글·영문 질의 모두 동작**하며, 음차·기술용어(예: `Superset`↔`슈퍼셋`, `Druid`)는 **교차언어로도 연결**됩니다.
- 검증: 한글 질의 5/5, 영문 질의 6/6 정상 응답(상위 결과 score 0.9+).
- 한계: 순수 의미번역(영 `refund` ↔ 한 `환불` 등 표기가 전혀 다른 개념쌍)은 top-1이 빗나갈 수 있습니다 → 동의어 사전(`dictionary.yaml`)에 `환불, refund` 같은 쌍을 넣어 보완합니다([동의어 가이드](GOLDEN_AND_DICTIONARY.md)).

---

## 검증 로그 (이 가이드가 실제로 통하는지)
격리된 임시 환경(fresh venv + 새 `~/.geryon`)에서 위 1~6단계를 그대로 실행해 확인했습니다:

| 단계 | 결과 |
|---|---|
| fresh venv 설치 + import | OK |
| `bootstrap`(모델 캐시 재사용) | `~/.geryon` + 빈 DB 생성 |
| `sync --days 30` (첨부 기본 미수집) | acquire 218 페이지 → **218 docs / 2,159 chunks / 2,159 embeddings**, ~2분 30초, `health: System healthy` |
| MCP `initialize` / `tools/list` / `search` | 성공 — 도구 7개 노출, `search` 정상 응답(상위 score ~0.97) |

→ **설치→수집→색인→MCP 호출이 한 흐름으로 동작**함을 확인. 다국어(한/영) 검색도 정상.
