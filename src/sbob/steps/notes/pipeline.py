"""Pipeline di una lezione: chunk → refiner (parallelo) → notes (parallelo) → assemblaggio.

Resilienza (come nell'originale):
  - refiner fallito → si usa il chunk originale
  - notes fallito   → banner ERRORE nel testo; l'output va in <stem>.parziale.md (non _appunti.md) così il run dopo lo rifà
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from sbob.core import prompts
from sbob.core.batch import atomic_write_text
from sbob.core.report import NeedsHuman
from sbob.core.text import normalize_math
from sbob.llm.base import Message
from sbob.llm.registry import Role
from sbob.steps.notes.chunker import chunk_text

CONTEXT_WORDS = 100
# Gli appunti NON devono riassumere: un blocco molto più corto del suo input è contenuto perso (visto in prova:
# Gemini ha risposto con 1 token, senza errore, e il blocco è sparito in silenzio).
MIN_NOTES_RATIO = 0.5
MIN_REFINED_RATIO = 0.7
_MARKERS = {"it": ("Inizio", "Fine", "ARGOMENTI"), "en": ("Start", "End", "TOPICS")}
SEPARATOR = "\n\n---\n\n"


class ChunkCache:
    """Risultati per blocco già pagati, in .sbob/cache/<stem>/: se il run si interrompe (quota, crash) si riprende.
    La chiave include modello e testo in ingresso, quindi cambiare modello o trascrizione invalida la cache."""

    def __init__(self, folder: Path | None):
        self.folder = folder

    def _path(self, kind: str, i: int, model: str, text: str) -> Path | None:
        if not self.folder:
            return None
        h = hashlib.sha256(f"{model}\0{text}".encode()).hexdigest()[:12]
        return self.folder / f"{kind}_{i:03d}_{h}.md"

    def get(self, kind: str, i: int, model: str, text: str) -> str | None:
        p = self._path(kind, i, model, text)
        return p.read_text(encoding="utf-8") if p and p.exists() else None

    def put(self, kind: str, i: int, model: str, text: str, result: str) -> None:
        if p := self._path(kind, i, model, text):
            atomic_write_text(p, result)


NO_CACHE = ChunkCache(None)


def wall_of_text(text: str) -> str | None:
    """Validatore: output lungo senza a capo = formattazione scadente → il registry ritenta."""
    return "muro di testo" if len(text) > 500 and text.count("\n") < 5 else None


def notes_validator(chunk: str) -> Callable[[str], str | None]:
    """Muro di testo oppure blocco troppo corto rispetto all'input → rifiuta e ritenta."""
    need = int(len(chunk.split()) * MIN_NOTES_RATIO)

    def check(text: str) -> str | None:
        got = len(text.split())
        if got < need:
            return f"blocco troppo corto ({got} parole, ne servono almeno {need}): contenuto perso"
        return wall_of_text(text)
    return check


def split_topics(text: str, lingua: str) -> tuple[str, list[str]]:
    """Toglie un'eventuale riga `ARGOMENTI: a; b; c` (se il modello la aggiunge di sua iniziativa) e la restituisce.
    Gli argomenti non vanno più negli appunti: la mappa (`sbob mappa`) produce concetti uniformi a parte."""
    key = _MARKERS.get(lingua, _MARKERS["it"])[2]
    rx = re.compile(rf"^\s*\**{key}\**\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    topics: list[str] = []
    for m in rx.finditer(text):
        topics = [t.strip(" .*") for t in m.group(1).split(";") if t.strip(" .*")]
    return rx.sub("", text).rstrip() + "\n", topics


def refine_chunk(role: Role, i: int, chunks: list[str], lingua: str, item: str, cache: ChunkCache = NO_CACHE) -> str:
    start, end, _ = _MARKERS.get(lingua, _MARKERS["it"])
    prev = start if i == 0 else " ".join(chunks[i - 1].split()[-CONTEXT_WORDS:])
    nxt = end if i >= len(chunks) - 1 else " ".join(chunks[i + 1].split()[:CONTEXT_WORDS])
    prompt = prompts.render(lingua, "refiner", previous_context=prev, current_chunk=chunks[i], next_context=nxt)
    if (hit := cache.get("refined", i, role.model, prompt)) is not None:
        return hit
    res = role.complete([Message.user(prompt)], item=f"{item}#{i + 1}")
    if not res.ok or not res.text or len(res.text.split()) < MIN_REFINED_RATIO * len(chunks[i].split()):
        return chunks[i]                          # fallback sicuro: il testo originale (non in cache: si ritenta)
    cache.put("refined", i, role.model, prompt, res.text)
    return res.text


def notes_chunk(role: Role, i: int, chunk: str, total: int, lingua: str, item: str,
                cache: ChunkCache = NO_CACHE) -> tuple[str, str | None]:
    """(testo, errore). In caso di errore il testo è il banner da inserire."""
    prompt = prompts.render(lingua, "notes", variante=role.provider_name, chunk_text=chunk, part_number=str(i + 1),
                            total_parts=str(total))
    prompt += "\n\n" + prompts.load(lingua, "notes_extra", role.provider_name).strip()
    if (hit := cache.get("notes", i, role.model, prompt)) is not None:
        return normalize_math(hit), None
    res = role.complete([Message.user(prompt)], item=f"{item}#{i + 1}", validate=notes_validator(chunk))
    if res.ok and res.text:
        cache.put("notes", i, role.model, prompt, res.text)
        return normalize_math(res.text), None
    return f"⚠️ ERRORE (blocco {i + 1}/{total}): {res.error}", res.error or "errore"


def _parallel(fn: Callable, args: list[tuple], workers: int) -> list:
    if workers <= 1 or len(args) <= 1:
        return [fn(*a) for a in args]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, *a) for a in args]
        try:
            return [f.result() for f in futures]
        except NeedsHuman:
            for f in futures:
                f.cancel()
            raise


def generate_notes(raw: str, refiner: Role, notes: Role, lingua: str, item: str, chunk_words: int,
                   log: Callable[[str], None] = lambda m: None,
                   cache: ChunkCache = NO_CACHE) -> tuple[str, list[str], list[str]]:
    """(testo finale, argomenti, errori). Se errori non è vuoto l'output è parziale."""
    chunks = chunk_text(raw, chunk_words)
    log(f"{item}: {len(chunks)} blocchi, raffinamento con {refiner.label}")
    refined = _parallel(refine_chunk, [(refiner, i, chunks, lingua, item, cache) for i in range(len(chunks))],
                        refiner.workers)
    log(f"{item}: generazione appunti con {notes.label}")
    outs = _parallel(notes_chunk, [(notes, i, c, len(refined), lingua, item, cache) for i, c in enumerate(refined)],
                     notes.workers)
    sections, topics, errors = [], [], []
    for text, err in outs:
        if err:
            errors.append(err)
            sections.append(text)
        else:
            body, t = split_topics(text, lingua)
            sections.append(body.rstrip())
            topics += t
    return SEPARATOR.join(sections) + "\n", list(dict.fromkeys(topics)), errors
