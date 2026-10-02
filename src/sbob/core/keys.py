"""Caricamento unico delle chiavi API dall'ambiente.

Per un prefisso P legge, in ordine: P_ACCOUNT1, P_ACCOUNT2, ... fino al primo buco,
e in più P da solo se presente. Le chiavi duplicate vengono rimosse.
"""

from __future__ import annotations

import os


class MissingKeyError(Exception):
    def __init__(self, prefix: str):
        super().__init__(
            f"Nessuna chiave trovata per {prefix}. Aggiungi nel .env: {prefix}_ACCOUNT1=... (oppure {prefix}=...)"
        )
        self.prefix = prefix


def load_keys(prefix: str, required: bool = True) -> list[str]:
    keys: list[str] = []
    i = 1
    while v := os.environ.get(f"{prefix}_ACCOUNT{i}", "").strip():
        keys.append(v)
        i += 1
    if v := os.environ.get(prefix, "").strip():
        keys.append(v)
    keys = list(dict.fromkeys(keys))
    if required and not keys:
        raise MissingKeyError(prefix)
    return keys


def mask(key: str) -> str:
    """Rappresentazione sicura per i log."""
    return f"…{key[-4:]}" if len(key) > 8 else "…"
