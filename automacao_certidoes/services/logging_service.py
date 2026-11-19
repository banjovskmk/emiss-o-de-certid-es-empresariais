"""Log em arquivo com remoção de segredos (senhas, tokens, cookies)."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET = re.compile(r"(?i)(senha|password|passwd|token|cookie|authorization|secret)(\s*[=:]\s*)\S+")


def sanitize(text: str) -> str:
    return _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text or "")


class _SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return sanitize(super().format(record))


def setup_logging(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("certidoes")
    logger.setLevel(level)
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(log_dir / "automacao.log", maxBytes=2_000_000,
                                      backupCount=5, encoding="utf-8")
        handler.setFormatter(_SanitizingFormatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
    return logger
