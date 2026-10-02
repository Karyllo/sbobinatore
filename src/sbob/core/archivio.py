"""Edizioni passate di un corso (stesso docente) come sottocartelle: <corso>/archivio/<anno>/.

Un'edizione passata NON è un corso nuovo in config: si deriva un `Course` che punta alla sua cartella, con lo stesso
slug e nomi di file, e si riusano i passi di sempre. Lo stato (manifest, cache, costi) è separato in <anno>/.sbob.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from typing import Any

from sbob.config import ConfigError, Course

_CODE = re.compile(r"^\s*(\d{4,6})\s*-\s*")
_PAREN = re.compile(r"\(([^()]*)\)")
DEFAULT_ARCHIVE_SOURCES = [{"tipo": "archivio"}, {"tipo": "txt", "file": "link.txt"}]


def parse_fullname(fullname: str) -> tuple[str | None, str | None]:
    """'078046 - TITOLO (2) (ZUNINO PAOLO) [2025-26]' → ('078046', 'ZUNINO PAOLO').
    Il docente è l'ULTIMA parentesi non numerica: salta '(1)', '(2)' e funziona anche con '[Semestre 2] (DOCENTE)'."""
    code = m.group(1) if (m := _CODE.match(fullname)) else None
    names = [p.strip() for p in _PAREN.findall(fullname) if re.search(r"[^\W\d_]", p) and not p.strip().isdigit()]
    return code, (names[-1] if names else None)


def _tokens(name: str) -> set[str]:
    nfkd = unicodedata.normalize("NFKD", name.casefold())
    return set(re.findall(r"[a-z]+", "".join(c for c in nfkd if not unicodedata.combining(c))))


def same_teacher(a: str | None, b: str | None) -> bool:
    """'CARELLO GIULIANA' = 'Giuliana Carello'; tollera il nome troncato ('FRATERNALI P') e un solo cognome."""
    if not a or not b:
        return False
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return all(any(t == u or (len(t) == 1 and u.startswith(t)) for u in long_) for t in short)


def current_teacher(course: Course, webeep_courses: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """(codice, docente) del corso: docente scelto in config, altrimenti quello del corso WeBeep collegato."""
    if not course.webeep_id:
        raise ConfigError(f"Collega prima il corso a WeBeep: sbob webeep collega {course.slug} <id>")
    cur = next((c for c in webeep_courses if c["id"] == course.webeep_id), None)
    if cur is None:
        raise ConfigError(f"Il corso WeBeep {course.webeep_id} non è tra i tuoi corsi (sbob webeep corsi)")
    code, teacher = parse_fullname(cur["nome"])
    return code, (course.archivio_docente or teacher)


def candidates(course: Course, webeep_courses: list[dict[str, Any]]) -> dict[str, Any]:
    """Edizioni passate del corso: {"codice", "docente", "trovate": {anno: [corsi]}, "altri_docenti": {anno: [corsi]}}.
    `trovate` = stesso codice e stesso docente; `altri_docenti` = stesso codice, docente diverso."""
    code, teacher = current_teacher(course, webeep_courses)
    found: dict[str, list[dict]] = {}
    others: dict[str, list[dict]] = {}
    for c in webeep_courses:
        c_code, c_teacher = parse_fullname(c["nome"])
        if c_code != code or not c["anno"] or c["id"] == course.webeep_id or c["anno"] == course.anno_accademico:
            continue
        info = {**c, "docente": c_teacher}
        (found if same_teacher(c_teacher, teacher) else others).setdefault(c["anno"], []).append(info)
    return {"codice": code, "docente": teacher, "trovate": found, "altri_docenti": others}


def teachers_for_code(course: Course, webeep_courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Docenti che hanno tenuto lo stesso codice: [{"docente", "anni": [...], "ids": [...]}] (per `docenti`)."""
    code, _ = current_teacher(course, webeep_courses)
    out: dict[str, dict] = {}
    for c in webeep_courses:
        c_code, c_teacher = parse_fullname(c["nome"])
        if c_code != code or not c_teacher:
            continue
        key = next((k for k in out if same_teacher(k, c_teacher)), c_teacher)
        e = out.setdefault(key, {"docente": key, "anni": [], "ids": []})
        e["anni"].append(c["anno"]); e["ids"].append(c["id"])
    return sorted(out.values(), key=lambda e: e["docente"])


def archive_course(course: Course, anno: str) -> Course:
    """Il Course dell'edizione `anno`: cartella <corso>/archivio/<anno>, stesso slug, fonti di default [archivio, link.txt]."""
    if anno not in course.archivio:
        have = ", ".join(sorted(course.archivio)) or "nessuna"
        raise ConfigError(f"Nessuna edizione '{anno}' per {course.slug} (configurate: {have}). "
                          f"Aggiungila con: sbob archivio {course.slug} aggiungi {anno}")
    return replace(course, cartella=course.cartella / "archivio" / anno, anno_accademico=anno,
                   webeep_id=course.archivio[anno], sorgenti=[dict(s) for s in DEFAULT_ARCHIVE_SOURCES],
                   sorgente=dict(DEFAULT_ARCHIVE_SOURCES[0]), materiale=None, archivio={}, archivio_docente=None,
                   archivio_di=course.slug, extra={k: v for k, v in course.extra.items() if k != "materiale_siti"})


def archives_of(course: Course) -> list[Course]:
    """Tutte le edizioni passate configurate, dalla più recente."""
    return [archive_course(course, a) for a in sorted(course.archivio, reverse=True)]
