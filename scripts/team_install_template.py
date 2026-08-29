#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""GeryonMCP 팀 배포 — 원클릭 설치 템플릿.

    uv run team_install_template.py    (권장; 또는  python3 team_install_template.py)

wheel·공유 인덱스 DB 를 받아 → uv tool install → geryon setup
(.cursor/.mcp.json 등록 + 샘플 검색) 까지 한 번에 끝냅니다.
맥 · 우분투 · 윈도우 공통(표준 라이브러리만 사용; 셸 스크립트 불필요).

다운로드는 **scp 우선**(SSH, 빠름) → 실패 시 **HTTP 폴백**(느릴 수 있음).
HTTP 폴백 시에만 토큰/비밀번호를 입력받습니다(scp 로 받으면 불필요).

── 이 템플릿을 실제로 쓰려면 ──────────────────────────────────────────
이 파일을 배포 대상 레포(팀원이 clone 하는 곳)로 복사하고, 아래 "설정"
블록의 5개 상수를 팀 환경에 맞게 바꾸세요(또는 같은 이름의 환경변수로
오버라이드). WHEEL 은 배포 서버에 항상 고정 파일명(geryonmcp-latest.whl)
으로 올려두면 버전이 바뀌어도 이 스크립트를 고칠 필요가 없습니다.
자세한 배포 절차는 GeryonMCP 의 docs/TEAM_DEPLOY.md 참고.
─────────────────────────────────────────────────────────────────────
"""
import getpass
import http.cookiejar
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ── 설정 — 팀 환경에 맞게 편집(또는 환경변수로 오버라이드) ──────────────
# scp 우선(빠름): user@host:dir
SCP_REMOTE = os.getenv("GERYON_INSTALL_SCP_REMOTE", "user@host:/path/to/dist")
# HTTP 폴백(/files/ 형태 = 파일 다운로드 엔드포인트; 트리/브라우징 URL 아님)
HTTP_BASE = os.getenv("GERYON_INSTALL_HTTP_BASE", "http://host:8888/files/dist")
# wheel 파일명은 PEP 427/440 규격(name-version-pytag-abitag-platform, 버전은 숫자로
# 시작)을 지켜야 해서 "latest" 같은 고정 별칭을 파일명 자체로 쓸 수 없다. 대신 배포
# 서버에 실제 파일명(예: geryonmcp-1.2.0-py3-none-any.whl)을 한 줄 적어둔 "포인터"
# 파일을 두면, 이 스크립트는 포인터만 고정 이름으로 받아 실제 파일명을 알아낸다 —
# 매 릴리스마다 이 스크립트를 고칠 필요가 없다. 배포 시: `uv build` 후 wheel 그대로
# 올리고, 포인터 파일에 그 파일명 한 줄만 적어 덮어쓰면 된다.
WHEEL_POINTER = os.getenv("GERYON_INSTALL_WHEEL_POINTER", "LATEST_WHEEL")
DB_FILE = os.getenv("GERYON_INSTALL_DB_FILE", "myproj.db")
MCP_NAME = os.getenv("GERYON_INSTALL_MCP_NAME", "myproj-search")
# ─────────────────────────────────────────────────────────────────────

PROJ = Path(__file__).resolve().parent
DB_PATH = PROJ / ".geryon" / DB_FILE


def _open_authed(url: str, secret: str):
    """토큰 우선(헤더+?token=), HTML(로그인 페이지)이면 비밀번호 세션 로그인 폴백."""
    def is_html(resp) -> bool:
        return "text/html" in (resp.headers.get("Content-Type") or "").lower()

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    sep = "&" if "?" in url else "?"
    turl = f"{url}{sep}token={urllib.parse.quote(secret)}"
    r = opener.open(urllib.request.Request(turl, headers={"Authorization": f"token {secret}"}))
    if not is_html(r):
        return r
    r.read()  # 로그인 페이지 — 비밀번호 폴백
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    page = opener.open(base + "/login").read().decode("utf-8", "ignore")
    m = re.search(r'name="_xsrf"[^>]*value="([^"]+)"', page)
    xsrf = m.group(1) if m else next((c.value for c in cj if c.name == "_xsrf"), "")
    body = urllib.parse.urlencode({"_xsrf": xsrf, "password": secret}).encode()
    opener.open(urllib.request.Request(base + "/login", data=body, headers={"Referer": base + "/login"}))
    r2 = opener.open(urllib.request.Request(url))
    if is_html(r2):
        sys.exit("  인증 실패 — 토큰/비밀번호가 올바른지 확인하세요.")
    return r2


def _download(url: str, dest: Path, secret: str) -> None:
    try:
        with _open_authed(url, secret) as r:
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                while True:
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r  {done * 100 // total}%", end="", flush=True)
            tmp.replace(dest)
        print()
    except urllib.error.HTTPError as e:
        sys.exit(f"\n  다운로드 실패 HTTP {e.code} — 토큰/비밀번호를 확인하세요.")
    except urllib.error.URLError as e:
        sys.exit(f"\n  서버 연결 실패: {e.reason} — VPN/사내망 연결을 확인하세요.")


def _run(cmd: list, desc: str) -> None:
    """단계 실행 — 실패하면 즉시(다음 단계로 넘어가지 않고) 명확히 중단."""
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f"\n✖ 실패: {desc} (종료코드 {e.returncode}). 위 출력을 확인하고 다시 실행하세요.")
    except FileNotFoundError:
        sys.exit(f"\n✖ 실패: '{cmd[0]}' 명령을 찾을 수 없습니다.")


def _scp(name: str, dest: Path) -> bool:
    """scp 로 받기(SSH, 빠름). 성공하면 True. scp 없거나 실패하면 False(→ HTTP 폴백)."""
    if not shutil.which("scp"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = f"{SCP_REMOTE}/{name}"
    try:
        # 키 인증이면 자동, 아니면 비밀번호 프롬프트(대화형). 연결 안 되면 빠르게 실패.
        r = subprocess.run(["scp", "-o", "ConnectTimeout=8", src, str(dest)])
        return r.returncode == 0 and dest.exists()
    except Exception:
        return False


_secret = {"v": os.getenv("GERYON_INSTALL_TOKEN")}


def _token() -> str:
    """HTTP 폴백 시에만 호출 — 토큰/비밀번호를 lazy 하게 1회 입력받는다."""
    if not _secret["v"]:
        _secret["v"] = getpass.getpass("  다운로드 토큰 또는 비밀번호(HTTP 폴백용): ").strip()
        if not _secret["v"]:
            sys.exit("  scp 가 실패했고 토큰/비밀번호도 없습니다 — 둘 중 하나가 필요합니다.")
    return _secret["v"]


def _resolve_wheel_name() -> str:
    """포인터 파일(LATEST_WHEEL, 실제 wheel 파일명 한 줄)을 받아 그 내용을 반환한다."""
    tmp = PROJ / f".{WHEEL_POINTER}.tmp"
    try:
        if _scp(WHEEL_POINTER, tmp):
            name = tmp.read_text().strip()
        else:
            _download(f"{HTTP_BASE}/{WHEEL_POINTER}", tmp, _token())
            name = tmp.read_text().strip()
    finally:
        tmp.unlink(missing_ok=True)
    if not name:
        sys.exit(f"  포인터 파일({WHEEL_POINTER})이 비어 있습니다 — 배포 서버 설정을 확인하세요.")
    return name


def main() -> None:
    if not shutil.which("uv"):
        sys.exit("'uv' 가 필요합니다. 먼저 설치하세요:\n"
                 "  맥/우분투:  curl -LsSf https://astral.sh/uv/install.sh | sh\n"
                 "  윈도우(PS): powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"")

    print("GeryonMCP 설치 — scp(빠름) 우선, 실패 시 HTTP 폴백.")

    # 1) wheel 받기 — 먼저 포인터로 실제 파일명을 알아낸 뒤 (scp → HTTP)
    print(f"▶ wheel 파일명 확인: {WHEEL_POINTER}")
    wheel_name = _resolve_wheel_name()
    print(f"  → {wheel_name}")
    whl = PROJ / wheel_name
    if _scp(wheel_name, whl):
        print("  scp 로 받음")
    else:
        print("  scp 불가 → HTTP 로 받기")
        _download(f"{HTTP_BASE}/{wheel_name}", whl, _token())

    # 2) 설치 (uv tool — 격리, OS 무관)
    print("▶ 설치: uv tool install")
    _run(["uv", "tool", "install", "--force", str(whl)], "wheel 설치")
    whl.unlink(missing_ok=True)

    # 3) DB 받기 (scp → HTTP). scp 로 받으면 setup 은 다운로드를 건너뜀.
    print(f"▶ DB: {DB_FILE}")
    db_via_scp = _scp(DB_FILE, DB_PATH)
    print("  scp 로 받음" if db_via_scp else "  scp 불가 → geryon setup 이 HTTP 로 받음(느릴 수 있음)")

    # 4) geryon setup — (필요시 DB 다운로드) + MCP 등록 + 샘플 검색
    #    설치 직후라 PATH 갱신 전일 수 있어 uv tool run 으로 호출(견고).
    geryon = (["geryon"] if shutil.which("geryon")
              else ["uv", "tool", "run", "--from", "geryonmcp", "geryon"])
    setup_args = ["setup", "--db-path", str(DB_PATH),
                  "--mcp", MCP_NAME, "--mcp-dir", str(PROJ), "--test"]
    if not db_via_scp:  # scp 실패 → setup 이 HTTP 로 다운로드(토큰/비밀번호 필요)
        setup_args = ["setup", "--db-url", f"{HTTP_BASE}/{DB_FILE}", "--token", _token()] + setup_args[1:]
    print("▶ geryon setup: 모델 + MCP 등록 + 샘플 검색")
    _run(geryon + setup_args, "geryon setup (MCP 등록/샘플 검색)")

    print(f"\n✅ 완료! 에디터(Cursor/Claude/VS Code)를 재시작하면 "
          f"'{MCP_NAME}' 검색이 활성화됩니다.")


if __name__ == "__main__":
    main()
