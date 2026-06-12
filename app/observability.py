"""Logging helpers for v2."""

from __future__ import annotations

import logging
import re
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]+"),
    re.compile(r"pcsk_[A-Za-z0-9_-]+"),
)


def redact_secrets(value: Any) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class SecretRedactionFilter(logging.Filter):
    """Defence-in-depth filter for provider logs and exception messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.getMessage())
        record.args = ()
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True
