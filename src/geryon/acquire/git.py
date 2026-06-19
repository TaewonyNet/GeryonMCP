"""GitAcquirer — 원격 저장소를 Bronze(clone된 작업트리)로 수집(멱등/증분).

레이아웃: `{bronze_dir}/<repo>/…`  (GitRepoConnector 가 읽는 형식과 동일)
  - 없으면 `git clone`, 있으면 `git fetch + reset --hard origin/<branch>`(증분).
표준 라이브러리 + `git` 실행파일(subprocess)만 사용.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from geryon.acquire.base import Acquirer
from geryon.acquire.manifest import write_manifest, get_as_of

logger = logging.getLogger(__name__)


def _repo_name(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    return name[:-4] if name.endswith(".git") else name


def normalize_repo_url(s: str) -> str:
    """사용자가 `git clone git@host:org/repo.git [dir]` 처럼 통째로 붙여넣어도 URL만 추출.
    플래그(--depth 등)·대상 디렉터리·따옴표 제거. 순수 URL은 그대로 둔다."""
    s = (s or "").strip().strip('"').strip("'")
    if not s:
        return s
    # "git clone ..." / "git -c .. clone .." 형태면 URL 토큰만 골라낸다.
    toks = s.split()
    if toks and toks[0] == "git":
        cands = [t for t in toks[1:] if t != "clone" and not t.startswith("-")]
        url = next((t for t in cands if "://" in t or "@" in t or t.endswith(".git")), None)
        return url or (cands[0] if cands else s)
    return s


def _authed_url(url: str) -> str:
    """clone URL 인증 — **기본은 사용자 git 인증**(SSH 키/credential helper)으로 그대로 clone.
    그게 없는 환경을 위해 `GERYON_GIT_TOKEN`(+선택 `GERYON_GIT_USERNAME`)이 있으면 https URL 에
    토큰을 주입한다(GitHub=토큰만, GitLab=`oauth2`, Bitbucket=`x-token-auth` 를 username 으로).
    SSH·이미 자격 포함·비 https URL 은 건드리지 않는다. (토큰은 로컬 clone 설정에만 남음 = bronze, gitignore)"""
    token = os.getenv("GERYON_GIT_TOKEN")
    if not token or not url.startswith("https://"):
        return url
    rest = url[len("https://"):]
    if "@" in rest.split("/", 1)[0]:   # 이미 user[:pass]@host → 그대로
        return url
    user = os.getenv("GERYON_GIT_USERNAME")
    cred = f"{user}:{token}" if user else token
    return f"https://{cred}@{rest}"


class GitAcquirer(Acquirer):
    def __init__(
        self,
        repos: list[str] | str,
        bronze_dir: str = "bronze/repos",
        depth: int | None = None,
        branch: str | None = None,
    ) -> None:
        raw = list(repos) if isinstance(repos, (list, tuple)) else [repos]
        self.repos = [normalize_repo_url(r) for r in raw if normalize_repo_url(r)]
        self.bronze_dir = Path(bronze_dir)
        self.depth = depth
        self.branch = branch

    def healthcheck(self) -> bool:
        return shutil.which("git") is not None and bool(self.repos)

    def _git(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

    def _current_branch(self, dest: Path) -> str:
        r = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=dest)
        return (r.stdout or "").strip() or "main"

    def acquire(self, *, force: bool = False, dry_run: bool = False) -> dict[str, int]:
        if not shutil.which("git"):
            raise RuntimeError("git 실행파일이 필요합니다(설치 후 다시 실행).")
        stats = {"cloned": 0, "updated": 0, "skipped": 0, "errors": 0}
        self.bronze_dir.mkdir(parents=True, exist_ok=True)

        for url in self.repos:
            dest = self.bronze_dir / _repo_name(url)
            try:
                prev_as_of = get_as_of(dest)  # 이전 HEAD 커밋(manifest)
                if (dest / ".git").exists():  # 증분: fetch + reset
                    if dry_run:
                        logger.info("[dry-run] update %s", dest)
                        stats["skipped"] += 1
                        continue
                    r = self._git(["fetch", "--all", "--prune"], cwd=dest)
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.strip() or "git fetch 실패")
                    br = self.branch or self._current_branch(dest)
                    rr = self._git(["reset", "--hard", f"origin/{br}"], cwd=dest)
                    if rr.returncode != 0:
                        raise RuntimeError(rr.stderr.strip() or "git reset 실패")
                    stats["updated"] += 1
                else:  # 최초 clone
                    if dry_run:
                        logger.info("[dry-run] clone %s → %s", url, dest)
                        stats["skipped"] += 1
                        continue
                    args = ["clone"]
                    if self.depth:
                        args += ["--depth", str(self.depth)]
                    if self.branch:
                        args += ["--branch", self.branch]
                    args += [_authed_url(url), str(dest)]   # 기본 plain, 토큰 있으면 주입
                    r = self._git(args)
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.strip() or "git clone 실패")
                    stats["cloned"] += 1
                # clone/pull 성공 → Bronze manifest(as_of=HEAD, last_change=git diff/log)
                self._write_manifest(dest, _repo_name(url), url, prev_as_of)
            except Exception as e:  # noqa: BLE001 — 한 repo 실패가 전체를 막지 않음
                logger.error("git 수집 실패 %s: %s", url, e)
                stats["errors"] += 1

        logger.info("git 수집 완료: %s", stats)
        return stats

    def _write_manifest(self, dest: Path, repo_name: str, url: str, prev_as_of: str | None) -> None:
        """repo manifest 기록 — as_of=HEAD, last_change=이전 as_of..HEAD 의 변경 파일·새 커밋."""
        head = self._git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
        added: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        if prev_as_of and prev_as_of != head:
            # 변경 파일: git diff --name-status <prev>..HEAD → source_id "repo:경로"
            diff = self._git(["diff", "--name-status", f"{prev_as_of}..{head}"], cwd=dest).stdout
            for line in diff.splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                st, rel = parts[0].strip(), parts[-1].strip()
                sid = f"{repo_name}:{rel}"
                if st.startswith("D"):
                    deleted.append(sid)
                elif st.startswith("A"):
                    added.append(sid)
                else:
                    modified.append(sid)
            # 새 커밋: git log <prev>..HEAD → source_id "repo@hash"
            log = self._git(["log", f"{prev_as_of}..{head}", "--format=%H"], cwd=dest).stdout
            for h in log.split():
                added.append(f"{repo_name}@{h[:12]}")
        write_manifest(
            dest, source="git", as_of=head, instance=url,
            last_change={"since": prev_as_of, "added": added, "modified": modified, "deleted": deleted},
        )
