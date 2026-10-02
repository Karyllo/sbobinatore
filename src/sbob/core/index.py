"""Livello di navigazione per l'agente, generato SENZA LLM a partire da <corso>/mappa/schede.json e dai file.

Per ogni corso:   <corso>/mappa/INDICE.md          lezioni in ordine, riassunto, concetti, prerequisiti, link ai file
                  <corso>/mappa/concetti/<Nome>.md dove un concetto è introdotto, ripreso, e per cosa è prerequisito
Globale (root):   <root>/mappa/INDICE.md           tutti i corsi + concetti condivisi tra corsi
                  <root>/mappa/catalog.json        lo stesso in JSON, per agenti/script

I link sono Markdown relativi `[testo](<percorso>)`: funzionano in Obsidian e un agente li può seguire come path.
Gli appunti non vengono mai toccati.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sbob.config import Course, Settings
from sbob.core import frontmatter, naming
from sbob.core.batch import atomic_write_text, list_inputs
from sbob.core.layout import Layout

SHORT = {"lez": "Lezione", "ese": "Esercitazione", "lab": "Laboratorio", "sem": "Seminario", "tde": "Tema d'esame"}
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]+')


def load_schede(course: Course) -> dict[str, dict[str, Any]]:
    p = Layout.of(course).mappa / "schede.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_schede(course: Course, schede: dict[str, dict[str, Any]]) -> None:
    atomic_write_text(Layout.of(course).mappa / "schede.json",
                      json.dumps(schede, ensure_ascii=False, indent=2, sort_keys=True))


def concept_file(name: str) -> str:
    return (_UNSAFE.sub("-", name).strip(" .-") or "concetto") + ".md"


def link(text: str, target: Path, start: Path) -> str:
    """Link Markdown relativo da `start` (cartella del file che lo contiene) a `target`."""
    return f"[{text}](<{os.path.relpath(target, start)}>)"


def lesson_title(stem: str) -> str:
    n = naming.parse(stem)
    return f"{SHORT[n.tipo]} {n.num:02d}" + (f" (parte {n.parte})" if n.parte else "") if n else stem


def _lessons(course: Course) -> list[dict[str, Any]]:
    """Tutte le lezioni con almeno una trascrizione o un appunto, con i path e la scheda se c'è."""
    lay = Layout.of(course)
    schede = load_schede(course)
    stems: set[str] = {p.stem for p in list_inputs(lay.trascrizioni, [".md"])}
    stems |= {p.stem.removesuffix(naming.NOTES_SUFFIX) for p in list_inputs(lay.appunti, [".md"])
              if p.stem.endswith(naming.NOTES_SUFFIX)}
    out = []
    for stem in sorted(stems):
        n = naming.parse(stem)
        notes = lay.appunti / f"{stem}{naming.NOTES_SUFFIX}.md"
        tr = lay.trascrizioni / f"{stem}.md"
        s = schede.get(stem, {})
        src = notes if notes.exists() else tr
        argomento = frontmatter.read(src)[0].get("argomento") if src.exists() else None
        out.append({
            "lezione": stem, "titolo": lesson_title(stem) + (f" — {argomento}" if argomento else ""),
            "argomento": argomento,
            "data": n.data.isoformat() if n else None, "tipo": n.tipo if n else None, "numero": n.num if n else None,
            "appunti": str(notes) if notes.exists() else None, "trascrizione": str(tr) if tr.exists() else None,
            "riassunto": s.get("riassunto"), "concetti": s.get("concetti", []), "prerequisiti": s.get("prerequisiti", []),
        })
    return out


def _concept_map(lessons: list[dict[str, Any]]) -> dict[str, dict[str, list[dict]]]:
    concepts: dict[str, dict[str, list[dict]]] = {}
    for l in lessons:
        for c in l["concetti"]:
            role = "introdotto" if c.get("ruolo") == "introdotto" else "ripreso"
            concepts.setdefault(c["nome"], {"introdotto": [], "ripreso": [], "prerequisito_per": []})[role].append(l)
        for p in l["prerequisiti"]:
            concepts.setdefault(p, {"introdotto": [], "ripreso": [], "prerequisito_per": []})["prerequisito_per"].append(l)
    return concepts


def _lesson_link(l: dict, start: Path) -> str:
    label = f"{l['titolo']} · {l['data']}" if l["data"] else l["titolo"]
    target = l["appunti"] or l["trascrizione"]
    return link(label, Path(target), start) if target else label


def render_course(course: Course) -> dict[str, Any]:
    lay = Layout.of(course)
    mappa, cdir = lay.mappa, lay.mappa / "concetti"
    lessons = _lessons(course)
    concepts = _concept_map(lessons)

    lines = [f"# Mappa — {course.nome} ({course.anno_accademico})", "",
             "> Mappa per la navigazione (generata da `sbob indice`, non modificare a mano).",
             "> Per un agente: leggi prima questo file, poi apri solo gli appunti pertinenti.", ""]
    missing = [l for l in lessons if l["riassunto"] is None]
    lines.append(f"**{len(lessons)} lezioni** · {len(concepts)} concetti" +
                 (f" · {len(missing)} senza scheda (lancia `sbob mappa {course.slug}`)" if missing else ""))
    lines.append("")
    for l in lessons:
        lines.append(f"## {_lesson_link(l, mappa)}")
        if l["riassunto"]:
            lines += ["", l["riassunto"]]
        if l["concetti"]:
            names = [link(c["nome"], cdir / concept_file(c["nome"]), mappa) +
                     (" *(nuovo)*" if c.get("ruolo") == "introdotto" else "") for c in l["concetti"]]
            lines += ["", "**Concetti:** " + ", ".join(names)]
        if l["prerequisiti"]:
            lines += ["", "**Prerequisiti:** " + ", ".join(l["prerequisiti"])]
        files = [link(k, Path(l[k]), mappa) for k in ("appunti", "trascrizione") if l[k]]
        if files:
            lines += ["", "File: " + " · ".join(files)]
        lines.append("")
    if concepts:
        lines += ["## Concetti del corso", ""]
        for name in sorted(concepts, key=str.casefold):
            c = concepts[name]
            n = len(c["introdotto"]) + len(c["ripreso"])
            lines.append(f"- {link(name, cdir / concept_file(name), mappa)} — {n} {'lezione' if n == 1 else 'lezioni'}"
                         + (f", prerequisito per {len(c['prerequisito_per'])}" if c["prerequisito_per"] else ""))
    atomic_write_text(mappa / "INDICE.md", "\n".join(lines).rstrip() + "\n")

    if cdir.exists():
        shutil.rmtree(cdir)                      # rigenerata da zero: niente concetti orfani
    for name, c in concepts.items():
        body = [f"# {name}", "", f"Corso: {link(course.nome, mappa / 'INDICE.md', cdir)}", ""]
        for key, title in (("introdotto", "Introdotto in"), ("ripreso", "Ripreso in"),
                           ("prerequisito_per", "Prerequisito per")):
            if c[key]:
                body += [f"## {title}", ""] + [f"- {_lesson_link(l, cdir)}" for l in c[key]] + [""]
        atomic_write_text(cdir / concept_file(name), "\n".join(body).rstrip() + "\n")

    return {"slug": course.slug, "nome": course.nome, "anno": course.anno_accademico,
            "cartella": str(course.cartella), "indice": str(mappa / "INDICE.md"), "lezioni": lessons,
            "concetti": sorted(concepts, key=str.casefold)}


def render_all(settings: Settings, only: list[str] | None = None) -> dict[str, Any]:
    courses = [c for s, c in settings.corsi.items() if not only or s in only]
    rendered = [render_course(c) for c in courses if c.cartella.exists()]
    root = settings.root / "mappa"
    shared: dict[str, list[str]] = {}
    for r in rendered:
        for name in r["concetti"]:
            shared.setdefault(name, []).append(r["slug"])
    lines = ["# Mappa di tutti i corsi", "",
             "> Punto di partenza per un agente: da qui ogni corso ha la sua mappa con riassunti e concetti.",
             f"> Generata il {datetime.now().strftime('%Y-%m-%d %H:%M')} da `sbob indice`.", ""]
    for r in rendered:
        n_sum = sum(1 for l in r["lezioni"] if l["riassunto"])
        lines.append(f"- {link(r['nome'], Path(r['indice']), root)} ({r['anno']}) — "
                     f"{len(r['lezioni'])} lezioni, {n_sum} con scheda, {len(r['concetti'])} concetti")
    cross = {k: v for k, v in shared.items() if len(v) > 1}
    if cross:
        lines += ["", "## Concetti condivisi tra corsi", ""]
        for name in sorted(cross, key=str.casefold):
            lines.append(f"- **{name}**: " + ", ".join(cross[name]))
    if not only:                                 # l'indice globale solo quando li abbiamo tutti
        atomic_write_text(root / "INDICE.md", "\n".join(lines).rstrip() + "\n")
        atomic_write_text(root / "catalog.json", json.dumps(
            {"generato": datetime.now().isoformat(timespec="seconds"), "corsi": rendered},
            ensure_ascii=False, indent=2))
    return {"corsi": rendered, "indice_globale": str(root / "INDICE.md") if not only else None}
