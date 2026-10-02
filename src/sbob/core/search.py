"""Ricerca full-text nei file dei corsi, pensata per un agente: restituisce poche righe giuste con la loro posizione
(corso, lezione, data, sezione, minuto) invece di file interi.

Unità di ricerca = paragrafo. Un paragrafo corrisponde se contiene TUTTI i termini (senza distinzione di
maiuscole e accenti); una frase tra virgolette vale come termine unico. Ordine: più occorrenze prima.
"""

from __future__ import annotations

import re
import shlex
import unicodedata
from pathlib import Path
from typing import Any

from sbob.config import Course, Settings
from sbob.core import frontmatter, index, naming
from sbob.core.batch import list_inputs, list_tree
from sbob.core.layout import Layout

SOURCES = ("mappa", "appunti", "trascrizioni", "materiale")
_HEADING = re.compile(r"^#{1,6}\s+(.*)")
_TIMESTAMP = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


def normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def terms_of(query: str) -> list[str]:
    try:
        parts = shlex.split(query)
    except ValueError:
        parts = query.split()
    return [normalize(p) for p in parts if p.strip()]


def _files(course: Course, source: str) -> list[Path]:
    lay = Layout.of(course)
    if source == "appunti":
        return [p for p in list_inputs(lay.appunti, [".md"]) if p.stem.endswith(naming.NOTES_SUFFIX)]
    if source == "trascrizioni":
        return list_inputs(lay.trascrizioni, [".md"])
    if source == "materiale":
        return list_tree(lay.materiale_md, [".md"])
    return []


def _paragraphs(body: str):
    """(paragrafo, titolo di sezione corrente, ultimo timestamp visto)."""
    heading, stamp = None, None
    buf: list[str] = []
    for line in body.split("\n") + [""]:
        if m := _HEADING.match(line):
            if buf:
                yield "\n".join(buf), heading, stamp
                buf = []
            heading = m.group(1).strip()
            continue
        if not line.strip():
            if buf:
                yield "\n".join(buf), heading, stamp
                buf = []
            continue
        buf.append(line)
        if ts := _TIMESTAMP.findall(line):
            stamp = ts[-1]


def _score(text: str, terms: list[str]) -> int:
    norm = normalize(text)
    if not all(t in norm for t in terms):
        return 0
    return sum(norm.count(t) for t in terms)


def _units(settings: Settings, corsi: list[str] | None, with_archives: bool):
    from sbob.core.archivio import archives_of

    for slug, course in settings.corsi.items():
        if corsi and slug not in corsi:
            continue
        yield course
        if with_archives:
            yield from (a for a in archives_of(course) if a.cartella.exists())


def search(settings: Settings, query: str, corsi: list[str] | None = None,
           dove: tuple[str, ...] = SOURCES, limit: int = 20, snippet_chars: int = 400,
           archivi: str = "auto") -> dict[str, Any]:
    """`archivi`: "auto" = solo l'anno in corso, e se non trova niente anche le edizioni passate (lo dichiara);
    "si" = sempre anche le edizioni passate; "no" = mai."""
    res = _search(settings, query, corsi, dove, limit, snippet_chars, archivi == "si")
    res["archivi_inclusi"] = archivi == "si"
    if archivi == "auto" and res["totale"] == 0 and res.get("termini"):
        wider = _search(settings, query, corsi, dove, limit, snippet_chars, True)
        if wider["totale"]:
            wider["archivi_inclusi"] = True
            wider["nota"] = "Nessun risultato nell'anno in corso: mostro le edizioni passate."
            return wider
    return res


def _search(settings: Settings, query: str, corsi: list[str] | None, dove: tuple[str, ...], limit: int,
            snippet_chars: int, with_archives: bool) -> dict[str, Any]:
    terms = terms_of(query)
    hits: list[dict[str, Any]] = []
    if not terms:
        return {"query": query, "risultati": [], "totale": 0}
    for course in _units(settings, corsi, with_archives):
        slug = course.slug
        edizione = course.anno_accademico
        if "mappa" in dove:
            for stem, card in index.load_schede(course).items():
                text = card.get("riassunto", "") + " " + " ".join(c["nome"] for c in card.get("concetti", []))
                if s := _score(text, terms):
                    hits.append({"corso": slug, "edizione": edizione, "fonte": "mappa", "lezione": stem, "punteggio": s + 5,
                                 "testo": card.get("riassunto", ""),
                                 "file": str(Layout.of(course).appunti / f"{stem}{naming.NOTES_SUFFIX}.md")})
        for source in dove:
            for path in _files(course, source):
                _, body = frontmatter.read(path)
                stem = path.stem.removesuffix(naming.NOTES_SUFFIX)
                for para, heading, stamp in _paragraphs(body):
                    if s := _score(para, terms):
                        text = " ".join(para.split())
                        hits.append({"corso": slug, "edizione": edizione, "fonte": source, "lezione": stem, "punteggio": s,
                                     "sezione": heading, "minuto": stamp, "file": str(path),
                                     "testo": text[:snippet_chars] + ("…" if len(text) > snippet_chars else "")})
    hits.sort(key=lambda h: (-h["punteggio"], h["edizione"] != settings.corsi[h["corso"]].anno_accademico, h["lezione"]))
    for h in hits:
        n = naming.parse(h["lezione"])
        h["data"] = n.data.isoformat() if n else None
    return {"query": query, "termini": terms, "totale": len(hits), "risultati": hits[:limit]}
