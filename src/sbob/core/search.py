"""Ricerca full-text nei file dei corsi, pensata per un agente: restituisce poche righe giuste con la loro posizione
(corso, lezione, data, sezione, minuto) invece di file interi.

Unità di ricerca = paragrafo. Un paragrafo corrisponde se contiene TUTTI i termini (senza distinzione di
maiuscole e accenti); una frase tra virgolette vale come termine unico.

Dove cerca, di default: mappa, appunti e materiale. Le trascrizioni (il parlato grezzo, con esitazioni e ripensamenti)
solo se le chiedi (`--in trascrizioni`) o se negli altri non c'è niente.

Ordine: per pertinenza, non per lunghezza. Punteggio = peso della fonte × densità dei termini nel paragrafo (cresce
con le occorrenze ma non con la lunghezza) + bonus se i termini sono nel titolo della sezione o tra i concetti della
mappa. Si mostra la migliore lezione per prima, e una lezione conta una volta sola nell'elenco `lezioni`.
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
DEFAULT_SOURCES = ("mappa", "appunti", "materiale")             # le trascrizioni solo su richiesta o come ripiego
WEIGHT = {"mappa": 1.5, "appunti": 1.2, "materiale": 1.0, "trascrizioni": 0.6}
HEADING_BONUS = 3.0
CONCEPT_BONUS = 4.0
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


def lesson_label(stem: str) -> str:
    """'2025-12-05_analisi_matematica_1_lez28' → 'Lezione 28 · 05/12/2025'."""
    n = naming.parse(stem)
    if not n:
        return stem
    kinds = {"lez": "Lezione", "ese": "Esercitazione", "lab": "Laboratorio", "sem": "Seminario", "tde": "Tema d'esame"}
    return f"{kinds.get(n.tipo, n.tipo)} {n.num:02d} · {n.data.strftime('%d/%m/%Y')}"


def relevance(source: str, text: str, terms: list[str], heading: str | None = None, concept_hit: bool = False) -> float:
    """0 se il paragrafo non contiene tutti i termini. Altrimenti peso × densità + bonus (titolo di sezione, concetto)."""
    count = _score(text, terms)
    if not count:
        return 0.0
    density = count / max(1, len(text.split())) ** 0.5            # sublineare: un paragrafo lungo non vince per lunghezza
    score = WEIGHT.get(source, 1.0) * density
    if heading and all(t in normalize(heading) for t in terms):
        score += HEADING_BONUS
    elif heading and any(t in normalize(heading) for t in terms):
        score += HEADING_BONUS / 3
    return score + (CONCEPT_BONUS if concept_hit else 0.0)


def snippet(text: str, terms: list[str], chars: int = 400) -> str:
    """Finestra di testo centrata sulla prima occorrenza (non l'inizio del paragrafo, che spesso non c'entra)."""
    text = " ".join(text.split())
    if len(text) <= chars:
        return text
    norm = normalize(text)
    pos = min((i for t in terms if (i := norm.find(t)) >= 0), default=0) if len(norm) == len(text) else 0
    start = max(0, min(pos - chars // 3, len(text) - chars))
    out = text[start:start + chars]
    return ("…" if start > 0 else "") + out + ("…" if start + chars < len(text) else "")


def highlight(text: str, terms: list[str]) -> str:
    """Evidenzia i termini con il markup di Rich (solo se normalizzare non cambia le lunghezze, altrimenti nessuna)."""
    norm = normalize(text)
    if len(norm) != len(text):
        return text.replace("[", "\\[")
    spans = sorted((m.start(), m.end()) for t in terms for m in re.finditer(re.escape(t), norm))
    out, last = [], 0
    for a, b in spans:
        if a < last:
            continue
        out.append(text[last:a].replace("[", "\\["))
        out.append(f"[bold yellow]{text[a:b]}[/bold yellow]")
        last = b
    out.append(text[last:].replace("[", "\\["))
    return "".join(out)


def _units(settings: Settings, corsi: list[str] | None, with_archives: bool):
    from sbob.core.archivio import archives_of

    for slug, course in settings.corsi.items():
        if corsi and slug not in corsi:
            continue
        yield course
        if with_archives:
            yield from (a for a in archives_of(course) if a.cartella.exists())


def search(settings: Settings, query: str, corsi: list[str] | None = None,
           dove: tuple[str, ...] | None = None, limit: int = 20, snippet_chars: int = 400,
           archivi: str = "auto") -> dict[str, Any]:
    """`dove`: None = mappa, appunti e materiale (e le trascrizioni solo se lì non c'è niente, lo dichiara in `nota`).
    `archivi`: "auto" = solo l'anno in corso, e se non trova niente anche le edizioni passate (lo dichiara);
    "si" = sempre anche le edizioni passate; "no" = mai."""
    explicit = bool(dove)
    sources: tuple[str, ...] = tuple(dove) if dove else DEFAULT_SOURCES
    res = _search(settings, query, corsi, sources, limit, snippet_chars, archivi == "si")
    res["archivi_inclusi"] = archivi == "si"
    if archivi == "auto" and res["totale"] == 0 and res.get("termini"):
        wider = _search(settings, query, corsi, sources, limit, snippet_chars, True)
        if wider["totale"]:
            wider["archivi_inclusi"] = True
            wider["nota"] = "Nessun risultato nell'anno in corso: mostro le edizioni passate."
            return wider
    if not explicit and res["totale"] == 0 and res.get("termini"):
        spoken = _search(settings, query, corsi, ("trascrizioni",), limit, snippet_chars, res["archivi_inclusi"])
        if spoken["totale"]:
            spoken["archivi_inclusi"] = res["archivi_inclusi"]
            spoken["nota"] = ("Non compare negli appunti, nella mappa né nel materiale: ecco dove il docente ne parla "
                              "nelle trascrizioni (parlato grezzo).")
            return spoken
    return res


def _search(settings: Settings, query: str, corsi: list[str] | None, dove: tuple[str, ...], limit: int,
            snippet_chars: int, with_archives: bool) -> dict[str, Any]:
    terms = terms_of(query)
    hits: list[dict[str, Any]] = []
    if not terms:
        return {"query": query, "risultati": [], "lezioni": [], "totale": 0}
    for course in _units(settings, corsi, with_archives):
        slug = course.slug
        edizione = course.anno_accademico
        if "mappa" in dove:
            for stem, card in index.load_schede(course).items():
                names = " ".join(c["nome"] for c in card.get("concetti", []))
                text = card.get("riassunto", "") + " " + names
                if s := relevance("mappa", text, terms, concept_hit=bool(_score(names, terms))):
                    hits.append({"corso": slug, "edizione": edizione, "fonte": "mappa", "lezione": stem, "punteggio": round(s, 3),
                                 "testo": card.get("riassunto", ""),
                                 "file": str(Layout.of(course).appunti / f"{stem}{naming.NOTES_SUFFIX}.md")})
        for source in dove:
            for path in _files(course, source):
                _, body = frontmatter.read(path)
                stem = path.stem.removesuffix(naming.NOTES_SUFFIX)
                for para, heading, stamp in _paragraphs(body):
                    if s := relevance(source, para, terms, heading):
                        hits.append({"corso": slug, "edizione": edizione, "fonte": source, "lezione": stem,
                                     "punteggio": round(s, 3), "sezione": heading, "minuto": stamp, "file": str(path),
                                     "testo": snippet(para, terms, snippet_chars)})
    own_year = {slug: c.anno_accademico for slug, c in settings.corsi.items()}
    hits.sort(key=lambda h: (-h["punteggio"], h["edizione"] != own_year.get(h["corso"]), h["lezione"]))
    for h in hits:
        n = naming.parse(h["lezione"])
        h["data"] = n.data.isoformat() if n else None
    # una riga per lezione: la sua migliore corrispondenza, quante ce ne sono e in quali sezioni (per orientarsi)
    lessons: dict[tuple[str, str, str], dict[str, Any]] = {}
    for h in hits:                                           # già in ordine di pertinenza: il primo è il migliore
        key = (h["corso"], h["edizione"], h["lezione"])
        entry = lessons.setdefault(key, {"corso": h["corso"], "edizione": h["edizione"], "lezione": h["lezione"],
                                        "data": h["data"], "punteggio": h["punteggio"], "paragrafi": 0, "fonti": [],
                                        "sezioni": [], "migliore": h})
        if entry["migliore"]["fonte"] == "mappa" and h["fonte"] != "mappa":
            entry["migliore"] = h                          # come esempio, il paragrafo (con la sezione) meglio del riassunto
        entry["paragrafi"] += 1
        if h["fonte"] not in entry["fonti"]:
            entry["fonti"].append(h["fonte"])
        if h.get("sezione") and h["sezione"] not in entry["sezioni"] and len(entry["sezioni"]) < 4:
            entry["sezioni"].append(h["sezione"])
    ranked = sorted(lessons.values(), key=lambda e: (-e["punteggio"], e["data"] or "", e["lezione"]))
    return {"query": query, "termini": terms, "totale": len(hits), "risultati": hits[:limit], "lezioni": ranked}
