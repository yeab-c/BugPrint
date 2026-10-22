"""Secret redaction for error text and other strings sent to the DB or to Bedrock.

Keeps the approach simple: regex patterns for common secret shapes.
If a string looks like a secret (key=value, token, password, AWS key, etc.),
replace the value with [REDACTED].

This is a best-effort filter, not a security guarantee.
"""

from __future__ import annotations

import re

# Patterns are tried in order. Each pattern must have a single named group
# called `secret` that captures only the sensitive value (not the key).
_PATTERNS: list[re.Pattern[str]] = [
    # AWS access key id (starts with AKIA or ASIA)
    re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),
    # Generic key=value / key: value where key suggests a secret
    re.compile(
        r"(?i)(?P<key>"
        r"(?:password|passwd|pwd|secret|token|api[_-]?key|auth[_-]?key|"
        r"access[_-]?key|private[_-]?key|client[_-]?secret|"
        r"aws[_-]?secret|bearer)"
        r")\s*[=:]\s*(?P<secret>[^\s,;'\"\n]{4,})",
    ),
    # Bearer token in Authorization header
    re.compile(r"(?i)Authorization:\s*Bearer\s+(?P<secret>[A-Za-z0-9\-._~+/]+=*)"),
    # Generic long hex/base64-ish strings after common key words
    re.compile(
        r"(?i)(?P<key>"
        r"(?:x-api-key|x-auth-token|x-access-token)"
        r"):\s*(?P<secret>[A-Za-z0-9\-._~+/]{20,})",
    ),
]

# Replacement text used for every redacted value
_PLACEHOLDER = "[REDACTED]"


def redact(text: str) -> str:
    """Return a copy of `text` with detected secrets replaced by [REDACTED].

    The function is idempotent: calling it twice gives the same result.
    """
    for pattern in _PATTERNS:
        if "secret" in pattern.groupindex:
            # Replace only the captured `secret` group
            text = pattern.sub(_replace_secret, text)
        else:
            # Whole match is the secret (e.g. AWS key id)
            text = pattern.sub(_PLACEHOLDER, text)
    return text


def _replace_secret(m: re.Match[str]) -> str:
    """Substitution function that keeps the key and replaces only the secret."""
    span_start, span_end = m.span("secret")
    full_start, _ = m.span()
    prefix = m.group()[: span_start - full_start]
    return prefix + _PLACEHOLDER
