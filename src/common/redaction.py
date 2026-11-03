"""Secret redaction applied at every logging/audit boundary.

Security rule (ENGINEERING_REFERENCE.md §5, SAS.md §8.0): secrets never reach
logs, audit entries, checkpoints, or escalation payloads.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|"
    r"connection[_-]?string|account[_-]?key|sas[_-]?token|credential)",
    re.IGNORECASE,
)
_SAFE_SUFFIXES = ("_ref", "_name", "_id", "_path", "_urn", "_endpoint")

_TEXT_PATTERNS = (
    re.compile(
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|account[_-]?key)\b"
        r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered.endswith(_SAFE_SUFFIXES):
        return False
    return bool(_SENSITIVE_KEY.search(key))


def redact_text(text: str) -> str:
    """Redact credential-shaped substrings in free text."""
    result = text
    for pattern in _TEXT_PATTERNS:
        if pattern.groups == 3:  # key, separator, value
            result = pattern.sub(lambda m: m.group(1) + m.group(2) + REDACTED, result)
        else:  # bearer/authorization prefix, value
            result = pattern.sub(lambda m: m.group(1) + REDACTED, result)
    return result


def redact(value: Any) -> Any:
    """Recursively redact mapping keys and credential-shaped strings."""
    if isinstance(value, dict):
        clean: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                clean[key] = REDACTED
            else:
                clean[key] = redact(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
