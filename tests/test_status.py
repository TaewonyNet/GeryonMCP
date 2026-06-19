import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.status import collect_status  # noqa: E402
from geryon.logging_setup import setup_logging  # noqa: E402
from geryon.store.db import init_db  # noqa: E402


def test_status_fields(tmp_path):
    db = tmp_path / "s.db"
    init_db(db).close()
    st = collect_status(db)
    for k in ["documents", "chunks", "chunk_embeddings", "tree_nodes", "categories", "by_source", "sqlite_vec", "model"]:
        assert k in st
    assert st["documents"] == 0
    assert st["sqlite_vec"] in (True, False)


def test_logging_to_stderr(capsys):
    setup_logging()
    logging.getLogger("geryon.x").info("hello-stderr")
    captured = capsys.readouterr()
    assert "hello-stderr" in captured.err       # stderr로
    assert "hello-stderr" not in captured.out # stdout 오염 금지
