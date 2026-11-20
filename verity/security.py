"""Privacy guard: redact secrets/PII before anything is embedded or stored."""
from __future__ import annotations

import re

# Order matters: more specific patterns first.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("api_key", re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[abp]|glpat)[-_][A-Za-z0-9_-]{16,}\b")),
    ("connection_string", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@]+@[^\s]+", re.I)),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    (
        "password",
        re.compile(
            r"(?i)\b(password|passwd|pwd|passphrase|secret|token|api[_ ]?key)\b(\s*(?:is|=|:)\s*)([^\s,;]+)"
        ),
    ),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("phone", re.compile(r"(?<!\w)\+?\d{1,3}[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")),
]

_OFF_RECORD = re.compile(
    r"(?i)\b(off the record|don'?t (?:remember|save|store) (?:this|that)|do not (?:remember|save|store))\b"
)


def _luhn_ok(digits: str) -> bool:
    nums = [int(d) for d in digits if d.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def redact(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, sorted list of finding kinds)."""
    findings: set[str] = set()
    out = text
    for kind, pat in _PATTERNS:
        def _sub(m: re.Match, kind=kind) -> str:
            if kind == "credit_card" and not _luhn_ok(m.group(0)):
                return m.group(0)
            findings.add(kind)
            if kind == "password":
                return f"{m.group(1)}{m.group(2)}[REDACTED:secret]"
            return f"[REDACTED:{kind}]"

        out = pat.sub(_sub, out)
    return out, sorted(findings)


def is_off_record(text: str) -> bool:
    return bool(_OFF_RECORD.search(text))
