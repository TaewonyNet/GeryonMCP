"""watch/sync 가 실행 폴더와 무관하게 설정을 찾고, 위험한 옵션 조합을 막는지."""
import sys

import pytest

from geryon import cli, config


def test_env_files_include_home_when_not_explicit(monkeypatch):
    monkeypatch.delenv("GERYON_ENV_FILE", raising=False)
    files = config._env_files()
    assert files[0].name == ".env" and not files[0].is_absolute()   # 실행 폴더 우선
    assert str(files[1]).endswith("/.geryon/.env")


def test_home_env_fills_keys_missing_from_cwd_env(tmp_path, monkeypatch):
    """systemd 처럼 설정 없는 폴더에서 돌아도 ~/.geryon/.env 로 설정을 찾는다."""
    home = tmp_path / "home"
    (home / ".geryon").mkdir(parents=True)
    (home / ".geryon" / ".env").write_text(
        "GERYON_T_A=home\nGERYON_T_B=home\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("GERYON_T_A=cwd\n", encoding="utf-8")
    for k in ("GERYON_T_A", "GERYON_T_B"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    monkeypatch.delenv("GERYON_ENV_FILE", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "_ENV_FILE_DIR", {})
    monkeypatch.chdir(work)

    config._load_dotenv()
    import os
    assert os.environ["GERYON_T_A"] == "cwd"     # 실행 폴더 .env 가 우선
    assert os.environ["GERYON_T_B"] == "home"    # 없는 키는 ~/.geryon/.env 에서
    assert config._ENV_FILE_DIR["GERYON_T_B"] == (home / ".geryon").resolve()


def _run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["geryon", *argv])
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


def test_watch_rejects_full(monkeypatch, capsys):
    assert _run_cli(monkeypatch, "watch", "--source", "confluence", "--full") == 2
    assert "--full" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", ["watch", "sync"])
def test_bronze_dir_requires_source(cmd, monkeypatch, capsys, tmp_path):
    assert _run_cli(monkeypatch, cmd, "--bronze-dir", str(tmp_path)) == 2
    assert "--source" in capsys.readouterr().err


def test_sync_lock_serializes(tmp_path, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "g.db")
    held = cli._hold_sync_lock()
    other = open(tmp_path / "g.db.sync.lock", "w")
    with pytest.raises(BlockingIOError):
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)   # 쥐고 있는 동안 다른 sync 는 못 잡음
    held.close()
    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)       # 놓으면 잡힌다
    other.close()


def test_sync_all_skips_unconfigured_sources(monkeypatch, capsys, tmp_path):
    """소스 미지정 sync(=watch 가 매 주기 부르는 것)는 설정 없는 git/jira 를 오류로 세지 않는다."""
    monkeypatch.setattr(config, "GIT_REPOS", [])
    monkeypatch.setattr(config, "JIRA_PROJECTS", [])
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    ran = []

    def fake(a, s):
        ran.append(s)
        raise RuntimeError("stop")
    monkeypatch.setattr(cli, "_build_acquirer", fake)
    monkeypatch.setattr(sys, "argv", ["geryon", "sync", "--dry-run"])
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out
    assert ran == ["confluence"]
    assert "git — 설정 없음" in out and "jira — 설정 없음" in out
