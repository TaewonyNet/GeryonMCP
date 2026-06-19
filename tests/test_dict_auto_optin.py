"""자동 유의어 사전(dictionary.auto.yaml)은 기본 OFF(opt-in) — GERYON_DICT_AUTO=1 일 때만 로드.
수동 사전(dictionary.yaml)은 플래그와 무관하게 항상 로드된다."""
import yaml
import geryon.config as config
from geryon.gold.search import GoldSearch


class _DummyRetriever:
    def search(self, *a, **k):
        return []


def _setup_gold(tmp_path):
    gold = tmp_path / ".geryon" / "gold"
    gold.mkdir(parents=True)
    (gold / "dictionary.yaml").write_text(
        yaml.safe_dump([{"term": "수동term", "synonyms": ["manual"]}], allow_unicode=True),
        encoding="utf-8")
    (gold / "dictionary.auto.yaml").write_text(
        yaml.safe_dump([{"term": "자동term", "synonyms": ["auto"]}], allow_unicode=True),
        encoding="utf-8")
    return tmp_path


def _terms(monkeypatch, tmp_path, auto_enabled):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(config, "DICT_AUTO_ENABLED", auto_enabled)
    gs = GoldSearch(retriever=_DummyRetriever())
    return {e.term for e in gs.dictionary}


def test_auto_dict_off_by_default(monkeypatch, tmp_path):
    _setup_gold(tmp_path)
    terms = _terms(monkeypatch, tmp_path, auto_enabled=False)
    assert "수동term" in terms          # 수동은 항상
    assert "자동term" not in terms      # 자동은 기본 OFF


def test_auto_dict_loaded_when_optin(monkeypatch, tmp_path):
    _setup_gold(tmp_path)
    terms = _terms(monkeypatch, tmp_path, auto_enabled=True)
    assert "수동term" in terms
    assert "자동term" in terms          # opt-in 시 병합
