"""geryon init / 소스 설정 소비 — .env 기록 + _build_acquirer 가 env 설정을 쓰는지."""
from types import SimpleNamespace
import pytest
import geryon.cli as cli
from geryon import config


def _args(**kw):
    base = dict(bronze_dir=None, days=30, all=False, since=None, until=None, space=None,
                max_pages=None, no_attachments=False, repo=None, depth=None, branch=None,
                project=None, max_issues=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_confluence_spaces_from_env_config(monkeypatch):
    monkeypatch.setattr(config, "CONFLUENCE_SPACES", ["ENG", "HR"])
    acq, _ = cli._build_acquirer(_args(), "confluence")
    assert acq.spaces == ["ENG", "HR"]


def test_confluence_space_flag_overrides_config(monkeypatch):
    monkeypatch.setattr(config, "CONFLUENCE_SPACES", ["ENG"])
    acq, _ = cli._build_acquirer(_args(space=["QA"]), "confluence")
    assert acq.spaces == ["QA"]


def test_git_repos_from_env_config(monkeypatch):
    monkeypatch.setattr(config, "GIT_REPOS", ["https://github.com/o/p"])
    acq, _ = cli._build_acquirer(_args(), "git")
    assert acq.repos == ["https://github.com/o/p"]


def test_jira_projects_from_env_config(monkeypatch):
    monkeypatch.setattr(config, "JIRA_PROJECTS", ["TDT", "ABC"])
    acq, _ = cli._build_acquirer(_args(), "jira")
    assert acq.projects == ["TDT", "ABC"]


def test_git_requires_repo_or_config(monkeypatch):
    monkeypatch.setattr(config, "GIT_REPOS", [])
    with pytest.raises(RuntimeError):
        cli._build_acquirer(_args(), "git")


def test_auto_window_uses_db_watermark(monkeypatch):
    # --days/--since/--all 미지정 → DB 워터마크 이후가 기본
    monkeypatch.setattr(cli, "_db_watermark_since", lambda s: "2026-05-01")
    acq, _ = cli._build_acquirer(_args(days=None), "confluence")
    assert acq.since == "2026-05-01"


def test_first_run_bootstraps_30_days(monkeypatch):
    # 워터마크 없음(빈 DB·첫 실행) → since 없음, 30일
    monkeypatch.setattr(cli, "_db_watermark_since", lambda s: None)
    acq, _ = cli._build_acquirer(_args(days=None), "confluence")
    assert acq.since is None and acq.days == 30


def test_explicit_days_overrides_watermark(monkeypatch):
    seen = {"n": 0}
    def wm(s):
        seen["n"] += 1
        return "2026-05-01"
    monkeypatch.setattr(cli, "_db_watermark_since", wm)
    acq, _ = cli._build_acquirer(_args(days=7), "confluence")   # 명시 → auto 아님
    assert acq.since is None and acq.days == 7 and seen["n"] == 0


def test_env_template_matches_sample():
    # 내장 ENV_TEMPLATE 와 저장소 .env.sample 일치(드리프트 방지)
    from pathlib import Path
    from geryon.config import ENV_TEMPLATE
    root = Path(__file__).resolve().parents[1]
    assert (root / ".env.sample").read_text(encoding="utf-8") == ENV_TEMPLATE


def test_seed_env_from_template(tmp_path):
    from geryon.config import seed_env_file, ENV_TEMPLATE
    p = tmp_path / ".env"
    assert seed_env_file(str(p)) is True
    assert p.read_text(encoding="utf-8") == ENV_TEMPLATE   # 템플릿 그대로 생성
    assert seed_env_file(str(p)) is False                  # 기존 .env 는 보존(덮어쓰기 X)


def test_env_write_uncomments_template_key(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# GERYON_CONFLUENCE_SPACES=ENG,HR\nCONFLUENCE_URL=placeholder\n", encoding="utf-8")
    cli._env_write({"GERYON_CONFLUENCE_SPACES": "QA", "CONFLUENCE_URL": "https://real"}, str(p))
    txt = p.read_text(encoding="utf-8")
    assert "GERYON_CONFLUENCE_SPACES=QA" in txt and "# GERYON_CONFLUENCE_SPACES" not in txt
    assert "CONFLUENCE_URL=https://real" in txt
    assert txt.count("GERYON_CONFLUENCE_SPACES=") == 1     # 중복 없이 주석 해제+치환


def test_env_write_create_then_update(tmp_path):
    p = tmp_path / ".env"
    cli._env_write({"A": "1", "B": "2"}, str(p))
    cli._env_write({"A": "9", "C": "3"}, str(p))   # A 갱신, C 추가, B 보존
    txt = p.read_text(encoding="utf-8")
    assert "A=9" in txt and "B=2" in txt and "C=3" in txt
    assert txt.count("A=") == 1   # 중복 추가가 아니라 교체
