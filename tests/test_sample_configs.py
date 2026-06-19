import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from geryon.config import ensure_sample_configs

def test_creates_when_missing(tmp_path):
    # 자격증명은 env 전용 — 샘플은 .env 만 생성(mcp.json 자격증명 폴백 제거)
    (tmp_path / ".env.sample").write_text("KEY=sample\n")
    created = ensure_sample_configs(tmp_path)
    assert (tmp_path / ".env").read_text() == "KEY=sample\n"
    assert len(created) == 1

def test_preserves_existing(tmp_path):
    # 사용자 항목(실제값)은 절대 덮어쓰지 않는다
    (tmp_path / ".env.sample").write_text("KEY=sample\n")
    (tmp_path / ".env").write_text("KEY=MY_REAL_VALUE\n")
    created = ensure_sample_configs(tmp_path)
    assert (tmp_path / ".env").read_text() == "KEY=MY_REAL_VALUE\n"  # 보존
    assert str(tmp_path / ".env") not in created

def test_no_sample_no_action(tmp_path):
    assert ensure_sample_configs(tmp_path) == []
