"""Classificazione unica degli errori degli SDK → ErrorKind. Si basa su codice di stato e tipo di eccezione;
il testo del messaggio serve solo per distinguere quota giornaliera da rate limit dentro un 429."""

from __future__ import annotations

import re

from sbob.llm.base import ErrorKind

# Solo segnali di quota NON temporanea. Attenzione: Google scrive "You exceeded your current quota" anche per i
# limiti al minuto (PerMinute), quindi quella frase da sola NON basta: serve "PerDay" o un problema di credito.
_QUOTA_HINTS = ("per day", "perday", "daily", "insufficient_quota", "insufficient balance", "credit")
# Google allega sempre "check your plan and billing details": la verità sta nel quotaId (…PerDay… vs …PerMinute…)
_QUOTA_ID = re.compile(r"quota_?id['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9_-]+)", re.IGNORECASE)


def status_of(exc: BaseException) -> int | None:
    """Codice HTTP: `status_code` (openai/anthropic) o `code` (google-genai)."""
    for attr in ("status_code", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    return None


def classify(exc: BaseException) -> ErrorKind:
    status = status_of(exc)
    msg = str(exc).lower().replace("_", "").replace("-", "") + " " + str(exc).lower()
    if status == 429:
        ids = _QUOTA_ID.findall(str(exc))
        if ids:
            return ErrorKind.QUOTA if any("perday" in i.lower() for i in ids) else ErrorKind.RATE_LIMIT
        return ErrorKind.QUOTA if any(h in msg for h in _QUOTA_HINTS) else ErrorKind.RATE_LIMIT
    if status == 402:
        return ErrorKind.QUOTA
    if status in (401, 403):
        return ErrorKind.AUTH
    if status is not None and status >= 500:
        return ErrorKind.SERVER
    if status in (408, 409):
        return ErrorKind.SERVER
    if status is not None and 400 <= status < 500:
        return ErrorKind.BAD_REQUEST
    name = type(exc).__name__.lower()
    if any(w in name for w in ("timeout", "connection", "network", "overloaded", "deadline")):
        return ErrorKind.SERVER
    return ErrorKind.OTHER
