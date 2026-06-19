"""Git 저장소 커넥터 — 로컬 클론된 repo를 표준 RawRecord로 산출.

Bitbucket·GitLab·GitHub 통합(remote URL로 호스트 자동 판별). 일반 문서 검색과 달리
**소스 코드·커밋 히스토리(메세지)** 까지 색인한다(doc_type=doc|code|commit).

대상(옵션):
- 문서 파일(*.md·*.rst·*.txt)          : 항상
- 소스 코드(*.py·*.sql·*.yaml·… )      : include_code (기본 True)
- 커밋 메세지/히스토리(git log)         : include_commits (기본 True)

Bronze 레이아웃: {db_path}/<repo>/...  (각 하위가 `git clone` 된 저장소; db_path 자체가 repo여도 됨)
기본 db_path = bronze/repos(SSOT: config.default_bronze). 자격증명 불필요. 환경변수:
  GERYON_GIT_CODE=0      코드 색인 끄기
  GERYON_GIT_COMMITS=0   커밋 색인 끄기
  GERYON_GIT_MAX_COMMITS 커밋 최대 수(기본 2000)
  GERYON_GIT_MAX_BYTES   파일 본문 상한(기본 100000)
"""
import os
import re
import subprocess
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from geryon.connectors.base import Connector
from geryon.domain.models import RawRecord, SourceType
from geryon.config import default_bronze

_DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".adoc"}
_CODE_EXT = {
    ".py", ".sql", ".yaml", ".yml", ".json", ".sh", ".tpl", ".toml", ".ini", ".cfg",
    ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rb", ".php", ".rs", ".scala",
    ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".swift", ".r", ".pl", ".lua", ".tf",
}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build",
              "__pycache__", ".mypy_cache", ".pytest_cache", "target", ".idea"}
_REC_SEP, _FLD_SEP = "\x1e", "\x1f"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _detect_source(repo: Path) -> SourceType:
    cfg = repo / ".git" / "config"
    text = cfg.read_text(encoding="utf-8", errors="ignore") if cfg.is_file() else ""
    if "github.com" in text:
        return SourceType.GITHUB
    if "gitlab" in text:
        return SourceType.GITLAB
    if "bitbucket" in text:
        return SourceType.BITBUCKET
    return SourceType.GITHUB


def _repo_web(repo: Path) -> tuple[str | None, str]:
    """remote URL → (웹 base URL, 현재 branch). 출처 원본 링크 생성용."""
    cfg = repo / ".git" / "config"
    text = cfg.read_text(encoding="utf-8", errors="ignore") if cfg.is_file() else ""
    m = re.search(r"url\s*=\s*(\S+)", text)
    base = None
    if m:
        # git@host:org/repo(.git) | https://[user@]host/org/repo(.git)
        mm = re.search(r"(?:git@|https?://)(?:[^@/]*@)?([^/:]+)[/:](.+?)(?:\.git)?/?$", m.group(1))
        if mm:
            base = f"https://{mm.group(1)}/{mm.group(2)}"
    try:
        branch = subprocess.run(
            ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip() or "main"
    except Exception:
        branch = "main"
    return base, branch


def _file_url(base: str | None, source: SourceType, branch: str, rel: str) -> str | None:
    if not base:
        return None
    if source == SourceType.GITLAB:
        return f"{base}/-/blob/{branch}/{rel}"
    if source == SourceType.BITBUCKET:
        return f"{base}/src/{branch}/{rel}"
    return f"{base}/blob/{branch}/{rel}"  # github


def _commit_url(base: str | None, source: SourceType, h: str) -> str | None:
    if not base:
        return None
    if source == SourceType.GITLAB:
        return f"{base}/-/commit/{h}"
    if source == SourceType.BITBUCKET:
        return f"{base}/commits/{h}"
    return f"{base}/commit/{h}"  # github


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or None
    return None


def _git_meta(repo: Path, rel: str) -> tuple[str | None, str | None]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%an|%aI", "--", rel],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if out and "|" in out:
            a, d = out.split("|", 1)
            return (a or None), (d or None)
    except Exception:
        pass
    return None, None


class GitRepoConnector(Connector):
    source_type: SourceType = SourceType.GITHUB
    # manifest.last_change(GitAcquirer 가 git diff/log 로 기록)의 source_id 로 증분.
    supports_incremental: bool = True
    db_path: Path

    def __init__(
        self,
        db_path: str | Path | None = None,
        include_code: bool | None = None,
        include_commits: bool | None = None,
        max_commits: int | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(default_bronze("git"))
        self.include_code = (os.getenv("GERYON_GIT_CODE", "1") != "0") if include_code is None else include_code
        self.include_commits = (os.getenv("GERYON_GIT_COMMITS", "1") != "0") if include_commits is None else include_commits
        self.max_commits = max_commits if max_commits is not None else _env_int("GERYON_GIT_MAX_COMMITS", 2000)
        self.max_bytes = _env_int("GERYON_GIT_MAX_BYTES", 100_000)
        # prune/seen 은 connector.source_type 기준 → 실제 저장되는 doc.source 와 일치해야 한다.
        # 저장소 remote 로 source(github/gitlab/bitbucket)를 런타임 판별(첫 저장소 기준).
        try:
            repos = list(self._iter_repos())
            if repos:
                self.source_type = _detect_source(repos[0])
        except Exception:
            pass  # 판별 실패 시 클래스 기본값(GITHUB) 유지

    def healthcheck(self) -> bool:
        return self.db_path.is_dir()

    def _iter_repos(self) -> Iterator[Path]:
        base = self.db_path.resolve()
        if (base / ".git").is_dir():
            yield base
            return
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / ".git").is_dir():
                yield child

    # ── 파일(문서+코드) ──
    def _iter_files(self, repo: Path, source: SourceType, repo_name: str,
                    base: str | None = None, branch: str = "main") -> Iterator[RawRecord]:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in files:
                ext = Path(fn).suffix.lower()
                is_doc = ext in _DOC_EXT
                is_code = ext in _CODE_EXT
                if not is_doc and not (is_code and self.include_code):
                    continue
                fpath = Path(root, fn)
                try:
                    body = fpath.read_text(encoding="utf-8", errors="ignore")[: self.max_bytes]
                except Exception:
                    continue
                if not body.strip():
                    continue
                rel = str(fpath.relative_to(repo))
                author, updated = _git_meta(repo, rel)
                if updated is None:
                    updated = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc).isoformat()
                yield RawRecord(
                    source=source, source_id=f"{repo_name}:{rel}",
                    raw_body=body, raw_format="markdown" if is_doc else "text",
                    title=(_first_heading(body) if is_doc else None) or rel,
                    url=_file_url(base, source, branch, rel), space_or_repo=repo_name,
                    metadata={"author": author, "updated_at": updated, "tags": [],
                              "doc_type": "doc" if is_doc else "code",
                              "hierarchy": [repo_name] + list(Path(rel).parts[:-1])},
                )

    # ── 커밋(메세지/히스토리) ──
    def _iter_commits(self, repo: Path, source: SourceType, repo_name: str,
                      base: str | None = None) -> Iterator[RawRecord]:
        fmt = _FLD_SEP.join(["%H", "%an", "%aI", "%s", "%b"]) + _REC_SEP
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), "log", f"-{self.max_commits}", f"--format={fmt}"],
                capture_output=True, text=True, timeout=60,
            ).stdout
        except Exception:
            return
        for rec in out.split(_REC_SEP):
            rec = rec.strip("\n")
            if not rec.strip():
                continue
            parts = rec.split(_FLD_SEP)
            if len(parts) < 4:
                continue
            h, author, date, subject = parts[0], parts[1], parts[2], parts[3]
            body = parts[4] if len(parts) > 4 else ""
            yield RawRecord(
                source=source, source_id=f"{repo_name}@{h[:12]}",
                raw_body=(subject + "\n" + body).strip(), raw_format="text",
                title=subject or h[:12], url=_commit_url(base, source, h), space_or_repo=repo_name,
                metadata={"author": author or None, "updated_at": date or None, "tags": [],
                          "doc_type": "commit", "hierarchy": [repo_name, "commits"]},
            )

    def iter_raw(self, only: set[str] | None = None) -> Iterator[RawRecord]:
        if not self.healthcheck():
            return
        for repo in self._iter_repos():
            source = _detect_source(repo)
            repo_name = repo.name
            base, branch = _repo_web(repo)  # 출처 원본 링크용
            for rec in self._iter_files(repo, source, repo_name, base, branch):
                if only is None or rec.source_id in only:
                    yield rec
            if self.include_commits:
                for rec in self._iter_commits(repo, source, repo_name, base):
                    if only is None or rec.source_id in only:
                        yield rec
