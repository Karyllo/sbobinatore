"""Utilità sul testo delle trascrizioni."""

from __future__ import annotations

import re

_NORM = re.compile(r"[\W_]+", re.UNICODE)


def _norm(word: str) -> str:
    return _NORM.sub("", word.lower())


_FENCE_LINE = re.compile(r"^\s*(```|~~~)")
_DISPLAY = re.compile(r"\\\[(.+?)\\\]", re.S)
_INLINE = re.compile(r"\\\((.+?)\\\)", re.S)


def normalize_math(text: str) -> str:
    """Formule con i delimitatori di Obsidian: `\\( x \\)` → `$x$`, `\\[ ... \\]` → `$$ ... $$`.
    I modelli (DeepSeek soprattutto) a volte usano la forma LaTeX pura, che Obsidian non renderizza e che molti viewer
    Markdown mangiano lasciando `( x )` e `[ ... ]`. Non tocca i blocchi di codice. Idempotente."""
    out: list[str] = []
    buf: list[str] = []
    fence: str | None = None

    def flush() -> None:
        if buf:
            chunk = "\n".join(buf)
            chunk = _DISPLAY.sub(lambda m: f"$$\n{m.group(1).strip()}\n$$" if "\n" in m.group(1)
                                 else f"$${m.group(1).strip()}$$", chunk)
            out.append(_INLINE.sub(lambda m: f"${m.group(1).strip()}$", chunk))
            buf.clear()

    for line in text.split("\n"):
        m = _FENCE_LINE.match(line)
        if m and fence is None:
            flush()
            fence = m.group(1)
            out.append(line)
        elif m and fence == m.group(1):
            fence = None
            out.append(line)
        elif fence is not None:
            out.append(line)
        else:
            buf.append(line)
    flush()
    return "\n".join(out)


def collapse_repetitions(text: str, max_ngram: int = 8, unigram_min: int = 6, ngram_min: int = 4) -> str:
    """Riduce a una sola occorrenza i loop di allucinazione dei trascrittori: lo stesso n-gramma ripetuto di fila
    almeno `unigram_min` volte (parole singole) o `ngram_min` volte (gruppi da 2..max_ngram parole).
    Ripetizioni brevi legittime ("no no no") restano intatte."""
    words = text.split()
    norm = [_norm(w) for w in words]
    out: list[str] = []
    i, n_words = 0, len(words)
    while i < n_words:
        for n in range(1, max_ngram + 1):
            gram = norm[i:i + n]
            if len(gram) < n or not any(gram):
                continue
            k = 1
            while norm[i + k * n: i + (k + 1) * n] == gram:
                k += 1
            if k >= (unigram_min if n == 1 else ngram_min):
                out.extend(words[i:i + n])
                i += k * n
                break
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def paragraphs(text: str, target_chars: int = 500) -> str:
    """Spezza un testo continuo in paragrafi di ~target_chars (alla prima parola oltre la soglia)."""
    blocks, cur, size = [], [], 0
    for w in text.split():
        cur.append(w)
        size += len(w) + 1
        if size >= target_chars:
            blocks.append(" ".join(cur))
            cur, size = [], 0
    if cur:
        blocks.append(" ".join(cur))
    return "\n\n".join(blocks)


_TS = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]")


def format_ts(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"[{h}:{m:02d}:{sec:02d}]" if h else f"[{m:02d}:{sec:02d}]"


def shift_timestamps(text: str, offset_seconds: float) -> str:
    """Sposta in avanti i [mm:ss] di un segmento audio che inizia a `offset_seconds`."""
    def repl(m: re.Match) -> str:
        h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        return format_ts(h * 3600 + mi * 60 + s + offset_seconds)
    return _TS.sub(repl, text) if offset_seconds else text


def strip_timestamps(text: str) -> str:
    """Toglie i [mm:ss] (per gli appunti: restano solo nelle trascrizioni)."""
    cleaned = re.sub(r"[ \t]*" + _TS.pattern + r"[ \t]*", " ", text)
    return "\n".join(line.strip() for line in cleaned.split("\n"))
