# 폐쇄망(air-gap) 설치 가이드

인터넷이 없는 망에서 GeryonMCP를 설치·운영하는 방법입니다. **검색·운영은 완전 오프라인**으로
동작하며(설계 목표), 막히는 지점은 단 하나 — **최초에 (1) Python 패키지와 (2) 모델 파일을 외부에서
반입**하는 것뿐입니다. 아래 절차는 모두 **실제 검증**되었습니다(맨 아래 검증 로그).

> 핵심: 질의 시 외부 호출 0. 모델 캐시는 **`~/.geryon/models` 한 디렉터리로 통합**되어 반입이 단순합니다.

---

## 0. 무엇이 인터넷을 쓰나 (요약)

| 단계 | 인터넷 | 폐쇄망 대응 |
|------|--------|------------|
| 검색 / `serve`(운영) | **불필요** | 그대로 동작 (검증됨) |
| 수집 `acquire`/`sync` | 사내 Confluence·Jira·Git **내부 주소**만 | 내부망 호스트면 OK, 외부 인터넷 불필요 |
| 설치(패키지) | 필요(PyPI) | **오프라인 휠 번들** 반입 |
| 모델 준비 | 필요(HuggingFace) | **모델 캐시** 반입 |

권장 전략은 두 가지입니다. 환경에 맞게 고르세요.

- **전략 A — 폐쇄망 노드가 직접 색인**: 휠 + 모델을 반입해 `sync`까지 폐쇄망에서 수행.
- **전략 B — 외부에서 빌드한 DB를 반입(권장·가장 단순)**: 연결망에서 색인을 끝낸 `geryon.db`만
  폐쇄망으로 옮기면, 폐쇄망 노드는 **검색만** 하므로 임베드 모델조차 필요 없습니다(rerank만).

---

## 1. 연결된 PC에서 준비물 만들기 (반출용)

> "연결된 PC"는 폐쇄망과 **OS·아키텍처·Python 버전이 동일**해야 합니다(휠 호환). 가능하면 동일 이미지 사용.

### 1-1. Python 패키지 휠 번들
```bash
# 프로젝트 루트에서 — 본체(geryonmcp) + 모든 의존성 휠을 한 디렉터리에 빌드
python3 -m pip wheel . -w geryon_bundle
#   → geryon_bundle/ 에 geryonmcp-1.0.0-...whl 포함 ~100여 개 .whl 생성
```
> `pip download .` 는 **본체 휠을 안 만들므로** 쓰지 마세요. 반드시 `pip wheel .` 사용.

### 1-2. 모델 캐시
```bash
# 모델을 통합 캐시(~/.geryon/models)로 받는다
geryon bootstrap            # 임베드(multilingual-e5-small) + rerank(bge-reranker-base) 다운로드
#   둘 다 ~/.geryon/models 아래로 저장됨(통합). int8 양자화본도 함께 생성.
```
받은 캐시를 통째로 압축해 반출:
```bash
tar czf geryon_models.tgz -C ~/.geryon models
```

### (전략 B인 경우) 1-3. 색인된 DB
```bash
# 연결망에서 실제 수집·색인까지 끝낸다 (자격증명은 .env 또는 환경변수)
geryon sync --source confluence --days 30     # 또는 --all
geryon status                                 # documents/chunk_embeddings 확인
# 반출 대상: ~/.geryon/geryon.db  (단일 파일)
```

**반출물 정리**
- `geryon_bundle/` (휠)
- `geryon_models.tgz` (모델 캐시) — 전략 B 검색 전용이면 rerank 모델만 있어도 됨
- (전략 B) `geryon.db`

> ⚠️ `geryon.db`는 **사내 위키 본문 전체**를 담습니다. 동일 권한 내부에서만 전달하고 외부 공개 금지.

---

## 2. 폐쇄망에서 설치

### 2-1. 패키지 오프라인 설치 (PyPI 차단)
```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links ./geryon_bundle geryonmcp
.venv/bin/geryon --help        # 명령 확인
```
- `--no-index` 가 PyPI 접근을 차단하고 번들 휠만 사용합니다.
- 시스템 전역 설치를 원하면 `uv tool install --offline .`(uv가 있고 휠이 캐시된 경우) 대신
  위 venv 방식이 가장 확실합니다.

### 2-2. 모델 캐시 반입
```bash
mkdir -p ~/.geryon
tar xzf geryon_models.tgz -C ~/.geryon      # → ~/.geryon/models/ 복원
```
임의 경로에 두려면 환경변수로 지정(`.env` 또는 export):
```
GERYON_MODEL_CACHE=/opt/geryon/models
```

### 2-3. 오프라인 강제 (선택, 권장)
HuggingFace가 갱신 확인차 네트워크를 시도하지 않도록 못박습니다:
```
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```
`.env`에 넣거나 셸에서 export. (캐시가 있으면 없어도 동작하지만, 폐쇄망에선 명시 권장.)

---

## 3. 폐쇄망에서 운영

### 전략 B (DB 반입) — 검색만
```bash
mkdir -p ~/.geryon && cp geryon.db ~/.geryon/geryon.db
geryon status            # documents/chunk_embeddings/sqlite_vec:true 확인
geryon search "배포 절차" -k 5
```

### 전략 A (직접 색인)
```bash
# 사내 내부 호스트로 수집 (외부 인터넷 불필요)
#   .env: CONFLUENCE_URL=https://<사내 Atlassian 내부주소>/wiki  등
geryon sync --source confluence --days 30
geryon status
```

### MCP 연결 (공통)
```json
{ "mcpServers": { "geryon": { "command": "/abs/path/.venv/bin/geryon", "args": ["serve"],
  "env": { "HF_HUB_OFFLINE": "1" } } } }
```

---

## 4. 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `No matching distribution found for geryonmcp` | 번들에 본체 휠 없음 | `pip download` 대신 **`pip wheel .`** 로 재생성 |
| 휠 설치 중 일부 패키지 빌드 실패 | OS/아키텍처/Python 버전 불일치 | 연결망 PC를 폐쇄망과 **동일 환경**으로 맞춰 재빌드 |
| 검색 시 모델 다운로드 시도/지연 | 모델 캐시 경로 불일치 | `~/.geryon/models` 복원 확인 또는 `GERYON_MODEL_CACHE` 지정, `HF_HUB_OFFLINE=1` |
| `reranker 로드 실패(하이브리드 폴백)` 로그 | rerank 모델 캐시 누락 | 모델 tgz 재반입(검색은 폴백으로 계속 동작은 함) |
| 첫 부팅 후 모델 사라짐 | (구버전) /tmp 캐시 | v1.0.0+는 `~/.geryon/models` 영구 통합 — 해당 없음 |

---

## 검증 로그 (이 가이드가 실제로 통하는지)

격리 환경에서 위 절차를 그대로 실행해 확인했습니다:

| 항목 | 결과 |
|------|------|
| `pip wheel .` 번들 생성 | geryonmcp-1.0.0 + 의존성 **104개 휠** |
| `pip install --no-index --find-links` (PyPI 차단) | 신규 venv 설치 **성공** |
| 모델 통합 캐시(`~/.geryon/models`) 임베드 로드 | `HF_HUB_OFFLINE=1`, `/tmp` 캐시 제거 상태에서 **dim 384 정상** |
| 오프라인 설치본 + 오프라인 모델 검색 | `geryon search` **정상 결과(score 1.0)** |

→ **설치(휠)·모델·검색 전 과정이 인터넷 없이 동작**함을 확인. 임베드·rerank 모델은
`~/.geryon/models` 한 디렉터리로 통합되어 반입 대상이 단순합니다.
