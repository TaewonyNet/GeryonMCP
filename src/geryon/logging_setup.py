"""구조적 로깅 설정. 전 모듈 `geryon.*` 로거, stderr 출력."""
import json
import logging
import os
import sys


def setup_logging() -> None:
    """GERYON_LOG_LEVEL / GERYON_LOG_FORMAT(text|json)로 제어. stdout은 MCP stdio 전용 → stderr만."""
    level = os.getenv("GERYON_LOG_LEVEL", "INFO").upper()
    fmt = os.getenv("GERYON_LOG_FORMAT", "text").lower()
    handler = logging.StreamHandler(sys.stderr)

    if fmt == "json":
        class _JsonFmt(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return json.dumps(
                    {"level": record.levelname, "logger": record.name, "msg": record.getMessage()},
                    ensure_ascii=False,
                )
        handler.setFormatter(_JsonFmt())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger("geryon")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
