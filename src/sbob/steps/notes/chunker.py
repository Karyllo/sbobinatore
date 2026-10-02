"""Divisione della trascrizione in blocchi di dimensione uniforme (per parole). Comportamento identico all'originale."""

from __future__ import annotations

import math
import re

DEFAULT_CHUNK_WORDS = 850


def split_mechanically(text: str, num_chunks: int) -> list[str]:
    """num_chunks blocchi uniformi per parole; il resto è distribuito sui primi (nessun chunk vuoto o sproporzionato)."""
    words = text.split()
    if not words:
        return []
    base, rem = divmod(len(words), num_chunks)
    chunks, cursor = [], 0
    for i in range(num_chunks):
        size = base + (1 if i < rem else 0)
        chunks.append(" ".join(words[cursor:cursor + size]))
        cursor += size
    return chunks


def chunk_text(raw: str, chunk_words: int = DEFAULT_CHUNK_WORDS) -> list[str]:
    clean = re.sub(r"\s+", " ", raw).strip()
    num = max(1, math.ceil(len(clean.split()) / chunk_words))
    return split_mechanically(clean, num)
