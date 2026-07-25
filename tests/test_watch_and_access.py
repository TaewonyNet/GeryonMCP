"""watch 자식 명령 프리픽스 + Confluence 스페이스 접근 점검(check_space_access) 단위 테스트."""
import sys

from geryon.acquire.confluence_atlassian import check_space_access
from geryon.cli import _geryon_cmd_prefix


# ── _geryon_cmd_prefix — geryon 이 PATH 에 없어도 깨지지 않는 자식 실행 프리픽스 ──

def test_prefix_uses_which_when_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _n: "/usr/local/bin/geryon")
    assert _geryon_cmd_prefix() == ["/usr/local/bin/geryon"]


def test_prefix_falls_back_to_interpreter_plus_script(monkeypatch):
    # PATH 에 geryon 이 없을 때: python 단독(=`python sync`, 깨짐)이 아니라 스크립트 경로를 동반해야 함.
    monkeypatch.setattr("shutil.which", lambda _n: None)
    assert _geryon_cmd_prefix() == [sys.executable, sys.argv[0]]


# ── check_space_access — 접근 불가 스페이스 확정 ──

class _StubClient:
    """list_spaces + _get 만 흉내내는 스텁. accessible=전역목록, has_pages=조회 시 결과 있는 스페이스."""

    def __init__(self, accessible, has_pages=None):
        self._accessible = list(accessible)
        self._has_pages = set(has_pages or [])
        self.probes = []  # 프로브된 스페이스 키 기록(호출 횟수 검증용)

    def list_spaces(self):
        return [(k, k) for k in self._accessible]

    def _get(self, path, params=None, retries=1):
        # cql 에서 space="KEY" 추출
        cql = (params or {}).get("cql", "")
        key = cql.split('space="', 1)[1].split('"', 1)[0]
        self.probes.append(key)
        return {"results": [{"id": "1"}] if key in self._has_pages else []}


def test_all_accessible_returns_empty(tmp_path):
    c = _StubClient(accessible=["A", "B", "C"])
    assert check_space_access(c, ["A", "B"], tmp_path) == []
    assert c.probes == []  # 전역 목록에 다 있으니 프로브 불필요


def test_missing_but_probe_finds_pages_not_lost(tmp_path):
    # X 는 전역목록엔 없지만(개인/비전역) 실제 조회 시 페이지가 있음 → 접근 가능, 불가 아님.
    c = _StubClient(accessible=["A"], has_pages=["X"])
    assert check_space_access(c, ["A", "X"], tmp_path) == []
    assert c.probes == ["X"]


def test_missing_and_empty_is_lost(tmp_path):
    c = _StubClient(accessible=["A"], has_pages=[])
    assert check_space_access(c, ["A", "GONE"], tmp_path) == ["GONE"]


def test_empty_accessible_shortcircuits_without_probing(tmp_path):
    # 전역 목록이 통째로 비었다 = 자격증명 무력화 → 프로브 없이 전부 불가로 확정.
    c = _StubClient(accessible=[])
    lost = check_space_access(c, ["A", "B", "C"], tmp_path)
    assert lost == ["A", "B", "C"]
    assert c.probes == []


def test_probe_cap_limits_http_calls(tmp_path):
    # 접근 불가 후보가 많아도 프로브는 최대 30회까지만(초과분은 확인 생략하고 불가로 간주).
    accessible = ["ROOT"]  # 비어있지 않게 해서 프로브 경로를 타게 함
    expected = [f"S{i}" for i in range(50)]
    c = _StubClient(accessible=accessible, has_pages=[])
    lost = check_space_access(c, expected, tmp_path)
    assert lost == expected           # 50개 모두 불가로 보고
    assert len(c.probes) == 30        # 그러나 실제 HTTP 프로브는 30회로 제한


def test_list_spaces_failure_skips_check(tmp_path):
    class _Boom:
        def list_spaces(self):
            raise RuntimeError("401")

    assert check_space_access(_Boom(), ["A"], tmp_path) == []


def test_empty_expected_returns_empty(tmp_path):
    c = _StubClient(accessible=["A"])
    assert check_space_access(c, [], tmp_path) == []
