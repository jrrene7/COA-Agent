import logging
import os
import re
from typing import Any


_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_REDACTED = "[REDACTED]"

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(
        r"(?i)(api[_-]?key|token|secret|password|authorization)"
        r"([\"']?\s*[:=]\s*[\"']?)([^\"'\s,;]+)"
    ),
]


def redact_text(value: str) -> str:
    """Redact common API keys, tokens, and password-like values."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            redacted = pattern.sub(
                lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
                redacted,
            )
        else:
            redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets before a record is formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact_text(message)
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the final rendered record, including tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(level: int | str | None = None) -> None:
    """Configure application logging with redaction and quieter dependencies."""
    if level is None:
        level = os.getenv("COA_LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(_DEFAULT_FORMAT))
    handler.addFilter(RedactingFilter())

    logging.basicConfig(level=level, handlers=[handler], force=True)

    for logger_name in ("httpx", "openai"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
