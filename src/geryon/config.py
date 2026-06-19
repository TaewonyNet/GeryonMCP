import os
from pathlib import Path


def _load_dotenv() -> None:
    """`.env`(없으면 무시)를 환경변수로 로드 — 모든 설정은 env 로 제어한다.
    이미 설정된 OS 환경변수가 우선(.env 는 미설정 키만 채움). 경로는 `GERYON_ENV_FILE`
    로 바꿀 수 있다. 외부 의존성 없는 최소 파서(KEY=VALUE, # 주석)."""
    path = Path(os.getenv("GERYON_ENV_FILE", ".env"))
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:   # OS env 우선
                os.environ[key] = val
    except Exception:
        pass


_load_dotenv()   # 다른 설정을 읽기 전에 .env 를 환경변수로 반영

GERYON_DIR = Path(os.path.expanduser("~/.geryon"))
# 인덱스 DB 경로. GERYON_DB 로 프로젝트별 DB 분리 가능(팀 배포 시 프로젝트마다 별도 인덱스).
# federation[25]: 콤마로 여러 DB 지정 시 검색이 여러 DB 후보를 모아 통합 rerank.
_RAW_DB = os.getenv("GERYON_DB", str(GERYON_DIR / "geryon.db"))
DB_PATHS = [p.strip() for p in _RAW_DB.split(",") if p.strip()]
DB_PATH = Path(DB_PATHS[0])  # 대표(단일) — 기존 코드 호환. ingest 등 쓰기는 단일 DB.
INDEX_DIR = GERYON_DIR / "index"
CONFIG_PATH = GERYON_DIR / "config.yaml"

# Bronze 원본 경로 SSOT(소스별). 기본은 CWD 의 bronze/<source> —
# .gitignore 의 `bronze/` 한 줄로 자동 제외되며, acquire(출력)·ingest/health(입력)가
# **동일 경로**를 본다(분리 실행 정합). GERYON_BRONZE_DIR 로 베이스 변경 가능.
BRONZE_BASE = os.getenv("GERYON_BRONZE_DIR", "bronze")


def default_bronze(source: str) -> str:
    """소스별 Bronze 기본 경로. acquire·connector·cli 가 모두 이 함수를 참조한다."""
    return {"git": f"{BRONZE_BASE}/repos", "jira": f"{BRONZE_BASE}/jira"}.get(
        source, f"{BRONZE_BASE}/confluence"
    )


# 하위호환 alias(confluence Bronze 기본 경로). env override 우선.
DEFAULT_CONFLUENCE_DB_PATH = Path(
    os.getenv("GERYON_CONFLUENCE_DB_PATH", default_bronze("confluence"))
)

RECENCY_HALF_LIFE_DAYS = 30.0
RECENCY_BOOST_CEILING = 0.15

# 관련성 게이트: 키워드 매칭이 없는 순수 벡터 히트는 cosine distance가
# 이 값보다 크면 제외(score < 1 - threshold). 실데이터 측정 기반:
# 무의미 질의 top score ≤ 0.875, 정상 도메인 질의 ≥ 0.890 → 경계 score 0.88(distance 0.12).
# env GERYON_RELEVANCE_DISTANCE로 조정 가능.
RELEVANCE_DISTANCE_THRESHOLD = float(os.getenv("GERYON_RELEVANCE_DISTANCE", "0.12"))

# Rerank: 키워드 후보를 cross-encoder로 재정렬.
# PoC: 키워드(unicode61)+ONNX rerank = ~570ms/74.2%, 벡터 brute-force보다 빠르고 정확.
# fastembed(ONNX, torch 불필요) 사용. 모델 로드 실패 시 하이브리드로 graceful fallback.
RERANK_ENABLED = os.getenv("GERYON_RERANK", "1") not in ("0", "false", "False", "")
# 기본 reranker는 MIT 라이선스(상업 OK). jina-v2-multilingual은 CC-BY-NC(비상업)라
# 오픈소스 부적합 → bge-reranker-base(MIT)로 교체, 골든 정확도 동급(86.5%/77.4%) 검증.
RERANK_MODEL = os.getenv("GERYON_RERANK_MODEL", "BAAI/bge-reranker-base")
RERANK_POOL = int(os.getenv("GERYON_RERANK_POOL", "60"))  # 키워드 후보 수(정확도↔지연 트레이드오프; PoC 최적 60)
# best-passage MAX 결합(1=ON): rerank 입력에 제목+쿼리매칭본문구절을 더해 max 합산 → 본문중심 문서 회복.
RERANK_PASSAGE = os.getenv("GERYON_RERANK_PASSAGE", "1") in ("1", "true", "True")
# 벡터 후보 합류 수(0=off, 기본 OFF). unicode61의 한국어 조사·복합형 놓침(17~82%)을
# 의미매칭 벡터로 보강하나, 확장골든 측정 결과 이득 +2%p에 지연 3.3배(1.2→4.0s)로 비용과다 →
# 기본 OFF·옵션 유지. 조사형 근본해결은 trigram 병행·ANN이 적합(백로그).
RERANK_VEC_POOL = int(os.getenv("GERYON_RERANK_VEC_POOL", "0"))
# onnxruntime 스레드 — PoC: 8스레드가 기본 대비 ~20% 빠름. 0=자동(미지정).
RERANK_THREADS = int(os.getenv("GERYON_RERANK_THREADS", str(os.cpu_count() or 0)))
# int8 동적 양자화 — 기본 ON. production int8 77.4%/741ms vs fp32 83.9%/1034ms:
# ~28% 빠르고 모델 4배↓(1.11→0.28GB)이나 정확도 -6.5%p. fp32 복귀는 GERYON_RERANK_QUANTIZE=0.
# `onnx` 패키지 필요(pyproject 포함). 양자화 실패 시 fp32 graceful fallback.
RERANK_QUANTIZE = os.getenv("GERYON_RERANK_QUANTIZE", "1") in ("1", "true", "True")
# 모델(임베드 + rerank) 통합 캐시 — 한 디렉터리에 모아 오프라인/폐쇄망 반입을 단순화.
# (임베드 기본값이 /tmp 였던 휘발 문제도 해소.) GERYON_RERANK_CACHE 는 하위호환 별칭.
MODEL_CACHE_DIR = os.getenv("GERYON_MODEL_CACHE", str(GERYON_DIR / "models"))
RERANK_CACHE_DIR = os.getenv("GERYON_RERANK_CACHE", MODEL_CACHE_DIR)

# 자동 유의어 사전(dictionary.auto.yaml) 로드 — 기본 OFF(opt-in).
# dict_bootstrap 은 코퍼스 괄호병기에서 동의어를 자동 추출하나, 진짜 동의어와
# 노이즈(작성자명·우연 괄호매칭)가 섞여 나온다(반자동: 사람 검수 후 확정 설계).
# 데이터에 따라 raw 자동사전이 골든 정확도를 떨어뜨릴 수 있어(측정 예: 87%→77%),
# 사용자가 자기 데이터가 동의어 연결에 적합한지 판단한 뒤 켜도록 기본 OFF로 둔다.
# 수동 사전(dictionary.yaml)은 이 플래그와 무관하게 항상 로드된다.
DICT_AUTO_ENABLED = os.getenv("GERYON_DICT_AUTO", "0") in ("1", "true", "True")


# 수집 소스 선택(env) — `geryon init` 가 .env 에 기록, `geryon sync`(소스 인자 없이)가 소비한다.
def _csv_env(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]

# Confluence: 수집할 스페이스 키(비우면 전체). Jira: 프로젝트 키. Git: 저장소 URL.
CONFLUENCE_SPACES = _csv_env("GERYON_CONFLUENCE_SPACES")
JIRA_PROJECTS = _csv_env("GERYON_JIRA_PROJECTS")
GIT_REPOS = _csv_env("GERYON_GIT_REPOS")

# 첨부 다운로드 필터 — 검색에 무의미하거나 무거운 파일은 건너뛴다(env 제어).
#  · 크기 상한: GERYON_ATTACH_MAX_MB(기본 50) 초과 시 skip(메타 fileSize + 응답 Content-Length 양쪽 검사).
#  · 확장자 차단: GERYON_ATTACH_SKIP_EXT(콤마구분) — 기본은 아래 표준 목록(압축·미디어·실행/디스크 이미지).
#    검색은 본문·텍스트 첨부 위주라, 이 카테고리는 색인 기여가 없고 용량만 큼.
ATTACH_MAX_MB = int(os.getenv("GERYON_ATTACH_MAX_MB", "50"))
ATTACH_MAX_BYTES = ATTACH_MAX_MB * 1024 * 1024
_DEFAULT_SKIP_EXT = (
    "zip,7z,rar,tar,gz,tgz,bz2,xz,"          # 압축·아카이브
    "mp4,mov,avi,mkv,wmv,flv,webm,"          # 동영상
    "mp3,wav,flac,m4a,aac,"                  # 오디오
    "iso,dmg,exe,msi,dll,bin,apk"            # 디스크 이미지·실행/바이너리
)
ATTACH_SKIP_EXT = {
    e.strip().lstrip(".").lower()
    for e in os.getenv("GERYON_ATTACH_SKIP_EXT", _DEFAULT_SKIP_EXT).split(",")
    if e.strip()
}


def ensure_directories():
    GERYON_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)


# 프로젝트 루트(이 파일 기준 src/geryon/ → ../../)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# (실제 설정 파일, 그 파일이 없을 때 복사해 쓸 샘플)
_SAMPLE_PAIRS = [(".env", ".env.sample")]   # 자격증명은 env 전용(.env). mcp.json 자격증명 폴백 없음.


def ensure_sample_configs(root: Path | None = None) -> list[str]:
    """설정 파일이 없으면 샘플(플레이스홀더)에서 새로 만든다.

    **이미 있는 사용자 항목은 절대 덮어쓰지 않는다**(없을 때만 생성).
    생성한 파일 경로 목록을 반환한다(호출부가 "값을 채우라" 안내에 사용).
    """
    base = root or _PROJECT_ROOT
    created: list[str] = []
    for target_name, sample_name in _SAMPLE_PAIRS:
        target, sample = base / target_name, base / sample_name
        if not target.exists() and sample.exists():
            target.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
            created.append(str(target))
    return created


# ── .env 템플릿 (패키지 내장; init/bootstrap 이 CWD 에 .env 를 만들 때 사용) ──
# 저장소의 .env.sample 과 동일 내용(테스트가 일치 보장). 설치본에는 .env.sample 파일이
# 포함되지 않으므로 문자열로 내장해 어디서 실행해도 .env 를 생성할 수 있게 한다.
ENV_TEMPLATE = r"""# GeryonMCP 환경변수 — 모든 설정은 env 로 제어합니다.
# 사용법: 필요한 값을 채운 뒤 저장. (이 파일은 `geryon bootstrap`/`init` 이 자동 생성)
#   .env 는 git 에 올라가지 않으며, GeryonMCP 실행 시 자동으로 환경변수로 로드됩니다.
#   대부분 선택값(주석)입니다. 외부 데이터 "수집"에만 자격증명이 필요합니다(검색은 불필요).
# ─────────────────────────────────────────────────────────────

# 빠른 설정: `geryon init` 이 아래 값들을 대화형/플래그로 .env 에 기록해줍니다.

# ── 1) Confluence 자격증명 (Confluence 수집 시 필요) ──
CONFLUENCE_URL=https://your-domain.atlassian.net/wiki
CONFLUENCE_USERNAME=you@example.com
CONFLUENCE_API_TOKEN=__YOUR_API_TOKEN__
# (선택) 수집할 스페이스 키(콤마). 비우면 전체 스페이스.
# GERYON_CONFLUENCE_SPACES=ENG,HR

# ── 2) Jira 자격증명 (Jira 수집 시) ──
# 같은 Atlassian 사이트면 위 CONFLUENCE_* 를 자동 재사용 — 아래는 사이트/토큰이 다를 때만.
# JIRA_URL=https://your-domain.atlassian.net
# JIRA_USERNAME=you@example.com
# JIRA_API_TOKEN=__YOUR_API_TOKEN__
# (선택) 수집할 프로젝트 키(콤마)
# GERYON_JIRA_PROJECTS=TDT,ABC

# ── 3) Git 저장소 (GitHub / GitLab / Bitbucket) ──
# (선택) 수집할 저장소 URL(콤마). SSH 권장: git@bitbucket.org:<workspace>/<repo>.git
# GERYON_GIT_REPOS=git@github.com:org/repo.git,git@bitbucket.org:ws/repo.git
# 기본은 사용자 git 인증(SSH 키 / credential helper)으로 clone → 토큰 불필요.
# 사용자 git 인증이 없는 환경에서만 HTTPS 토큰을 주입한다:
#   GitHub  → GERYON_GIT_TOKEN 만
#   GitLab  → GERYON_GIT_USERNAME=oauth2     + GERYON_GIT_TOKEN
#   Bitbucket → GERYON_GIT_USERNAME=x-token-auth + GERYON_GIT_TOKEN
# GERYON_GIT_TOKEN=__YOUR_GIT_TOKEN__
# GERYON_GIT_USERNAME=

# ── 4) 경로 ──
# Bronze(원본) 베이스 디렉터리(기본 ./bronze → confluence|repos|jira 하위로 분리).
# GERYON_BRONZE_DIR=./bronze
# 검색 DB(기본 ~/.geryon/geryon.db). 콤마로 여러 개 지정 시 federation 검색.
# GERYON_DB=~/.geryon/geryon.db

# ── 5) 동의어 사전 ──
# 자동 유의어 사전(dictionary.auto.yaml) 로드 — 기본 OFF(opt-in). 데이터 적합성 확인 후 1.
# GERYON_DICT_AUTO=0

# ── 6) 첨부 (기본 미수집) ──
# 첨부 본문은 색인되지 않아 검색은 본문만 씁니다. 첨부는 `acquire/sync --attachments` 일 때만 받습니다.
# 받을 때의 필터(아래)만 의미 있음 — 크기 상한 MB(초과 skip), 차단 확장자(콤마).
# GERYON_ATTACH_MAX_MB=50
# GERYON_ATTACH_SKIP_EXT=zip,7z,rar,tar,gz,mp4,mov,mp3,iso,exe,dmg

# ── 7) 검색·재정렬 튜닝 (기본값 권장) ──
# GERYON_RERANK=1                       # 재정렬 on/off(0=하이브리드 폴백)
# GERYON_RERANK_MODEL=BAAI/bge-reranker-base
# GERYON_RERANK_POOL=60                 # 후보 풀(정확도↔속도)
# GERYON_RERANK_PASSAGE=1               # best-passage(1=본문중심 강화, 0=제목만·빠름)
# GERYON_RERANK_VEC_POOL=0              # 벡터 후보 합류(0=off)
# GERYON_RERANK_QUANTIZE=1              # int8(1=빠름·모델 4배↓, 0=fp32 정확도)
# GERYON_RERANK_THREADS=0               # onnxruntime 스레드(0=자동)
# GERYON_MODEL_CACHE=~/.geryon/models   # 임베드+rerank 통합 모델 캐시(폐쇄망 반입 단위)
# GERYON_RERANK_CACHE=~/.geryon/models  # (하위호환 별칭, 미지정 시 GERYON_MODEL_CACHE 사용)

# ── 8) 로깅 ──
# GERYON_LOG_LEVEL=INFO
# GERYON_LOG_FORMAT=text   # text | json
"""


def seed_env_file(path: str | Path = ".env") -> bool:
    """`.env` 가 없으면 내장 템플릿(ENV_TEMPLATE)으로 생성. 생성했으면 True.
    기존 .env 는 절대 덮어쓰지 않는다(사용자 값 보존)."""
    p = Path(path)
    if p.exists():
        return False
    p.write_text(ENV_TEMPLATE, encoding="utf-8")
    return True
