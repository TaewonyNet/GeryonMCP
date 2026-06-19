import sys
import argparse
from datetime import datetime
from geryon.pipeline.ingest import IngestionPipeline, CONNECTOR_REGISTRY
from geryon.store.vector import VectorStore
from geryon.store.tree import TreeStore
from geryon.mcp.server import create_mcp_server


def _open_authed_url(url: str, secret: str | None):
    """인증 서버(예: Jupyter)에서 파일 응답을 연다.

    secret 을 토큰으로 먼저 시도(Authorization 헤더 + ?token=).
    응답이 HTML(로그인 페이지)이면 같은 secret 을 **비밀번호**로 보고
    `/login` 세션 로그인(쿠키) 후 재시도한다. 토큰/비밀번호 어느 쪽이든 동작.
    실패 시 RuntimeError. 반환값은 read 가능한 응답(컨텍스트 매니저).
    """
    import re
    import http.cookiejar
    import urllib.parse
    import urllib.request

    def _is_html(resp) -> bool:
        return "text/html" in (resp.headers.get("Content-Type") or "").lower()

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    # 1) 토큰 시도 (헤더 + 쿼리스트링 양쪽)
    if secret:
        sep = "&" if "?" in url else "?"
        turl = f"{url}{sep}token={urllib.parse.quote(secret)}"
        resp = opener.open(urllib.request.Request(turl, headers={"Authorization": f"token {secret}"}))
    else:
        resp = opener.open(urllib.request.Request(url))
    if not _is_html(resp):
        return resp
    resp.read()  # 로그인 페이지 — 버리고 비밀번호 폴백
    if not secret:
        raise RuntimeError("인증이 필요합니다(토큰 또는 비밀번호).")

    # 2) 비밀번호 세션 로그인 폴백
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    login_url = base + "/login"
    page = opener.open(login_url).read().decode("utf-8", "ignore")
    m = re.search(r'name="_xsrf"[^>]*value="([^"]+)"', page)
    xsrf = m.group(1) if m else next((c.value for c in cj if c.name == "_xsrf"), "")
    body = urllib.parse.urlencode({"_xsrf": xsrf, "password": secret}).encode()
    opener.open(urllib.request.Request(login_url, data=body, headers={"Referer": login_url}))
    resp2 = opener.open(urllib.request.Request(url))
    if _is_html(resp2):
        raise RuntimeError("인증 실패 — 토큰/비밀번호가 올바른지 확인하세요.")
    return resp2


def _register_mcp(proj_dir, name: str, db_path) -> None:
    """프로젝트에 MCP 설정 생성/병합 — .cursor/mcp.json(Cursor) + .mcp.json(Claude Code)."""
    import json
    from pathlib import Path
    proj = Path(proj_dir or ".").resolve()
    server_cfg = {"command": "geryon", "args": ["serve"], "env": {"GERYON_DB": str(db_path)}}
    for rel in (".cursor/mcp.json", ".mcp.json"):
        cfg_path = proj / rel
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}  # 깨진 파일이면 새로 구성(기존 백업은 사용자 git 으로)
        data.setdefault("mcpServers", {})[name] = server_cfg
        cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  MCP 등록: {cfg_path}  (server '{name}')")


def _sample_search(db_path) -> None:
    """설치/DB 가 실제로 동작하는지 샘플 질의로 즉시 확인."""
    try:
        from geryon.store.repository import SqliteRepository
        from geryon.search.factory import build_searcher
        searcher = build_searcher(repository=SqliteRepository(db_path))
        print("  샘플 검색:")
        for q in ("배포 프로세스", "정책 평가 코드"):
            hits = searcher.search(q, k=2)
            if hits:
                print(f"    {q!r} → " + ", ".join(f"{h.title}[{getattr(h, 'source', '?')}]" for h in hits))
            else:
                print(f"    {q!r} → (결과 없음 — DB 색인 확인)")
    except Exception as te:
        print(f"  (샘플 검색 건너뜀: {te})", file=sys.stderr)


def _ingest_guide(stats: dict) -> None:
    """ingest/sync 결과를 사람이 읽기 쉬운 가이드로 출력(최초 실행 안내 포함)."""
    ins = stats.get("inserted", 0)
    upd = stats.get("updated", 0)
    skp = stats.get("skipped", 0)
    dl = stats.get("deleted", 0)
    err = stats.get("errors", 0)
    mode = stats.get("mode", "?")
    if stats.get("first_run"):
        print("  ⓘ 최초 실행 — 기준 시각이 없어 전체를 색인했습니다(다음부터 자동 증분).")
    if mode == "full":
        print(f"  ✓ 전체 색인: 신규 {ins} · 변경 {upd} · 건너뜀 {skp} · 삭제 {dl}")
    elif ins + upd + dl == 0:
        print(f"  ✓ 이미 최신 상태입니다 — 변경 없음(확인 {skp}건).")
    else:
        line = f"  ✓ 증분 갱신: 신규 {ins} · 변경(덮어쓰기) {upd}"
        if dl:
            line += f" · 삭제 {dl}"
        print(line)
    if err:
        print(f"  ⚠ 오류 {err}건 — 로그(stderr) 확인.")
    if stats.get("started_at") and stats.get("finished_at"):
        print(f"    소요: {stats['started_at'][11:19]} → {stats['finished_at'][11:19]}")
    print("    다음: `geryon serve` (또는 에디터의 MCP 재시작)")


def _db_watermark_since(source: str) -> str | None:
    """DB 의 그 소스 최신 수정시각(YYYY-MM-DD). DB만 공유받아도 '이후만' 수집 가능(Bronze 불필요)."""
    try:
        from geryon.store.repository import SqliteRepository
        from geryon.domain.models import SourceType
        wm = SqliteRepository().latest_doc_updated_at(SourceType(source))
        return wm.strftime("%Y-%m-%d") if wm else None
    except Exception:
        return None


def _build_acquirer(args, source: str):
    """args(공용 acquire 인자) + source 로 Acquirer 인스턴스와 bronze 경로를 만든다.
    소스 선택(스페이스/프로젝트/repo)은 CLI 인자 우선, 없으면 env 설정(`geryon init` 기록)을 쓴다."""
    from geryon.acquire import ACQUIRER_REGISTRY
    from geryon import config
    acq_cls = ACQUIRER_REGISTRY.get(source)
    if not acq_cls:
        raise RuntimeError(f"Unknown source '{source}'. Available: {list(ACQUIRER_REGISTRY)}")
    bronze = args.bronze_dir or config.default_bronze(source)
    # 수집 창 자동 결정: --all/--since/--days 명시가 없으면 기본=DB 워터마크 이후(증분),
    # 워터마크 없으면(첫 실행) 30일 부트스트랩. 명시 플래그가 항상 우선.
    auto_window = (not getattr(args, "all", False)
                   and not getattr(args, "since", None)
                   and getattr(args, "days", None) is None)
    if source == "confluence":
        since, until = getattr(args, "since", None), getattr(args, "until", None)
        if (auto_window or getattr(args, "since_db", False)) and not since:
            since = _db_watermark_since(source)   # DB 워터마크 이후만(Bronze 불필요)
            if since:
                print(f"  증분: DB 워터마크 {since} 이후 수정분만 수집(--days/--all 로 변경)")
        for label, val in (("--since", since), ("--until", until)):
            if val is not None:
                try:
                    datetime.strptime(val, "%Y-%m-%d")
                except ValueError:
                    raise RuntimeError(f"{label} 형식 오류: '{val}' (YYYY-MM-DD 형태로 지정)")
        if since and until and since > until:
            raise RuntimeError(f"--since({since}) 가 --until({until}) 보다 늦습니다.")
        spaces = getattr(args, "space", None) or config.CONFLUENCE_SPACES or None
        days = args.days if args.days is not None else 30   # since 있으면 acquire 가 days 무시
        # 첨부 기본 미수집 — `--attachments` 로만 켠다(`--no-attachments` 는 구 호환, 무시)
        want_attach = getattr(args, "attachments", False) and not getattr(args, "no_attachments", False)
        acq = acq_cls(bronze_dir=bronze, days=days, all_pages=args.all,
                      max_pages=args.max_pages, download_attachments=want_attach,
                      since=since, until=until, spaces=spaces)
    elif source == "git":
        repos = getattr(args, "repo", None) or config.GIT_REPOS
        if not repos:
            raise RuntimeError("git 수집은 --repo <url> 또는 GERYON_GIT_REPOS(env) 가 필요합니다.")
        acq = acq_cls(repos=repos, bronze_dir=bronze, depth=args.depth, branch=args.branch)
    elif source == "jira":
        projects = ([args.project] if getattr(args, "project", None) else config.JIRA_PROJECTS)
        if not projects:
            raise RuntimeError("jira 수집은 --project <키> 또는 GERYON_JIRA_PROJECTS(env) 가 필요합니다.")
        days = args.days
        if auto_window or getattr(args, "since_db", False):
            wm = _db_watermark_since(source)
            if wm:
                from datetime import datetime as _dt
                days = max((_dt.now() - _dt.strptime(wm, "%Y-%m-%d")).days, 0) + 1
                print(f"  증분: DB 워터마크 {wm} 이후만(≈ 최근 {days}일)")
        if days is None:
            days = 30   # 첫 실행(워터마크 없음) 부트스트랩
        acq = acq_cls(project=projects, bronze_dir=bronze, days=days,
                      all_issues=args.all, max_issues=getattr(args, "max_issues", None))
    else:
        acq = acq_cls()
    return acq, bronze


def _is_scp_target(dest: str) -> bool:
    """scp 원격 대상(user@host:path / host:path)인지. 로컬/마운트 경로·URL 은 False."""
    if "://" in dest:
        return False
    head = dest.split(":", 1)[0]
    if "@" in head:
        return True
    if ":" in dest:
        if len(head) == 1 and dest[1:3] in (":\\", ":/"):  # 윈도우 드라이브문자 C:\
            return False
        if head and "/" not in head and "\\" not in head:
            return True
    return False


def _env_write(updates: dict, path: str = ".env") -> None:
    """`.env` 의 키를 갱신 — 기존 `KEY=` 는 값 교체, 주석 처리된 `# KEY=`(템플릿)는 **주석 해제+설정**,
    둘 다 없으면 끝에 추가. 그 외 줄·주석은 보존. 파일 없으면 새로 만든다."""
    import re as _re
    from pathlib import Path as _P
    p = _P(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        s = line.strip()
        m = _re.match(r"^#?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", s)  # KEY= 또는 # KEY=
        key = m.group(1) if m else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")   # 템플릿의 주석/플레이스홀더를 실제 값으로
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items()]
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def _select_from(items: list, what: str, tty: bool) -> list[str]:
    """(key, name) 목록을 번호와 함께 보여주고 선택을 받는다(콤마/공백, 'all'=전체, Enter=건너뜀)."""
    if not items:
        print(f"  ({what} 0건 — 권한/토큰을 확인하세요)")
        return []
    for i, (k, n) in enumerate(items, 1):
        print(f"   {i:>3}. {k}  —  {(n or '')[:50]}")
    raw = input(f"  선택할 {what}(번호/키 콤마, 'all'=전체, Enter=건너뜀): ").strip() if tty else ""
    if not raw:
        return []
    if raw.lower() == "all":
        return [k for k, _ in items]
    keyset = {k for k, _ in items}
    chosen: list[str] = []
    for t in raw.replace(",", " ").split():
        if t.isdigit() and 1 <= int(t) <= len(items):
            chosen.append(items[int(t) - 1][0])
        elif t in keyset:
            chosen.append(t)
    return chosen


def _init_git_list_from_hosts(ask, tty):
    """(opt-in) 호스트 토큰으로 저장소 목록을 불러와 선택. (repos, token, git_username) 반환.
    조직이 토큰 인증을 막으면(특히 Bitbucket) 실패 — 그땐 URL 직접 입력이 정답."""
    from geryon.acquire.git_hosts import list_repos, GIT_USERNAME_FOR
    all_repos: list[str] = []
    host_creds: dict[str, tuple[str, str]] = {}
    for host in ("github", "gitlab", "bitbucket"):
        while True:
            token = ask(f"  [{host}] 토큰(이 호스트 건너뛰려면 Enter):", secret=True)
            if not token:
                break
            owner = username = None
            if host == "github":
                owner = input("    GitHub org/user (Enter=내 토큰의 repo): ").strip() or None
            elif host == "bitbucket":
                owner = input("    Bitbucket 워크스페이스(slug): ").strip() or None
                username = input("    사용자명 (스코프 API토큰=이메일 / 액세스토큰이면 Enter): ").strip() or None
            try:
                print(f"    {host} 저장소 목록을 불러옵니다…")
                repos = list_repos(host, token, owner=owner, username=username)
            except Exception as e:  # noqa: BLE001
                print(f"    ⚠ {host} 실패: {e}")
                if (input("    [Y]재시도 / [n]건너뜀(→ URL 직접입력 권장): ").strip().lower() or "y").startswith("y"):
                    continue
                break
            sel = _select_from(repos, f"{host} 저장소", tty)
            if sel:
                all_repos += sel
                host_creds[host] = (token, username if host == "bitbucket" else GIT_USERNAME_FOR.get(host, ""))
            break
    token = guser = None
    if len(host_creds) == 1:
        token, guser = next(iter(host_creds.values()))
    elif len(host_creds) > 1:
        print("  ⓘ 여러 호스트 토큰은 .env 한 곳에 담지 않습니다 — clone 은 SSH 권장.")
    return all_repos, token, guser


def _run_init(args) -> None:
    """가이드형 설정 — Confluence → Jira → Git 순. Confluence/Jira 는 **토큰 검증 후
    스페이스/프로젝트 목록을 보여주고 선택**하게 한다(TTY). 플래그를 주면 그 값으로 비대화 기록."""
    import getpass
    from geryon.config import seed_env_file
    if seed_env_file(".env"):          # .env 없으면 내장 템플릿으로 생성(주석된 전체 옵션 포함)
        print("  .env 생성(템플릿 기반) — 입력값을 채웁니다.")
    tty = sys.stdin.isatty()
    vals: dict[str, str] = {}

    def _ask(msg, secret=False):
        if not tty:
            return None
        v = (getpass.getpass(msg + " ") if secret else input(msg + " ")).strip()
        return v or None

    # ── 1) Confluence ──
    print("[1/3] Confluence")
    c_url = args.confluence_url or _ask("  URL (https://<도메인>.atlassian.net/wiki):")
    c_user = args.confluence_user or _ask("  로그인 이메일:")
    c_token = args.confluence_token or _ask("  API 토큰:", secret=True)
    if c_url and c_user and c_token:
        vals.update(CONFLUENCE_URL=c_url, CONFLUENCE_USERNAME=c_user, CONFLUENCE_API_TOKEN=c_token)
        if args.space:
            vals["GERYON_CONFLUENCE_SPACES"] = ",".join(args.space)
        elif tty:
            from geryon.acquire.confluence_atlassian import ConfluenceClient
            while True:    # 실패 시 토큰 재입력 재시도
                try:
                    print("  토큰 확인 중 — 스페이스 목록을 불러옵니다…")
                    spaces = ConfluenceClient(c_url, c_user, vals["CONFLUENCE_API_TOKEN"]).list_spaces()
                except Exception as e:  # noqa: BLE001
                    print(f"  ⚠ 스페이스 목록 실패(토큰/URL 확인): {e}")
                    if not (input("  토큰을 다시 입력해 재시도할까요? [Y/n]: ").strip().lower() or "y").startswith("y"):
                        break
                    nt = _ask("  Confluence API 토큰:", secret=True)
                    if not nt:
                        break
                    vals["CONFLUENCE_API_TOKEN"] = nt
                    continue
                sel = _select_from(spaces, "스페이스", tty)
                if sel:
                    vals["GERYON_CONFLUENCE_SPACES"] = ",".join(sel)
                else:
                    print("  → 스페이스 필터 없음(전체 수집).")
                break

    # ── 2) Jira ──
    print("[2/3] Jira")
    if args.jira_project:
        vals["GERYON_JIRA_PROJECTS"] = ",".join(args.jira_project)
    elif tty:
        same = (input("  Confluence 와 같은 Atlassian 사이트인가요? [Y/n]: ").strip().lower() or "y")
        if same.startswith("y") and c_url and c_user and c_token:
            j_url, j_user, j_token = c_url, c_user, c_token   # CONFLUENCE_* 재사용
        else:
            j_url = _ask("  Jira URL (https://<도메인>.atlassian.net):")
            j_user = _ask("  로그인 이메일:")
            j_token = _ask("  API 토큰:", secret=True)
            if j_url and j_user and j_token:
                vals.update(JIRA_URL=j_url, JIRA_USERNAME=j_user, JIRA_API_TOKEN=j_token)
        if j_url and j_user and j_token:
            from geryon.acquire.jira_atlassian import JiraClient
            base = j_url.rstrip("/").removesuffix("/wiki")
            jtok = j_token
            while True:    # 실패 시 토큰 재입력 재시도
                try:
                    print("  토큰 확인 중 — 프로젝트 목록을 불러옵니다…")
                    projects = JiraClient(base, j_user, jtok).list_projects()
                except Exception as e:  # noqa: BLE001
                    print(f"  ⚠ 프로젝트 목록 실패(토큰/URL 확인): {e}")
                    if not (input("  토큰을 다시 입력해 재시도할까요? [Y/n]: ").strip().lower() or "y").startswith("y"):
                        break
                    nt = _ask("  Jira API 토큰:", secret=True)
                    if not nt:
                        break
                    jtok = nt
                    vals.update(JIRA_URL=j_url, JIRA_USERNAME=j_user, JIRA_API_TOKEN=nt)
                    continue
                sel = _select_from(projects, "프로젝트", tty)
                if sel:
                    vals["GERYON_JIRA_PROJECTS"] = ",".join(sel)
                break

    # ── 3) Git — URL 입력 우선(가장 안정적). 목록 자동조회는 'list'(주로 GitHub/GitLab). ──
    print("[3/3] Git (GitHub/GitLab/Bitbucket)")
    from geryon.acquire.git import normalize_repo_url
    if args.git_repo:                       # 플래그: 직접 지정('git clone ...' 붙여넣기도 정규화)
        vals["GERYON_GIT_REPOS"] = ",".join(normalize_repo_url(r) for r in args.git_repo)
        if args.git_token:
            vals["GERYON_GIT_TOKEN"] = args.git_token
    elif tty:
        print("  저장소 URL 을 입력하세요(콤마). SSH 권장: git@bitbucket.org:<workspace>/<repo>.git")
        print("  ('git clone ...' 통째로 붙여넣어도 됩니다 / 'list'=목록조회 / Enter=건너뜀)")
        first = input("  > ").strip()
        if first.lower() == "list":
            repos, gtok, guser = _init_git_list_from_hosts(_ask, tty)
            if repos:
                vals["GERYON_GIT_REPOS"] = ",".join(dict.fromkeys(repos))
                if gtok:
                    vals["GERYON_GIT_TOKEN"] = gtok
                if guser:
                    vals["GERYON_GIT_USERNAME"] = guser
        elif first:
            urls = [normalize_repo_url(x) for x in first.split(",") if x.strip()]
            vals["GERYON_GIT_REPOS"] = ",".join(u for u in urls if u)
            gt = _ask("  HTTPS 토큰(SSH/credential helper 쓰면 Enter):", secret=True)
            if gt:
                vals["GERYON_GIT_TOKEN"] = gt

    if not vals:
        print("입력이 없어 .env 를 변경하지 않았습니다(플래그 또는 대화형으로 값을 주세요).")
        return
    _env_write(vals)
    masked = {k: ("***" if "TOKEN" in k else v) for k, v in vals.items()}
    print(f"\n✓ .env 갱신: {masked}")
    nexts = []
    if "CONFLUENCE_URL" in vals or "GERYON_CONFLUENCE_SPACES" in vals:
        nexts.append("geryon sync --source confluence")
    if "GERYON_GIT_REPOS" in vals:
        nexts.append("geryon sync --source git")
    if "GERYON_JIRA_PROJECTS" in vals:
        nexts.append("geryon sync --source jira")
    print("다음 단계:")
    for c in nexts or ["geryon sync --source confluence"]:
        print(f"  {c}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Geryon CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Acquire subcommand — 외부 소스 → Bronze 원본 파일(멱등/증분 수집)
    def _add_acquire_args(p) -> None:
        p.add_argument("--source", type=str, default="confluence", help="수집 소스: confluence | git")
        p.add_argument("--bronze-dir", type=str, default=None, help="Bronze 출력 경로(기본: confluence→confluence_db, git→repos)")
        p.add_argument("--days", type=int, default=None,
                       help="[confluence/jira] 최근 N일 수정분. 미지정 시 기본=DB 워터마크 이후(증분), DB가 비었으면 30일")
        p.add_argument("--since", type=str, default=None,
                       help="[confluence] 수집 구간 시작(YYYY-MM-DD) — 지정 시 --days 무시")
        p.add_argument("--until", type=str, default=None,
                       help="[confluence] 수집 구간 끝(YYYY-MM-DD, 기본: 오늘)")
        p.add_argument("--all", action="store_true", help="[confluence] 전체 수집(--days/--since 무시)")
        p.add_argument("--since-db", action="store_true",
                       help="DB 워터마크 이후만 수집(이제 기본 동작 — --days/--since/--all 미지정 시 자동). 명시는 강제용")
        p.add_argument("--space", action="append", help="[confluence] 스페이스 키로 한정(여러 번; 미지정 시 env GERYON_CONFLUENCE_SPACES 또는 전체)")
        p.add_argument("--max-pages", type=int, default=None, help="[confluence] 최대 페이지 수(테스트용)")
        p.add_argument("--attachments", action="store_true",
                       help="[confluence] 첨부도 다운로드(기본 미수집 — 첨부 본문은 색인되지 않아 검색은 본문만 사용)")
        p.add_argument("--no-attachments", action="store_true", help=argparse.SUPPRESS)  # (구) 호환: 이제 기본이 OFF
        p.add_argument("--repo", action="append", help="[git] 원격 저장소 URL(여러 번 지정 가능)")
        p.add_argument("--depth", type=int, default=None, help="[git] shallow clone 깊이(미지정=full)")
        p.add_argument("--branch", type=str, default=None, help="[git] 브랜치")
        p.add_argument("--project", type=str, default=None, help="[jira] 프로젝트 키(예: TDT)")
        p.add_argument("--max-issues", type=int, default=None, help="[jira] 최대 이슈 수(테스트용)")
        p.add_argument("--dry-run", action="store_true", help="기록 없이 대상만 출력")
        p.add_argument("--force", action="store_true", help="기존 Bronze 무시하고 강제 재수집")

    acquire_parser = subparsers.add_parser("acquire", help="외부 소스 → Bronze 원본 파일(멱등/증분 수집)")
    _add_acquire_args(acquire_parser)

    # Ingest subcommand — Bronze 원본 파일 → 검색 DB(Silver)
    ingest_parser = subparsers.add_parser("ingest", help="Bronze 원본 → 검색 DB 적재")
    ingest_parser.add_argument("--source", type=str, help="Source to ingest")
    ingest_parser.add_argument("--path", type=str, help="Bronze 데이터 경로(예: confluence_db/ 또는 git repo 디렉터리). 미지정 시 기본 경로")
    ingest_parser.add_argument("--no-vector", action="store_true", help="벡터 임베딩 생략(키워드+rerank만, 빠른 색인)")
    ingest_parser.add_argument("--full", action="store_true", help="전체 재구축(force upsert + Safety Gate 우회)")
    ingest_parser.add_argument("--no-prune", action="store_true", help="Bronze에 없는 DB 문서를 삭제하지 않음(추가/갱신만)")

    # Sync subcommand — acquire + ingest(한 번에). 평소 갱신용.
    sync_parser = subparsers.add_parser("sync", help="acquire + ingest 를 한 번에(평소 갱신용)")
    _add_acquire_args(sync_parser)
    sync_parser.add_argument("--no-vector", action="store_true", help="[ingest] 벡터 임베딩 생략")
    sync_parser.add_argument("--full", action="store_true", help="[ingest] 전체 재구축")
    sync_parser.add_argument("--no-prune", action="store_true", help="[ingest] Bronze에 없는 DB 문서 삭제 안 함")

    # Reindex subcommand
    reindex_parser = subparsers.add_parser("reindex", help="Reindex records")
    reindex_parser.add_argument("--source", type=str, help="Source to reindex")
    reindex_parser.add_argument("--path", type=str, help="소스 데이터 경로")
    reindex_parser.add_argument("--full", action="store_true", help="Perform full reindex")

    # Serve subcommand
    subparsers.add_parser("serve", help="Serve MCP server")

    # Bootstrap subcommand
    subparsers.add_parser("bootstrap", help="Bootstrap Geryon environment")

    # Init subcommand — 자격증명·수집 소스를 .env 에 설정(대화형+플래그)
    init_parser = subparsers.add_parser("init", help="자격증명·수집 소스(스페이스/프로젝트/repo)를 .env 에 설정")
    init_parser.add_argument("--confluence-url", default=None)
    init_parser.add_argument("--confluence-user", default=None)
    init_parser.add_argument("--confluence-token", default=None)
    init_parser.add_argument("--space", action="append", help="Confluence 스페이스 키(여러 번)")
    init_parser.add_argument("--jira-project", action="append", help="Jira 프로젝트 키(여러 번)")
    init_parser.add_argument("--git-repo", action="append", help="Git 저장소 URL(여러 번)")
    init_parser.add_argument("--git-token", default=None, help="Git HTTPS 토큰(SSH 쓰면 불필요)")

    # Setup subcommand — 크로스 플랫폼 온보딩(맥/우분투/윈도우 공통): 모델 캐시 + (선택)공유 DB 받기
    setup_parser = subparsers.add_parser("setup", help="온보딩: 모델 캐시 + 선택적 공유 DB 다운로드(OS 무관, 셸 불필요)")
    setup_parser.add_argument("--db-url", type=str, help="공유 인덱스 DB 다운로드 URL(방식 B). 미지정 시 모델만 준비")
    setup_parser.add_argument("--db-path", type=str, help="DB 저장 경로(기본: GERYON_DB 또는 ~/.geryon/geryon.db)")
    setup_parser.add_argument("--token", type=str, help="다운로드 인증 토큰/비밀번호(예: Jupyter 토큰). 미지정 시 대화형 입력")
    setup_parser.add_argument("--mcp", type=str, metavar="NAME", help="MCP 서버 등록명. .cursor/mcp.json·.mcp.json 을 생성/병합")
    setup_parser.add_argument("--mcp-dir", type=str, default=".", help="MCP 설정을 둘 프로젝트 경로(기본: 현재 디렉토리)")
    setup_parser.add_argument("--test", action="store_true", help="설치 후 샘플 검색으로 동작 확인")

    # Publish subcommand — 로컬 인덱스 DB 를 공유(업로드/덮어쓰기) + 선택적 MCP 등록(setup 의 대칭)
    publish_parser = subparsers.add_parser("publish", help="로컬 DB 를 원격(scp)/공유 경로에 업로드(덮어쓰기) + 선택적 MCP 등록")
    publish_parser.add_argument("--to", type=str, required=True, help="업로드 대상: scp(user@host:/path) 또는 파일/마운트 경로")
    publish_parser.add_argument("--db-path", type=str, help="올릴 DB(기본: GERYON_DB)")
    publish_parser.add_argument("--mcp", type=str, metavar="NAME", help="본인 MCP 등록명(.cursor/.mcp.json)")
    publish_parser.add_argument("--mcp-dir", type=str, default=".", help="MCP 설정을 둘 경로(기본: 현재 디렉토리)")
    publish_parser.add_argument("--test", action="store_true", help="업로드 후 샘플 검색으로 동작 확인")

    # Health subcommand
    subparsers.add_parser("health", help="Check system health")

    # Status subcommand
    subparsers.add_parser("status", help="인덱스/상태 JSON")

    # Demo subcommand — 자격증명 없이 검색 체험(가상 데이터)
    subparsers.add_parser("demo", help="가상 문서로 검색 데모(자격증명 불필요)")

    # Search subcommand — CLI 직접 검색(스크립트·디버깅). MCP `search` 와 동일 코어(geryon.search.query).
    search_parser = subparsers.add_parser("search", help="검색 질의를 직접 실행(표/JSON)")
    search_parser.add_argument("query", help="검색 질의")
    search_parser.add_argument("-k", type=int, default=10, help="결과 수(기본 10)")
    search_parser.add_argument("--offset", type=int, default=0)
    search_parser.add_argument("--json", action="store_true", help="JSON 출력(MCP search 와 동일 형식)")
    search_parser.add_argument("--user", help="개인화 user_id")
    search_parser.add_argument("--source", action="append", help="소스 필터(반복 지정)")
    search_parser.add_argument("--space", action="append", help="공간/저장소 필터(반복)")
    search_parser.add_argument("--tag", action="append", help="태그 필터(반복)")
    search_parser.add_argument("--author", action="append", help="작성자 필터(반복)")
    search_parser.add_argument("--date-from", help="시작일 YYYY-MM-DD")
    search_parser.add_argument("--date-to", help="종료일 YYYY-MM-DD")

    args = parser.parse_args()

    # 구조적 로깅 초기화(stderr) — stdout은 MCP/데이터 전용
    from geryon.logging_setup import setup_logging
    setup_logging()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "acquire":
        source = args.source or "confluence"
        print(f"Acquiring source: {source} → Bronze")
        try:
            acq, bronze = _build_acquirer(args, source)
            stats = acq.acquire(force=args.force, dry_run=args.dry_run)
            print(f"Acquire 완료! Bronze: {bronze}  Stats: {stats}")
        except Exception as e:
            print(f"Error running acquire: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "sync":
        source = args.source or "confluence"
        print(f"Sync source: {source} (acquire + ingest)")
        try:
            # 1) acquire
            acq, bronze = _build_acquirer(args, source)
            print("▶ acquire → Bronze")
            astats = acq.acquire(force=args.force, dry_run=args.dry_run)
            print(f"  acquire: {astats}")
            if args.dry_run:
                print("Sync(dry-run) 완료 — ingest 는 생략.")
                return
            # 2) ingest (acquire 가 만든 Bronze 를 색인)
            connector_cls = CONNECTOR_REGISTRY.get(source)
            if not connector_cls:
                print(f"ingest 미지원 소스: {source}", file=sys.stderr)
                sys.exit(1)
            connector = connector_cls(bronze)
            vs = None if getattr(args, "no_vector", False) else VectorStore()
            pipeline = IngestionPipeline(vector_store=vs, tree_store=TreeStore())
            print("▶ ingest → DB")
            istats = pipeline.run(connector, full_reindex=args.full, prune=not args.no_prune,
                                  incremental=not args.full)  # 기본 증분, --full=전체
            print(f"Sync 완료! acquire={astats}")
            _ingest_guide(istats)
        except Exception as e:
            print(f"Error running sync: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "ingest":
        print(f"Ingesting source: {args.source}")
        source = args.source or "confluence"
        connector_cls = CONNECTOR_REGISTRY.get(source)
        if not connector_cls:
            print(f"Error: Unknown source '{source}'. Available: {list(CONNECTOR_REGISTRY.keys())}", file=sys.stderr)
            sys.exit(1)
        connector = connector_cls(args.path) if getattr(args, "path", None) else connector_cls()
        vs = None if getattr(args, "no_vector", False) else VectorStore()
        pipeline = IngestionPipeline(vector_store=vs, tree_store=TreeStore())
        try:
            stats = pipeline.run(connector,
                                 full_reindex=getattr(args, "full", False),
                                 prune=not getattr(args, "no_prune", False),
                                 incremental=not getattr(args, "full", False))  # 기본 증분, --full=전체
            print(f"Ingestion successful! Stats: {stats}")
            _ingest_guide(stats)
        except Exception as e:
            print(f"Error running ingestion: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "reindex":
        print(f"Reindexing source: {args.source}, full: {args.full}")
        source = args.source or "confluence"
        connector_cls = CONNECTOR_REGISTRY.get(source)
        if not connector_cls:
            print(f"Error: Unknown source '{source}'. Available: {list(CONNECTOR_REGISTRY.keys())}", file=sys.stderr)
            sys.exit(1)
        connector = connector_cls(args.path) if getattr(args, "path", None) else connector_cls()
        pipeline = IngestionPipeline(vector_store=VectorStore(), tree_store=TreeStore())
        try:
            stats = pipeline.run(connector, full_reindex=args.full)
            print(f"Reindexing successful! Stats: {stats}")
        except Exception as e:
            print(f"Error running reindexing: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "serve":
        # MCP stdio 규약: stdout 은 프로토콜 전용 — 안내/로그는 반드시 stderr 로.
        print("Starting Geryon MCP server...", file=sys.stderr)
        create_mcp_server().run()
    elif args.command == "demo":
        print("GeryonMCP 데모 — 가상 문서로 검색을 시연합니다(자격증명 불필요).")
        try:
            from geryon.config import GERYON_DIR, ensure_directories
            from geryon.store.repository import SqliteRepository
            from geryon.search.factory import build_searcher
            from geryon._demo import seed_documents
            ensure_directories()
            demo_db = GERYON_DIR / "demo.db"
            if demo_db.exists():
                demo_db.unlink()  # 매번 신선하게
            repo = SqliteRepository(demo_db)
            docs = seed_documents()
            for d in docs:
                repo.upsert(d)
            print(f"  가상 문서 {len(docs)}건 적재 (DB: {demo_db})")
            searcher = build_searcher(repository=repo)
            for q in ["무중단 배포 절차", "휴가 신청", "API 인증", "백업"]:
                hits = searcher.search(q, k=3)
                print(f"\n  질의: {q!r}")
                for h in hits:
                    print(f"     - {h.title}")
            print("\n데모 완료. 실제 데이터는 .env(자격증명) 설정 후 `geryon sync`로 수집·색인하세요.")
        except Exception as e:
            print(f"Demo failed: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "bootstrap":
        print("Bootstrapping Geryon environment...")
        try:
            # .env 가 없으면 현재 디렉터리에 내장 템플릿으로 생성(설치본엔 .env.sample 파일이
            # 없으므로 ENV_TEMPLATE 사용). 기존 .env 는 절대 덮어쓰지 않는다.
            from geryon.config import seed_env_file
            if seed_env_file(".env"):
                print("  설정 파일 생성: .env — 값을 채운 뒤 다시 실행하세요(또는 `geryon init`).")
            from geryon.store.db import init_db
            _ = init_db()
            from geryon.embed.embedder import LocalEmbedder
            _ = LocalEmbedder()
            # rerank 모델도 선다운로드(best-passage 주경로 — 콜드 11s를 설치 시점으로)
            try:
                from geryon.search.rerank import get_reranker
                from geryon.config import RERANK_MODEL
                _ = get_reranker(RERANK_MODEL)
                print("  rerank 모델 prefetch 완료.")
            except Exception as re:
                print(f"  (rerank prefetch 건너뜀: {re})", file=sys.stderr)
            print("Bootstrap completed successfully!")
        except Exception as e:
            print(f"Bootstrap failed: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "setup":
        print("GeryonMCP 온보딩(setup) — 맥/우분투/윈도우 공통")
        try:
            import os
            from pathlib import Path
            from geryon.config import seed_env_file, DB_PATH
            # 1) 설정 파일(.env) + 모델 캐시 (= bootstrap 과 동일). cwd 에 내장 템플릿으로 생성.
            if seed_env_file(".env"):
                print("  설정 파일 생성: .env")
            # 공유 DB 를 받을 거면 빈 DB 를 만들지 않는다(다운로드 DB 가 스키마 포함;
            # 다운로드 실패 시 '받은 척' 하는 빈 DB 가 남지 않도록).
            if not getattr(args, "db_url", None):
                from geryon.store.db import init_db
                _ = init_db()
            from geryon.embed.embedder import LocalEmbedder
            _ = LocalEmbedder()
            try:
                from geryon.search.rerank import get_reranker
                from geryon.config import RERANK_MODEL
                _ = get_reranker(RERANK_MODEL)
                print("  검색 모델 준비 완료(embed + rerank).")
            except Exception as re:
                print(f"  (rerank prefetch 건너뜀: {re})", file=sys.stderr)
            # 2) 공유 DB 다운로드(방식 B) — 표준 라이브러리만 사용(OS 무관, curl/Invoke-WebRequest 불필요)
            if getattr(args, "db_url", None):
                import urllib.request
                import urllib.error
                dest = Path(args.db_path) if getattr(args, "db_path", None) else DB_PATH
                dest.parent.mkdir(parents=True, exist_ok=True)
                # 인증 토큰: --token > 환경변수 > 대화형 입력(http(s) 이고 TTY 일 때)
                token = getattr(args, "token", None) or os.getenv("GERYON_DB_TOKEN")
                if not token and args.db_url.lower().startswith("http") and sys.stdin.isatty():
                    import getpass
                    token = getpass.getpass("  다운로드 토큰 또는 비밀번호(없으면 Enter): ").strip() or None
                print(f"  공유 DB 다운로드: {args.db_url}")
                print(f"           → {dest}")
                tmp = dest.with_suffix(dest.suffix + ".part")
                try:
                    # 토큰/비밀번호 자동 처리(비밀번호면 세션 로그인 폴백)
                    with _open_authed_url(args.db_url, token) as resp:
                        total = int(resp.headers.get("Content-Length", 0))
                        done = 0
                        with open(tmp, "wb") as f:
                            while True:
                                chunk = resp.read(256 * 1024)
                                if not chunk:
                                    break
                                f.write(chunk)
                                done += len(chunk)
                                if total:
                                    pct = min(100, done * 100 // total)
                                    print(f"\r    {pct}% ({done // (1024*1024)}/{total // (1024*1024)} MB)",
                                          end="", file=sys.stderr, flush=True)
                except urllib.error.HTTPError as he:
                    tmp.unlink(missing_ok=True)
                    hint = " — 토큰/비밀번호를 확인하세요." if he.code in (401, 403) else ""
                    raise RuntimeError(f"다운로드 실패 HTTP {he.code} {he.reason}{hint}") from None
                except Exception:
                    tmp.unlink(missing_ok=True)
                    raise
                tmp.replace(dest)  # 원자적 교체(부분 다운로드 방지)
                mb = dest.stat().st_size // (1024 * 1024)
                print(f"\n  DB 저장 완료: {dest} ({mb} MB)")
                print("  → mcp.json 의 GERYON_DB 가 이 경로를 가리키는지 확인하세요.")
            else:
                print("  (공유 DB 미지정 — 모델만 준비. 색인은 `geryon ingest`, 받기는 `--db-url`)")
            # DB 위치(이후 MCP·테스트가 공유)
            db_ref = Path(args.db_path).resolve() if getattr(args, "db_path", None) else DB_PATH
            # 3) MCP 등록  4) 샘플 검색
            if getattr(args, "mcp", None):
                _register_mcp(getattr(args, "mcp_dir", "."), args.mcp, db_ref)
            if getattr(args, "test", False):
                _sample_search(db_ref)
            print("Setup 완료! 에디터를 재시작하면 검색 MCP가 활성화됩니다.")
        except Exception as e:
            print(f"Setup failed: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "publish":
        print("GeryonMCP DB 공유(publish) — 로컬 인덱스를 업로드(덮어쓰기)")
        try:
            import shutil
            import subprocess
            from pathlib import Path
            from geryon.config import DB_PATH
            db = Path(args.db_path).resolve() if getattr(args, "db_path", None) else DB_PATH
            if not db.exists():
                print(f"로컬 DB 가 없습니다: {db} — 먼저 `geryon ingest` 로 색인하세요.", file=sys.stderr)
                sys.exit(1)
            dest = args.to
            mb = db.stat().st_size // (1024 * 1024)
            print(f"  업로드(기존 덮어쓰기): {db} ({mb} MB)")
            print(f"           → {dest}")
            if _is_scp_target(dest):
                if not shutil.which("scp"):
                    print("scp 가 필요합니다(SSH). 설치 후 다시 실행하세요.", file=sys.stderr)
                    sys.exit(1)
                r = subprocess.run(["scp", "-o", "ConnectTimeout=8", str(db), dest])
                if r.returncode != 0:
                    print(f"업로드 실패(scp 종료코드 {r.returncode}) — 접근/경로를 확인하세요.", file=sys.stderr)
                    sys.exit(1)
            else:
                d = Path(dest)
                if d.is_dir() or dest.endswith(("/", "\\")):
                    d = d / db.name
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(db, d)  # 덮어쓰기
            print("  업로드 완료. 받는 쪽은 `geryon setup --db-url/--db-path` 로 최신 DB 를 받습니다.")
            if getattr(args, "mcp", None):
                _register_mcp(getattr(args, "mcp_dir", "."), args.mcp, db)
            if getattr(args, "test", False):
                _sample_search(db)
            print("Publish 완료!")
        except Exception as e:
            print(f"Publish failed: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "health":
        print("Checking system health...")
        failures: list[str] = []

        try:
            from geryon.store.db import init_db
            conn = init_db()
            conn.close()
        except Exception as e:
            failures.append(f"Database health check failed: {e}")

        # 원본 데이터 디렉터리는 수집(ingest) 전엔 없는 게 정상 → 경고만(시스템 비정상 아님)
        try:
            from geryon.connectors.confluence import ConfluenceConnector
            connector = ConfluenceConnector()
            if not connector.healthcheck():
                print(f"  참고: 원본 데이터 '{connector.db_path}' 없음 — `geryon ingest` 전이면 정상.", file=sys.stderr)
        except Exception as e:
            print(f"  참고: connector 점검 건너뜀: {e}", file=sys.stderr)

        try:
            from geryon.embed.embedder import LocalEmbedder
            _ = LocalEmbedder()
        except Exception as e:
            failures.append(f"Model health check failed: {e}")

        if failures:
            for failure in failures:
                print(failure, file=sys.stderr)
            sys.exit(1)
        else:
            print("System healthy.")
            sys.exit(0)
    elif args.command == "status":
        import json
        from geryon.status import collect_status
        print(json.dumps(collect_status(), ensure_ascii=False))  # status는 데이터이므로 stdout
    elif args.command == "init":
        _run_init(args)
    elif args.command == "search":
        import json
        from geryon.config import DB_PATHS
        from geryon.search.factory import build_searcher
        from geryon.search.query import run_search
        searcher = build_searcher(db_paths=[str(p) for p in DB_PATHS])  # 단일/federation 공통
        result = run_search(
            searcher, args.query, k=args.k, offset=args.offset, user_id=args.user,
            sources=args.source, spaces=args.space, tags=args.tag, authors=args.author,
            date_from=args.date_from, date_to=args.date_to,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False))  # 데이터 → stdout
        else:
            hits = result["hits"]
            if not hits:
                print("(결과 없음)")
            for i, h in enumerate(hits, 1):
                title = (h["title"] or "")[:60]
                loc = f"{h['source']}/{h['space_or_repo'] or '-'}"
                print(f"{i:2}. [{h['score']:.3f}] {title}  ({loc})")
                if h.get("url"):
                    print(f"      {h['url']}")
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
